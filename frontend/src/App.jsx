import { useEffect, useRef, useState } from 'react';
import BatchPanel from './components/BatchPanel.jsx';
import Board from './components/Board.jsx';
import SetupWizard from './components/SetupWizard.jsx';
import {
  apiAiBatchGame, apiExternalMove, apiImport, apiLegal, apiMovePawn, apiNew, apiPlaceWall,
  apiSkillAiSelect, apiSkillPlay, apiSkillSelect, apiSkills,
  buildReplay, parseReplay,
  sandLocalLegal, wallLocalLegal,
} from './api.js';

// 历史记录中文格式化
function fmtHist(h, seatNames, skillDefs) {
  const who = `${seatNames[h.player]}(${h.player === 0 ? '先手' : '后手'})`;
  if (h.type === 'move') return `${who} 走子 → (${h.to[0]}, ${h.to[1]})`;
  if (h.type === 'skill') {
    const nm = skillDefs[h.skill]?.name ?? h.skill;
    const extra = h.skill === 'make_sand' && h.to ? ` @ (${h.to[0]}, ${h.to[1]})` : '';
    return `${who} 打出技能【${nm}】${extra}（双方得知）`;
  }
  if (h.type === 'select_skills') return `${who} 选定技能卡`;
  if (h.type === 'wall') {
    const w = h.wall;
    const kind = w.kind === 'L' ? `L 墙 ${w.arm}` : w.orientation === 'H' ? '横墙' : '竖墙';
    return `${who} 放${kind} @ (${w.wr}, ${w.wc})`;
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
  const [legal, setLegal] = useState({ moves: [], phased: false });
  const [mode, setMode] = useState('move');
  const [wallSel, setWallSel] = useState({ kind: 'straight', orientation: 'H', arm: 'NW' });
  const [ghost, setGhost] = useState(null);
  const [seatNames, setSeatNames] = useState(['红', '蓝']);
  const [seatTypes, setSeatTypes] = useState(['human', 'human']);
  const [redSeat, setRedSeat] = useState(0); // 红方所在席位（颜色跟身份：红恒红、蓝恒蓝）
  // 身份色号：红方 0（红系）、蓝方 1（蓝系），替代原来的席位色号
  const idc = (seat) => (seat === redSeat ? 0 : 1);
  const [seatAIs, setSeatAIs] = useState(['builtin-random', 'builtin-random']); // AI 席位来源（后端 aid）
  const [aiLim, setAiLim] = useState({ timeout: 5, memoryMb: 256 }); // 全局 AI 资源限制（向导配置）
  const [autoAI, setAutoAI] = useState(false);
  const [busy, setBusy] = useState(false);
  const [skillDefs, setSkillDefs] = useState({});
  const [sandMode, setSandMode] = useState(false); // 流沙陷阱选格模式
  const histRef = useRef(null); // 棋谱列表，用于自动跟随最新

  useEffect(() => {
    apiSkills().then(setSkillDefs).catch(() => {});
  }, []);

  // refs 供 AI 走到底循环读取最新值
  const stateRef = useRef(state); stateRef.current = state;
  const typesRef = useRef(seatTypes); typesRef.current = seatTypes;
  const aisRef = useRef(seatAIs); aisRef.current = seatAIs;
  const gidRef = useRef(gid); gidRef.current = gid;
  const limRef = useRef(aiLim); limRef.current = aiLim;

  const shown = snaps.length ? snaps[Math.min(step, snaps.length - 1)] : null;
  const isReplay = snaps.length > 0 && step < snaps.length - 1;
  const live = gid && state && state.winner == null && !isReplay;
  // 人类只能在自己（人类席位）回合行动；AI 席位只能按“AI 行棋 / AI 走到底”
  const humanTurn = !!live && seatTypes[state.turn] === 'human';
  const turnIsAI = !!live && seatTypes[state.turn] === 'ai';

  // 非回放时棋谱列表自动滚到底部跟随最新
  useEffect(() => {
    if (!isReplay && histRef.current) histRef.current.scrollTop = histRef.current.scrollHeight;
  }, [snaps.length, isReplay]);

  // 墙所属方（按 history 中放墙顺序对应 walls 数组）
  const wallOwners = (() => {
    if (!shown) return [];
    const owners = [];
    for (const h of shown.history || []) if (h.type === 'wall') owners.push(h.player);
    return owners;
  })();

  async function refreshLegal(g, st) {
    try { setLegal(await apiLegal(g, st.turn)); } catch { setLegal({ moves: [], phased: false }); }
  }

  function appendSnap(st) {
    // 函数式更新 step，避免连走循环中闭包拿到过期 snaps 导致进度条不跟随
    setSnaps((prev) => { setStep(prev.length); return [...prev, st]; });
    setState(st);
    setGhost(null);
    refreshLegal(gidRef.current, st);
  }

  // 批量棋谱查看：只读回放（gid 置空 → live 为假，全部行动按钮自动禁用）
  const [batchOpen, setBatchOpen] = useState(false); // 批量面板
  const [resumeJob, setResumeJob] = useState(null); // 预览返回时恢复的 job
  const [batchView, setBatchView] = useState(null); // {jobId, index, results}

  function loadBatchStates(jobId, index, states, meta, results) {
    if (!states?.length) return;
    setAutoAI(false); setBusy(false);
    setGid(null);
    setSeatNames([meta.first ?? '先手', meta.second ?? '后手']);
    setSeatTypes(['ai', 'ai']);
    setSeatAIs([meta.first ?? 'builtin-random', meta.second ?? 'builtin-random']);
    setRedSeat(0);
    setSnaps(states);
    setStep(0);
    setState(states[0]);
    setMode('move');
    setSandMode(false);
    setGhost(null);
    setLegal({ moves: [], phased: false });
    setWizardOpen(false);
    setBatchOpen(false);
    setBatchView({ jobId, index, results });
  }

  async function gotoBatchGame(jobId, ni, results) {
    if (ni < 0 || ni >= results.length || busy) return;
    setBusy(true);
    try {
      const g = await apiAiBatchGame(jobId, ni);
      loadBatchStates(jobId, ni, g.states, g.meta, results);
    } catch (e) { alert(`载入棋谱失败：${e.message}`); } finally { setBusy(false); }
  }

  function stepBatchGame(d) {
    const bv = batchView;
    if (!bv) return;
    gotoBatchGame(bv.jobId, bv.index + d, bv.results);
  }

  function backToBatch() {
    // 返回批量结果：面板按 jobId 恢复轮询（服务端为准），预览状态清空
    if (!batchView) return;
    setResumeJob(batchView.jobId);
    setBatchView(null);
    setBatchOpen(true);
  }

  async function createGame(cfg) {    setBusy(true);
    try {
      const j = await apiNew({ n: cfg.n, m: cfg.m, seed: cfg.seed, goal_A: cfg.goal_A, goal_B: cfg.goal_B });
      const names = [cfg.participants[cfg.seatOf[0]].name, cfg.participants[cfg.seatOf[1]].name];
      const types = [cfg.participants[cfg.seatOf[0]].type, cfg.participants[cfg.seatOf[1]].type];
      // 双方选技能卡：人类用向导所选，AI 跑各自的选牌阶段容器
      const glim = { timeout: cfg.aiTimeout ?? 5, memoryMb: cfg.aiMemoryMb ?? 256 };
      let cur = j.state;
      for (let seat = 0; seat < 2; seat++) {
        const p = cfg.seatOf[seat];
        if (cfg.skillPicks[p]) {
          cur = (await apiSkillSelect(j.id, seat, cfg.skillPicks[p])).state;
        } else {
          cur = (await apiSkillAiSelect(j.id, seat, cfg.seatAIs[seat], glim)).state;
        }
      }
      setGid(j.id);
      setSeatNames(names);
      setSeatTypes(types);
      setRedSeat(cfg.seatOf.indexOf(cfg.participants.findIndex((p) => p.name === '红')));
      setSeatAIs(cfg.seatAIs ?? ['builtin-random', 'builtin-random']);
      setAiLim(glim);
      setSnaps([cur]);
      setStep(0);
      setState(cur);
      setMode('move');
      setSandMode(false);
      setWizardOpen(false);
      refreshLegal(j.id, cur);
    } catch (e) { alert(`开局失败：${e.message}`); } finally { setBusy(false); }
  }

  async function doMove(r, c) {
    if (!humanTurn) return;
    setBusy(true);
    try { appendSnap((await apiMovePawn(gid, [r, c])).state); }
    catch (e) { alert(e.message); } finally { setBusy(false); }
  }

  async function doPlaceWall(wall) {
    if (!humanTurn) return;
    const chk = wallLocalLegal(state, state.turn, wall);
    if (!chk.ok) { alert(`此处不可放墙：${chk.reason}`); return; }
    setBusy(true);
    try {
      appendSnap((await apiPlaceWall(gid, {
        kind: wall.kind, wr: wall.wr, wc: wall.wc,
        orientation: wall.kind === 'L' ? null : wall.orientation,
        arm: wall.kind === 'L' ? wall.arm : null,
      })).state);
    } catch (e) { alert(e.message); } finally { setBusy(false); }
  }

  async function doPlaySkill(skill, to) {
    if (!humanTurn || busy) return;
    if (skill === 'make_sand' && !to) { setSandMode(true); setMode('move'); return; }
    if (to) {
      const chk = sandLocalLegal(state, to[0], to[1]);
      if (!chk.ok) { alert(`此处不可布沙：${chk.reason}`); return; }
    }
    setBusy(true);
    try {
      appendSnap((await apiSkillPlay(gid, skill, to ?? null)).state);
      setSandMode(false);
    } catch (e) { alert(e.message); } finally { setBusy(false); }
  }

  async function aiOnce(g) {
    const st = stateRef.current;
    // AI 席位跑各自容器；人类回合点“AI 行棋”则由随机示例代走
    const aid = (st && aisRef.current[st.turn]) || 'builtin-random';
    const j = await apiExternalMove(g ?? gidRef.current, aid, limRef.current);
    appendSnap(j.state);
    return j.state;
  }

  async function aiMove() {
    if (!live || busy) return;
    setBusy(true);
    try { await aiOnce(); } catch (e) { alert(e.message); } finally { setBusy(false); }
  }

  async function aiToEnd() {
    // 开关式连走：开启后每当轮到 AI 便自动走一步；人类回合人类自己走，走完 AI 自动续
    if (autoAI) { setAutoAI(false); return; }
    if (!live) return;
    setAutoAI(true);
  }

  // 连走驱动：开启且活局且轮到 AI 时延迟走一步（人机/双 AI 通用，终局自动关闭）
  useEffect(() => {
    if (autoAI && state && state.winner != null) { setAutoAI(false); return; }
    if (!autoAI || !live || !turnIsAI) return;
    const t = setTimeout(() => {
      aiOnce().catch((e) => { setAutoAI(false); alert(e.message); });
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoAI, live, turnIsAI, step]);

  function exportKifu() {
    if (!snaps.length) return;
    const doc = buildReplay(snaps, { seatNames, seatTypes, seatAIs, redSeat });
    if (!doc) return;
    const blob = new Blob([JSON.stringify(doc)], { type: 'application/json' });
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
      const replay = parseReplay(j);
      if (replay) {
        setSnaps(replay.snaps);
        setStep(0);
        setState(replay.snaps[replay.snaps.length - 1]);
        if (replay.meta) {
          setSeatNames(replay.meta.seatNames); setSeatTypes(replay.meta.seatTypes);
          setSeatAIs(replay.meta.seatAIs ?? ['builtin-random', 'builtin-random']);
          if (replay.meta.redSeat === 0 || replay.meta.redSeat === 1) setRedSeat(replay.meta.redSeat);
        }
        setGid(null);
        setWizardOpen(false);
      } else if (j.snaps) {
        setSnaps(j.snaps);
        setStep(0);
        setState(j.snaps[j.snaps.length - 1]);
        if (j.meta) { setSeatNames(j.meta.seatNames); setSeatTypes(j.meta.seatTypes); setSeatAIs(j.meta.seatAIs ?? ['builtin-random', 'builtin-random']); if (j.meta.redSeat === 0 || j.meta.redSeat === 1) setRedSeat(j.meta.redSeat); }
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

  // 手牌可见性：对战进行中人类只能看自己方；回放/终局复盘/导入回放/双 AI 观战时全可见
  const spectate = seatTypes[0] !== 'human' && seatTypes[1] !== 'human';
  const revealAll = spectate || isReplay || !live;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><span className="logo">♞</span> Quoridor 改版</div>
        <div className="topactions">
          <button className="btn ghost" onClick={() => setWizardOpen(true)}>新对局</button>
          <button className="btn ghost" onClick={() => { setResumeJob(null); setBatchOpen(true); }}>批量对战</button>
          <button className="btn ghost" onClick={exportKifu} disabled={!state}>导出棋谱</button>
          <label className="btn ghost filebtn">导入/回放<input type="file" accept=".json" onChange={importKifu} hidden /></label>
        </div>
      </header>

      {wizardOpen && !batchOpen && (
        <SetupWizard onCreate={createGame} onCancel={() => setWizardOpen(false)} hasGame={!!state} />
      )}

      {batchOpen && (
        <BatchPanel key={resumeJob ?? 'new'} initialJob={resumeJob}
          onView={loadBatchStates} onClose={() => setBatchOpen(false)} />
      )}

      {shown && !wizardOpen && !batchOpen && (
        <div className="main">
          <section className="card boardcard">
            <div className="statusbar">
              <span className={`turnbadge p${idc(shown.turn)}`}>
                轮到 {seatNames[shown.turn]}（{shown.turn === 0 ? '先手' : '后手'}）
              </span>
              {shown.bonus_moves > 0 && <span className="pill warn">流沙：同一人继续行动</span>}
              {isReplay && <span className="pill">回放中 {step + 1}/{snaps.length}</span>}
              {batchView && (
                <>
                  <button className="btn ghost mini" onClick={backToBatch}>← 返回批量</button>
                  <button className="btn ghost mini" disabled={batchView.index <= 0 || busy}
                    onClick={() => stepBatchGame(-1)}>上一局</button>
                  <select value={batchView.index} disabled={busy}
                    onChange={(e) => gotoBatchGame(batchView.jobId, Number(e.target.value), batchView.results)}>
                    {batchView.results.map((r) => (
                      <option key={r.index} value={r.index}>
                        #{r.index + 1} {r.winner_aid ?? (r.winner_seat === -1 ? '平局' : '异常')}
                      </option>
                    ))}
                  </select>
                  <button className="btn ghost mini" disabled={batchView.index >= batchView.results.length - 1 || busy}
                    onClick={() => stepBatchGame(1)}>下一局</button>
                </>
              )}
              {shown.winner != null && (
                <span className="pill win">{shown.winner === -1 ? `平局（${shown.win_reason}）` : `胜者：${seatNames[shown.winner]}（${shown.win_reason}）`}</span>
              )}
            </div>
            <div className="wallsline">
              <span className={`wcount p${idc(0)}`}>先手·{seatNames[0]} 墙 {shown.walls_left[0]}（L券 {shown.l_bonus?.[0] ?? 0}）</span>
              <span className={`wcount p${idc(1)}`}>后手·{seatNames[1]} 墙 {shown.walls_left[1]}（L券 {shown.l_bonus?.[1] ?? 0}）</span>
              {shown.must_move && <span className="pill warn">连续行动：只能走子</span>}
              {(shown.l_bonus?.[shown.turn] ?? 0) > 0 && (
                <span className="pill warn" title="改造现打现放：本次行动必须放置 L 墙，否则作废">
                  改造待放置：须放 L 墙，否则作废
                </span>
              )}
              {shown.phase_buff?.[shown.turn] && <span className="pill">穿墙就绪</span>}
              {shown.free_buff?.[shown.turn] && <span className="pill">免费墙就绪</span>}
              <span className="muted small">A=[{shown.goal_A.join(',')}] 先手底线　B=[{shown.goal_B.join(',')}] 后手顶线</span>
            </div>
            <Board st={shown} legal={humanTurn && mode === 'move' && !sandMode ? legal.moves : []}
              phased={humanTurn && legal.phased} redSeat={redSeat}
              mode={mode} wallSel={wallSel} ghost={ghost}
              wallOwners={wallOwners.map((s) => (s === 0 || s === 1 ? (s === redSeat ? 0 : 1) : s))}
              interactive={!!humanTurn}
              onCellClick={(r, c) => doMove(r, c)}
              onSlotHover={(w) => setGhost({ wall: w, ...wallLocalLegal(state, state.turn, w) })}
              onSlotLeave={() => setGhost(null)}
              onSlotClick={(w) => doPlaceWall(w)}
              sandMode={sandMode}
              onSandClick={(r, c) => doPlaySkill('make_sand', [r, c])} />
            {sandMode && (
              <div className="rowbtns">
                <span className="hl">流沙陷阱：在棋盘上点一个格（禁死点/已有流沙/棋子/获胜点）</span>
                <button className="btn ghost" onClick={() => setSandMode(false)}>取消</button>
              </div>
            )}
            <div className="legend">
              <span><i className="sw death" />死点不可进</span>
              <span><i className="sw sand" />流沙：对方连走两次</span>
              <span><i className="sw gr" />红方目标</span>
              <span><i className="sw bl" />蓝方目标</span>
            </div>
          </section>

          <aside className="side">
            <div className="card">
              <h3>行动</h3>
              <div className="seg">
                {['move', 'wall'].map((v) => (
                  <button key={v} className={`segbtn${mode === v ? ' active' : ''}`}
                    disabled={v === 'wall' && !!shown.must_move}
                    title={v === 'wall' && shown.must_move ? '连续行动中只能走子' : ''}
                    onClick={() => { setMode(v); setGhost(null); }}>
                    {v === 'move' ? '走子' : '放墙'}
                  </button>
                ))}
              </div>
              {mode === 'wall' && (
                <div>
                  <div className="seg">
                    <button className={`segbtn${wallSel.kind === 'straight' && wallSel.orientation === 'H' ? ' active' : ''}`}
                      onClick={() => setWallSel({ kind: 'straight', orientation: 'H', arm: 'NW' })}>横墙</button>
                    <button className={`segbtn${wallSel.kind === 'straight' && wallSel.orientation === 'V' ? ' active' : ''}`}
                      onClick={() => setWallSel({ kind: 'straight', orientation: 'V', arm: 'NW' })}>竖墙</button>
                    <button className={`segbtn${wallSel.kind === 'L' ? ' active' : ''}`}
                      disabled={(state?.l_bonus?.[state.turn] ?? 0) <= 0}
                      title="需先打出改造技能获得 L 券"
                      onClick={() => setWallSel({ kind: 'L', orientation: null, arm: 'NW' })}>
                      L 墙（券×{state?.l_bonus?.[state.turn] ?? 0}）
                    </button>
                  </div>
                  {wallSel.kind === 'L' && (
                    <div className="seg">
                      {['NW', 'NE', 'SW', 'SE'].map((a) => (
                        <button key={a} className={`segbtn${wallSel.arm === a ? ' active' : ''}`}
                          onClick={() => setWallSel({ ...wallSel, arm: a })}>{a}</button>
                      ))}
                    </div>
                  )}
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

            <div className="card">
              <h3>技能卡{shown.seq_skill_used ? '（本序列已打出过）' : ''}</h3>
              <p className="muted small">行动前可打出一张，不占轮次；连续行动序列中最多一张。对战中 AI 手牌对人类隐藏。</p>
              {[0, 1].map((seat) => {
                const hand = shown.hands?.[seat] ?? {};
                const ids = Object.keys(hand);
                const mine = humanTurn && shown.turn === seat;
                const hidden = !revealAll && seatTypes[seat] !== 'human';
                const total = ids.reduce((s, id) => s + hand[id], 0);
                return (
                  <div className="hand" key={seat}>
                    <div className={`seatname p${idc(seat)}`}>{seatNames[seat]}（{seat === 0 ? '先手' : '后手'}）</div>
                    {hidden ? (
                      <span className="muted small">AI 手牌 ×{total}（对战中隐藏）</span>
                    ) : (
                      <>
                        {ids.length === 0 && <span className="muted small">无手牌</span>}
                        {ids.map((id) => (
                          <span className="skillchip" key={id} title={skillDefs[id]?.desc ?? id}>
                            {skillDefs[id]?.name ?? id}×{hand[id]}
                            <button className="btn ghost mini"
                              disabled={!mine || busy || shown.seq_skill_used}
                              title={mine ? (shown.seq_skill_used ? '本序列已打出过' : '行动前打出') : '轮到该方时打出'}
                              onClick={() => doPlaySkill(id)}>
                              打出
                            </button>
                          </span>
                        ))}
                      </>
                    )}
                  </div>
                );
              })}
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
              <div className="history" ref={histRef}>
                {(shown.history || []).map((h, i) => (
                  <div key={i} className={`${i === (shown.history.length - 1) ? 'hl' : ''} side${idc(h.player)}`}>
                    {i + 1}. {fmtHist(h, seatNames, skillDefs)}
                  </div>
                ))}
              </div>
            </div>
          </aside>
        </div>
      )}

      {!shown && !wizardOpen && !batchOpen && <p className="muted">点击「新对局」开始。</p>}
    </div>
  );
}
