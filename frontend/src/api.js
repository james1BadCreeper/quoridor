// 后端接口封装 + 前端规则镜像（与 backend/app/engine.py 保持一致）。

export const API = '';

async function req(path, options) {
  const r = await fetch(`${API}${path}`, options);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

const json = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const apiNew = ({ n, m, seed, goal_A, goal_B }) =>
  req('/api/games/new', json({ n, m, seed, goal_A, goal_B }));
export const apiLegal = (gid, player) =>
  req(`/api/games/${gid}/legal-moves?player=${player}`).then((j) => ({ moves: j.moves || [], phased: !!j.phased }));
export const apiMovePawn = (gid, to) => req(`/api/games/${gid}/moves/pawn`, json({ to }));
export const apiPlaceWall = (gid, wall) => req(`/api/games/${gid}/moves/wall`, json(wall));
export const apiImport = (state) => req('/api/games/import', json(state));
export const apiSkills = () => req('/api/skills').then((j) => j.skills);
export const apiSkillSelect = (gid, player, skills) =>
  req(`/api/games/${gid}/skills/select`, json({ player, skills }));
export const apiSkillAiSelect = (gid, player, aid, lim) =>
  req(`/api/games/${gid}/skills/ai-select`, json({ player, aid, timeout: lim.timeout, memory_mb: lim.memoryMb }));
export const apiSkillPlay = (gid, skill, to) =>
  req(`/api/games/${gid}/skills/play`, json({ skill, to: to ?? null }));
export const apiAiList = () => req('/api/ai/list').then((j) => j.ais || []);
export const apiAiUpload = async (file) => {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch(`${API}/api/ai/upload`, { method: 'POST', body: fd });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
};
export const apiExternalMove = (gid, aid, lim) =>
  req(`/api/games/${gid}/ai-external-move`, json({ aid, timeout: lim.timeout, memory_mb: lim.memoryMb }));
export const apiMapPreview = (n, m, seed) =>
  req('/api/map/preview', json({ n, m, seed }));
export const apiAiGoals = (aid, { n, m, deads, sands }, lim) =>
  req('/api/ai/goals', json({ aid, n, m, deads, sands, timeout: lim.timeout, memory_mb: lim.memoryMb })).then((j) => ({ goal_A: j.goal_A, goal_B: j.goal_B }));
export const apiAiSide = (aid, { n, m, deads, sands, goal_A, goal_B }, lim) =>
  req('/api/ai/side', json({ aid, n, m, deads, sands, goal_A, goal_B, timeout: lim.timeout, memory_mb: lim.memoryMb })).then((j) => j.side);

// —— 单面墙阻断边（镜像 engine.wall_edges），用于悬停预览的本地合法性判断 ——
function edgesOf(w) {
  const e = [];
  const add = (r1, c1, r2, c2) => { e.push(`${r1},${c1},${r2},${c2}`, `${r2},${c2},${r1},${c1}`); };
  if (w.kind === 'L' && w.arm) {
    const { wr, wc } = w;
    if (w.arm.includes('N')) add(wr - 1, wc - 1, wr - 1, wc);
    if (w.arm.includes('S')) add(wr, wc - 1, wr, wc);
    if (w.arm.includes('W')) add(wr - 1, wc - 1, wr, wc - 1);
    if (w.arm.includes('E')) add(wr - 1, wc, wr, wc);
  } else if (w.orientation === 'H') {
    add(w.wr - 1, w.wc, w.wr, w.wc);
    add(w.wr - 1, w.wc + 1, w.wr, w.wc + 1);
  } else if (w.orientation === 'V') {
    add(w.wr, w.wc - 1, w.wr, w.wc);
    add(w.wr + 1, w.wc - 1, w.wr + 1, w.wc);
  }
  return e;
}

function inBounds(st, w) {
  const { n, m } = st;
  if (w.kind === 'L') return w.wr >= 1 && w.wr <= n - 1 && w.wc >= 1 && w.wc <= m - 1 && ['NW', 'NE', 'SW', 'SE'].includes(w.arm);
  if (w.orientation === 'H') return w.wr >= 1 && w.wr <= n - 1 && w.wc >= 0 && w.wc <= m - 2;
  if (w.orientation === 'V') return w.wr >= 0 && w.wr <= n - 2 && w.wc >= 1 && w.wc <= m - 1;
  return false;
}

// 本地预判放墙是否合法（最终以后端校验为准）
export function wallLocalLegal(st, player, w) {
  if (!st || st.winner != null) return { ok: false, reason: '对局已结束' };
  if (w.kind === 'L' && (st.l_bonus?.[player] ?? 0) <= 0) return { ok: false, reason: '无 L 墙放置权' };
  if (!st.free_buff?.[player] && st.walls_left[player] <= 0) return { ok: false, reason: '无剩余墙' };
  if (!inBounds(st, w)) return { ok: false, reason: '越界' };
  const edges = edgesOf(w);
  if (!edges.length) return { ok: false, reason: '未知墙类型' };
  const used = new Set();
  for (const old of st.walls) for (const k of edgesOf(old)) used.add(k);
  if (edges.some((k) => used.has(k))) return { ok: false, reason: '与已有墙重叠' };
  return { ok: true, reason: '' };
}

// —— 回放文件格式 quoridor-replay/1：头部静态信息 + 每步动态 + 事件增量 ——
// 旧全快照格式每个 snap 重复尺寸/死点/目标列，且 history 跨 snap 平方级重复；
// 新格式只记一次静态，墙/流沙/历史由事件累积重建，体积小一个量级。
export const REPLAY_FORMAT = 'quoridor-replay/1';

const DYNAMIC_KEYS = ['turn', 'pawns', 'walls_left', 'hands', 'l_bonus', 'phase_buff',
  'free_buff', 'must_move', 'seq_skill_used', 'bonus_moves', 'winner', 'win_reason'];
// 首步存全量（含 walls/sands），后续只存增量与变化字段（墙/流沙由事件累积重建，
// 其余字段与上一步相同则省略，解析时合并——旧全量文件同样可读）
const FULL_KEYS = [...DYNAMIC_KEYS, 'walls', 'sands'];
const eqJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export function buildReplay(snaps, meta) {
  if (!snaps.length) return null;
  const first = snaps[0];
  const header = {
    n: first.n, m: first.m, walls_total: first.walls_total,
    deads: first.deads, goal_A: first.goal_A, goal_B: first.goal_B, skill_k: first.skill_k,
    pawns0: first.pawns, walls_left0: first.walls_left,
    hands0: first.hands, l_bonus0: first.l_bonus, meta,
  };
  let prev = null;
  const plies = snaps.map((s, i) => {
    const prevLen = i === 0 ? 0 : snaps[i - 1].history.length;
    const dyn = { events: (s.history || []).slice(prevLen) };
    for (const k of (i === 0 ? FULL_KEYS : DYNAMIC_KEYS)) {
      if (i === 0 || k === 'turn' || k === 'pawns' || !eqJson(s[k], prev[k])) dyn[k] = s[k];
    }
    prev = s;
    return dyn;
  });
  return { format: REPLAY_FORMAT, header, plies };
}

export function parseReplay(j) {
  if (!j || j.format !== REPLAY_FORMAT) return null;
  const H = j.header;
  // 兼容首步全量缺 walls/sands 的旧增量文件（回退为空开局累积）
  let walls = [...(j.plies[0]?.walls ?? [])], sands = [...(j.plies[0]?.sands ?? [])];
  let history = [];
  // 动态基线：首步全量，不足补头部初值；后续与上一步合并
  let base = {
    turn: 0, pawns: H.pawns0, walls_left: H.walls_left0, hands: H.hands0, l_bonus: H.l_bonus0,
    phase_buff: [false, false], free_buff: [false, false],
    must_move: false, seq_skill_used: false, bonus_moves: 0, winner: null, win_reason: null,
  };
  const snaps = j.plies.map((p, i) => {
    if (i > 0) {
      for (const e of p.events || []) {
        if (e.type === 'wall') walls.push(e.wall);
        if (e.type === 'skill' && e.skill === 'make_sand' && e.to) sands.push(e.to);
      }
    }
    base = { ...base, ...p };
    for (const e of p.events || []) history.push(e);
    return {
      n: H.n, m: H.m, walls_total: H.walls_total, deads: H.deads,
      goal_A: H.goal_A, goal_B: H.goal_B, skill_k: H.skill_k,
      pawns: base.pawns, turn: base.turn, walls: [...walls], walls_left: base.walls_left,
      sands: [...sands], hands: base.hands, l_bonus: base.l_bonus,
      phase_buff: base.phase_buff, free_buff: base.free_buff,
      must_move: base.must_move, seq_skill_used: base.seq_skill_used,
      bonus_moves: base.bonus_moves, winner: base.winner, win_reason: base.win_reason,
      history: [...history], started: true, skills_picked: [true, true],
    };
  });
  return { snaps, meta: H.meta };
}

// 技能卡数 F(n,m)（镜像 engine.skill_count）：最小地图取 2，随墙数增长
export const skillK = (n, m) => Math.max(2, Math.floor(Math.floor(((n + 1) * (m + 1)) / 10) / 5));

// 本地预判流沙落点是否合法（镜像 play_skill 的 make_sand 校验）
export function sandLocalLegal(st, r, c) {
  if (!st || st.winner != null) return { ok: false, reason: '对局已结束' };
  if (!(0 <= r && r < st.n && 0 <= c && c < st.m)) return { ok: false, reason: '越界' };
  if (st.deads.some(([x, y]) => x === r && y === c)) return { ok: false, reason: '不能选死点' };
  if (st.sands.some(([x, y]) => x === r && y === c)) return { ok: false, reason: '该格已有流沙' };
  if (st.pawns.some(([x, y]) => x === r && y === c)) return { ok: false, reason: '不能选棋子所在格' };
  if ((r === st.n - 1 && st.goal_A.includes(c)) || (r === 0 && st.goal_B.includes(c))) {
    return { ok: false, reason: '不能选获胜点' };
  }
  return { ok: true, reason: '' };
}
