"""外部 AI 运行器：zip 上传校验、docker 容器内编译、沙箱运行、AI 对战。

安全边界（MVP）：AI 二进制只在容器内运行，--network none、无新镜像拉取、
内存 256MB、pids 上限、可配置超时；zip 解包防路径穿越、白名单扩展名。
"""

from __future__ import annotations

import io
import json
import subprocess
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import engine
from .engine import Wall

# 上传的 AI 存放目录（仓库 data/ais/<aid>/src + Dockerfile + meta.json）
AI_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "ais"
BASE_IMAGE = "gcc:14-bookworm"  # 编译/运行基镜像（需预先 docker load 导入）
EXAMPLE_DIR = Path(__file__).resolve().parent / "ai_cpp_example"  # 作者模板目录

# 内置默认 AI：aid → (主源文件, 展示名)。源码与作者模板同源，首次使用时自动在容器内编译。
BUILTINS: dict[str, tuple[str, str]] = {
    "builtin-greedy": ("ai_example.cpp", "贪心示例（内置）"),
    "builtin-random": ("ai_random.cpp", "随机示例（内置）"),
}
BUILTIN_FILES = ("ai_common.hpp", "json.hpp")  # 主文件之外的公共文件

ALLOWED_SUFFIX = {".cpp", ".cc", ".c", ".h", ".hpp"}  # zip 内允许的源码扩展名
MAX_FILES = 64  # 单个 zip 最多文件数
MAX_TOTAL = 8 * 1024 * 1024  # 解压后总大小上限 8MB
BUILD_TIMEOUT = 240  # docker build 超时（秒）
RUN_CONTAINER_TIMEOUT = 30  # 单步容器运行超时上限（秒）
DEFAULT_MEMORY_MB = 256  # 容器内存默认（MiB）
MIN_MEMORY_MB = 64  # 容器内存下限（MiB）
MAX_MEMORY_MB = 2048  # 容器内存上限（MiB）
MAX_BATCH_GAMES = 1000  # 批量对战局数上限
MAX_BATCH_PARALLEL = 16  # 批量对战并行数上限
MAX_JOBS = 10  # 内存中保留的批量任务数（超出的最旧任务被清理）

_BUILD_LOCK = threading.Lock()  # 同镜像并发构建加锁（日常运行 inspect 不走锁）
JOBS: dict[str, dict] = {}  # job_id → 批量任务（含 threading.Lock）


class AIError(ValueError):
    """AI 上传/编译/运行失败（message 可直接返回给作者排查）。"""


def image_tag(aid: str) -> str:
    return f"quoridor-ai:{aid}"


def _aid_dir(aid: str) -> Path:
    d = AI_DIR / aid
    if not d.is_dir():
        raise AIError(f"AI 不存在：{aid}")
    return d


def save_ai_zip(data: bytes, filename: str) -> str:
    """校验并解包上传的 zip，返回 aid。"""
    if not filename.lower().endswith(".zip"):
        raise AIError("只接受 .zip 文件")
    if len(data) > MAX_TOTAL:
        raise AIError("zip 过大（上限 8MB）")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise AIError("不是有效的 zip 文件")
    names = [i.filename for i in zf.infolist() if not i.is_dir()]
    if not names:
        raise AIError("zip 为空")
    if len(names) > MAX_FILES:
        raise AIError(f"文件过多（上限 {MAX_FILES} 个）")
    total = sum(i.file_size for i in zf.infolist())
    if total > MAX_TOTAL:
        raise AIError("解压后过大（上限 8MB）")
    for n in names:
        p = Path(n)
        if p.is_absolute() or ".." in p.parts:
            raise AIError(f"非法路径：{n}")
        if p.suffix.lower() not in ALLOWED_SUFFIX:
            raise AIError(f"不允许的文件类型：{n}（仅 {sorted(ALLOWED_SUFFIX)}）")
    if not any(Path(n).suffix.lower() in (".cpp", ".cc", ".c") for n in names):
        raise AIError("zip 内须至少包含一个 .cpp/.cc/.c 源文件")
    aid = uuid.uuid4().hex[:8]
    src = AI_DIR / aid / "src"
    src.mkdir(parents=True)
    for n in names:
        dest = src / Path(n).name  # 拍平存放（AI 为单目录工程）
        dest.write_bytes(zf.read(n))
    (AI_DIR / aid / "meta.json").write_text(
        json.dumps({"aid": aid, "name": filename}, ensure_ascii=False), encoding="utf-8"
    )
    return aid


def build_ai(aid: str, timeout: int = BUILD_TIMEOUT) -> tuple[bool, str]:
    """在容器内编译 AI 源码，生成运行镜像。返回 (成功, 日志尾)。

    上传 zip 无需包含 ai_common.hpp / json.hpp：缺失时后端自动补入模板版本；
    若作者自带同名文件，以作者的为准。
    """
    d = _aid_dir(aid)
    src = d / "src"
    for f in BUILTIN_FILES:
        if not (src / f).exists():
            (src / f).write_bytes((EXAMPLE_DIR / f).read_bytes())
    (d / "Dockerfile").write_text(
        f"FROM {BASE_IMAGE}\n"
        "WORKDIR /ai\n"
        "COPY src/ /ai/src/\n"
        'RUN sh -c \'g++ -std=c++17 -O2 -o /ai/ai $(find /ai/src -name "*.cpp" -o -name "*.cc" -o -name "*.c")\'\n'
        'ENTRYPOINT ["/ai/ai"]\n',
        encoding="utf-8",
    )
    try:
        p = subprocess.run(
            ["docker", "build", "-t", image_tag(aid), "."],
            cwd=d, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise AIError("服务器未安装 docker")
    except subprocess.TimeoutExpired:
        raise AIError(f"编译超时（{timeout}s）")
    log = (p.stdout + p.stderr)[-3000:]
    if p.returncode != 0:
        raise AIError(f"编译失败：\n{log}")
    return True, log


def image_built(aid: str) -> bool:
    """该 AI 是否已有编译好的运行镜像。"""
    p = subprocess.run(
        ["docker", "image", "inspect", image_tag(aid)],
        capture_output=True, text=True, timeout=15,
    )
    return p.returncode == 0


def ensure_builtin(aid: str) -> None:
    """物化内置 AI 源码并在缺镜像时编译（与作者上传走同一套容器内编译）。"""
    if aid not in BUILTINS:
        return
    import hashlib
    main_src, name = BUILTINS[aid]
    src = AI_DIR / aid / "src"
    src.mkdir(parents=True, exist_ok=True)
    # 内置源码与作者模板同源，每次同步，保证镜像与模板一致
    blobs = {Path(main_src).name: (EXAMPLE_DIR / main_src).read_bytes()}
    for f in BUILTIN_FILES:
        blobs[f] = (EXAMPLE_DIR / f).read_bytes()
    for fname, data in blobs.items():
        (src / fname).write_bytes(data)
    digest = hashlib.sha256(b"".join(blobs[f] for f in sorted(blobs))).hexdigest()[:16]
    meta_path = AI_DIR / aid / "meta.json"
    try:
        built_digest = json.loads(meta_path.read_text(encoding="utf-8")).get("digest")
    except (OSError, ValueError):
        built_digest = None
    # 常见路径：已是最新构建，直接返回（无锁，不阻塞批量并行每步的 inspect）
    if built_digest == digest and image_built(aid):
        return
    with _BUILD_LOCK:
        # 锁内复查：等锁期间另一线程可能已构建好
        try:
            cur = json.loads(meta_path.read_text(encoding="utf-8")).get("digest")
        except (OSError, ValueError):
            cur = None
        if cur == digest and image_built(aid):
            return
        build_ai(aid)
        meta_path.write_text(
            json.dumps({"aid": aid, "name": name, "builtin": True, "digest": digest}, ensure_ascii=False),
            encoding="utf-8",
        )


def ensure_image(aid: str) -> None:
    """确保 aid 可运行：内置自动构建；上传的须已编译过，否则报错。"""
    if aid in BUILTINS:
        ensure_builtin(aid)
        return
    _aid_dir(aid)
    if not image_built(aid):
        raise AIError(f"AI 未编译：{aid}（先 POST /api/ai/upload 构建）")


def run_ai(aid: str, payload: dict, timeout: float = 5,
           memory_mb: int = DEFAULT_MEMORY_MB) -> dict:
    """运行 AI 容器一步：stdin 输入 json，取 stdout 最后一行非空解析为决策。

    决策格式同 ai_cpp_example：{"type":"move",...} / {"type":"wall",...}，
    或带技能 {"skill":..,"to":..,"action":{...}}；选牌阶段返回 {"skills":[...]}。
    参数先校验（坏参数直接报错，不浪费一次镜像构建）。
    """
    if timeout <= 0 or timeout > RUN_CONTAINER_TIMEOUT:
        raise AIError(f"超时须在 (0,{RUN_CONTAINER_TIMEOUT}] 秒内")
    if not MIN_MEMORY_MB <= memory_mb <= MAX_MEMORY_MB:
        raise AIError(f"内存须在 [{MIN_MEMORY_MB},{MAX_MEMORY_MB}] MiB 内")
    ensure_image(aid)
    try:
        p = subprocess.run(
            ["docker", "run", "--rm", "-i", "--network", "none",
             f"--memory={memory_mb}m", "--pids-limit=64", "--cpus=0.5", image_tag(aid)],
            input=json.dumps(payload), capture_output=True, text=True, timeout=timeout + 5,
        )
    except FileNotFoundError:
        raise AIError("服务器未安装 docker")
    except subprocess.TimeoutExpired:
        raise AIError(f"AI 运行超时（>{timeout}s），判负")
    if p.returncode != 0:
        raise AIError(f"AI 进程异常退出（code={p.returncode}）：{(p.stderr or '')[-500:]}")
    lines = [ln for ln in (p.stdout or "").splitlines() if ln.strip()]
    if not lines:
        raise AIError("AI 无输出")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        raise AIError(f"AI 输出不是合法 json：{lines[-1][:200]}")


def ai_goals(aid: str, n: int, m: int, deads: set, sands: set, timeout: float = 5,
             memory_mb: int = DEFAULT_MEMORY_MB) -> tuple[list[int], list[int]]:
    """AI 出题：跑出题阶段容器，返回校验过的 A/B 列集（非法出题抛 AIError）。
    出题能看到预览地形（与建局地形一致，见 /api/map/preview）。"""
    if not 9 <= m <= 15:
        raise AIError(f"m 须在 [9,15] 内：{m}")
    out = run_ai(aid, {"phase": "goals", "n": n, "m": m,
                       "deads": sorted(deads), "sands": sorted(sands)}, timeout, memory_mb)
    try:
        return engine.validate_goal_sets(m, out["goal_A"], out["goal_B"])
    except (ValueError, KeyError, TypeError) as e:
        raise AIError(f"出题非法：{e}")


def ai_side(aid: str, n: int, m: int, deads: set, sands: set,
            goal_A: list[int], goal_B: list[int], timeout: float = 5,
            memory_mb: int = DEFAULT_MEMORY_MB) -> str:
    """AI 选边：跑选边阶段容器，返回 first（先手+A）或 second（后手+B）。"""
    out = run_ai(aid, {"phase": "side", "n": n, "m": m,
                       "deads": sorted(deads), "sands": sorted(sands),
                       "goal_A": goal_A, "goal_B": goal_B}, timeout, memory_mb)
    side = out.get("side") if isinstance(out, dict) else None
    if side not in ("first", "second"):
        raise AIError(f"选边非法（须 first/second）：{str(out)[:200]}")
    return side


# "random" 为保留别名：指向容器随机示例（引擎内置 Python 随机已删除）。
RANDOM_SIDE = "builtin-random"


def select_for(side: str, st: engine.GameState, timeout: float,
               memory_mb: int = DEFAULT_MEMORY_MB) -> list[str]:
    """某席位的赛前选牌：跑选牌阶段容器（random 即容器随机）。"""
    if side == "random":
        side = RANDOM_SIDE
    out = run_ai(side, {"phase": "select", "skill_k": st.skill_k, "n": st.n, "m": st.m,
                        "deads": sorted(st.deads), "sands": sorted(st.sands),
                        "goal_A": st.goal_A, "goal_B": st.goal_B}, timeout, memory_mb)
    skills = out.get("skills")
    if not isinstance(skills, list) or len(skills) != st.skill_k:
        raise AIError(f"选牌输出非法（须恰好 {st.skill_k} 张）：{str(out)[:200]}")
    return skills


def _step_side(side: str, st: engine.GameState, timeout: float,
               memory_mb: int = DEFAULT_MEMORY_MB, log: list | None = None) -> dict:
    """某席位行动一轮并落到引擎；返回 {"skill":..,"actions":[已执行..],"applied":n}。

    新协议一次输出覆盖整轮：{"skill":..,"to":..,"actions":[{..},...]}。
    形状错误（非对象、actions 缺失/不是 1~2 个对象）直接判负；
    技能与首个行动非法直接判负；执行中轮到对方或终局后，
    剩余 actions 作废（不再校验，比如首步踩流沙后第二步无论是什么都作废）。
    """
    if side == "random":
        side = RANDOM_SIDE
    me = st.turn
    d = run_ai(side, st.to_ai_dict(), timeout, memory_mb)
    if log is not None:
        log.append(json.loads(json.dumps(d)))  # 存档原始决策，供批量棋谱按需重建
    if not isinstance(d, dict):
        raise AIError(f"决策须为 json 对象：{str(d)[:200]}")
    actions = d.get("actions")
    if (not isinstance(actions, list) or not 1 <= len(actions) <= 2
            or not all(isinstance(a, dict) for a in actions)):
        raise AIError(f"actions 须为 1~2 个行动对象：{str(d)[:200]}")
    played = None
    if "skill" in d:
        try:
            engine.play_skill(st, d["skill"], d.get("to"))
        except (ValueError, KeyError, TypeError) as e:
            raise AIError(f"打出手牌非法（{d.get('skill')}）：{e}")
        played = {"skill": d["skill"], "to": d.get("to")}
    if d.get("skill") == "double_move" and len(actions) != 2:
        raise AIError("连续行动须一次输出两步")
    done = []
    for a in actions:
        if st.winner is not None or st.turn != me:
            break  # 轮次已结束（走子获胜/踩流沙/换人），剩余作废
        t = a.get("type")
        try:
            if t == "move":
                engine.apply_pawn_move(st, a["to"])
            elif t == "wall":
                engine.apply_wall(st, Wall.from_dict(a["wall"]))
            else:
                raise AIError(f"未知行动类型：{t}")
        except (ValueError, KeyError, TypeError) as e:
            raise AIError(f"行动非法：{e}")
        done.append({"type": t})
    return {"player": me, "skill": played, "actions": done, "applied": len(done)}


def apply_external_decision(aid: str, st: engine.GameState, timeout: float,
                            memory_mb: int = DEFAULT_MEMORY_MB) -> dict:
    """单步接口用：aid 跑整轮决策并落到引擎（random 即容器随机）。"""
    return _step_side(aid, st, timeout, memory_mb)


def play_match(white: str, black: str, n: int | None = None, m: int | None = None,
               seed: int | None = None, max_plies: int = 800, timeout: float = 5,
               memory_mb: int = DEFAULT_MEMORY_MB, chooser: str = "random",
               on_ply=None) -> dict:
    """AI 对战：side 为 "random" 或已编译的 aid；犯规/超时者判负，超步数判平局。

    出题流程（与前端向导一致）：chooser（white/black/random 之一表出题方，
    random 即抛硬币）先定 A/B 列集，另一方再选边；出题方为 aid 则跑出题容器，
    选边方为 aid 则跑选边容器，否则引擎随机。
    seed 为空时现场随机一个并记录，保证结果可复现、可重建。
    on_ply(plies) 每轮回调一次（批量进度用）；返回含决策日志 log 与双方选牌
    skills，供批量棋谱按需重建（/match 接口会剥离这两项）。
    """
    import random as _random
    if max_plies <= 0 or max_plies > 3000:
        raise AIError("max_plies 须在 (0,3000] 内")
    if chooser not in ("white", "black", "random"):
        raise AIError("chooser 须为 white/black/random")
    if seed is None:
        seed = _random.Random().randint(0, 2 ** 31 - 1)
    rng = _random.Random(seed)
    n = n or rng.randint(9, 15)
    m = m or rng.randint(9, 15)
    if not (9 <= n <= 15 and 9 <= m <= 15):
        raise AIError("n, m 必须在 [9,15] 内")
    sides = [white, black]
    chooser_side = chooser if chooser != "random" else rng.choice(["white", "black"])
    chooser_idx = 0 if chooser_side == "white" else 1
    chooser_id, picker_id = sides[chooser_idx], sides[1 - chooser_idx]
    # 地形与目标列无关：先预览地形（与随后建局的地形一致），出题/选边都看真地图
    _pv = engine.new_game(n=n, m=m, seed=seed,
                          goal_A=list(range(m // 2)), goal_B=list(range(m // 2)))
    terrain_d, terrain_s = _pv.deads, _pv.sands
    try:
        if chooser_id == "random":
            goal_A, goal_B = engine.pick_goal_sets(m, rng)
        else:
            ensure_image(chooser_id)
            goal_A, goal_B = ai_goals(chooser_id, n, m, terrain_d, terrain_s, timeout, memory_mb)
        if picker_id == "random":
            picker_side = rng.choice(["first", "second"])
        else:
            ensure_image(picker_id)
            picker_side = ai_side(picker_id, n, m, terrain_d, terrain_s, goal_A, goal_B, timeout, memory_mb)
    except AIError as e:
        raise AIError(f"出题/选边失败：{e}")
    st = engine.new_game(n=n, m=m, seed=seed, goal_A=goal_A, goal_B=goal_B)
    # picker 选 first 即坐先手席（seat0），否则坐后手席；sides 即席位顺序
    sides = [picker_id, chooser_id] if picker_side == "first" else [chooser_id, picker_id]
    log: list = []  # 每轮原始决策（按需重建棋谱用）
    skills: list = [[], []]
    for pl, side in enumerate(sides):
        if side != "random":
            try:
                ensure_image(side)
            except AIError as e:
                st.winner = 1 - pl
                st.win_reason = f"{'先手' if pl == 0 else '后手'}AI 不可用：{e}"
                return {"winner": st.winner, "win_reason": st.win_reason, "plies": 0,
                        "sides": sides, "state": st.to_dict()}
        try:
            picks = select_for(side, st, timeout, memory_mb)
            skills[pl] = picks
            engine.select_skills(st, pl, picks)
        except AIError as e:
            st.winner = 1 - pl
            st.win_reason = f"{'先手' if pl == 0 else '后手'}AI 选牌犯规：{e}"
            return {"winner": st.winner, "win_reason": st.win_reason, "plies": 0,
                    "sides": sides, "state": st.to_dict()}
    plies = 0
    while st.winner is None and plies < max_plies:
        side = sides[st.turn]
        try:
            plies += _step_side(side, st, timeout, memory_mb, log)["applied"]
        except AIError as e:
            st.winner = 1 - st.turn
            st.win_reason = f"{'先手' if st.turn == 0 else '后手'}AI 犯规：{e}"
            break
        plies += 1
        if on_ply is not None:
            on_ply(plies)
    if st.winner is None:
        st.winner = -1  # 超出步数上限判平局
        st.win_reason = f"达到步数上限（{max_plies}），判平局"
    return {"winner": st.winner, "win_reason": st.win_reason, "plies": plies,
            "sides": sides, "chooser": chooser_side, "picker_side": picker_side,
            "goal_A": goal_A, "goal_B": goal_B, "state": st.to_dict(),
            "n": n, "m": m, "seed": seed, "skills": skills, "log": log}


def start_batch(white: str, black: str, n: int | None, m: int | None, seed: int | None,
                games: int, max_parallel: int, max_plies: int = 800,
                timeout: float = 5, memory_mb: int = DEFAULT_MEMORY_MB,
                chooser: str = "random") -> str:
    """批量对战：N 局（seed=base+i，每局独立出题/选边/选牌），后台线程池跑。

    立即返回 job_id；进度与结果轮询 get_batch；单局棋谱按需 rebuild_batch_game。
    每局只存决策日志（约数十 KB），1000 局常驻内存可控。
    """
    import random as _random
    if not 1 <= games <= MAX_BATCH_GAMES:
        raise AIError(f"对局数须在 [1,{MAX_BATCH_GAMES}] 内")
    if not 1 <= max_parallel <= MAX_BATCH_PARALLEL:
        raise AIError(f"并行数须在 [1,{MAX_BATCH_PARALLEL}] 内")
    if n is not None and not 9 <= n <= 15:
        raise AIError("n 必须在 [9,15] 内")
    if m is not None and not 9 <= m <= 15:
        raise AIError("m 必须在 [9,15] 内")
    base = seed if seed is not None else _random.Random().randint(0, 2 ** 31 - 1)
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {
        "job_id": job_id, "white": white, "black": black, "n": n, "m": m,
        "base_seed": base, "games": games, "max_parallel": max_parallel,
        "max_plies": max_plies, "timeout": timeout, "memory_mb": memory_mb,
        "chooser": chooser, "total": games, "done": 0, "finished": False,
        "running": {}, "results": [], "tables": [None] * games,
        "lock": threading.Lock(),
    }
    while len(JOBS) > MAX_JOBS:  # 只保留最近的任务
        oldest = next(k for k in JOBS if k != job_id)
        del JOBS[oldest]
    threading.Thread(target=_run_batch, args=(job_id,), daemon=True).start()
    return job_id


def _run_batch(job_id: str) -> None:
    job = JOBS[job_id]
    try:
        with ThreadPoolExecutor(max_workers=job["max_parallel"]) as ex:
            list(ex.map(lambda i: _batch_one(job_id, i), range(job["games"])))
    finally:
        with job["lock"]:
            job["finished"] = True


def _batch_one(job_id: str, i: int) -> None:
    job = JOBS[job_id]
    gs = job["base_seed"] + i
    with job["lock"]:
        job["running"][i] = 0

    def on_ply(p: int) -> None:
        with job["lock"]:
            job["running"][i] = p

    try:
        r = play_match(job["white"], job["black"], job["n"], job["m"], gs,
                       job["max_plies"], job["timeout"], job["memory_mb"],
                       job["chooser"], on_ply)
        w = r["winner"]
        row = {"index": i, "seed": gs, "white": job["white"], "black": job["black"],
               "first": r["sides"][0], "second": r["sides"][1],
               "winner_seat": w, "winner_aid": r["sides"][w] if w in (0, 1) else None,
               "win_reason": r["win_reason"], "plies": r["plies"]}
        table = {"n": r["n"], "m": r["m"], "seed": gs,
                 "goal_A": r["goal_A"], "goal_B": r["goal_B"],
                 "skills": r["skills"], "log": r["log"], "sides": r["sides"]}
    except AIError as e:
        row = {"index": i, "seed": gs, "white": job["white"], "black": job["black"],
               "first": None, "second": None,
               "winner_seat": None, "winner_aid": None,
               "win_reason": f"开局失败：{e}", "plies": 0}
        table = None
    except Exception as e:  # 兜底：任何意外都不卡住整批任务
        row = {"index": i, "seed": gs, "white": job["white"], "black": job["black"],
               "first": None, "second": None,
               "winner_seat": None, "winner_aid": None,
               "win_reason": f"内部错误：{type(e).__name__}: {e}", "plies": 0}
        table = None
    with job["lock"]:
        job["running"].pop(i, None)
        job["results"].append(row)
        job["tables"][i] = table
        job["done"] += 1


def get_batch(job_id: str) -> dict:
    """批量任务状态（轮询用）：完成数、进行中各局步数、已完赛轻量结果。"""
    job = JOBS.get(job_id)
    if job is None:
        raise AIError("批量任务不存在（可能已被清理或服务重启）")
    with job["lock"]:
        return {"job_id": job_id, "white": job["white"], "black": job["black"],
                "base_seed": job["base_seed"], "total": job["total"],
                "done": job["done"], "finished": job["finished"],
                "running": dict(job["running"]),
                "results": sorted(job["results"], key=lambda r: r["index"])}


def rebuild_batch_game(job_id: str, index: int) -> dict:
    """按决策日志重放单局，返回逐轮棋谱 states（与前端 state 格式一致）+ meta。"""
    job = JOBS.get(job_id)
    if job is None:
        raise AIError("批量任务不存在（可能已被清理或服务重启）")
    if not 0 <= index < job["games"]:
        raise AIError(f"局号须在 [0,{job['games']}) 内")
    g = job["tables"][index]
    if g is None:
        raise AIError("该局尚未结束或开局失败，无棋谱")
    st = engine.new_game(n=g["n"], m=g["m"], seed=g["seed"],
                         goal_A=g["goal_A"], goal_B=g["goal_B"])
    engine.select_skills(st, 0, g["skills"][0])
    engine.select_skills(st, 1, g["skills"][1])
    states = [st.to_dict()]
    for d in g["log"]:
        try:
            if "skill" in d:
                engine.play_skill(st, d["skill"], d.get("to"))
            me = st.turn
            for a in d.get("actions") or []:
                if st.winner is not None or st.turn != me:
                    break  # 与 _step_side 一致：轮次结束则剩余作废
                if a.get("type") == "move":
                    engine.apply_pawn_move(st, a["to"])
                elif a.get("type") == "wall":
                    engine.apply_wall(st, Wall.from_dict(a["wall"]))
        except (ValueError, KeyError, TypeError):
            break  # 该决策在原局即犯规，棋谱止步于此前缀
        states.append(st.to_dict())
    row = next((r for r in job["results"] if r["index"] == index), {})
    if st.winner is None and row.get("winner_seat") in (0, 1, -1):
        # 原局终局标记（犯规判负/步数判平）重放无法产生，从记录补上
        st.winner = row["winner_seat"]
        st.win_reason = row.get("win_reason")
        states[-1] = st.to_dict()
    return {"states": states,
            "meta": {"white": job["white"], "black": job["black"],
                     "first": g["sides"][0], "second": g["sides"][1],
                     "winner_seat": row.get("winner_seat"),
                     "win_reason": row.get("win_reason"), "plies": row.get("plies")}}


def list_ais() -> list[dict]:
    """默认 AI（内置，自动构建）+ 已上传的 AI（含是否已编译）。"""
    out = []
    for aid, (_, name) in BUILTINS.items():
        try:
            built = image_built(aid)
        except (AIError, OSError):
            built = False
        out.append({"aid": aid, "name": name, "built": built, "builtin": True})
    if not AI_DIR.is_dir():
        return out
    for d in sorted(AI_DIR.iterdir()):
        if not d.is_dir() or not (d / "src").is_dir() or d.name in BUILTINS:
            continue
        name = d.name
        try:
            name = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("name", d.name)
        except (OSError, ValueError):
            pass
        try:
            built = image_built(d.name)
        except (AIError, OSError):
            built = False
        out.append({"aid": d.name, "name": name, "built": built, "builtin": False})
    return out
