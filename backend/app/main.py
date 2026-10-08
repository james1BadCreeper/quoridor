"""FastAPI 入口：对局创建、走子/放墙、合法动作、外部 AI 上传对战、棋谱导入导出。"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
import zipfile

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import ai_runner
from .ai_runner import AIError
from .engine import (
    SKILLS,
    GameState,
    Wall,
    apply_pawn_move,
    apply_wall,
    is_wall_legal,
    legal_pawn_moves,
    new_game,
    play_skill,
    select_skills,
)
from .models import (
    AIGoalsRequest,
    AISideRequest,
    BatchRequest,
    ExternalMoveRequest,
    MapPreviewRequest,
    MatchRequest,
    NewGameRequest,
    PawnMoveRequest,
    SkillAISelectRequest,
    SkillPlayRequest,
    SkillSelectRequest,
    WallDTO,
)

app = FastAPI(title="Quoridor 改版后端")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 内存对局表（MVP 简化：重启丢失，以棋谱 JSON 为准）
GAMES: dict[str, GameState] = {}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/games/new")
def create_game(req: NewGameRequest) -> dict:
    try:
        st = new_game(n=req.n, m=req.m, seed=req.seed, goal_A=req.goal_A, goal_B=req.goal_B)
    except ValueError as e:
        raise HTTPException(400, str(e))
    gid = uuid.uuid4().hex[:8]
    GAMES[gid] = st
    return {"id": gid, "state": st.to_dict()}


@app.get("/api/games/{gid}")
def get_game(gid: str) -> dict:
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    return {"id": gid, "state": st.to_dict()}


@app.get("/api/games/{gid}/legal-moves")
def get_legal_moves(gid: str, player: int | None = None) -> dict:
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    p = state_turn if (state_turn := player) is not None else st.turn
    phased = bool(st.phase_buff[p])  # 穿墙 buff 生效中：返回无视墙的走位
    return {"player": p, "moves": legal_pawn_moves(st, p, ignore_walls=phased), "phased": phased}


@app.post("/api/games/{gid}/moves/pawn")
def post_pawn_move(gid: str, req: PawnMoveRequest) -> dict:
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    try:
        apply_pawn_move(st, req.to)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": gid, "state": st.to_dict()}


@app.post("/api/games/{gid}/moves/wall")
def post_wall(gid: str, req: WallDTO) -> dict:
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    if req.kind == "L" and req.arm not in ("NW", "NE", "SW", "SE"):
        raise HTTPException(400, "L 墙朝向必须为 NW/NE/SW/SE")
    if req.kind != "L" and req.orientation not in ("H", "V"):
        raise HTTPException(400, "墙朝向必须为 H 或 V")
    w = Wall(wr=req.wr, wc=req.wc, orientation=req.orientation, kind=req.kind, arm=req.arm)
    try:
        apply_wall(st, w)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": gid, "state": st.to_dict()}


@app.post("/api/games/{gid}/moves/wall/preview")
def preview_wall(gid: str, req: WallDTO) -> dict:
    """仅校验放墙是否合法，不真正落子（供前端高亮）。"""
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    if req.kind == "L" and req.arm not in ("NW", "NE", "SW", "SE"):
        return {"legal": False, "message": "L 墙朝向必须为 NW/NE/SW/SE"}
    if req.kind != "L" and req.orientation not in ("H", "V"):
        return {"legal": False, "message": "墙朝向必须为 H 或 V"}
    w = Wall(wr=req.wr, wc=req.wc, orientation=req.orientation, kind=req.kind, arm=req.arm)
    ok, msg = is_wall_legal(st, st.turn, w, free=st.free_buff[st.turn])
    return {"legal": ok, "message": msg}


@app.get("/api/skills")
def list_skills() -> dict:
    """技能卡一览（含中文名与描述，供前端选牌）。"""
    return {"skills": SKILLS}


@app.post("/api/games/{gid}/skills/select")
def post_skill_select(gid: str, req: SkillSelectRequest) -> dict:
    """赛前选技能卡（数量须等于 skill_k，可重复）。"""
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    try:
        select_skills(st, req.player, req.skills)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": gid, "state": st.to_dict()}


@app.post("/api/games/{gid}/skills/ai-select")
def post_skill_ai_select(gid: str, req: SkillAISelectRequest) -> dict:
    """AI 席位赛前选技能卡：跑该 AI 的选牌阶段容器（开局向导用）。"""
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    try:
        picks = ai_runner.select_for(req.aid, st, req.timeout, req.memory_mb)
        select_skills(st, req.player, picks)
    except (AIError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"id": gid, "picks": picks, "state": st.to_dict()}


@app.post("/api/games/{gid}/skills/play")
def post_skill_play(gid: str, req: SkillPlayRequest) -> dict:
    """行动前打出一张技能卡（不占轮次，使用时双方均得知）。"""
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    try:
        play_skill(st, req.skill, req.to)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": gid, "state": st.to_dict()}


@app.post("/api/ai/upload")
def upload_ai(file: UploadFile = File(...)) -> dict:
    """上传 AI 源码 zip（只收 .cpp/.cc/.c/.h/.hpp），在 docker 容器内编译成运行镜像。"""
    try:
        aid = ai_runner.save_ai_zip(file.file.read(), file.filename or "ai.zip")
    except AIError as e:
        raise HTTPException(400, str(e))
    try:
        ok, log = ai_runner.build_ai(aid)
    except AIError as e:
        raise HTTPException(400, f"aid={aid}，{e}")
    return {"aid": aid, "built": ok, "log": log}


@app.get("/api/ai/list")
def list_ais() -> dict:
    """默认 AI（内置贪心/随机，首次使用自动构建）+ 已上传的 AI 一览。"""
    return {"ais": ai_runner.list_ais()}


@app.post("/api/games/{gid}/ai-external-move")
def post_external_move(gid: str, req: ExternalMoveRequest) -> dict:
    """已上传 AI 走一步（人机对战、AI 对 AI 演示用）；决策非法/超时报 400。"""
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    if st.winner is not None:
        raise HTTPException(400, "对局已结束")
    if not st.started:
        raise HTTPException(400, "双方选完技能卡后方可行动")
    try:
        summary = ai_runner.apply_external_decision(req.aid, st, req.timeout, req.memory_mb)
    except AIError as e:
        raise HTTPException(400, str(e))
    return {"id": gid, **summary, "state": st.to_dict()}


@app.post("/api/ai/match")
def run_match(req: MatchRequest) -> dict:
    """AI 对战：出题方（chooser）定 A/B 列集，另一方选边；犯规/超时者判负，超步数判平局。"""
    try:
        r = ai_runner.play_match(req.white, req.black, req.n, req.m,
                                 req.seed, req.max_plies, req.timeout,
                                 req.memory_mb, req.chooser)
    except AIError as e:
        raise HTTPException(400, str(e))
    r.pop("log", None)  # 决策日志只供批量重建，单局对战不返回
    r.pop("skills", None)
    return r


@app.post("/api/ai/batch")
def start_batch(req: BatchRequest) -> dict:
    """批量对战：后台并行跑 N 局（seed=base+i，每局独立出题/选边/选牌），立即返回 job_id 轮询。"""
    try:
        job_id = ai_runner.start_batch(req.white, req.black, req.n, req.m, req.seed,
                                       req.games, req.max_parallel, req.max_plies,
                                       req.timeout, req.memory_mb, req.chooser)
    except AIError as e:
        raise HTTPException(400, str(e))
    return {"job_id": job_id}


@app.get("/api/ai/batch/{job_id}")
def batch_status(job_id: str) -> dict:
    """批量进度：完成数、进行中各局步数、已完赛轻量结果。"""
    try:
        return ai_runner.get_batch(job_id)
    except AIError as e:
        raise HTTPException(404, str(e))


@app.get("/api/ai/batch/{job_id}/games/{index}")
def batch_game(job_id: str, index: int) -> dict:
    """按决策日志重放单局，返回逐轮棋谱 states + meta（前端直接载入回放）。"""
    try:
        return ai_runner.rebuild_batch_game(job_id, index)
    except AIError as e:
        raise HTTPException(404 if "不存在" in str(e) else 400, str(e))


@app.get("/api/ai/batch/{job_id}/download")
def batch_download(job_id: str, background: BackgroundTasks) -> FileResponse:
    """打包下载全部棋谱 zip（每局 game_XXXX.json + summary.json，完赛后可用）。"""
    try:
        job = ai_runner.get_batch(job_id)
    except AIError as e:
        raise HTTPException(404, str(e))
    if not job["finished"]:
        raise HTTPException(400, "批量尚未完赛")
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    try:
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("summary.json", json.dumps(
                {"job_id": job_id, "white": job["white"], "black": job["black"],
                 "base_seed": job["base_seed"], "total": job["total"],
                 "results": job["results"]}, ensure_ascii=False))
            for r in job["results"]:
                g = ai_runner.rebuild_batch_game(job_id, r["index"])
                z.writestr(f"game_{r['index']:04d}.json",
                           json.dumps(g, ensure_ascii=False))
    except AIError as e:
        os.unlink(tmp.name)
        raise HTTPException(400, str(e))
    background.add_task(os.unlink, tmp.name)
    return FileResponse(tmp.name, filename=f"batch_{job_id}.zip")


@app.post("/api/map/preview")
def map_preview(req: MapPreviewRequest) -> dict:
    """预览本局地形：与随后同 n/m/seed 建局的地形完全一致
    （地形只与出生点/首末行保护格有关，与目标列无关，故可用哑目标列先行生成）。"""
    try:
        st = new_game(n=req.n, m=req.m, seed=req.seed,
                      goal_A=list(range(req.m // 2)), goal_B=list(range(req.m // 2)))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"n": st.n, "m": st.m, "deads": sorted(st.deads), "sands": sorted(st.sands)}


@app.post("/api/ai/goals")
def post_ai_goals(req: AIGoalsRequest) -> dict:
    """AI 出题：返回校验过的 A/B 获胜列集（非法出题报 400）。"""
    try:
        a, b = ai_runner.ai_goals(req.aid, req.n, req.m,
                                  {tuple(x) for x in req.deads},
                                  {tuple(x) for x in req.sands}, req.timeout,
                                  req.memory_mb)
    except AIError as e:
        raise HTTPException(400, str(e))
    return {"aid": req.aid, "goal_A": a, "goal_B": b}


@app.post("/api/ai/side")
def post_ai_side(req: AISideRequest) -> dict:
    """AI 选边：返回 first（先手+A）或 second（后手+B），非法报 400。"""
    try:
        side = ai_runner.ai_side(req.aid, req.n, req.m, {tuple(x) for x in req.deads},
                                 {tuple(x) for x in req.sands},
                                 req.goal_A, req.goal_B, req.timeout, req.memory_mb)
    except AIError as e:
        raise HTTPException(400, str(e))
    return {"aid": req.aid, "side": side}


@app.get("/api/games/{gid}/export")
def export_game(gid: str, viewer: int | None = None) -> dict:
    """导出 json 格式棋谱（可直接保存回放）。viewer=0/1 时按该视角剥离对方手牌与选牌内容。"""
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    if viewer is not None and viewer not in (0, 1):
        raise HTTPException(400, "viewer 须为 0 或 1")
    return st.to_dict(viewer=viewer)


@app.post("/api/games/import")
def import_game(state: dict) -> dict:
    """由棋谱 json 恢复对局（回放查看用）。"""
    try:
        st = GameState.from_dict(state)
    except Exception as e:
        raise HTTPException(400, f"棋谱格式错误：{e}")
    gid = uuid.uuid4().hex[:8]
    GAMES[gid] = st
    return {"id": gid, "state": st.to_dict()}
