"""外部 AI 运行器：zip 上传校验、docker 容器内编译、沙箱运行、AI 对战。

安全边界（MVP）：AI 二进制只在容器内运行，--network none、无新镜像拉取、
内存 256MB、pids 上限、可配置超时；zip 解包防路径穿越、白名单扩展名。
"""

from __future__ import annotations

import io
import json
import subprocess
import uuid
import zipfile
from pathlib import Path

from . import engine
from .engine import Wall

# 上传的 AI 存放目录（仓库 data/ais/<aid>/src + Dockerfile + meta.json）
AI_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "ais"
BASE_IMAGE = "gcc:14-bookworm"  # 编译/运行基镜像（需预先 docker load 导入）

ALLOWED_SUFFIX = {".cpp", ".cc", ".c", ".h", ".hpp"}  # zip 内允许的源码扩展名
MAX_FILES = 64  # 单个 zip 最多文件数
MAX_TOTAL = 8 * 1024 * 1024  # 解压后总大小上限 8MB
BUILD_TIMEOUT = 240  # docker build 超时（秒）
RUN_CONTAINER_TIMEOUT = 30  # 单步容器运行超时上限（秒）


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
    """在容器内编译 AI 源码，生成运行镜像。返回 (成功, 日志尾)。"""
    d = _aid_dir(aid)
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


def run_ai(aid: str, payload: dict, timeout: float = 5) -> dict:
    """运行 AI 容器一步：stdin 输入 json，取 stdout 最后一行非空解析为决策。

    决策格式同 ai_cpp_example：{"type":"move",...} / {"type":"wall",...}，
    或带技能 {"skill":..,"to":..,"action":{...}}；选牌阶段返回 {"skills":[...]}。
    """
    _aid_dir(aid)
    if timeout <= 0 or timeout > RUN_CONTAINER_TIMEOUT:
        raise AIError(f"超时须在 (0,{RUN_CONTAINER_TIMEOUT}] 秒内")
    try:
        p = subprocess.run(
            ["docker", "run", "--rm", "-i", "--network", "none",
             "--memory=256m", "--pids-limit=64", "--cpus=0.5", image_tag(aid)],
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


def _select_for(side: str, st: engine.GameState, timeout: float) -> list[str]:
    """某席位的赛前选牌：aid 跑选牌阶段容器，random 用引擎随机。"""
    if side == "random":
        import random as _random
        return engine.random_skill_picks(st.skill_k, _random.Random())
    out = run_ai(side, {"phase": "select", "skill_k": st.skill_k, "n": st.n, "m": st.m,
                        "deads": sorted(st.deads), "sands": sorted(st.sands),
                        "goal_A": st.goal_A, "goal_B": st.goal_B}, timeout)
    skills = out.get("skills")
    if not isinstance(skills, list) or len(skills) != st.skill_k:
        raise AIError(f"选牌输出非法（须恰好 {st.skill_k} 张）：{str(out)[:200]}")
    return skills


def _step_side(side: str, st: engine.GameState, rng, timeout: float) -> dict:
    """某席位行动一步并落到引擎；返回 {"skill":..,"action":..} 摘要。"""
    import random as _random
    rng = rng or _random.Random()
    played = None
    if side == "random":
        decision = engine.random_ai_skill(st, rng)
        if decision is not None:
            engine.play_skill(st, decision["skill"], decision["to"])
            played = decision
        if st.must_move:
            moves = engine.legal_pawn_moves(st, st.turn)
            if not moves:
                raise AIError("无合法走子")
            engine.apply_pawn_move(st, list(rng.choice(moves)))
            return {"player": st.turn, "type": "move", "skill": played}
        action = engine.random_ai_move(st, rng)
    else:
        d = run_ai(side, st.to_dict(), timeout)
        if not isinstance(d, dict):
            raise AIError(f"决策须为 json 对象：{str(d)[:200]}")
        if "skill" in d:
            try:
                engine.play_skill(st, d["skill"], d.get("to"))
            except ValueError as e:
                raise AIError(f"打出手牌非法（{d.get('skill')}）：{e}")
            played = {"skill": d["skill"], "to": d.get("to")}
        action = d.get("action", d)
    if not isinstance(action, dict) or action.get("type") == "skill":
        raise AIError(f"行动非法：{str(action)[:200]}")
    try:
        if action.get("type") == "move":
            engine.apply_pawn_move(st, action["to"])
        elif action.get("type") == "wall":
            engine.apply_wall(st, Wall.from_dict(action["wall"]))
        else:
            raise AIError(f"未知行动类型：{action.get('type')}")
    except (ValueError, KeyError, TypeError) as e:
        raise AIError(f"行动非法：{e}")
    return {"player": st.turn, "type": action.get("type"), "skill": played}


def play_match(white: str, black: str, n: int | None = None, m: int | None = None,
               seed: int | None = None, max_plies: int = 800, timeout: float = 5) -> dict:
    """AI 对战：side 为 "random" 或已编译的 aid；犯规/超时/输出非法者判负。"""
    import random as _random
    if max_plies <= 0 or max_plies > 3000:
        raise AIError("max_plies 须在 (0,3000] 内")
    st = engine.new_game(n=n, m=m, seed=seed)
    sides = [white, black]
    for pl, side in enumerate(sides):
        if side != "random" and not image_built(side):
            raise AIError(f"AI 未编译：{side}（先 POST /api/ai/upload 构建）")
        try:
            engine.select_skills(st, pl, _select_for(side, st, timeout))
        except AIError as e:
            st.winner = 1 - pl
            st.win_reason = f"{'先手' if pl == 0 else '后手'}AI 选牌犯规：{e}"
            return {"winner": st.winner, "win_reason": st.win_reason, "plies": 0,
                    "sides": sides, "state": st.to_dict()}
    rng = _random.Random(seed)
    plies = 0
    while st.winner is None and plies < max_plies:
        side = sides[st.turn]
        try:
            _step_side(side, st, rng, timeout)
        except AIError as e:
            st.winner = 1 - st.turn
            st.win_reason = f"{'先手' if st.turn == 0 else '后手'}AI 犯规：{e}"
            break
        plies += 1
    if st.winner is None:
        st.winner = -1  # 超出步数上限判平局
        st.win_reason = f"达到步数上限（{max_plies}），判平局"
    return {"winner": st.winner, "win_reason": st.win_reason, "plies": plies,
            "sides": sides, "state": st.to_dict()}


def list_ais() -> list[dict]:
    """扫描已上传的 AI（含是否已编译）。"""
    if not AI_DIR.is_dir():
        return []
    out = []
    for d in sorted(AI_DIR.iterdir()):
        if not d.is_dir() or not (d / "src").is_dir():
            continue
        name = d.name
        try:
            name = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("name", d.name)
        except (OSError, ValueError):
            pass
        try:
            built = image_built(d.name)
        except AIError:
            built = False
        out.append({"aid": d.name, "name": name, "built": built})
    return out
