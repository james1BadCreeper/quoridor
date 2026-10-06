"""改版 Quoridor 核心规则引擎。

坐标约定（中文注释）：
- 棋盘为 n 行 × m 列，格子坐标 (r, c)，r∈[0,n), c∈[0,m)。
- 先手出生在顶行 (0, m//2)，后手出生在底行 (n-1, m//2)。
- 先手目标：底行 (n-1, c)，c∈A；后手目标：顶行 (0, c)，c∈B。
  A、B 为列集合，各自大小均为 floor(m/2)，相互独立（可相交、可留空列）。
- 墙为直墙（长 2）：横墙阻断纵向移动，竖墙阻断横向移动。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

# 类型别名
Orientation = Literal["H", "V"]


@dataclass
class Wall:
    """一面直墙：orientation H/V，位置 (wr, wc) 含义见 blocked_edges()。"""

    wr: int
    wc: int
    orientation: Orientation

    def to_dict(self) -> dict:
        return {
            "wr": self.wr,
            "wc": self.wc,
            "orientation": self.orientation,
        }

    @staticmethod
    def from_dict(d: dict) -> "Wall":
        if d.get("kind", "straight") != "straight":
            raise ValueError("旧棋谱含已删除的 L 墙，无法导入")
        if d.get("orientation") not in ("H", "V"):
            raise ValueError(f"未知墙朝向：{d.get('orientation')}")
        return Wall(
            wr=d["wr"],
            wc=d["wc"],
            orientation=d["orientation"],
        )


@dataclass
class GameState:
    n: int
    m: int
    walls_total: int  # 每人初始墙数 v
    pawns: list[list[int]]  # [先手(r,c), 后手(r,c)]
    walls_left: list[int]  # 剩余墙数
    walls: list[Wall] = field(default_factory=list)
    deads: set[tuple[int, int]] = field(default_factory=set)
    sands: set[tuple[int, int]] = field(default_factory=set)
    goal_A: list[int] = field(default_factory=list)  # 先手目标列
    goal_B: list[int] = field(default_factory=list)  # 后手目标列
    turn: int = 0  # 0 先手，1 后手
    bonus_moves: int = 0  # 因流沙获得的连续行动剩余次数（>0 时同一人继续走）
    winner: int | None = None
    win_reason: str | None = None
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "m": self.m,
            "walls_total": self.walls_total,
            "pawns": self.pawns,
            "walls_left": self.walls_left,
            "walls": [w.to_dict() for w in self.walls],
            "deads": sorted(self.deads),
            "sands": sorted(self.sands),
            "goal_A": self.goal_A,
            "goal_B": self.goal_B,
            "turn": self.turn,
            "bonus_moves": self.bonus_moves,
            "winner": self.winner,
            "win_reason": self.win_reason,
            "history": self.history,
        }

    @staticmethod
    def from_dict(d: dict) -> "GameState":
        s = GameState(
            n=d["n"],
            m=d["m"],
            walls_total=d["walls_total"],
            pawns=[list(p) for p in d["pawns"]],
            walls_left=list(d["walls_left"]),
            walls=[Wall.from_dict(w) for w in d.get("walls", [])],
            deads=set(tuple(x) for x in d.get("deads", [])),
            sands=set(tuple(x) for x in d.get("sands", [])),
            goal_A=list(d.get("goal_A", [])),
            goal_B=list(d.get("goal_B", [])),
            turn=d.get("turn", 0),
            bonus_moves=d.get("bonus_moves", 0),
            winner=d.get("winner"),
            win_reason=d.get("win_reason"),
            history=list(d.get("history", [])),
        )
        return s


# ---------------- 棋盘生成 ----------------

def wall_count(n: int, m: int) -> int:
    """双方各有 v=floor((n+1)*(m+1)/10) 个墙。"""
    return ((n + 1) * (m + 1)) // 10


def pick_goal_sets(m: int, rng: random.Random) -> tuple[list[int], list[int]]:
    """随机选两个各大小 floor(m/2) 的列集合 A、B（相互独立，可相交）。"""
    k = m // 2
    a = sorted(rng.sample(range(m), k))
    b = sorted(rng.sample(range(m), k))
    return a, b


def _has_path(state: GameState, player: int) -> bool:
    """BFS 判断 player 是否有步行路径到达目标（忽略剩余墙数）。"""
    from collections import deque

    blocked = build_blocked_edges(state)
    sr, sc = state.pawns[player]
    if (sr, sc) in state.deads:
        return False
    # 目标集合
    if player == 0:
        goals = {(state.n - 1, c) for c in state.goal_A}
    else:
        goals = {(0, c) for c in state.goal_B}
    if (sr, sc) in goals:
        return True
    opp = state.pawns[1 - player]
    seen = {(sr, sc)}
    dq = deque([(sr, sc)])
    while dq:
        r, c = dq.popleft()
        for nr, nc in [ (r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1) ]:
            if not (0 <= nr < state.n and 0 <= nc < state.m):
                continue
            if (nr, nc) in state.deads:
                continue
            if [nr, nc] == opp:
                continue  # 简化规则：不能进入对方格，无跳子
            if (r, c, nr, nc) in blocked:
                continue
            if (nr, nc) in seen:
                continue
            if (nr, nc) in goals:
                return True
            seen.add((nr, nc))
            dq.append((nr, nc))
    return False


def build_blocked_edges(state: GameState) -> set[tuple[int, int, int, int]]:
    """由当前墙集合计算被阻断的相邻格双向边。"""
    blocked: set[tuple[int, int, int, int]] = set()

    def add(r1: int, c1: int, r2: int, c2: int) -> None:
        blocked.add((r1, c1, r2, c2))
        blocked.add((r2, c2, r1, c1))

    for w in state.walls:
        if w.orientation == "H":
            # 交界行 wr，覆盖列 wc, wc+1
            add(w.wr - 1, w.wc, w.wr, w.wc)
            add(w.wr - 1, w.wc + 1, w.wr, w.wc + 1)
        elif w.orientation == "V":
            add(w.wr, w.wc - 1, w.wr, w.wc)
            add(w.wr + 1, w.wc - 1, w.wr + 1, w.wc)
    return blocked


def wall_edges(w: Wall) -> set[tuple[int, int, int, int]]:
    """单面墙阻断的单向边（用于重叠检测）。"""
    s: set[tuple[int, int, int, int]] = set()

    def add(r1: int, c1: int, r2: int, c2: int) -> None:
        s.add((r1, c1, r2, c2))
        s.add((r2, c2, r1, c1))

    if w.orientation == "H":
        add(w.wr - 1, w.wc, w.wr, w.wc)
        add(w.wr - 1, w.wc + 1, w.wr, w.wc + 1)
    elif w.orientation == "V":
        add(w.wr, w.wc - 1, w.wr, w.wc)
        add(w.wr + 1, w.wc - 1, w.wr + 1, w.wc)
    return s


def validate_goal_sets(m: int, goal_A: list[int], goal_B: list[int]) -> tuple[list[int], list[int]]:
    """校验人类指定的 A/B 列集合：各大小 floor(m/2)，范围内，内部无重复（A、B 间可相交）。"""
    k = m // 2
    a, b = sorted(goal_A), sorted(goal_B)
    if len(a) != k or len(b) != k:
        raise ValueError(f"A、B 大小必须各为 m//2={k}")
    if any(not isinstance(c, int) or not 0 <= c < m for c in a + b):
        raise ValueError(f"获胜列必须在 [0,{m}) 内")
    if len(set(a)) != k or len(set(b)) != k:
        raise ValueError("A、B 内部不能有重复列")
    return a, b


def new_game(
    n: int | None = None,
    m: int | None = None,
    seed: int | None = None,
    dead_ratio: float = 0.04,
    sand_ratio: float = 0.04,
    goal_A: list[int] | None = None,
    goal_B: list[int] | None = None,
) -> GameState:
    """随机开局：n,m∈[9,15]，死点/流沙各约 4%，保连通；A/B 可手动指定或随机。"""
    rng = random.Random(seed)
    n = n or rng.randint(9, 15)
    m = m or rng.randint(9, 15)
    if not (9 <= n <= 15 and 9 <= m <= 15):
        raise ValueError("n, m 必须在 [9,15] 内")
    v = wall_count(n, m)
    start0 = [0, m // 2]
    start1 = [n - 1, m // 2]
    if goal_A is None and goal_B is None:
        goal_A, goal_B = pick_goal_sets(m, rng)
    elif goal_A is None or goal_B is None:
        raise ValueError("A、B 必须同时指定或同时留空")
    else:
        goal_A, goal_B = validate_goal_sets(m, list(goal_A), list(goal_B))

    # 出生点与其周围一圈、双方底线行不受死点/流沙影响
    protected = {tuple(start0), tuple(start1)}
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            for base in (start0, start1):
                r, c = base[0] + dr, base[1] + dc
                if 0 <= r < n and 0 <= c < m:
                    protected.add((r, c))
    for c in range(m):
        protected.add((0, c))
        protected.add((n - 1, c))

    candidates = [(r, c) for r in range(n) for c in range(m) if (r, c) not in protected]
    n_dead = round(n * m * dead_ratio)
    n_sand = round(n * m * sand_ratio)

    # 最多重试 60 次保证双方开局有路
    for _ in range(60):
        rng.shuffle(candidates)
        deads = set(candidates[:n_dead])
        rest = [x for x in candidates if x not in deads]
        sands = set(rest[:n_sand])
        st = GameState(
            n=n, m=m, walls_total=v,
            pawns=[list(start0), list(start1)],
            walls_left=[v, v],
            deads=deads, sands=sands,
            goal_A=goal_A, goal_B=goal_B,
        )
        if _has_path(st, 0) and _has_path(st, 1):
            return st
    # 兜底：无特殊格
    return GameState(
        n=n, m=m, walls_total=v,
        pawns=[list(start0), list(start1)],
        walls_left=[v, v],
        goal_A=goal_A, goal_B=goal_B,
    )


# ---------------- 合法动作 ----------------

def legal_pawn_moves(state: GameState, player: int) -> list[list[int]]:
    """正交一步：不出界、不进死点/对方格、不穿墙。"""
    blocked = build_blocked_edges(state)
    r, c = state.pawns[player]
    opp = state.pawns[1 - player]
    out = []
    for nr, nc in [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)]:
        if not (0 <= nr < state.n and 0 <= nc < state.m):
            continue
        if (nr, nc) in state.deads:
            continue
        if [nr, nc] == opp:
            continue
        if (r, c, nr, nc) in blocked:
            continue
        out.append([nr, nc])
    return out


def _wall_in_bounds(state: GameState, w: Wall) -> bool:
    n, m = state.n, state.m
    if w.orientation == "H":
        return 1 <= w.wr <= n - 1 and 0 <= w.wc <= m - 2
    if w.orientation == "V":
        return 0 <= w.wr <= n - 2 and 1 <= w.wc <= m - 1
    return False


def is_wall_legal(state: GameState, player: int, w: Wall) -> tuple[bool, str]:
    """放墙合法性：有余墙、范围内、不与已有墙重边。"""
    if state.walls_left[player] <= 0:
        return False, "该玩家无剩余墙"
    if not _wall_in_bounds(state, w):
        return False, "墙位置越界"
    edges = wall_edges(w)
    if not edges:
        return False, "未知墙类型"
    existing: set[tuple[int, int, int, int]] = set()
    for old in state.walls:
        existing |= wall_edges(old)
    if edges & existing:
        return False, "与已有墙重叠"
    return True, ""


def _advance_turn(state: GameState) -> None:
    """回合推进：若 bonus_moves>0 则同一人继续，否则换人。"""
    if state.bonus_moves > 0:
        state.bonus_moves -= 1
        if state.bonus_moves > 0:
            return  # 仍是同一人（流沙链式触发可叠加，此处保持 turn 不变）
        # bonus 耗尽后换人
    state.turn = 1 - state.turn


def apply_pawn_move(state: GameState, to: list[int] | tuple[int, int]) -> GameState:
    """走子：合法则移动，踩流沙对方连走两次；到达目标则获胜；否则判围死。"""
    if state.winner is not None:
        raise ValueError("对局已结束")
    player = state.turn
    tr, tc = int(to[0]), int(to[1])
    if [tr, tc] not in legal_pawn_moves(state, player):
        raise ValueError(f"非法走子 {(tr, tc)}")
    state.pawns[player] = [tr, tc]
    state.history.append({"player": player, "type": "move", "to": [tr, tc]})

    # 到达目标直接获胜（先于围死判定）
    if player == 0 and (tr, tc - 0) == (state.n - 1, tc) and tc in state.goal_A:
        state.winner = player
        state.win_reason = "到达获胜点"
        return state
    if player == 1 and (tr == 0) and tc in state.goal_B:
        state.winner = player
        state.win_reason = "到达获胜点"
        return state

    # 流沙：对方连续行动两次 → 对方获得 bonus（共走 2 步）
    if (tr, tc) in state.sands:
        state.turn = 1 - player
        state.bonus_moves = 1  # 对方走一步后仍不换人，相当于连续两次
        state.history.append({"player": player, "type": "quicksand", "note": "对方连续行动两次"})
    else:
        _advance_turn(state)

    # 围死判定：无路径者直接获胜（规则 4）
    check_surround_win(state)
    return state


def apply_wall(state: GameState, w: Wall) -> GameState:
    """放墙：允许堵死路径，但堵死后被围者按规则 4 直接获胜。"""
    if state.winner is not None:
        raise ValueError("对局已结束")
    player = state.turn
    ok, msg = is_wall_legal(state, player, w)
    if not ok:
        raise ValueError(f"非法放墙：{msg}")
    state.walls.append(w)
    state.walls_left[player] -= 1
    state.history.append({"player": player, "type": "wall", "wall": w.to_dict()})
    _advance_turn(state)
    check_surround_win(state)
    return state


def check_surround_win(state: GameState) -> None:
    """若某玩家无任何步行路径到目标，则该被围者直接获胜。"""
    if state.winner is not None:
        return
    p0 = _has_path(state, 0)
    p1 = _has_path(state, 1)
    if not p0 and not p1:
        # 双方皆无路：判当前行动方的对方获胜？简化判先手获胜并注明。
        # 实际极罕见；选择让刚行动的人承担后果：turn 已切换，故 turn 方为受害对手…统一判 turn 方获胜更直观。
        state.winner = state.turn
        state.win_reason = "双方均被围死，轮到行动者获胜"
    elif not p0:
        state.winner = 0
        state.win_reason = "对方被围死，被围者直接获胜"
    elif not p1:
        state.winner = 1
        state.win_reason = "对方被围死，被围者直接获胜"


def random_ai_move(state: GameState, rng: random.Random | None = None) -> dict:
    """示例 AI：随机走子或随机放直墙。返回可直接记入棋谱的动作字典。"""
    rng = rng or random.Random()
    player = state.turn
    moves = legal_pawn_moves(state, player)
    # 30% 尝试放墙（若有余墙）
    if state.walls_left[player] > 0 and rng.random() < 0.3:
        for _ in range(50):
            if rng.random() < 0.5:
                w = Wall(wr=rng.randint(1, state.n - 1),
                         wc=rng.randint(0, state.m - 2), orientation="H")
            else:
                w = Wall(wr=rng.randint(0, state.n - 2),
                         wc=rng.randint(1, state.m - 1), orientation="V")
            ok, _ = is_wall_legal(state, player, w)
            if ok:
                return {"player": player, "type": "wall", "wall": w.to_dict()}
    if not moves:
        raise ValueError("无合法走子")
    return {"player": player, "type": "move", "to": list(rng.choice(moves))}
