// SVG 棋盘：格子 / 死点 / 流沙 / 获胜点 / 棋子 / 墙体 / 悬停预览 / 点击放墙。
import { useMemo } from 'react';

export const CELL = 40;
export const GAP = 12;
const P = CELL + GAP; // 步长

const has = (list, r, c) => list.some(([x, y]) => x === r && y === c);

// —— 几何：各类墙槽矩形 ——
export function hSlotRect(wr, wc) {
  return { x: GAP + wc * P, y: wr * P, w: 2 * CELL + GAP, h: GAP };
}
export function vSlotRect(wr, wc) {
  return { x: wc * P, y: GAP + wr * P, w: GAP, h: 2 * CELL + GAP };
}
// L 墙各臂矩形（与后端 arm 语义一致：N=北竖段，S=南竖段，W=西横段，E=东横段）
export function lSegRects(wr, wc, arm) {
  const segs = [];
  if (arm.includes('N')) segs.push({ x: wc * P, y: GAP + (wr - 1) * P, w: GAP, h: CELL });
  if (arm.includes('S')) segs.push({ x: wc * P, y: GAP + wr * P, w: GAP, h: CELL });
  if (arm.includes('W')) segs.push({ x: GAP + (wc - 1) * P, y: wr * P, w: CELL, h: GAP });
  if (arm.includes('E')) segs.push({ x: GAP + wc * P, y: wr * P, w: CELL, h: GAP });
  // 拐角补块
  segs.push({ x: wc * P, y: wr * P, w: GAP, h: GAP });
  return segs;
}

function wallShapes(w) {
  if (w.kind === 'L') return lSegRects(w.wr, w.wc, w.arm);
  if (w.orientation === 'H') return [hSlotRect(w.wr, w.wc)];
  if (w.orientation === 'V') return [vSlotRect(w.wr, w.wc)];
  return [];
}

function slotWall(wallSel, a, b) {
  if (wallSel.kind === 'L') return { kind: 'L', wr: a, wc: b, orientation: null, arm: wallSel.arm };
  return { kind: 'straight', wr: a, wc: b, orientation: wallSel.orientation, arm: null };
}

export default function Board({
  st, legal, phased, mode, wallSel, ghost, wallOwners,
  interactive, onCellClick, onSlotHover, onSlotLeave, onSlotClick,
  sandMode, onSandClick,
}) {
  const W = st.m * P + GAP;
  const H = st.n * P + GAP;
  const legalSet = useMemo(() => new Set(legal.map(([r, c]) => `${r},${c}`)), [legal]);

  // 放墙槽位（横墙 / 竖墙 / L 墙交点）
  const slots = useMemo(() => {
    const out = [];
    if (wallSel.kind === 'L') {
      for (let wr = 1; wr <= st.n - 1; wr++)
        for (let wc = 1; wc <= st.m - 1; wc++) {
          const segs = lSegRects(wr, wc, wallSel.arm);
          const xs = segs.map((s) => s.x), ys = segs.map((s) => s.y);
          out.push({
            key: `L${wr},${wc}`, wall: slotWall(wallSel, wr, wc),
            rect: {
              x: Math.min(...xs) - 3, y: Math.min(...ys) - 3,
              w: Math.max(...xs.map((x, i) => x + segs[i].w)) - Math.min(...xs) + 6,
              h: Math.max(...ys.map((y, i) => y + segs[i].h)) - Math.min(...ys) + 6,
            },
          });
        }
      return out;
    }
    if (wallSel.orientation === 'H') {
      for (let wr = 1; wr <= st.n - 1; wr++)
        for (let wc = 0; wc <= st.m - 2; wc++) out.push({ key: `H${wr},${wc}`, rect: hSlotRect(wr, wc), wall: slotWall(wallSel, wr, wc) });
    } else {
      for (let wr = 0; wr <= st.n - 2; wr++)
        for (let wc = 1; wc <= st.m - 1; wc++) out.push({ key: `V${wr},${wc}`, rect: vSlotRect(wr, wc), wall: slotWall(wallSel, wr, wc) });
    }
    return out;
  }, [st.n, st.m, wallSel]);

  const ghostShapes = ghost ? wallShapes(ghost.wall) : [];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="qboard" role="img" aria-label="棋盘">
      <defs>
        <radialGradient id="pawn0" cx="35%" cy="30%">
          <stop offset="0%" stopColor="#fecdd3" /><stop offset="100%" stopColor="#f43f5e" />
        </radialGradient>
        <radialGradient id="pawn1" cx="35%" cy="30%">
          <stop offset="0%" stopColor="#bfdbfe" /><stop offset="100%" stopColor="#3b82f6" />
        </radialGradient>
        <linearGradient id="sandg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#fde68a" /><stop offset="100%" stopColor="#d97706" />
        </linearGradient>
      </defs>

      <rect x={0} y={0} width={W} height={H} rx={12} className="q-groove" />

      {/* 格子 */}
      {Array.from({ length: st.n }, (_, r) =>
        Array.from({ length: st.m }, (_, c) => {
          const x = GAP + c * P, y = GAP + r * P;
          const dead = has(st.deads, r, c);
          const sand = has(st.sands, r, c);
          const gA = r === st.n - 1 && st.goal_A.includes(c);
          const gB = r === 0 && st.goal_B.includes(c);
          const isLegal = interactive && mode === 'move' && legalSet.has(`${r},${c}`);
          return (
            <g key={`${r},${c}`}>
              <rect x={x} y={y} width={CELL} height={CELL} rx={7}
                className={`q-cell${dead ? ' is-dead' : ''}${sand ? ' is-sand' : ''}${gA ? ' is-goalA' : ''}${gB ? ' is-goalB' : ''}${isLegal ? ' is-legal' : ''}`}
                fill={sand ? 'url(#sandg)' : undefined} />
              {gA && <path d={`M ${x + CELL / 2 - 6} ${y + 6} l 6 8 l 6 -8 z`} className="q-goalmarkA" />}
              {gB && <path d={`M ${x + CELL / 2 - 6} ${y + CELL - 6} l 6 -8 l 6 8 z`} className="q-goalmarkB" />}
              {dead && (
                <g className="q-deadmark" strokeLinecap="round">
                  <line x1={x + 12} y1={y + 12} x2={x + CELL - 12} y2={y + CELL - 12} />
                  <line x1={x + CELL - 12} y1={y + 12} x2={x + 12} y2={y + CELL - 12} />
                </g>
              )}
              {sand && !dead && (
                <g className="q-sandmark" fill="currentColor">
                  <circle cx={x + 13} cy={y + CELL - 11} r={2.4} />
                  <circle cx={x + CELL / 2} cy={y + CELL - 11} r={2.4} />
                  <circle cx={x + CELL - 13} cy={y + CELL - 11} r={2.4} />
                </g>
              )}
              {isLegal && <circle cx={x + CELL / 2} cy={y + CELL / 2} r={6} className={`q-dot${phased ? ' phased' : ''}`} />}
              {/* 棋子 */}
              {st.pawns[0][0] === r && st.pawns[0][1] === c && (
                <g className={st.turn === 0 && st.winner == null ? 'q-pawnturn' : ''}>
                  <circle cx={x + CELL / 2} cy={y + CELL / 2} r={14} fill="url(#pawn0)" className="q-pawn p0" />
                  <text x={x + CELL / 2} y={y + CELL / 2 + 5} textAnchor="middle" className="q-pawntext">先</text>
                </g>
              )}
              {st.pawns[1][0] === r && st.pawns[1][1] === c && (
                <g className={st.turn === 1 && st.winner == null ? 'q-pawnturn' : ''}>
                  <circle cx={x + CELL / 2} cy={y + CELL / 2} r={14} fill="url(#pawn1)" className="q-pawn p1" />
                  <text x={x + CELL / 2} y={y + CELL / 2 + 5} textAnchor="middle" className="q-pawntext">后</text>
                </g>
              )}
              {/* 点击层：走子高亮格，或流沙陷阱选格 */}
              <rect x={x} y={y} width={CELL} height={CELL} fill="transparent"
                className={`${isLegal ? 'q-clickable' : ''}${sandMode && interactive && !dead ? ' sandpick' : ''}`}
                onClick={() => {
                  if (sandMode && interactive && !dead) onSandClick(r, c);
                  else if (isLegal) onCellClick(r, c);
                }} />
            </g>
          );
        }),
      )}

      {/* 已放墙（按所属方着色） */}
      {st.walls.map((w, i) => (
        <g key={i} className={`q-wall owner${wallOwners[i] ?? 'x'}`}>
          {wallShapes(w).map((s, j) => (
            <rect key={j} x={s.x + 1} y={s.y + 1} width={s.w - 2} height={s.h - 2} rx={3} />
          ))}
        </g>
      ))}

      {/* 悬停预览 */}
      {ghost && (
        <g className={`q-ghost ${ghost.ok ? 'ok' : 'bad'}`}>
          {ghostShapes.map((s, j) => (
            <rect key={j} x={s.x + 1} y={s.y + 1} width={s.w - 2} height={s.h - 2} rx={3} />
          ))}
        </g>
      )}

      {/* 放墙槽位热区 */}
      {interactive && mode === 'wall' && slots.map((s) => (
        <rect key={s.key} x={s.rect.x} y={s.rect.y} width={s.rect.w} height={s.rect.h}
          fill="transparent" className="q-slot"
          onMouseEnter={() => onSlotHover(s.wall)}
          onMouseLeave={onSlotLeave}
          onClick={() => onSlotClick(s.wall)} />
      ))}
    </svg>
  );
}
