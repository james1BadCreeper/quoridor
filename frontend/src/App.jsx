import { useRef, useState } from 'react';
import Board from './components/Board.jsx';
import SetupWizard from './components/SetupWizard.jsx';
import { apiAiMove, apiImport, apiLegal, apiMovePawn, apiNew, apiPlaceWall, wallLocalLegal } from './api.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 历史记录中文格式化
function fmtHist(h, seatNames) {
  const who = `${seatNames[h.player]}(${h.player === 0 ? '先手' : '后手'})`;
  if (h.type === 'move') return `${who} 走子 → (${h.to[0]}, ${h.to[1]})`;
  if (h.type === 'wall') {
    const w = h.wall;
    const wallName = w.orientation === 'H' ? '横墙' : '竖墙';
    return `${who} 放${wallName} @ (${w.wr}, ${w.wc})`;
  }
  if (h.type === 'quicksand') return `${who} 踩中流沙，对方连续行动两次`;
  return JSON.stringify(h);
}

export default function App() {
  const [wizardOpen, setWizardOpen] = useState(true);
  const [gid, setGid] = useState(null);
  const [state, setState] = useState(null); // 最新状态（权威）
  const [snaps, setSnaps] = useState([]); // 快照，用于回放
  const [step, setStep] = useState(0);
  const [legal, setLegal] = useState([]);
  const [mode, setMode] = useState('move');
  const [wallSel, setWallSel] = useState({ orientation: 'H' });
  const [ghost, setGhost] = useState(null);
  const [seatNames, setSeatNames] = useState(['先手', '后手']);
  const [seatTypes, setSeatTypes] = useState(['human', 'human']);
  const [autoAI, setAutoAI] = useState(false);
  const [busy, setBusy] = useState(false);

  // refs 供 AI 走到底循环读取最新值
  const stateRef = useRef(state); stateRef.current = state;
  const typesRef = useRef(seatTypes); typesRef.current = seatTypes;
  const gidRef = useRef(gid); gidRef.current = gid;
  const autoRef = useRef(false);

  const shown = snaps.length ? snaps[Math.min(step, snaps.length - 1)] : null;
  const isReplay = snaps.length > 0 && step < snaps.length - 1;
  const live = gid && state && state.winner == null && !isReplay;

  // 墙所属方（按 history 中放墙顺序对应 walls 数组）
  const wallOwners = (() => {
    if (!shown) return [];
    const owners = [];
    for (const h of shown.history || []) if (h.type === 'wall') owners.push(h.player);
    return owners;
  })();

  async function refreshLegal(g, st) {
    try { setLegal(await apiLegal(g, st.turn)); } catch { setLegal([]); }
  }

  function appendSnap(st) {
    setSnaps((prev) => [...prev, st]);
    setStep(snaps.length); // snaps 为追加前数组，其长度即新末尾下标
    setState(st);
    setGhost(null);
    refreshLegal(gidRef.current, st);
  }

  async function createGame(cfg) {
    setBusy(true);
    try {
      const j = await apiNew({ n: cfg.n, m: cfg.m, seed: cfg.seed, goal_A: cfg.goal_A, goal_B: cfg.goal_B });
      const names = [cfg.participants[cfg.seatOf[0]].name, cfg.participants[cfg.seatOf[1]].name];
      const types = [cfg.participants[cfg.seatOf[0]].type, cfg.participants[cfg.seatOf[1]].type];
      setGid(j.id);
      setSeatNames(names);
      setSeatTypes(types);
      setSnaps([j.state]);
      setStep(0);
      setState(j.state);
      setMode('move');
      setWizardOpen(false);
      refreshLegal(j.id, j.state);
    } catch (e) { alert(`开局失败：${e.message}`); } finally { setBusy(false); }
  }

  async function doMove(r, c) {
    if (!live) return;
    setBusy(true);
    try { appendSnap((await apiMovePawn(gid, [r, c])).state); }
    catch (e) { alert(e.message); } finally { setBusy(false); }
  }

  async function doPlaceWall(wall) {
    if (!live) return;
    const chk = wallLocalLegal(state, state.turn, wall);
    if (!chk.ok) { alert(`此处不可放墙：${chk.reason}`); return; }
    setBusy(true);
    try {
      appendSnap((await apiPlaceWall(gid, {
        wr: wall.wr, wc: wall.wc, orientation: wall.orientation,
      })).state);
    } catch (e) { alert(e.message); } finally { setBusy(false); }
  }

  async function aiOnce(g) {
    const j = await apiAiMove(g ?? gidRef.current);
    appendSnap(j.state);
    return j.state;
  }

  async function aiMove() {
    if (!live || busy) return;
    setBusy(true);
    try { await aiOnce(); } catch (e) { alert(e.message); } finally { setBusy(false); }
  }

  async function aiToEnd() {
    if (autoRef.current) { autoRef.current = false; return; }
    autoRef.current = true;
    setAutoAI(true);
    try {
      for (let i = 0; i < 500 && autoRef.current; i++) {
        const s = stateRef.current;
        if (!s || s.winner != null) break;
        if (typesRef.current[s.turn] !== 'ai') break;
        if (!gidRef.current) break;
        try { await aiOnce(); } catch (e) { alert(e.message); break; }
        await sleep(350);
      }
    } finally { autoRef.current = false; setAutoAI(false); }
  }

  function exportKifu() {
    if (!state) return;
    const blob = new Blob([JSON.stringify({ state, snaps, meta: { seatNames, seatTypes } }, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `kifu_${gid ?? 'local'}.json`;
    a.click();
  }

  async function importKifu(e) {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const j = JSON.parse(await f.text());
      if (j.snaps) {
        setSnaps(j.snaps);
        setStep(0);
        setState(j.snaps[j.snaps.length - 1]);
        if (j.meta) { setSeatNames(j.meta.seatNames); setSeatTypes(j.meta.seatTypes); }
        setGid(null);
        setWizardOpen(false);
      } else {
        const out = await apiImport(j.state || j);
        setGid(out.id);
        setState(out.state);
        setSnaps([out.state]);
        setStep(0);
        setWizardOpen(false);
        refreshLegal(out.id, out.state);
      }
    } catch (err) { alert(`导入失败：${err.message}`); }
    e.target.value = '';
  }

  const turnIsAI = state && seatTypes[state.turn] === 'ai';

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><span className="logo">♞</span> Quoridor 改版</div>
        <div className="topactions">
          <button className="btn ghost" onClick={() => setWizardOpen(true)}>新对局</button>
          <button className="btn ghost" onClick={exportKifu} disabled={!state}>导出棋谱</button>
          <label className="btn ghost filebtn">导入/回放<input type="file" accept=".json" onChange={importKifu} hidden /></label>
        </div>
      </header>

      {wizardOpen && (
        <SetupWizard onCreate={createGame} onCancel={() => setWizardOpen(false)} hasGame={!!state} />
      )}

      {shown && !wizardOpen && (
        <div className="main">
          <section className="card boardcard">
            <div className="statusbar">
              <span className={`turnbadge p${shown.turn}`}>
                轮到 {seatNames[shown.turn]}（{shown.turn === 0 ? '先手●' : '后手○'}）
              </span>
              {shown.bonus_moves > 0 && <span className="pill warn">流沙：同一人继续行动</span>}
              {isReplay && <span className="pill">回放中 {step + 1}/{snaps.length}</span>}
              {shown.winner != null && (
                <span className="pill win">胜者：{seatNames[shown.winner]}（{shown.win_reason}）</span>
              )}
            </div>
            <div className="wallsline">
              <span className="wcount p0">● 墙 {shown.walls_left[0]}</span>
              <span className="wcount p1">○ 墙 {shown.walls_left[1]}</span>
              <span className="muted small">A=[{shown.goal_A.join(',')}] → 先手底线绿标　B=[{shown.goal_B.join(',')}] → 后手顶线蓝标</span>
            </div>
            <Board st={shown} legal={live && mode === 'move' ? legal : []}
              mode={mode} wallSel={wallSel} ghost={ghost} wallOwners={wallOwners}
              interactive={!!live}
              onCellClick={(r, c) => doMove(r, c)}
              onSlotHover={(w) => setGhost({ wall: w, ...wallLocalLegal(state, state.turn, w) })}
              onSlotLeave={() => setGhost(null)}
              onSlotClick={(w) => doPlaceWall(w)} />
            <div className="legend">
              <span><i className="sw death" />死点不可进</span>
              <span><i className="sw sand" />流沙：对方连走两次</span>
              <span><i className="sw ga" />先手目标</span>
              <span><i className="sw gb" />后手目标</span>
            </div>
          </section>

          <aside className="side">
            <div className="card">
              <h3>行动</h3>
              <div className="seg">
                {['move', 'wall'].map((v) => (
                  <button key={v} className={`segbtn${mode === v ? ' active' : ''}`}
                    onClick={() => { setMode(v); setGhost(null); }}>
                    {v === 'move' ? '走子' : '放墙'}
                  </button>
                ))}
              </div>
              {mode === 'wall' && (
                <div>
                  <div className="seg">
                    <button className={`segbtn${wallSel.orientation === 'H' ? ' active' : ''}`}
                      onClick={() => setWallSel({ orientation: 'H' })}>横墙</button>
                    <button className={`segbtn${wallSel.orientation === 'V' ? ' active' : ''}`}
                      onClick={() => setWallSel({ orientation: 'V' })}>竖墙</button>
                  </div>
                  <p className="muted small">把鼠标移到棋盘间隙上预览，绿色可放、红色非法，点击落子。</p>
                </div>
              )}
              <div className="rowbtns">
                <button className={`btn${turnIsAI ? ' primary' : ' ghost'}`} disabled={!live || busy} onClick={aiMove}>
                  AI 行棋{turnIsAI ? '（轮到 AI）' : ''}
                </button>
                <button className="btn ghost" disabled={!live || busy} onClick={aiToEnd}>
                  {autoAI ? '停止连走' : 'AI 走到底'}
                </button>
              </div>
            </div>

            {snaps.length > 1 && (
              <div className="card">
                <h3>回放</h3>
                <input type="range" className="slider" min={0} max={snaps.length - 1} value={step}
                  onChange={(e) => setStep(Number(e.target.value))} />
                <div className="muted small">{step + 1} / {snaps.length} 步{isReplay ? '（点击棋盘已锁定，拖到末尾继续）' : ''}</div>
              </div>
            )}

            <div className="card">
              <h3>棋谱（{shown.history?.length ?? 0} 手）</h3>
              <div className="history">
                {(shown.history || []).map((h, i) => (
                  <div key={i} className={i === (shown.history.length - 1) ? 'hl' : ''}>
                    {i + 1}. {fmtHist(h, seatNames)}
                  </div>
                ))}
              </div>
            </div>
          </aside>
        </div>
      )}

      {!shown && !wizardOpen && <p className="muted">点击「新对局」开始。</p>}
    </div>
  );
}
