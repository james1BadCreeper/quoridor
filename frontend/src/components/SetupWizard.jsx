// 开局向导：完整规则流程 —— 随机出题人 → 选 A/B 列集 → 另一方选边 → 选技能卡。
import { useEffect, useState } from 'react';
import { apiAiList, apiAiUpload, apiSkills, skillK } from '../api.js';

const FALLBACK_SKILLS = {  l_remodel: { name: '改造', desc: '获得 1 次 L 形墙放置权' },
  double_move: { name: '连续行动', desc: '本回合连续移动两次' },
  phase_walk: { name: '穿墙', desc: '下一次走子无视墙' },
  make_sand: { name: '流沙陷阱', desc: '将一个格变为流沙' },
  free_wall: { name: '免费墙', desc: '下一次放墙不消耗存量' },
};

// 参与者名字着色：含“红”用红色，其余保持默认高亮蓝
const nameCls = (n) => (n && n.includes('红') ? 'hl-red' : 'hl');

const randInt = (a, b) => a + Math.floor(Math.random() * (b - a + 1));

function shuffled(cols) {
  const a = [...cols];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

const randomGoals = (m) => {
  const k = Math.floor(m / 2);
  const pick = () => shuffled([...Array(m).keys()]).slice(0, k).sort((x, y) => x - y);
  return { a: pick(), b: pick() }; // A、B 相互独立，可相交、可留空列
};

function ColPicker({ m, goalA, goalB, k, phase, setPhase, onToggle, onRandom, onClear, chooserName }) {
  const cols = [...Array(m).keys()];
  return (
    <div>
      <p className="muted">
        出题人 <b className={nameCls(chooserName)}>{chooserName}</b> 正在出题：A、B 各选 {k} 列，相互独立
        （可以重叠，也可以留空列）。直接点列即可，选满一集后会自动切到另一集。
        A = 先手的获胜列，B = 后手的获胜列。
      </p>
      <div className="seg" style={{ maxWidth: 260 }}>
        {['A', 'B'].map((p) => (
          <button key={p} className={`segbtn${phase === p ? ' active' : ''}`} onClick={() => setPhase(p)}>
            选 {p} 集
          </button>
        ))}
      </div>
      <div className="colpicker">
        {cols.map((c) => {
          const inA = goalA.includes(c), inB = goalB.includes(c);
          const tag = inA && inB ? 'A+B' : inA ? 'A' : inB ? 'B' : '';
          return (
            <button key={c}
              className={`colbtn${inA ? ' inA' : ''}${inB ? ' inB' : ''}`}
              onClick={() => onToggle(c)}>
              <span className="coltag">{tag}</span>
              {c}
            </button>
          );
        })}
      </div>
      <div className="rowbtns">
        <span className="counter">A: {goalA.length}/{k}　B: {goalB.length}/{k}</span>
        <button className="btn ghost" onClick={onRandom}>随机填充</button>
        <button className="btn ghost" onClick={onClear}>清空</button>
      </div>
    </div>
  );
}

export default function SetupWizard({ onCreate, onCancel, hasGame }) {
  const [step, setStep] = useState(0);
  const [n, setN] = useState('');
  const [m, setM] = useState('');
  const [seed, setSeed] = useState('');
  const [names, setNames] = useState(['红方', '蓝方']);
  const [ptypes, setPtypes] = useState(['human', 'human']);
  const [aiIds, setAiIds] = useState(['random', 'random']); // AI 席位来源：random=引擎随机基线，或 aid
  const [aiOptions, setAiOptions] = useState([]); // 后端默认 + 已上传 AI 列表
  const [uploading, setUploading] = useState(false);
  // 定稿后的开局参数
  const [fm, setFm] = useState(9);
  const [fn, setFn] = useState(9);
  const [chooser, setChooser] = useState(0);
  const [goalA, setGoalA] = useState([]);
  const [goalB, setGoalB] = useState([]);
  const [phase, setPhase] = useState('A'); // 当前正在选 A 还是 B
  const [side, setSide] = useState(null); // picker 选 'first' | 'second'
  const [skillDefs, setSkillDefs] = useState(FALLBACK_SKILLS);
  const [skillPicks, setSkillPicks] = useState([[], []]); // 每位参与者的技能卡（AI 席位开局时随机）

  useEffect(() => {
    apiSkills().then(setSkillDefs).catch(() => {});
    apiAiList().then(setAiOptions).catch(() => {});
  }, []);

  async function uploadAi(i, file) {
    if (!file) return;
    setUploading(true);
    try {
      const j = await apiAiUpload(file);
      const list = await apiAiList();
      setAiOptions(list);
      setAiIds(aiIds.map((v, k) => (k === i ? j.aid : v)));
    } catch (e) { alert(`上传失败：${e.message}`); } finally { setUploading(false); }
  }

  const picker = 1 - chooser;
  const k = Math.floor(fm / 2);
  const kk = skillK(fn, fm); // 本局技能卡数 F(n,m)
  const goalsReady = goalA.length === k && goalB.length === k;
  const skillsReady =
    skillPicks[0].length === kk && skillPicks[1].length === kk &&
    [...skillPicks[0], ...skillPicks[1]].every((s) => s in skillDefs);

  function setPick(i, arr) {
    setSkillPicks(skillPicks.map((v, j) => (j === i ? arr : v)));
  }

  function randomPicks() {
    const ids = Object.keys(skillDefs);
    return Array.from({ length: kk }, () => ids[Math.floor(Math.random() * ids.length)]);
  }

  function toSetup2() {
    const nn = n === '' ? randInt(9, 15) : Math.min(15, Math.max(9, Number(n) || 9));
    const mm = m === '' ? randInt(9, 15) : Math.min(15, Math.max(9, Number(m) || 9));
    const ch = Math.random() < 0.5 ? 0 : 1;
    setFn(nn); setFm(mm); setChooser(ch);
    if (ptypes[ch] === 'ai') {
      const g = randomGoals(mm);
      setGoalA(g.a); setGoalB(g.b);
    } else { setGoalA([]); setGoalB([]); }
    if (ptypes[1 - ch] === 'ai') setSide(Math.random() < 0.5 ? 'first' : 'second');
    else setSide(null);
    setStep(1);
  }

  function toggleCol(c) {
    // A、B 相互独立，可重叠。当前相位已满则自动切到未满的另一相位，避免点击无响应。
    let ph = phase;
    const curLen = (ph === 'A' ? goalA : goalB).length;
    const otherLen = (ph === 'A' ? goalB : goalA).length;
    const inCur = (ph === 'A' ? goalA : goalB).includes(c);
    if (!inCur && curLen >= k && otherLen < k) {
      ph = ph === 'A' ? 'B' : 'A';
      setPhase(ph);
    }
    if (ph === 'A') {
      if (goalA.includes(c)) setGoalA(goalA.filter((x) => x !== c));
      else if (goalA.length < k) {
        const na = [...goalA, c].sort((x, y) => x - y);
        setGoalA(na);
        if (na.length >= k && goalB.length < k) setPhase('B'); // A 满后自动进 B 相位
      }
    } else {
      if (goalB.includes(c)) setGoalB(goalB.filter((x) => x !== c));
      else if (goalB.length < k) {
        const nb = [...goalB, c].sort((x, y) => x - y);
        setGoalB(nb);
        if (nb.length >= k && goalA.length < k) setPhase('A'); // B 满后自动回 A 相位补选
      }
    }
  }

  function start() {
    // 座位映射：先手 seat0、后手 seat1 各由哪位参与者担任
    const seatOf = side === 'first' ? [picker, chooser] : [chooser, picker];
    onCreate({
      n: fn, m: fm,
      seed: seed === '' ? null : Number(seed),
      goal_A: goalA, goal_B: goalB,
      participants: [{ name: names[0], type: ptypes[0] }, { name: names[1], type: ptypes[1] }],
      seatOf, chooser, side,
      // 人类席位用所选牌，AI 席位传 null（由后端随机）
      skillPicks: [ptypes[0] === 'human' ? skillPicks[0] : null,
                   ptypes[1] === 'human' ? skillPicks[1] : null],
      // 每席 AI 来源：random=引擎随机基线，否则为后端 aid（含内置与上传）
      seatAIs: [cfg_ai(0), cfg_ai(1)],
    });

    function cfg_ai(seat) {
      const p = seatOf[seat];
      if (ptypes[p] !== 'ai') return null;
      return aiIds[p];
    }
  }

  function adjustPick(i, id, delta) {
    const cur = skillPicks[i];
    const count = cur.filter((s) => s === id).length;
    if (delta > 0 && cur.length < kk) setPick(i, [...cur, id]);
    if (delta < 0 && count > 0) {
      const idx = cur.indexOf(id);
      setPick(i, cur.filter((_, j) => j !== idx));
    }
  }

  return (
    <div className="card wizard">
      <div className="steps">
        {['基本设置', '出题选集合', '选边', '选技能卡'].map((t, i) => (
          <span key={t} className={`step${i === step ? ' active' : ''}${i < step ? ' done' : ''}`}>
            {i + 1}. {t}
          </span>
        ))}
      </div>

      {step === 0 && (
        <div>
          <div className="grid2">
            <label>行 n<input value={n} onChange={(e) => setN(e.target.value)} placeholder="空 = 9~15 随机" /></label>
            <label>列 m<input value={m} onChange={(e) => setM(e.target.value)} placeholder="空 = 9~15 随机" /></label>
            <label>随机种子<input value={seed} onChange={(e) => setSeed(e.target.value)} placeholder="可选" /></label>
          </div>
          <div className="grid2">
            {[0, 1].map((i) => (
              <div className="pseat" key={i}>
                <label>参与者 {i === 0 ? '甲' : '乙'}<input value={names[i]}
                  onChange={(e) => setNames(names.map((v, j) => (j === i ? e.target.value : v)))} /></label>
                <select value={ptypes[i]}
                  onChange={(e) => setPtypes(ptypes.map((v, j) => (j === i ? e.target.value : v)))}>
                  <option value="human">人类</option>
                  <option value="ai">AI</option>
                </select>
                {ptypes[i] === 'ai' && (
                  <>
                    <select value={aiIds[i]}
                      onChange={(e) => setAiIds(aiIds.map((v, j) => (j === i ? e.target.value : v)))}>
                      <option value="random">随机基线（引擎内置）</option>
                      {aiOptions.map((a) => (
                        <option key={a.aid} value={a.aid}>
                          {a.name}{a.builtin ? '' : '（已上传）'}{a.built ? '' : '（未编译）'}
                        </option>
                      ))}
                    </select>
                    <label className="btn ghost filebtn" title="上传 C++ 源码 zip，后端在 docker 内编译">
                      {uploading ? '编译中…' : '上传 AI'}
                      <input type="file" accept=".zip" hidden disabled={uploading}
                        onChange={(e) => { uploadAi(i, e.target.files[0]); e.target.value = ''; }} />
                    </label>
                  </>
                )}
              </div>
            ))}
          </div>
          <p className="muted">下一步将随机决定出题人（出 A/B 集合），另一方随后选择“先手 + A”或“后手 + B”。</p>
          <div className="rowbtns">
            <button className="btn primary" onClick={toSetup2}>下一步：随机出题人</button>
            {hasGame && <button className="btn ghost" onClick={onCancel}>取消</button>}
          </div>
        </div>
      )}

      {step === 1 && (
        <div>
          <p>本局棋盘 <b>{fn}×{fm}</b>（k = m//2 = {k}），出题人是 <b className={nameCls(names[chooser])}>{names[chooser]}</b>
            （{ptypes[chooser] === 'human' ? '人类出题' : 'AI 出题'}），应战人是 <b>{names[picker]}</b>。</p>
          {ptypes[chooser] === 'human' ? (
            <ColPicker m={fm} goalA={goalA} goalB={goalB} k={k} phase={phase} setPhase={setPhase}
              onToggle={toggleCol}
              chooserName={names[chooser]}
              onRandom={() => { const g = randomGoals(fm); setGoalA(g.a); setGoalB(g.b); }}
              onClear={() => { setGoalA([]); setGoalB([]); }} />
          ) : (
            <div>
              <p className="muted">AI 已随机出题：A = [{goalA.join(', ')}]，B = [{goalB.join(', ')}]</p>
              <button className="btn ghost" onClick={() => { const g = randomGoals(fm); setGoalA(g.a); setGoalB(g.b); }}>
                重新随机
              </button>
            </div>
          )}
          <div className="rowbtns">
            <button className="btn ghost" onClick={() => setStep(0)}>上一步</button>
            <button className="btn primary" disabled={!goalsReady} onClick={() => setStep(2)}>
              下一步：选边
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div>
          <p>应战人 <b className={nameCls(names[picker])}>{names[picker]}</b> 请选择：</p>
          <div className="sidecards">
            <button className={`sidecard${side === 'first' ? ' sel' : ''}`} onClick={() => ptypes[picker] === 'human' && setSide('first')}>
              <b>先手 + 集合 A</b>
              <span>顶行出发，目标底行列 [{goalA.join(', ')}]</span>
            </button>
            <button className={`sidecard${side === 'second' ? ' sel' : ''}`} onClick={() => ptypes[picker] === 'human' && setSide('second')}>
              <b>后手 + 集合 B</b>
              <span>底行出发，目标顶行列 [{goalB.join(', ')}]</span>
            </button>
          </div>
          {ptypes[picker] === 'ai' && (
            <div className="rowbtns">
              <span className="muted">AI 已随机选边：{side === 'first' ? '先手 + A' : '后手 + B'}</span>
              <button className="btn ghost" onClick={() => setSide(side === 'first' ? 'second' : 'first')}>重新随机</button>
            </div>
          )}
          <div className="rowbtns">
            <button className="btn ghost" onClick={() => setStep(1)}>上一步</button>
            <button className="btn primary" disabled={!side} onClick={() => {
              // AI 席位先随机一版预览（可重随），人类席位保留已选
              setSkillPicks([0, 1].map((i) => (
                ptypes[i] === 'ai' ? randomPicks() : (skillPicks[i].length === kk ? skillPicks[i] : [])
              )));
              setStep(3);
            }}>
              下一步：选技能卡
            </button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div>
          <p>本局每人选 <b className="hl">{kk} 张</b>技能卡（F(n,m)=max(2, v//5)，可重复；AI 的牌赛前对人类不可见）。</p>
          {[0, 1].map((i) => (
            <div className="pseat" key={i} style={{ marginBottom: 10 }}>
              <b>{names[i]}（{ptypes[i] === 'human' ? '人类自选' : 'AI 随机'}）：{ptypes[i] === 'human' ? `${skillPicks[i].length}/${kk}` : `开局随机 ${kk} 张`}</b>
              {ptypes[i] === 'human' ? (
                <>
                  {Object.entries(skillDefs).map(([id, d]) => {
                    const count = skillPicks[i].filter((s) => s === id).length;
                    return (
                      <div className="skillrow" key={id}>
                        <span><b>{d.name}</b> <span className="muted small">{d.desc}</span></span>
                        <span>
                          <button className="btn ghost mini" onClick={() => adjustPick(i, id, -1)}>−</button>
                          <b> {count} </b>
                          <button className="btn ghost mini" onClick={() => adjustPick(i, id, 1)}>＋</button>
                        </span>
                      </div>
                    );
                  })}
                  <div className="rowbtns">
                    <button className="btn ghost" onClick={() => setPick(i, randomPicks())}>随机填充</button>
                    <button className="btn ghost" onClick={() => setPick(i, [])}>清空</button>
                  </div>
                </>
              ) : (
                <p className="muted small">AI 席位开局时由后端随机选牌，赛前不向人类展示牌面（同机双人对战除外，双方皆为人类时互可见）。</p>
              )}
            </div>
          ))}
          <div className="rowbtns">
            <button className="btn ghost" onClick={() => setStep(2)}>上一步</button>
            <button className="btn primary" disabled={!skillsReady} onClick={start}>开始对局</button>
          </div>
        </div>
      )}
    </div>
  );
}
