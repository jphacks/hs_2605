import { useEffect, useReducer, useRef, useState } from 'react';
import { createMockSource } from './data/mockSource';
import { createModeSource, selectDashboardMode } from './data/mode';
import type { DashboardMode } from './data/mode';
import { createWebSocketSource } from './data/webSocketSource';
import type { ConnectionState } from './data/webSocketSource';
import { createNotificationState, reduceNotifications } from './data/notifications';
import NotificationPanel from './NotificationPanel';
import ConnectionStatus from './ConnectionStatus';
import { resolveEndpoints, watchHealth } from './data/health';
import type { ServerHealth } from './data/health';
import { deriveLiveView } from './data/liveView';
import type { BreathingState, StatusSource } from './data/status';
import { useDashboard } from './useDashboard';
import WaveChart from './WaveChart';
import BoardPanel from './BoardPanel';
import DashboardIcon from './DashboardIcon';
import { describeStatus, stateWords } from './data/statusView';

const configuredInterval = Number(import.meta.env.VITE_LIVE_SAMPLE_INTERVAL_MS);
const liveIntervalMs = Number.isFinite(configuredInterval) && configuredInterval > 0 ? configuredInterval : null;
const TOKEN_KEY = 'ibuki-pairing-token';
function readToken(): string | null { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } }
function saveToken(token: string | null) {
  try { if (token) localStorage.setItem(TOKEN_KEY, token); else localStorage.removeItem(TOKEN_KEY); } catch { /* kept in memory only */ }
}
const endpoints = resolveEndpoints(window.location.href, import.meta.env.VITE_LIVE_WS_URL, import.meta.env.VITE_LIVE_HEALTH_URL);

export default function App() {
  const mode = selectDashboardMode(import.meta.env.DEV, window.location.search);
  return <Dashboard mode={mode} />;
}

function Dashboard({ mode }: { mode: DashboardMode }) {
  const demo = mode === 'DEMO';
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [liveReady, setLiveReady] = useState(false);
  const [notifications, dispatchNotification] = useReducer(reduceNotifications, undefined, createNotificationState);
  const [board, setBoard] = useState<boolean | null>(null);
  const [health, setHealth] = useState<ServerHealth | null>(null);
  const [resetKey, setResetKey] = useState(0);
  const [receivedAt, setReceivedAt] = useState<number | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuToggleRef = useRef<HTMLButtonElement>(null);
  const diagnosticsRef = useRef<HTMLDetailsElement>(null);
  useEffect(() => { if (!demo) return watchHealth(endpoints.health, setHealth); }, [demo]);
  // The server's siren keeps sounding until someone acknowledges it; this is the dashboard's way.
  const [stoppingSiren, setStoppingSiren] = useState(false);
  useEffect(() => { if (!health?.siren) setStoppingSiren(false); }, [health?.siren]);
  // With pairing on (IBUKI_PAIRING=1), only a device that entered the server window's code may stop it.
  const [token, setToken] = useState<string | null>(readToken);
  const [pairCode, setPairCode] = useState('');
  const [pairError, setPairError] = useState<string | null>(null);
  const needsPairing = !!health?.pairing && !token;
  const stopSiren = () => {
    setStoppingSiren(true);
    fetch(new URL('/api/button', endpoints.health).href, { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(response => { if (response.status === 403) { saveToken(null); setToken(null); setStoppingSiren(false); } })
      .catch(() => setStoppingSiren(false));
  };
  const pair = () => {
    setPairError(null);
    fetch(new URL('/api/pair', endpoints.health).href, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: pairCode.trim() }) })
      .then(response => response.json().then(body => ({ ok: response.ok, body })))
      .then(({ ok, body }) => {
        if (ok && typeof body.token === 'string') { saveToken(body.token); setToken(body.token); setPairCode(''); }
        else setPairError('番号が違います。サーバーの画面の 6 桁の番号を入れてください');
      })
      .catch(() => setPairError('サーバーに届きませんでした'));
  };
  const [mock] = useState(() => demo ? createMockSource() : null);
  const [source] = useState<StatusSource>(() => createModeSource<StatusSource>(mode, {
    demo: () => mock!,
    live: () => {
      let lastTime: number | null = null;
      let inputConnected: boolean | null = null;
      const live = createWebSocketSource({
        url: endpoints.ws,
        onConnection(next) {
          dispatchNotification({ type: 'connection', value: next });
          setConnection(next);
          if (next !== 'connected') { setLiveReady(false); lastTime = null; setResetKey(key => key + 1); }
          if (next === 'connecting') { setBoard(null); setHealth(null); inputConnected = null; }
        },
        onAlert: event => dispatchNotification({ type: 'event', event, receivedAt: Date.now() }),
        onInfo: event => dispatchNotification({ type: 'event', event, receivedAt: Date.now() }),
        onBoard(connected) {
          dispatchNotification({ type: 'board', connected });
          if (connected !== inputConnected) { setLiveReady(false); lastTime = null; setResetKey(key => key + 1); }
          inputConnected = connected;
          setBoard(connected);
        },
      });
      return { subscribe: listener => live.subscribe(status => {
        dispatchNotification({ type: 'status', status });
        // /ws initially sends a cached status. Wait for a progressing timestamp.
        setLiveReady(inputConnected !== false && lastTime !== null && status.t > lastTime);
        lastTime = status.t;
        setReceivedAt(Date.now()); // Browser receipt time only; never reinterpret the board clock.
        listener(status);
      }) };
    },
  }));
  const [selected, setSelected] = useState<BreathingState>('OK');
  const data = useDashboard(source, demo ? 50 : liveIntervalMs, resetKey);
  const liveView = deriveLiveView(data.status, connection, board, liveReady, health);
  const status = demo ? data.status : liveView.status;
  // Keep showing the drawn wave while the data is current, even when this update has none: the
  // graph pauses (grey dashed "no reading") instead of emptying, which looked like a restart.
  const points = demo || liveView.status ? data.points : [];
  const current = status?.state;
  const statusView = describeStatus({ state: current ?? null, shaking: status?.shaking ?? false,
    placeholder: !demo && liveView.placeholder, waiting: demo ? 'デモを準備しています' : liveView.notice });
  const sourceTag = demo ? '模擬データ' : liveView.placeholder ? '暫定の処理' : liveView.replay ? '録画を再生中' : 'ライブ';
  const bpm = demo ? status?.bpm ?? null : liveView.bpm;
  const rateNote = demo ? 'シミュレーションの値です'
    : !status || current === 'EMPTY' ? '赤ちゃんがベッドにいるときに表示します'
    : liveView.placeholder ? '暫定の処理では呼吸数を計算しません'
    : bpm !== null ? '直近20秒の呼吸から計算しています'
    : current === 'CHECK' || current === 'ALERT' ? '呼吸が検知できていません'
    : current === 'OK' ? '動いている間は計算しません'
    : '呼吸が安定すると表示されます（20秒ほど）';
  // Why the graph has no new wave, when the status above doesn't already say it (nobody there, shaking).
  const chartNote = demo || !liveView.status || liveView.waveAvailable || current === 'EMPTY' || status?.shaking ? null
    : '呼吸の波を準備しています（寝かせた直後や揺れのあとは数秒かかります）';

  const openDiagnostics = () => {
    setMenuOpen(false);
    if (diagnosticsRef.current) {
      diagnosticsRef.current.open = true;
      diagnosticsRef.current.querySelector('summary')?.focus();
    }
  };
  const receiptTime = !demo && status && receivedAt !== null ? receivedAt : null;

  return <main id="dashboard">
    <header className="app-header">
      <h1 className="brand"><span className="brand-symbol"><DashboardIcon name="brand" /></span><span>息吹～IBUKI～<small>赤ちゃんの呼吸の動きを、見えるかたちに。</small></span></h1>
      <nav id="dashboard-navigation" className={`dashboard-nav${menuOpen ? ' is-open' : ''}`} aria-label="ダッシュボード内の移動" onKeyDown={event => { if (event.key === 'Escape') { setMenuOpen(false); menuToggleRef.current?.focus(); } }}>
        <a href="#dashboard" aria-current="page" onClick={() => setMenuOpen(false)}>ダッシュボード</a>
        {!demo && <a href="#notifications" onClick={() => setMenuOpen(false)}>通知履歴</a>}
        {!demo && <button onClick={openDiagnostics}>接続詳細</button>}
      </nav>
      <div className="header-controls"><span className="demo-badge"><span className="dot"/>{demo ? '模擬データ' : 'LIVE'}</span>{!demo && health?.pairing && token && <span className="demo-badge pair-badge">ペアリング済み</span>}<button ref={menuToggleRef} className="menu-toggle" aria-label="メニュー" aria-expanded={menuOpen} aria-controls="dashboard-navigation" onClick={() => setMenuOpen(open => !open)}><DashboardIcon name="menu" /></button></div>
    </header>
    <section className={`status-overview tone-${statusView.tone}`}>
      <section className="status-banner">
        <span className="status-icon"><DashboardIcon name={({ alert: 'alert', check: 'alert', shake: 'wave', empty: 'empty', wait: 'wave', ok: 'movement', still: 'moon' } as const)[statusView.tone]} /></span>
        <div className="status-content">
          <div className="status-copy" {...(current === 'ALERT' ? { role: 'alert' } : { 'aria-live': 'polite' as const })} aria-atomic="true"><span className="status-eyebrow">現在の状態</span><h2>{statusView.title}</h2><p>{statusView.detail}{demo ? '（シミュレーション）' : ''}</p></div>
          {!demo && health?.siren && !needsPairing && <button className="siren-stop" onClick={stopSiren} disabled={stoppingSiren}>
            {stoppingSiren ? '停止を要求中…' : 'アラームを止める'}<small>赤ちゃんの様子を確認したら押してください（ボードの SW1 と同じです）</small></button>}
          {!demo && health?.siren && needsPairing && <form className="pair-form" onSubmit={event => { event.preventDefault(); pair(); }}>
            <label>この端末からアラームを止めるには、サーバーの画面の 6 桁の番号を入れてください<input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={pairCode} onChange={event => setPairCode(event.target.value)} /></label>
            <button type="submit" disabled={pairCode.trim().length !== 6}>ペアリング</button>
            {pairError && <p role="alert">{pairError}</p>}
            <small>ボードの SW1 なら、いつでも止められます。</small></form>}
        </div>
      </section>
      {!demo && <ConnectionStatus connection={connection} board={board} ready={liveReady} health={health}
        placeholder={liveView.placeholder} shaking={data.status?.shaking ?? false} waveAvailable={liveView.waveAvailable}
        url={endpoints.ws} notice={liveView.notice} detailsRef={diagnosticsRef} receivedAt={receiptTime} />}
    </section>
    <section className="dashboard-grid">
      <article className="panel waveform">
        <div className="panel-heading"><div className="waveform-heading"><h2>呼吸の動き</h2><details className="chart-help"><summary aria-label="波形の説明"><DashboardIcon name="info" /></summary><div><p>受信した胸の動き（mm）を直近30秒分表示します。破線は測定なしの区間です。</p><p>横軸の0秒は30秒前、30秒は最新です。表示範囲は±2mmから、大きな値に合わせて2倍ずつ広がります。測定値は変更しません。</p>{chartNote && <p>{chartNote}</p>}<p>波形だけで呼吸や安全を確認するものではありません。</p></div></details></div><span className="chart-range">直近30秒</span></div>
        <WaveChart points={points} t={status?.t ?? null} alert={current === 'ALERT'} demo={demo} label={`胸の動きの波形。${statusView.title}`}/>
        <div className="chart-footer"><span><i/>胸の動き<span className="chart-source"> · {sourceTag}</span></span><span><i className="idle-key"/>測定なし</span></div>
      </article>
      <aside className="metrics" aria-label="いまの様子">
        <article className="panel metric-card bpm"><span className="metric-icon"><DashboardIcon name="breathing" /></span><div className="metric-copy"><h2>呼吸数</h2><div className={`bpm-value${bpm == null ? ' bpm-value--none' : ''}`}>{bpm?.toFixed(1) ?? '—'}<span>回/分</span></div><p className="muted bpm-note">{rateNote}</p></div><span className="metric-tag">{bpm === null ? '—' : demo ? '模擬値' : '推定値'}</span></article>
        <article className="panel metric-card"><span className="metric-icon"><DashboardIcon name="bed" /></span><div className="metric-copy"><h2>ベッド</h2><strong className="presence">{!status ? '—' : status.present ? 'います' : 'いません'}</strong><p className="muted metric-note">{!status ? 'データを待っています' : status.present ? '対象を検知しています' : '対象を検知していません'}</p></div><span className="metric-tag">{!status ? '—' : demo ? '模擬値' : status.present ? '検知中' : '不在'}</span></article>
        <article className="panel metric-card"><span className="metric-icon"><DashboardIcon name="movement" /></span><div className="metric-copy"><h2>体の動き</h2><strong className="presence">{!status || !status.present ? '—' : current === 'OK' ? '動いています' : 'じっとしています'}</strong><p className="muted metric-note">{!status || !status.present ? '体の動きは表示できません' : current === 'OK' ? '体の動きを検知しています' : '体の動きが少ない状態です'}</p></div><span className="metric-tag">{!status || !status.present ? '—' : demo ? '模擬値' : '受信値'}</span></article>
        <p className="metrics-note">呼吸数：{rateNote}</p>
      </aside>
    </section>
    {!demo && <BoardPanel health={health} />}
    {!demo && <NotificationPanel state={notifications} />}
    {demo && <section className="panel controls"><div><p className="eyebrow">INTERACTIVE DEMO</p><h2>シーンを切り替える</h2><p className="muted">表示したい模擬状態を選択してください。</p></div><div className="state-buttons" role="group" aria-label="模擬状態の選択">{(Object.keys(stateWords) as BreathingState[]).map(state => <button key={state} aria-pressed={selected === state} onClick={() => { mock?.setState(state); setSelected(state); }} className={selected === state ? 'selected' : ''}><span>{state}</span><small>{stateWords[state].title}</small></button>)}</div></section>}
    <footer>{demo && <p><strong>すべてシミュレーションです。</strong>実際のセンサーには接続していません。</p>}<p>本プロトタイプは医療機器ではなく、診断・安全の保証・SIDS（乳幼児突然死症候群）の予防を目的としません。</p><div><span>息吹～IBUKI～ — JPHACKS 2026</span></div></footer>
  </main>;
}
