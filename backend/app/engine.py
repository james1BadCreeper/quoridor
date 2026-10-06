"""改版 Quoridor 核心规则引擎。

坐标约定（中文注释）：
- 棋盘为 n 行 × m 列，格子坐标 (r, c)，r∈[0,n), c∈[0,m)。
- 先手出生在顶行 (0, m//2)，后手出生在底行 (n-1, m//2)。
- 先手目标：底行 (n-1, c)，c∈A；后手目标：顶行 (0, c)，c∈B。
  A、B 为列集合，各自大小均为 floor(m/2)，相互独立（可相交、可留空列）。
- 墙为直墙（长 2）：横墙阻断纵向移动，竖墙阻断横向移动。
  打出技能“改造”后可放置 L 形墙（1+1 直角，总长 2）。
- 技能卡：开局选定目标点、知晓地图后，双方各选 k 张（可重复，对方不可见，
  同机对战除外）。行动前可打出一张，不占轮次；连续行动序列中最多打出一张。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

# 类型别名
Orientation = Literal["H", "V"]
LArm = Literal["NW", "NE", "SW", "SE"]
SkillId = Literal["l_remodel", "double_move", "phase_walk", "make_sand", "free_wall"]

# 技能定义：id → 中文名与描述
SKILLS: dict[str, dict[str, str]] = {
    "l_remodel": {"name": "改造", "desc": "获得 1 次 L 形墙放置权（放置时消耗 1 面墙存量）"},
    "double_move": {"name": "连续行动", "desc": "本回合连续移动两次（两次都必须是走子）"},
    "phase_walk": {"name": "穿墙", "desc": "下一次走子无视墙（仍不能进入死点）"},
    "make_sand": {"name": "流沙陷阱", "desc": "将一个格变为流沙（不能选死点/已有流沙/棋子格/获胜点）"},
    "free_wall": {"name": "免费墙", "desc": "下一次放墙不消耗墙存量"},
}


@dataclass
class Wall:
    """一面墙：直墙（长 2）或技能放置的 L 墙（1+1 直角）。"""

    wr: int
    wc: int
    orientation: Orientation | None = None  # 直墙 H/V；L 墙为 None
    kind: str = "straight"  # straight 或 L
    arm: LArm | None = None  # L 墙朝向 NW/NE/SW/SE

    def to_dict(self) -> dict:
        return {
            "wr": self.wr,
            "wc": self.wc,
            "orientation": self.orientation,
            "kind": self.kind,
            "arm": self.arm,
        }

    @staticmethod
    def from_dict(d: dict) -> "Wall":
        if d.get("kind", "straight") == "L" and d.get("arm") not in ("NW", "NE", "SW", "SE"):
            raise ValueError(f"未知 L 墙朝向：{d.get('arm')}")
        if d.get("kind", "straight") not in ("straight", "L"):
            raise ValueError(f"未知墙类型：{d.get('kind')}")
        if d.get("kind", "straight") == "straight" and d.get("orientation") not in ("H", "V"):
            raise ValueError(f"未知墙朝向：{d.get('orientation')}")
        return Wall(
            wr=d["wr"],
            wc=d["wc"],
            orientation=d.get("orientation"),
            kind=d.get("kind", "straight"),
            arm=d.get("arm"),
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
    bonus_moves: int = 0  # 连续行动剩余次数（>0 时同一人继续走）
    winner: int | None = None
    win_reason: str | None = None
    history: list[dict] = field(default_factory=list)
    # —— 技能系统 ——
    skill_k: int = 0  # 每人赛前选取的技能卡数 F(n,m)
    hands: list[dict] = field(default_factory=lambda: [{}, {}])  # 每人手牌 {技能id: 张数}
    skills_picked: list[bool] = field(default_factory=lambda: [False, False])
    started: bool = True  # 双方选完技能后方可行动（直接构造的状态默认为已开始）
    l_bonus: list[int] = field(default_factory=lambda: [0, 0])  # L 墙放置权（改造技能）
    phase_buff: list[bool] = field(default_factory=lambda: [False, False])  # 穿墙 buff（下次走子生效）
    free_buff: list[bool] = field(default_factory=lambda: [False, False])  # 免费墙 buff（下次放墙生效）
    must_move: bool = False  # 连续行动中：只能走子不能放墙
    seq_skill_used: bool = False  # 当前行动序列中是否已打出过技能卡

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
            "skill_k": self.skill_k,
            "hands": self.hands,
            "skills_picked": self.skills_picked,
            "started": self.started,
            "l_bonus": self.l_bonus,
            "phase_buff": self.phase_buff,
            "free_buff": self.free_buff,
            "must_move": self.must_move,
            "seq_skill_used": self.seq_skill_used,
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
            skill_k=d.get("skill_k", 0),
            hands=[dict(h) for h in d.get("hands", [{}, {}])],
            skills_picked=list(d.get("skills_picked", [True, True])),
            started=d.get("started", True),
            l_bonus=list(d.get("l_bonus", [0, 0])),
            phase_buff=list(d.get("phase_buff", [False, False])),
            free_buff=list(d.get("free_buff", [False, False])),
            must_move=d.get("must_move", False),
            seq_skill_used=d.get("seq_skill_used", False),
        )
        return s


# ---------------- 棋盘生成 ----------------

def wall_count(n: int, m: int) -> int:
    """双方各有 v=floor((n+1)*(m+1)/10) 个墙。"""
    return ((n + 1) * (m + 1)) // 10


def skill_count(n: int, m: int) -> int:
    """技能卡数 F(n,m)：最小地图 9×9 取 2，随墙数增长，15×15 取 5。"""
    return max(2, wall_count(n, m) // 5)


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
        for nr, nc in _step_neighbors(state, r, c, player, blocked):
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
        if w.kind == "L":
            wr, wc = w.wr, w.wc
            # 北臂：分隔 (wr-1,wc-1)-(wr-1,wc)；南臂：(wr,wc-1)-(wr,wc)
            # 西臂：分隔 (wr-1,wc-1)-(wr,wc-1)；东臂：(wr-1,wc)-(wr,wc)
            if w.arm and "N" in w.arm:
                add(wr - 1, wc - 1, wr - 1, wc)
            if w.arm and "S" in w.arm:
                add(wr, wc - 1, wr, wc)
            if w.arm and "W" in w.arm:
                add(wr - 1, wc - 1, wr, wc - 1)
            if w.arm and "E" in w.arm:
                add(wr - 1, wc, wr, wc)
        elif w.orientation == "H":
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

    if w.kind == "L":
        wr, wc = w.wr, w.wc
        if w.arm and "N" in w.arm:
            add(wr - 1, wc - 1, wr - 1, wc)
        if w.arm and "S" in w.arm:
            add(wr, wc - 1, wr, wc)
        if w.arm and "E" in w.arm:
            add(wr - 1, wc, wr, wc)
        if w.arm and "W" in w.arm:
            add(wr - 1, wc - 1, wr, wc - 1)
    elif w.orientation == "H":
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
            skill_k=skill_count(n, m),
            started=False,  # 双方选完技能卡后方可行动
        )
        if _has_path(st, 0) and _has_path(st, 1):
            return st
    # 兜底：无特殊格
    return GameState(
        n=n, m=m, walls_total=v,
        pawns=[list(start0), list(start1)],
        walls_left=[v, v],
        goal_A=goal_A, goal_B=goal_B,
        skill_k=skill_count(n, m),
        started=False,
    )


# ---------------- 合法动作 ----------------

def _step_neighbors(
    state: GameState, r: int, c: int, player: int,
    blocked: set[tuple[int, int, int, int]],
) -> list[list[int]]:
    """从 (r, c) 出发的单步可达格（含跳子规则）。

    标准跳子：邻格为对方棋子时，直线跳过；直线落点被挡（出界/死点/墙）时，
    改走对方棋子两侧的斜格。对方所在格本身不可停留。
    """
    opp = state.pawns[1 - player]
    out: list[list[int]] = []
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nr, nc = r + dr, c + dc
        if not (0 <= nr < state.n and 0 <= nc < state.m):
            continue
        if (nr, nc) in state.deads:
            continue
        if (r, c, nr, nc) in blocked:
            continue
        if [nr, nc] != opp:
            out.append([nr, nc])
            continue
        # 跳子：直线落点
        br, bc = nr + dr, nc + dc
        straight_ok = (
            0 <= br < state.n and 0 <= bc < state.m
            and (br, bc) not in state.deads
            and (nr, nc, br, bc) not in blocked
        )
        if straight_ok:
            out.append([br, bc])
        else:
            # 直线被挡：对方棋子两侧斜格（检查对方格→斜格的墙）
            for sdr, sdc in ((dc, dr), (-dc, -dr)):
                tr, tc = nr + sdr, nc + sdc
                if not (0 <= tr < state.n and 0 <= tc < state.m):
                    continue
                if (tr, tc) in state.deads:
                    continue
                if (nr, nc, tr, tc) in blocked:
                    continue
                if [tr, tc] not in out:
                    out.append([tr, tc])
    return out


def legal_pawn_moves(state: GameState, player: int, ignore_walls: bool = False) -> list[list[int]]:
    """合法走子：正交一步 + 跳子（穿墙 buff 下无视墙，仍不能进死点/对方格）。"""
    blocked = set() if ignore_walls else build_blocked_edges(state)
    r, c = state.pawns[player]
    return _step_neighbors(state, r, c, player, blocked)


def _wall_in_bounds(state: GameState, w: Wall) -> bool:
    n, m = state.n, state.m
    if w.kind == "L":
        return 1 <= w.wr <= n - 1 and 1 <= w.wc <= m - 1 and w.arm in ("NW", "NE", "SW", "SE")
    if w.orientation == "H":
        return 1 <= w.wr <= n - 1 and 0 <= w.wc <= m - 2
    if w.orientation == "V":
        return 0 <= w.wr <= n - 2 and 1 <= w.wc <= m - 1
    return False


def is_wall_legal(state: GameState, player: int, w: Wall, free: bool = False) -> tuple[bool, str]:
    """放墙合法性：有余墙（免费墙除外）、L 放置权、范围内、不与已有墙重边。"""
    if w.kind == "L" and state.l_bonus[player] <= 0:
        return False, "无 L 墙放置权（需先打出改造技能）"
    if not free and state.walls_left[player] <= 0:
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
    """回合推进：bonus 剩余则同一人继续（递减），否则换人并开启新的行动序列。"""
    if state.bonus_moves > 0:
        state.bonus_moves -= 1
        return  # 同一人继续行动
    state.turn = 1 - state.turn
    state.seq_skill_used = False
    state.must_move = False


def apply_pawn_move(state: GameState, to: list[int] | tuple[int, int]) -> GameState:
    """走子：合法则移动，踩流沙对方连走两次；到达目标则获胜；否则判围死。"""
    if state.winner is not None:
        raise ValueError("对局已结束")
    if not state.started:
        raise ValueError("双方选完技能卡后方可行动")
    player = state.turn
    tr, tc = int(to[0]), int(to[1])
    phasing = state.phase_buff[player]
    if [tr, tc] not in legal_pawn_moves(state, player, ignore_walls=phasing):
        raise ValueError(f"非法走子 {(tr, tc)}")
    state.pawns[player] = [tr, tc]
    state.phase_buff[player] = False  # 穿墙 buff 在走子后消耗
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

    # 流沙：对方连续行动两次；若本方处于连续行动（S2）中，剩余步数被流沙覆盖
    if (tr, tc) in state.sands:
        state.turn = 1 - player
        state.bonus_moves = 1  # 对方走一步后仍不换人，相当于连续两次
        state.must_move = False
        state.seq_skill_used = False  # 对方开启新的行动序列
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
    if not state.started:
        raise ValueError("双方选完技能卡后方可行动")
    player = state.turn
    if state.must_move:
        raise ValueError("连续行动中两次都必须是移动，不能放墙")
    free = state.free_buff[player]
    ok, msg = is_wall_legal(state, player, w, free=free)
    if not ok:
        raise ValueError(f"非法放墙：{msg}")
    state.walls.append(w)
    if w.kind == "L":
        state.l_bonus[player] -= 1
    if free:
        state.free_buff[player] = False
    else:
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


def goal_cells(state: GameState) -> set[tuple[int, int]]:
    """双方获胜点格集合（S4 流沙陷阱禁选）。"""
    cells = {(state.n - 1, c) for c in state.goal_A}
    cells |= {(0, c) for c in state.goal_B}
    return cells


def select_skills(state: GameState, player: int, picks: list[str]) -> GameState:
    """赛前选技能卡：数量须等于 skill_k，可重复，对方不可见（同机对战除外）。"""
    if player not in (0, 1):
        raise ValueError("player 必须为 0 或 1")
    if state.skills_picked[player]:
        raise ValueError("该玩家已选过技能卡")
    if len(picks) != state.skill_k:
        raise ValueError(f"须恰好选择 {state.skill_k} 张技能卡")
    for s in picks:
        if s not in SKILLS:
            raise ValueError(f"未知技能：{s}")
    hand: dict[str, int] = {}
    for s in picks:
        hand[s] = hand.get(s, 0) + 1
    state.hands[player] = hand
    state.skills_picked[player] = True
    state.history.append({"player": player, "type": "select_skills", "skills": list(picks)})
    if all(state.skills_picked):
        state.started = True
    return state


def random_skill_picks(skill_k: int, rng: random.Random | None = None) -> list[str]:
    """随机选 k 张技能卡（可重复）。"""
    rng = rng or random.Random()
    ids = list(SKILLS.keys())
    return [rng.choice(ids) for _ in range(skill_k)]


def play_skill(
    state: GameState,
    skill: str,
    to: list[int] | tuple[int, int] | None = None,
) -> GameState:
    """行动前打出一张技能卡（不占轮次，使用时双方均得知）。

    连续行动序列中最多打出一张；to 仅流沙陷阱使用，为目标格 [r, c]。
    """
    if state.winner is not None:
        raise ValueError("对局已结束")
    if not state.started:
        raise ValueError("双方选完技能卡后方可行动")
    player = state.turn
    if skill not in SKILLS:
        raise ValueError(f"未知技能：{skill}")
    if state.hands[player].get(skill, 0) <= 0:
        raise ValueError(f"手牌中没有技能 {SKILLS[skill]['name']}")
    if state.seq_skill_used:
        raise ValueError("本行动序列中已打出过技能卡")
    entry: dict = {"player": player, "type": "skill", "skill": skill}

    if skill == "l_remodel":
        state.l_bonus[player] += 1
    elif skill == "double_move":
        state.bonus_moves += 1  # 本回合再行动一次
        state.must_move = True  # 两次都必须是移动
    elif skill == "phase_walk":
        state.phase_buff[player] = True
    elif skill == "free_wall":
        state.free_buff[player] = True
    elif skill == "make_sand":
        if to is None:
            raise ValueError("流沙陷阱须指定目标格")
        tr, tc = int(to[0]), int(to[1])
        if not (0 <= tr < state.n and 0 <= tc < state.m):
            raise ValueError("目标格越界")
        if (tr, tc) in state.deads:
            raise ValueError("不能选死点")
        if (tr, tc) in state.sands:
            raise ValueError("该格已有流沙")
        if [tr, tc] in state.pawns:
            raise ValueError("不能选棋子所在格")
        if (tr, tc) in goal_cells(state):
            raise ValueError("不能选获胜点")
        state.sands.add((tr, tc))
        entry["to"] = [tr, tc]

    state.hands[player][skill] -= 1
    if state.hands[player][skill] <= 0:
        del state.hands[player][skill]
    state.seq_skill_used = True
    state.history.append(entry)
    return state


def random_sand_cell(state: GameState, rng: random.Random) -> list[int] | None:
    """为示例 AI 随机找一个合法的流沙落点，找不到返回 None。"""
    goals = goal_cells(state)
    for _ in range(50):
        r, c = rng.randrange(state.n), rng.randrange(state.m)
        if (r, c) in state.deads or (r, c) in state.sands:
            continue
        if [r, c] in state.pawns or (r, c) in goals:
            continue
        return [r, c]
    return None


def random_ai_skill(state: GameState, rng: random.Random | None = None) -> dict | None:
    """示例 AI 的技能决策：25% 概率打出一张手牌（无牌或序列已用过则返回 None）。"""
    rng = rng or random.Random()
    player = state.turn
    if state.seq_skill_used or not state.hands[player]:
        return None
    if rng.random() >= 0.25:
        return None
    skill = rng.choice(sorted(state.hands[player].keys()))
    to = None
    if skill == "make_sand":
        to = random_sand_cell(state, rng)
        if to is None:
            return None
    return {"skill": skill, "to": to}


def random_ai_move(state: GameState, rng: random.Random | None = None) -> dict:
    """示例 AI：随机走子或随机放墙（有 L 放置权时也可能放 L 墙）。"""
    rng = rng or random.Random()
    player = state.turn
    # 穿墙 buff 生效中则按无视墙选步（否则 buff 会被浪费）
    moves = legal_pawn_moves(state, player, ignore_walls=state.phase_buff[player])
    # 30% 尝试放墙（若有余墙或免费墙）
    if (state.walls_left[player] > 0 or state.free_buff[player]) and rng.random() < 0.3:
        for _ in range(50):
            roll = rng.random()
            if state.l_bonus[player] > 0 and roll < 0.25:
                w = Wall(wr=rng.randint(1, state.n - 1), wc=rng.randint(1, state.m - 1),
                         orientation=None, kind="L",
                         arm=rng.choice(["NW", "NE", "SW", "SE"]))
            elif roll < 0.6:
                w = Wall(wr=rng.randint(1, state.n - 1),
                         wc=rng.randint(0, state.m - 2), orientation="H")
            else:
                w = Wall(wr=rng.randint(0, state.n - 2),
                         wc=rng.randint(1, state.m - 1), orientation="V")
            ok, _ = is_wall_legal(state, player, w, free=state.free_buff[player])
            if ok:
                return {"player": player, "type": "wall", "wall": w.to_dict()}
    if not moves:
        raise ValueError("无合法走子")
    return {"player": player, "type": "move", "to": list(rng.choice(moves))}
