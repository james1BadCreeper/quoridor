"""FastAPI 数据模型（请求/响应体）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WallDTO(BaseModel):
    kind: str = Field(description="straight 或 L")
    wr: int
    wc: int
    orientation: str | None = None  # H / V
    arm: str | None = None  # NW / NE / SW / SE


class NewGameRequest(BaseModel):
    n: int | None = Field(default=None, description="行数，不填则 9~15 随机")
    m: int | None = Field(default=None, description="列数，不填则 9~15 随机")
    seed: int | None = None


class PawnMoveRequest(BaseModel):
    to: list[int]  # [r, c]


class AIRequest(BaseModel):
    seed: int | None = None
