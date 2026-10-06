"""FastAPI 入口：对局创建、走子/放墙、合法动作、随机 AI、棋谱导入导出。"""

from __future__ import annotations

import random
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

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
    random_ai_move,
    random_ai_skill,
    random_skill_picks,
    select_skills,
)
from .models import (
    AIRequest,
    NewGameRequest,
    PawnMoveRequest,
    SkillPlayRequest,
    SkillRandomRequest,
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


@app.post("/api/games/{gid}/ai-move")
def post_ai_move(gid: str, req: AIRequest) -> dict:
    """示例随机 AI 落子（人类/AI 混战、AI 对 AI 演示用），偶尔会打出手牌。"""
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    if st.winner is not None:
        raise HTTPException(400, "对局已结束")
    if not st.started:
        raise HTTPException(400, "双方选完技能卡后方可行动")
    rng = random.Random(req.seed)
    played = None
    try:
        decision = random_ai_skill(st, rng)
        if decision is not None:
            play_skill(st, decision["skill"], decision["to"])
            played = decision
        # 连续行动中只能走子
        if st.must_move:
            moves = legal_pawn_moves(st, st.turn)
            if not moves:
                raise HTTPException(400, "无合法走子")
            apply_pawn_move(st, list(rng.choice(moves)))
            action = {"player": st.turn, "type": "move"}
        else:
            action = random_ai_move(st, rng)
            if action["type"] == "move":
                apply_pawn_move(st, action["to"])
            else:
                apply_wall(st, Wall.from_dict(action["wall"]))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": gid, "action": action, "skill": played, "state": st.to_dict()}


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


@app.post("/api/games/{gid}/skills/random")
def post_skill_random(gid: str, req: SkillRandomRequest) -> dict:
    """为某玩家随机选技能卡（AI 席位用）。"""
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    try:
        picks = random_skill_picks(st.skill_k, random.Random(req.seed))
        select_skills(st, req.player, picks)
    except ValueError as e:
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


@app.get("/api/games/{gid}/export")
def export_game(gid: str) -> dict:
    """导出 json 格式棋谱（可直接保存回放）。"""
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    return st.to_dict()


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
