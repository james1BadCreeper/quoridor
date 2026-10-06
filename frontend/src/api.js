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
export const apiAiMove = (gid) => req(`/api/games/${gid}/ai-move`, json({}));
export const apiImport = (state) => req('/api/games/import', json(state));
export const apiSkills = () => req('/api/skills').then((j) => j.skills);
export const apiSkillSelect = (gid, player, skills) =>
  req(`/api/games/${gid}/skills/select`, json({ player, skills }));
export const apiSkillRandom = (gid, player) =>
  req(`/api/games/${gid}/skills/random`, json({ player }));
export const apiSkillPlay = (gid, skill, to) =>
  req(`/api/games/${gid}/skills/play`, json({ skill, to: to ?? null }));

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
