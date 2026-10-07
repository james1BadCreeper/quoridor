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
    # 上传示例 AI（cpp + json.hpp）并在容器内编译
    payload = make_zip({
        "ai_example.cpp": (EXAMPLE_DIR / "ai_example.cpp").read_bytes(),
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

    # 已上传 AI 走一步
    r = c.post(f"/api/games/{gid}/ai-external-move", json={"aid": aid, "timeout": 10})
    assert r.status_code == 200, r.text
    assert r.json()["action"]["type"] in ("move", "wall")

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
