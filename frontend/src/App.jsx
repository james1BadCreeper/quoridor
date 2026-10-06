import { useState } from 'react';

const API = '';

// 新开对局
async function apiNew(n, m, seed) {
  const r = await fetch(`${API}/api/games/new`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ n, m, seed }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export default function App() {
  const [gid, setGid] = useState(null);
  const [state, setState] = useState(null);
  const [snaps, setSnaps] = useState([]); // 快照数组，用于回放
  const [step, setStep] = useState(0);
  const [n, setN] = useState('');
  const [m, setM] = useState('');
  const [seed, setSeed] = useState('');
  const [players, setPlayers] = useState(['human', 'human']); // 每方 human/ai
  const [legal, setLegal] = useState([]);
  const [wallForm, setWallForm] = useState({ kind: 'straight', orientation: 'H', wr: 1, wc: 0, arm: 'NW' });

  const shown = snaps.length ? snaps[step] : state;

  async function refreshLegal(g, st) {
    const r = await fetch(`${API}/api/games/${g}/legal-moves?player=${st.turn}`);
    const j = await r.json();
    setLegal(j.moves || []);
  }

  function pushSnap(st) {
    setSnaps((s) => [...s, st]);
    setStep((s) => s + 0); // 保持指向最新稍后处理
  }

  async function newGame() {
    const j = await apiNew(
      n === '' ? null : Number(n), m === '' ? null : Number(m),
      seed === '' ? null : Number(seed),
    );
    setGid(j.id);
    setState(j.state);
    setSnaps([j.state]);
    setStep(0);
    refreshLegal(j.id, j.state);
  }

  async function doMove(to) {
    const r = await fetch(`${API}/api/games/${gid}/moves/pawn`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to }),
    });
    if (!r.ok) { alert(await r.text()); return; }
    const j = await r.json();
    setState(j.state);
    setSnaps((s) => [...s, j.state]);
    setStep(snaps.length); // 指向最新
    refreshLegal(gid, j.state);
  }

  async function doWall() {
    const body = {
      kind: wallForm.kind, wr: Number(wallForm.wr), wc: Number(wallForm.wc),
      orientation: wallForm.kind === 'straight' ? wallForm.orientation : null,
      arm: wallForm.kind === 'L' ? wallForm.arm : null,
    };
    const r = await fetch(`${API}/api/games/${gid}/moves/wall`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) { alert(await r.text()); return; }
    const j = await r.json();
    setState(j.state);
    setSnaps((s) => [...s, j.state]);
    setStep(snaps.length);
    refreshLegal(gid, j.state);
  }

  async function aiMove() {
    const r = await fetch(`${API}/api/games/${gid}/ai-move`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (!r.ok) { alert(await r.text()); return; }
    const j = await r.json();
    setState(j.state);
    setSnaps((s) => [...s, j.state]);
    setStep(snaps.length);
    refreshLegal(gid, j.state);
  }

  // 导出 json 棋谱（含快照以便回放）
  function exportKifu() {
    const blob = new Blob([JSON.stringify({ state, snaps }, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `kifu_${gid}.json`;
    a.click();
  }

  // 由文件导入棋谱并查看回放
  async function importKifu(e) {
    const f = e.target.files[0];
    if (!f) return;
    const j = JSON.parse(await f.text());
    if (j.snaps) {
      setSnaps(j.snaps);
      setStep(0);
      setState(j.snaps[j.snaps.length - 1]);
      setGid(null);
    } else {
      // 纯后端棋谱：恢复为对局
      const r = await fetch(`${API}/api/games/import`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(j.state || j),
      });
      const out = await r.json();
      setGid(out.id);
      setState(out.state);
      setSnaps([out.state]);
      setStep(0);
      refreshLegal(out.id, out.state);
    }
  }

  function cellClass(r, c) {
    if (!shown) return 'cell';
    const dead = shown.deads.some(([x, y]) => x === r && y === c);
    const sand = shown.sands.some(([x, y]) => x === r && y === c);
    let cls = 'cell';
    if (dead) cls += ' dead';
    if (sand) cls += ' sand';
    if (shown.n && r === shown.n - 1 && shown.goal_A.includes(c)) cls += ' goalA';
    if (shown.n && r === 0 && shown.goal_B.includes(c)) cls += ' goalB';
    if (legal.some(([x, y]) => x === r && y === c) && snaps.length && step === snaps.length - 1) cls += ' legal';
    return cls;
  }

  function cellText(r, c) {
    if (!shown) return '';
    if (shown.deads.some(([x, y]) => x === r && y === c)) return '✕';
    let t = '';
    if (shown.sands.some(([x, y]) => x === r && y === c)) t += '~';
    if (shown.pawns[0][0] === r && shown.pawns[0][1] === c) t += '●'; // 先手
    if (shown.pawns[1][0] === r && shown.pawns[1][1] === c) t += '○'; // 后手
    return t;
  }

  const isReplay = snaps.length > 0 && step < snaps.length - 1;

  return (
    <div className="app">
      <h2>Quoridor 改版（React + FastAPI）</h2>
      <div className="panel">
        <label>行 n <input value={n} onChange={(e) => setN(e.target.value)} placeholder="空=随机9~15" size={8} /></label>
        <label>列 m <input value={m} onChange={(e) => setM(e.target.value)} placeholder="空=随机9~15" size={8} /></label>
        <label>种子 <input value={seed} onChange={(e) => setSeed(e.target.value)} placeholder="可选" size={8} /></label>
        <label>先手 <select value={players[0]} onChange={(e) => setPlayers([e.target.value, players[1]])}>
          <option value="human">人类</option><option value="ai">AI(随机示例)</option>
        </select></label>
        <label>后手 <select value={players[1]} onChange={(e) => setPlayers([players[0], e.target.value])}>
          <option value="human">人类</option><option value="ai">AI(随机示例)</option>
        </select></label>
        <button onClick={newGame}>新对局</button>
        <span style={{ marginLeft: 8 }}>●=先手（目标底行绿框A） ○=后手（目标顶行蓝框B） ✕=死点 ~=流沙</span>
      </div>

      {shown && (
        <div className="panel">
          <div>轮到：{shown.turn === 0 ? '先手 ●' : '后手 ○'}（{players[shown.turn] === 'ai' ? 'AI' : '人类'}）
            剩余墙：先手 {shown.walls_left[0]} / 后手 {shown.walls_left[1]}
            {shown.winner != null && <b style={{ color: 'red' }}> 胜者：{shown.winner === 0 ? '先手' : '后手'}（{shown.win_reason}）</b>}
            {shown.bonus_moves > 0 && <span>（流沙：同一人继续行动）</span>}
            {isReplay && <span style={{ color: '#888' }}>（回放中，非最新）</span>}
          </div>
          <div className="board" style={{ marginTop: 8 }}>
            {Array.from({ length: shown.n }, (_, r) => (
              <div className="row" key={r}>
                {Array.from({ length: shown.m }, (_, c) => (
                  <div key={c} className={cellClass(r, c)}
                    onClick={() => { if (!isReplay && gid) doMove([r, c]); }}>
                    {cellText(r, c)}
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div style={{ marginTop: 8 }}>
            <button disabled={!gid || isReplay} onClick={aiMove}>AI 走一步（随机示例）</button>
            <button disabled={!gid || isReplay} onClick={doWall}>放墙</button>
            <select value={wallForm.kind} onChange={(e) => setWallForm({ ...wallForm, kind: e.target.value })}>
              <option value="straight">直墙</option><option value="L">L墙</option>
            </select>
            {wallForm.kind === 'straight' ? (
              <select value={wallForm.orientation} onChange={(e) => setWallForm({ ...wallForm, orientation: e.target.value })}>
                <option value="H">横</option><option value="V">竖</option>
              </select>
            ) : (
              <select value={wallForm.arm} onChange={(e) => setWallForm({ ...wallForm, arm: e.target.value })}>
                <option>NW</option><option>NE</option><option>SW</option><option>SE</option>
              </select>
            )}
            <label>wr <input value={wallForm.wr} size={3} onChange={(e) => setWallForm({ ...wallForm, wr: e.target.value })} /></label>
            <label>wc <input value={wallForm.wc} size={3} onChange={(e) => setWallForm({ ...wallForm, wc: e.target.value })} /></label>
          </div>
          <div style={{ marginTop: 8 }}>
            <button onClick={exportKifu}>导出棋谱 json</button>
            <label>导入/回放 <input type="file" accept=".json" onChange={importKifu} /></label>
          </div>
          {snaps.length > 1 && (
            <div style={{ marginTop: 8 }}>
              回放：<input type="range" min={0} max={snaps.length - 1} value={step}
                onChange={(e) => setStep(Number(e.target.value))} />
              {step + 1}/{snaps.length}
            </div>
          )}
          <div className="history">
            {(shown.history || []).map((h, i) => <div key={i}>{i}: {JSON.stringify(h)}</div>)}
          </div>
        </div>
      )}
    </div>
  );
}
