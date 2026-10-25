import { useEffect, useReducer, useState } from 'react';
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
import { describeStatus, stateWords } from './data/statusView';

const configuredInterval = Number(import.meta.env.VITE_LIVE_SAMPLE_INTERVAL_MS);
const liveIntervalMs = Number.isFinite(configuredInterval) && configuredInterval > 0 ? configuredInterval : null;
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
  useEffect(() => { if (!demo) return watchHealth(endpoints.health, setHealth); }, [demo]);
  // The server's siren keeps sounding until someone acknowledges it; this is the dashboard's way.
  const [stoppingSiren, setStoppingSiren] = useState(false);
  useEffect(() => { if (!health?.siren) setStoppingSiren(false); }, [health?.siren]);
  const stopSiren = () => {
    setStoppingSiren(true);
    fetch(new URL('/api/button', endpoints.health).href, { method: 'POST' }).catch(() => setStoppingSiren(false));
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

  return <main>
    <header>
      <h1 className="brand"><span className="brand-symbol">≈</span><span>息吹～IBUKI～<small>赤ちゃんの呼吸の動きを、見えるかたちに。</small></span></h1>
      <div className="header-controls"><span className="demo-badge"><span className="dot"/>{demo ? 'シミュレーションモード' : 'LIVE モード'}</span></div>
    </header>
    {!demo && <ConnectionStatus connection={connection} board={board} ready={liveReady} health={health}
      placeholder={liveView.placeholder} shaking={data.status?.shaking ?? false} waveAvailable={liveView.waveAvailable}
      url={endpoints.ws} notice={liveView.notice} />}
    {!demo && health?.siren && <button className="siren-stop" onClick={stopSiren} disabled={stoppingSiren}>
      {stoppingSiren ? 'アラームを止めています…' : 'アラームを止める'}<small>赤ちゃんの様子を確認したら押してください（ボードの SW1 と同じです）</small></button>}
    <section className={`status-banner tone-${statusView.tone}`} aria-live="polite" aria-atomic="true">
      <span className="status-icon">{{ alert: '!', check: '!', shake: '〜', empty: '○', wait: '…', ok: '≈', still: '≈' }[statusView.tone]}</span>
      <div><h2>{statusView.title}</h2><p>{statusView.detail}{demo ? '（シミュレーション）' : ''}</p></div>
    </section>
    <section className="dashboard-grid">
      <article className="panel waveform">
        <div className="panel-heading"><div><p className="eyebrow">BREATHING MOTION</p><h2>呼吸の動き</h2></div><span className="chart-live"><span className="dot"/>{sourceTag}</span></div>
        <p className="chart-description">胸の動き（mm） · 直近30秒</p>
        {chartNote && <p className="muted" role="status">{chartNote}</p>}
        <WaveChart points={points} t={status?.t ?? null} alert={current === 'ALERT'} demo={demo} label={`胸の動きの波形。${statusView.title}`}/>
        <div className="chart-footer"><span><i/>胸の動き{demo ? '（シミュレーション）' : ''}</span><span><i className="idle-key"/>測定なし（揺れ・学習中・不在のとき）</span></div>
      </article>
      <aside className="metrics" aria-label="いまの様子">
        <article className="panel bpm"><p className="eyebrow">BREATHING RATE</p><h2>呼吸数</h2><div className={`bpm-value${bpm == null ? ' bpm-value--none' : ''}`}>{bpm?.toFixed(1) ?? '—'}<span>回 / 分</span></div><p className="muted">{rateNote}</p></article>
        <article className="panel metric-row"><div><p className="eyebrow">IN BED</p><h2>ベッド</h2></div><strong className="presence">{!status ? '—' : status.present ? 'います' : 'いません'}</strong></article>
        <article className="panel metric-row"><div><p className="eyebrow">MOVEMENT</p><h2>体の動き</h2></div><strong className="presence">{!status || !status.present ? '—' : current === 'OK' ? '動いています' : 'じっとしています'}</strong></article>
      </aside>
    </section>
    {!demo && <NotificationPanel state={notifications} />}
    {demo && <section className="panel controls"><div><p className="eyebrow">INTERACTIVE DEMO</p><h2>シーンを切り替える</h2><p className="muted">表示したい模擬状態を選択してください。</p></div><div className="state-buttons" role="group" aria-label="模擬状態の選択">{(Object.keys(stateWords) as BreathingState[]).map(state => <button key={state} aria-pressed={selected === state} onClick={() => { mock?.setState(state); setSelected(state); }} className={selected === state ? 'selected' : ''}><span>{state}</span><small>{stateWords[state].title}</small></button>)}</div></section>}
    <footer>{demo && <p><strong>すべてシミュレーションです。</strong>実際のセンサーには接続していません。</p>}<p>本プロトタイプは医療機器ではなく、診断・安全の保証・SIDS（乳幼児突然死症候群）の予防を目的としません。</p><div><span>息吹～IBUKI～ — JPHACKS 2026</span></div></footer>
  </main>;
}
