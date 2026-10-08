"""外部 AI 接口测试：zip 校验（无 docker 可跑）+ 上传编译/走子/对战（需 docker）。"""

import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ai_runner
from app.main import app

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "app" / "ai_cpp_example"


def docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    p = subprocess.run(["docker", "image", "inspect", ai_runner.BASE_IMAGE],
                       capture_output=True, timeout=30)
    return p.returncode == 0


needs_docker = pytest.mark.skipif(not docker_ready(), reason="需要 docker + gcc:14-bookworm 镜像")


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_rejects_bad_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_runner, "AI_DIR", tmp_path)
    with pytest.raises(ValueError, match="只接受 .zip"):
        ai_runner.save_ai_zip(b"xxx", "ai.tar.gz")


def test_zip_rejects_slip_and_exe(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_runner, "AI_DIR", tmp_path)
    with pytest.raises(ValueError, match="非法路径"):
        ai_runner.save_ai_zip(make_zip({"../evil.cpp": b"int main(){}"}), "a.zip")
    with pytest.raises(ValueError, match="不允许"):
        ai_runner.save_ai_zip(make_zip({"ai.exe": b"xx"}), "a.zip")
    with pytest.raises(ValueError, match="至少包含"):
        ai_runner.save_ai_zip(make_zip({"only.h": b"// header only"}), "a.zip")


def test_zip_ok_flattens(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_runner, "AI_DIR", tmp_path)
    aid = ai_runner.save_ai_zip(make_zip({"sub/a.cpp": b"int main(){return 0;}", "j.hpp": b"//h"}), "my.zip")
    assert (tmp_path / aid / "src" / "a.cpp").exists()
    assert (tmp_path / aid / "src" / "j.hpp").exists()


@needs_docker
def test_upload_build_move_and_match(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_runner, "AI_DIR", tmp_path)
    c = TestClient(app)
    # 上传示例 AI（cpp + 公共头 + json.hpp）并在容器内编译
    payload = make_zip({
        "ai_example.cpp": (EXAMPLE_DIR / "ai_example.cpp").read_bytes(),
        "ai_common.hpp": (EXAMPLE_DIR / "ai_common.hpp").read_bytes(),
        "json.hpp": (EXAMPLE_DIR / "json.hpp").read_bytes(),
    })
    r = c.post("/api/ai/upload", files={"file": ("ai.zip", payload, "application/zip")})
    assert r.status_code == 200, r.text
    aid = r.json()["aid"]
    assert r.json()["built"] is True

    # 开局并选完技能
    g = c.post("/api/games/new", json={"n": 9, "m": 9, "seed": 7}).json()
    gid, k = g["id"], g["state"]["skill_k"]
    c.post(f"/api/games/{gid}/skills/select", json={"player": 0, "skills": ["double_move"] * k})
    c.post(f"/api/games/{gid}/skills/select", json={"player": 1, "skills": ["phase_walk"] * k})

    # 已上传 AI 走一轮
    r = c.post(f"/api/games/{gid}/ai-external-move", json={"aid": aid, "timeout": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] >= 1 and all(a["type"] in ("move", "wall") for a in body["actions"])

    # 非法 aid 报 400
    r = c.post(f"/api/games/{gid}/ai-external-move", json={"aid": "nope"})
    assert r.status_code == 400

    # 完整对战：示例 AI（先手）vs random
    r = c.post("/api/ai/match", json={"white": aid, "black": "random", "n": 9, "m": 9,
                                      "seed": 7, "max_plies": 400, "timeout": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["winner"] in (0, 1, -1) and body["plies"] > 0
    assert body["state"]["winner"] == body["winner"]

    # 一览能看到
    assert aid in [a["aid"] for a in c.get("/api/ai/list").json()["ais"]]
    subprocess.run(["docker", "rmi", ai_runner.image_tag(aid)], capture_output=True, timeout=60)


@needs_docker
def test_builtin_defaults():
    c = TestClient(app)
    aids = {a["aid"]: a for a in c.get("/api/ai/list").json()["ais"]}
    assert aids["builtin-greedy"]["builtin"] and aids["builtin-random"]["builtin"]
    # 内置随机走一步（容器路径）
    g = c.post("/api/games/new", json={"n": 9, "m": 9, "seed": 11}).json()
    gid, k = g["id"], g["state"]["skill_k"]
    c.post(f"/api/games/{gid}/skills/select", json={"player": 0, "skills": ["phase_walk"] * k})
    c.post(f"/api/games/{gid}/skills/select", json={"player": 1, "skills": ["phase_walk"] * k})
    r = c.post(f"/api/games/{gid}/ai-external-move", json={"aid": "builtin-random", "timeout": 10})
    assert r.status_code == 200, r.text
    # 内置贪心对 random 短对战
    r = c.post("/api/ai/match", json={"white": "builtin-greedy", "black": "random", "n": 9, "m": 9,
                                      "seed": 9, "max_plies": 200, "timeout": 10})
    assert r.status_code == 200, r.text
    assert r.json()["winner"] in (0, 1, -1)


@needs_docker
def test_ai_goals_and_side():
    c = TestClient(app)
    for aid in ("builtin-greedy", "builtin-random"):
        r = c.post("/api/ai/goals", json={"aid": aid, "m": 9, "timeout": 10})
        assert r.status_code == 200, r.text
        a, b = r.json()["goal_A"], r.json()["goal_B"]
        assert len(a) == 4 and len(b) == 4 and len(set(a)) == 4 and len(set(b)) == 4
        assert all(0 <= x < 9 for x in a + b)
        r = c.post("/api/ai/side", json={"aid": aid, "n": 9, "m": 9, "deads": [[4, 4]],
                                         "sands": [], "goal_A": a, "goal_B": b, "timeout": 10})
        assert r.status_code == 200, r.text
        assert r.json()["side"] in ("first", "second")
    # 非法 m 报 400
    assert c.post("/api/ai/goals", json={"aid": "builtin-random", "m": 8}).status_code == 400
    # 对战支持指定出题方：贪心出题、随机选边
    r = c.post("/api/ai/match", json={"white": "builtin-greedy", "black": "random", "n": 9, "m": 9,
                                      "seed": 3, "max_plies": 200, "timeout": 10, "chooser": "white"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chooser"] == "white" and body["picker_side"] in ("first", "second")
    assert body["winner"] in (0, 1, -1) and len(body["goal_A"]) == 4


def test_export_viewer():
    c = TestClient(app)
    g = c.post("/api/games/new", json={"n": 9, "m": 9, "seed": 7}).json()
    gid, k = g["id"], g["state"]["skill_k"]
    c.post(f"/api/games/{gid}/skills/select", json={"player": 0, "skills": ["double_move"] * k})
    c.post(f"/api/games/{gid}/skills/select", json={"player": 1, "skills": ["phase_walk"] * k})
    full = c.get(f"/api/games/{gid}/export").json()
    assert full["hands"][0] == {"double_move": 2}
    v1 = c.get(f"/api/games/{gid}/export?viewer=1").json()
    assert v1["hands"] == [{}, {"phase_walk": 2}]
    assert c.get(f"/api/games/{gid}/export?viewer=2").status_code == 400


@needs_docker
def test_upload_without_common_headers(tmp_path, monkeypatch):
    """上传 zip 无需自带 ai_common.hpp / json.hpp，后端自动补入。"""
    monkeypatch.setattr(ai_runner, "AI_DIR", tmp_path)
    c = TestClient(app)
    payload = make_zip({"my_ai.cpp": (EXAMPLE_DIR / "ai_random.cpp").read_bytes()})
    r = c.post("/api/ai/upload", files={"file": ("ai.zip", payload, "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["built"] is True
    subprocess.run(["docker", "rmi", ai_runner.image_tag(r.json()["aid"])], capture_output=True, timeout=60)


@needs_docker
def test_double_requires_two_actions(tmp_path, monkeypatch):
    """连续行动只给一步直接判负（400）。"""
    monkeypatch.setattr(ai_runner, "AI_DIR", tmp_path)
    c = TestClient(app)
    src = '#include <iostream>\nint main(){std::cout << "{\\"skill\\":\\"double_move\\",\\"actions\\":[{\\"type\\":\\"move\\",\\"to\\":[1,4]}]}";}'
    payload = make_zip({"bad.cpp": src.encode()})
    aid = c.post("/api/ai/upload", files={"file": ("ai.zip", payload, "application/zip")}).json()["aid"]
    g = c.post("/api/games/new", json={"n": 9, "m": 9, "seed": 7}).json()
    gid, k = g["id"], g["state"]["skill_k"]
    c.post(f"/api/games/{gid}/skills/select", json={"player": 0, "skills": ["double_move"] * k})
    c.post(f"/api/games/{gid}/skills/select", json={"player": 1, "skills": ["phase_walk"] * k})
    r = c.post(f"/api/games/{gid}/ai-external-move", json={"aid": aid, "timeout": 10})
    assert r.status_code == 400 and "两步" in r.text, r.text
    subprocess.run(["docker", "rmi", ai_runner.image_tag(aid)], capture_output=True, timeout=60)


def test_map_preview_matches_new_game():
    """preview 接口地形与同参建局一致（无需 docker）。"""
    c = TestClient(app)
    r = c.post("/api/map/preview", json={"n": 11, "m": 13, "seed": 42})
    assert r.status_code == 200, r.text
    t = r.json()
    g = c.post("/api/games/new", json={"n": 11, "m": 13, "seed": 42,
                                       "goal_A": [0, 2, 4, 6, 8, 10],
                                       "goal_B": [1, 3, 5, 7, 9, 11]})
    assert g.status_code == 200, g.text
    st = g.json()["state"]
    assert sorted(t["deads"]) == sorted(st["deads"])
    assert sorted(t["sands"]) == sorted(st["sands"])


def test_map_preview_rejects_bad_size():
    c = TestClient(app)
    r = c.post("/api/map/preview", json={"n": 5, "m": 9, "seed": 1})
    assert r.status_code == 400


def test_ai_limits_rejected_before_build():
    """坏的时限/内存直接 400（参数先校验，无需 docker、不触发构建）。"""
    c = TestClient(app)
    r = c.post("/api/ai/goals", json={"aid": "builtin-random", "n": 9, "m": 9, "memory_mb": 32})
    assert r.status_code == 400 and "内存" in r.text
    r = c.post("/api/ai/goals", json={"aid": "builtin-random", "n": 9, "m": 9, "timeout": 99})
    assert r.status_code == 400 and "超时" in r.text
    r = c.post("/api/ai/side", json={"aid": "builtin-random", "memory_mb": 4096})
    assert r.status_code == 400 and "内存" in r.text


def test_batch_validation_no_docker():
    """批量参数校验（无需 docker）。"""
    c = TestClient(app)
    base = {"white": "random", "black": "random"}
    assert c.post("/api/ai/batch", json={**base, "games": 0}).status_code == 400
    assert c.post("/api/ai/batch", json={**base, "games": 1001}).status_code == 400
    assert c.post("/api/ai/batch", json={**base, "max_parallel": 0}).status_code == 400
    assert c.post("/api/ai/batch", json={**base, "max_parallel": 17}).status_code == 400
    assert c.post("/api/ai/batch", json={**base, "n": 5}).status_code == 400
    assert c.get("/api/ai/batch/nope").status_code == 404


def test_rebuild_batch_game_no_docker():
    """决策日志重放（无需 docker，手工注入一局）。"""
    from app import engine as E
    st0 = E.new_game(n=9, m=9, seed=11, goal_A=[0, 1, 2, 3], goal_B=[5, 6, 7, 8])
    mv = [list(x) for x in E.legal_pawn_moves(st0, 0)][0]
    ai_runner.JOBS["t-rebuild"] = {
        "games": 1, "white": "random", "black": "random",
        "results": [{"index": 0, "winner_seat": None, "win_reason": None, "plies": 1}],
        "tables": [{"n": 9, "m": 9, "seed": 11,
                    "goal_A": [0, 1, 2, 3], "goal_B": [5, 6, 7, 8],
                    "skills": [["phase_walk", "phase_walk"], ["phase_walk", "phase_walk"]],
                    "log": [{"actions": [{"type": "move", "to": mv}]}],
                    "sides": ["random", "random"]}],
    }
    try:
        g = ai_runner.rebuild_batch_game("t-rebuild", 0)
    finally:
        del ai_runner.JOBS["t-rebuild"]
    assert len(g["states"]) == 2
    assert [h["type"] for h in g["states"][0]["history"]] == ["select_skills", "select_skills"]
    assert g["states"][1]["pawns"][0] == mv
    assert len(g["states"][1]["history"]) == 3


@needs_docker
def test_batch_end_to_end():
    """批量 3 局小步数端到端：进度→完赛→单局重建一致。"""
    import time
    c = TestClient(app)
    r = c.post("/api/ai/batch", json={"white": "builtin-random", "black": "builtin-random",
                                      "n": 9, "m": 9, "seed": 5, "games": 3,
                                      "max_parallel": 2, "max_plies": 6, "timeout": 10})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    s = {}
    for _ in range(180):
        s = c.get(f"/api/ai/batch/{jid}").json()
        assert s["done"] <= s["total"] == 3
        assert isinstance(s["running"], dict)
        if s["finished"]:
            break
        time.sleep(1)
    assert s["finished"] and s["done"] == 3 and len(s["results"]) == 3
    row0 = s["results"][0]
    g = c.get(f"/api/ai/batch/{jid}/games/0")
    assert g.status_code == 200, g.text
    states = g.json()["states"]
    assert states[-1]["winner"] == row0["winner_seat"]
    assert [h["type"] for h in states[0]["history"]] == ["select_skills", "select_skills"]
    assert len(states[-1]["history"]) >= len(states[0]["history"])
    assert c.get(f"/api/ai/batch/{jid}/games/9").status_code in (400, 404)


@needs_docker
def test_batch_download_zip():
    """批量下载 zip：完赛后含 summary + 每局棋谱；未完赛 400。"""
    import time
    import zipfile
    c = TestClient(app)
    r = c.post("/api/ai/batch", json={"white": "builtin-random", "black": "builtin-random",
                                      "n": 9, "m": 9, "seed": 5, "games": 2,
                                      "max_parallel": 2, "max_plies": 6, "timeout": 10})
    jid = r.json()["job_id"]
    assert c.get(f"/api/ai/batch/{jid}/download").status_code == 400  # 未完赛
    for _ in range(180):
        s = c.get(f"/api/ai/batch/{jid}").json()
        if s["finished"]:
            break
        time.sleep(1)
    assert s["finished"]
    d = c.get(f"/api/ai/batch/{jid}/download")
    assert d.status_code == 200
    z = zipfile.ZipFile(__import__("io").BytesIO(d.content))
    names = z.namelist()
    assert "summary.json" in names
    assert "game_0000.json" in names and "game_0001.json" in names
    import json as _json
    g0 = _json.loads(z.read("game_0000.json"))
    assert len(g0["states"]) > 1 and "meta" in g0
    assert c.get("/api/ai/batch/nope/download").status_code == 404
