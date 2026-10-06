"""FastAPI 入口：对局创建、走子/放墙、合法动作、随机 AI、棋谱导入导出。"""

from __future__ import annotations

import random
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .engine import (
    GameState,
    Wall,
    apply_pawn_move,
    apply_wall,
    is_wall_legal,
    legal_pawn_moves,
    new_game,
    random_ai_move,
)
from .models import AIRequest, NewGameRequest, PawnMoveRequest, WallDTO

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
    return {"player": p, "moves": legal_pawn_moves(st, p)}


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
    w = Wall(kind=req.kind, wr=req.wr, wc=req.wc,  # type: ignore[arg-type]
             orientation=req.orientation, arm=req.arm)  # type: ignore[arg-type]
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
    w = Wall(kind=req.kind, wr=req.wr, wc=req.wc,  # type: ignore[arg-type]
             orientation=req.orientation, arm=req.arm)  # type: ignore[arg-type]
    ok, msg = is_wall_legal(st, st.turn, w)
    return {"legal": ok, "message": msg}


@app.post("/api/games/{gid}/ai-move")
def post_ai_move(gid: str, req: AIRequest) -> dict:
    """示例随机 AI 落子（人类/AI 混战、AI 对 AI 演示用）。"""
    st = GAMES.get(gid)
    if st is None:
        raise HTTPException(404, "对局不存在")
    if st.winner is not None:
        raise HTTPException(400, "对局已结束")
    rng = random.Random(req.seed)
    action = random_ai_move(st, rng)
    try:
        if action["type"] == "move":
            apply_pawn_move(st, action["to"])
        else:
            apply_wall(st, Wall.from_dict(action["wall"]))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": gid, "action": action, "state": st.to_dict()}


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
