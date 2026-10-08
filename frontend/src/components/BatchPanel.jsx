// 批量对战独立面板：配置 → 进度（含进行中各局步数）→ 统计 → 逐局查看/打包下载。
import { useEffect, useState } from 'react';
import { API, apiAiBatchGame, apiAiBatchStart, apiAiBatchStatus, apiAiList } from '../api.js';

// 批量统计：双方同 AI 只看先/后手，否则看双方胜率（附先手胜率与平均步数）
function BatchStats({ results, white, black, aiName }) {
  const tot = results.length;
  const pct = (x) => (tot ? `${(100 * x / tot).toFixed(1)}%` : '—');
  const draws = results.filter((r) => r.winner_seat === -1).length;
  const errs = results.filter((r) => r.winner_seat == null).length;
  const firstWins = results.filter((r) => r.winner_seat === 0).length;
  const played = results.filter((r) => r.plies > 0);
  const avg = played.length ? (played.reduce((a, r) => a + r.plies, 0) / played.length).toFixed(1) : '—';
  const wWins = results.filter((r) => r.winner_aid === white).length;
  const bWins = results.filter((r) => r.winner_aid === black).length;
  const body = (white && white === black)
    ? `先手胜 ${firstWins}（${pct(firstWins)}）　后手胜 ${tot - firstWins - draws - errs}（${pct(tot - firstWins - draws - errs)}）`
    : `${aiName(white)}胜 ${wWins}（${pct(wWins)}）　${aiName(black)}胜 ${bWins}（${pct(bWins)}）　先手胜率 ${pct(firstWins)}`;
  return <p>{body}　平局 {draws}　异常 {errs}　平均步数 {avg}</p>;
}

export default function BatchPanel({ initialJob, onView, onClose }) {
  const [aiOptions, setAiOptions] = useState([]);
  const [white, setWhite] = useState('builtin-random');
  const [black, setBlack] = useState('builtin-random');
  const [n, setN] = useState('');
  const [m, setM] = useState('');
  const [seed, setSeed] = useState('');
  const [aiTimeout, setAiTimeout] = useState('5');
  const [aiMemory, setAiMemory] = useState('256');
  const [batchN, setBatchN] = useState('10');
  const [batchPar, setBatchPar] = useState('4');
  const [batchJob, setBatchJob] = useState(initialJob ?? null);
  const [batchStat, setBatchStat] = useState(null);
  const [batchErr, setBatchErr] = useState(null);
  const aiName = (aid) => (aiOptions.find((a) => a.aid === aid)?.name || aid);

  useEffect(() => {
    apiAiList().then(setAiOptions).catch(() => {});
  }, []);

  useEffect(() => {
    if (!batchJob) return;
    let stop = false;
    const tick = async () => {
      try {
        const s = await apiAiBatchStatus(batchJob);
        if (stop) return;
        setBatchStat(s);
        if (s.finished) clearInterval(timer);
      } catch (e) { if (!stop) { setBatchErr(e.message); clearInterval(timer); } }
    };
    const timer = setInterval(tick, 1000);
    tick();
    return () => { stop = true; clearInterval(timer); };
  }, [batchJob]);

  async function startBatch() {
    const games = Math.min(1000, Math.max(1, Number(batchN) || 10));
    const par = Math.min(16, Math.max(1, Number(batchPar) || 4));
    setBatchN(String(games)); setBatchPar(String(par));
    setBatchErr(null); setBatchStat(null);
    try {
      const id = await apiAiBatchStart({
        white, black,
        n: n === '' ? null : Math.min(15, Math.max(9, Number(n) || 9)),
        m: m === '' ? null : Math.min(15, Math.max(9, Number(m) || 9)),
        seed: seed === '' ? null : Number(seed),
        games, max_parallel: par,
        timeout: Math.min(30, Math.max(1, Number(aiTimeout) || 5)),
        memory_mb: Math.min(2048, Math.max(64, Number(aiMemory) || 256)),
      });
      setBatchJob(id);
    } catch (e) { setBatchErr(e.message); }
  }

  async function viewOne(i) {
    setBatchErr(null);
    try {
      const g = await apiAiBatchGame(batchJob, i);
      onView?.(batchJob, i, g.states, g.meta, batchStat?.results ?? []);
    } catch (e) { setBatchErr(e.message); }
  }

  const running = batchJob && !batchStat?.finished;
  return (
    <div className="card">
      <h3>批量对战</h3>
      <p className="muted small">每局 seed=基准+i，独立出题/选边/选牌；尺寸/种子空=每局随机。AI 请先在开局向导上传。</p>
      <div className="grid2">
        <label>白方 AI（出题方之一）<select value={white} onChange={(e) => setWhite(e.target.value)}>
          {aiOptions.map((a) => <option key={a.aid} value={a.aid}>{a.name}{a.builtin ? '' : '（已上传）'}</option>)}
        </select></label>
        <label>黑方 AI<select value={black} onChange={(e) => setBlack(e.target.value)}>
          {aiOptions.map((a) => <option key={a.aid} value={a.aid}>{a.name}{a.builtin ? '' : '（已上传）'}</option>)}
        </select></label>
        <label>行 n<input value={n} onChange={(e) => setN(e.target.value)} placeholder="空 = 每局随机" /></label>
        <label>列 m<input value={m} onChange={(e) => setM(e.target.value)} placeholder="空 = 每局随机" /></label>
        <label>基准种子<input value={seed} onChange={(e) => setSeed(e.target.value)} placeholder="空 = 随机" /></label>
        <label>AI 单步时限（秒）<input value={aiTimeout} onChange={(e) => setAiTimeout(e.target.value)} placeholder="1~30，默认 5" /></label>
        <label>AI 容器内存（MiB）<input value={aiMemory} onChange={(e) => setAiMemory(e.target.value)} placeholder="64~2048，默认 256" /></label>
      </div>
      <div className="grid2">
        <label>对局数<input value={batchN} onChange={(e) => setBatchN(e.target.value)} placeholder="1~1000，默认 10" /></label>
        <label>最大并行<input value={batchPar} onChange={(e) => setBatchPar(e.target.value)} placeholder="1~16，默认 4" /></label>
      </div>
      <div className="rowbtns">
        <button className="btn primary" disabled={running} onClick={startBatch}>开始批量对战</button>
        {batchStat?.finished && (
          <button className="btn ghost" onClick={() => { setBatchJob(null); setBatchStat(null); setBatchErr(null); }}>
            再来一批
          </button>
        )}
        <a className="btn ghost" style={{ display: batchStat?.finished ? '' : 'none' }}
          href={`${API}/api/ai/batch/${batchJob}/download`}>下载全部棋谱（zip）</a>
        <button className="btn ghost" onClick={onClose}>关闭</button>
      </div>
      {batchErr && <p className="muted small">批量失败：{batchErr}</p>}
      {batchStat && (
        <div>
          <p>进度 <b>{batchStat.done}/{batchStat.total}</b>{batchStat.finished ? '（完赛）' : ''}　基准种子 {batchStat.base_seed}</p>
          <div className="pbar"><i style={{ width: `${(100 * batchStat.done / Math.max(1, batchStat.total)).toFixed(1)}%` }} /></div>
          {!batchStat.finished && Object.keys(batchStat.running).length > 0 && (
            <p className="muted small">进行中：{Object.entries(batchStat.running)
              .sort((a, b) => a[0] - b[0]).map(([i, p]) => `#${Number(i) + 1}:${p}步`).join('　')}</p>
          )}
          {batchStat.finished && batchStat.results.length > 0 && (
            <>
              <BatchStats results={batchStat.results} white={batchStat.white} black={batchStat.black} aiName={aiName} />
              <div className="history">
                {batchStat.results.map((r) => (
                  <div key={r.index}>
                    #{r.index + 1} seed={r.seed}　{r.winner_aid ? `胜者 ${aiName(r.winner_aid)}` : (r.winner_seat === -1 ? '平局' : '异常')}　{r.plies}步
                    <button className="btn ghost mini" style={{ marginLeft: 8 }} onClick={() => viewOne(r.index)}>查看</button>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
