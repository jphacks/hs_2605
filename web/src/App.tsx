import { useEffect, useReducer, useState } from 'react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, XAxis, YAxis } from 'recharts';
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
import { WINDOW_S } from './data/waveform';

const states: Record<BreathingState, { label: string; title: string; description: string }> = {
  EMPTY: { label: '不在', title: '対象を検知していません', description: '対象がいないシーンをシミュレーションしています。' },
  OK: { label: '動きあり', title: '呼吸の動きを表示中', description: 'ゆるやかな胸の動きを、サンプル波形で表現しています。' },
  STILL: { label: '静止', title: '静かな動きを表示中', description: '呼吸の動きがあり、体動が少ないシーンです。' },
  CHECK: { label: '確認', title: '保護者による確認が必要', description: '動きが小さくなったシーンです。実際の状態判定ではありません。' },
  ALERT: { label: 'アラート', title: 'アラートの表示デモ', description: '保護者へのお知らせ画面を再現しています。外部通知は送信しません。' },
};
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
  const end = Math.max(WINDOW_S, (status?.t ?? 0) / 1000);
  const effectiveIntervalMs = status?.dt_ms ?? (demo ? 50 : liveIntervalMs);
  const current = status?.state;
  const copy = current ? states[current] : null;
  const readingLabel = demo ? '模擬値' : liveView.placeholder ? '暫定値 · placeholder' : liveView.replay ? '録画データ' : '受信値';
  const time = status ? `${Math.floor(status.t / 60000).toString().padStart(2, '0')}:${Math.floor(status.t / 1000 % 60).toString().padStart(2, '0')}` : '--:--';
  const title = !status ? demo ? 'デモを準備しています' : '有効なステータスを待っています'
    : demo ? copy!.title : liveView.placeholder ? '暫定処理 · 呼吸判定ではありません' : status.shaking ? '揺れのため波形を利用できません' : `Brain の受信状態：${current}`;

  return <main>
    <header>
      <a className="brand" href="#"><span className="brand-symbol">≈</span><span>息吹～IBUKI～<small>MOTION MONITOR</small></span></a>
      <div className="header-controls"><span className="demo-badge"><span className="dot"/>{demo ? 'シミュレーションモード' : 'LIVE モード'}</span></div>
    </header>
    {!demo && <ConnectionStatus connection={connection} board={board} ready={liveReady} health={health}
      placeholder={liveView.placeholder} shaking={data.status?.shaking ?? false} waveAvailable={liveView.waveAvailable}
      url={endpoints.ws} notice={liveView.notice} />}
    {!demo && health?.siren && <button className="siren-stop" onClick={stopSiren} disabled={stoppingSiren}>
      {stoppingSiren ? 'アラームを止めています…' : 'アラームを止める'}<small>赤ちゃんの様子を確認したら押してください（ボードの SW1 と同じです）</small></button>}
    <section className="intro"><div><p className="eyebrow">赤ちゃんの呼吸の動きを、見えるかたちに。</p><h1>小さな動きに、<br className="mobile-break"/>目を向ける。</h1><p className="muted">呼吸の動きを可視化する、息吹～IBUKI～ダッシュボード。</p></div><div className="session"><span>{mode} SESSION</span><strong>{time}</strong><small>{demo ? '経過時間 · 模擬データ' : '送信側の時刻 t · ms として解釈'}</small></div></section>
    <section className={`status-banner state-${current?.toLowerCase() ?? 'empty'}`} aria-live="polite" aria-atomic="true"><span className="status-icon">{current === 'ALERT' || current === 'CHECK' ? '!' : '≈'}</span><div><span className="eyebrow">{current ? `${current} · ${demo ? copy!.label : 'サーバー状態'}` : 'WAITING · データ待ち'}</span><h2>{title}</h2><p>{demo ? copy?.description : liveView.notice}</p></div><span className="sim-label">{demo ? '模擬状態' : liveView.placeholder ? '暫定処理' : '受信状態'}</span></section>
    {!demo && <NotificationPanel state={notifications} />}
    <section className="dashboard-grid">
      <article className="panel waveform">
        <div className="panel-heading"><div><p className="eyebrow">BREATHING MOTION</p><h2>呼吸の動き</h2></div><span className="chart-live"><span className="dot"/>{demo ? '模擬データ · 20 Hz' : `${readingLabel} · ${effectiveIntervalMs ? `間隔 ${effectiveIntervalMs} ms` : '時間間隔は推定'}`}</span></div>
        <p className="chart-description">胸の変位（mm） · 直近30秒{!demo && !effectiveIntervalMs ? ' · バッチ時刻から補間（初回は次の受信を待機）' : ''}</p>
        {!demo && !liveView.waveAvailable && <p className="muted" role="status">{liveView.notice}</p>}
        <div className="chart" role="img" aria-label={`${readingLabel}の波形。${demo ? copy?.label ?? '準備中' : liveView.notice}`}>
          <ResponsiveContainer width="100%" height="100%"><LineChart data={points} margin={{ top: 16, right: 18, bottom: 8, left: 0 }} accessibilityLayer>
            <CartesianGrid stroke="#e6ede9" strokeDasharray="3 6" vertical={false}/>
            <XAxis type="number" dataKey="time" domain={[end - WINDOW_S, end]} ticks={[end - 30, end - 20, end - 10, end]} tickFormatter={v => Math.round(v - end) === 0 ? '今' : `${Math.round(v - end)}秒`} tickLine={false} axisLine={false} tick={{ fill: '#7b8983', fontSize: 12 }}/>
            <YAxis type="number" domain={demo ? [-2, 2] : ['auto', 'auto']} tickFormatter={v => `${Number(Number(v).toFixed(2))}`} tickLine={false} axisLine={false} width={46} tick={{ fill: '#7b8983', fontSize: 12 }}/>
            <Line type="linear" dataKey="idle" stroke="#a9b3ae" strokeWidth={2} strokeDasharray="6 5" dot={false} isAnimationActive={false}/>
            <Line type="linear" dataKey="displacement" stroke={current === 'ALERT' ? '#c25d50' : '#438b77'} strokeWidth={2.5} dot={false} isAnimationActive={false}/>
          </LineChart></ResponsiveContainer>
        </div>
        <div className="chart-footer"><span><i/>胸の動き{demo ? '（シミュレーション）' : ''}</span><span><i className="idle-key"/>測定なし（揺れ・学習中・不在のとき）</span></div>
      </article>
      <aside className="metrics" aria-label={`${readingLabel}の指標`}>
        <article className="panel bpm"><p className="eyebrow">BREATHING RATE · {readingLabel}</p><h2>推定呼吸数</h2><div className="bpm-value">{(demo ? status?.bpm : liveView.bpm)?.toFixed(1) ?? '—'}<span>回 / 分</span></div><p className="muted">{demo ? 'シミュレーションの値です。年齢別の判定基準ではありません。' : liveView.placeholder ? 'placeholder は呼吸数を判定しません。常に「—」です。' : 'Brain の推定値です。波形利用不可・更新停止・不明の場合は「—」を表示します。'}</p></article>
        <article className="panel metric-row"><div><p className="eyebrow">MOVEMENT · {readingLabel}</p><h2>体動</h2></div><div className="metric-answer"><strong>{!status ? '—' : !status.present ? '対象なし' : demo ? status.motion >= 50 ? '動きあり' : '少ない' : '指標を受信'}</strong><small>動き指標 {status?.motion ?? '—'}</small></div></article>
        <article className="panel metric-row"><div><p className="eyebrow">PRESENCE · {readingLabel}</p><h2>在室</h2></div><strong className="presence">{!status ? '—' : status.present ? '検知あり' : '検知なし'}</strong></article>
      </aside>
    </section>
    {demo && <section className="panel controls"><div><p className="eyebrow">INTERACTIVE DEMO</p><h2>シーンを切り替える</h2><p className="muted">表示したい模擬状態を選択してください。</p></div><div className="state-buttons" role="group" aria-label="模擬状態の選択">{(Object.keys(states) as BreathingState[]).map(state => <button key={state} aria-pressed={selected === state} onClick={() => { mock?.setState(state); setSelected(state); }} className={selected === state ? 'selected' : ''}><span>{state}</span><small>{states[state].label}</small></button>)}</div></section>}
    <footer><p><strong>{demo ? 'すべてシミュレーションです。' : liveView.sourceLabel}</strong> {demo ? '実際のセンサーには接続していません。' : '接続や受信は、呼吸判定の精度を保証するものではありません。'}</p><p>本プロトタイプは医療機器ではなく、診断・安全の保証・SIDS（乳幼児突然死症候群）の予防を目的としません。</p><div><span>息吹～IBUKI～ — JPHACKS 2026</span><span>WEB PROTOTYPE / 03</span></div></footer>
  </main>;
}
