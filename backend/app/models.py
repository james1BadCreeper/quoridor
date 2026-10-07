"""FastAPI 数据模型（请求/响应体）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WallDTO(BaseModel):
    wr: int
    wc: int
    orientation: str | None = None  # H / V（直墙）
    kind: str = "straight"  # straight 或 L（L 墙需改造技能放置权）
    arm: str | None = None  # L 墙朝向 NW / NE / SW / SE


class NewGameRequest(BaseModel):
    n: int | None = Field(default=None, description="行数，不填则 9~15 随机")
    m: int | None = Field(default=None, description="列数，不填则 9~15 随机")
    seed: int | None = None
    goal_A: list[int] | None = Field(default=None, description="人类指定的 A 列集合，不填则随机")
    goal_B: list[int] | None = Field(default=None, description="人类指定的 B 列集合，不填则随机")


class PawnMoveRequest(BaseModel):
    to: list[int]  # [r, c]


class AIRequest(BaseModel):
    seed: int | None = None


class SkillSelectRequest(BaseModel):
    player: int
    skills: list[str]


class SkillRandomRequest(BaseModel):
    player: int
    seed: int | None = None


class SkillPlayRequest(BaseModel):
    skill: str
    to: list[int] | None = None  # 仅流沙陷阱使用


class ExternalMoveRequest(BaseModel):
    aid: str  # 已上传并编译的 AI id
    timeout: float = Field(default=5, description="AI 单步运行超时（秒）")


class MatchRequest(BaseModel):
    white: str = Field(default="random", description='"random" 或 aid（先手方参与者）')
    black: str = Field(default="random", description='"random" 或 aid（后手方参与者）')
    n: int | None = None
    m: int | None = None
    seed: int | None = None
    max_plies: int = Field(default=800, description="步数上限，超限判平局")
    timeout: float = Field(default=5, description="AI 单步运行超时（秒）")
    chooser: str = Field(default="random", description="出题方：white/black/random（抛硬币），另一方选边")


class AIGoalsRequest(BaseModel):
    aid: str
    m: int = Field(description="列数，AI 出 A/B 列集")
    timeout: float = 5


class AISideRequest(BaseModel):
    aid: str
    n: int = 9
    m: int = 9
    deads: list[list[int]] = Field(default_factory=list)
    sands: list[list[int]] = Field(default_factory=list)
    goal_A: list[int] = Field(default_factory=list)
    goal_B: list[int] = Field(default_factory=list)
    timeout: float = 5
