import { useState } from 'react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import { createMockSource } from './data/mockSource';
import { createWebSocketSource } from './data/webSocketSource';
import type { AlertEvent, ConnectionState } from './data/webSocketSource';
import type { BreathingState, StatusSource } from './data/status';
import { useDashboard } from './useDashboard';

type Mode = 'DEMO' | 'LIVE';
const states: Record<BreathingState, { label: string; title: string; description: string }> = {
  EMPTY: { label: '不在', title: '対象を検知していません', description: '対象がいないシーンをシミュレーションしています。' },
  OK: { label: '動きあり', title: '呼吸の動きを表示中', description: 'ゆるやかな胸の動きを、サンプル波形で表現しています。' },
  STILL: { label: '静止', title: '静かな動きを表示中', description: '呼吸の動きがあり、体動が少ないシーンです。' },
  CHECK: { label: '確認', title: '保護者による確認が必要', description: '動きが小さくなったシーンです。実際の状態判定ではありません。' },
  ALERT: { label: 'アラート', title: 'アラートの表示デモ', description: '保護者へのお知らせ画面を再現しています。外部通知は送信しません。' },
};
const connectionLabels: Record<ConnectionState, string> = {
  connecting: '接続中', connected: '接続済み', disconnected: '切断・再接続待ち', stale: 'データ更新停止',
};
const configuredInterval = Number(import.meta.env.VITE_LIVE_SAMPLE_INTERVAL_MS);
const liveIntervalMs = Number.isFinite(configuredInterval) && configuredInterval > 0 ? configuredInterval : null;

export default function App() {
  const [mode, setMode] = useState<Mode>('DEMO');
  // Remounting clears history, alert events, and all socket/timer subscriptions.
  return <Dashboard key={mode} mode={mode} onModeChange={setMode} />;
}

function Dashboard({ mode, onModeChange }: { mode: Mode; onModeChange: (mode: Mode) => void }) {
  const demo = mode === 'DEMO';
  const [connection, setConnection] = useState<ConnectionState>('connecting');
  const [liveReady, setLiveReady] = useState(false);
  const [alert, setAlert] = useState<AlertEvent | null>(null);
  const [mock] = useState(createMockSource);
  const [source] = useState<StatusSource>(() => {
    if (demo) return mock;
    const live = createWebSocketSource({
      onConnection(next) {
        setConnection(next);
        if (next !== 'connected') setLiveReady(false);
        if (next === 'connecting') setAlert(null);
      },
      onAlert: setAlert,
    });
    return { subscribe: listener => live.subscribe(status => { setLiveReady(true); listener(status); }) };
  });
  const [selected, setSelected] = useState<BreathingState>('OK');
  const data = useDashboard(source, demo ? 50 : liveIntervalMs);
  const available = demo || connection === 'connected' && liveReady;
  const status = available ? data.status : null;
  const points = available ? data.points : [];
  const current = status?.state;
  const copy = current ? states[current] : null;
  const readingLabel = demo ? '模擬値' : '受信値';
  const time = status ? `${Math.floor(status.t / 60000).toString().padStart(2, '0')}:${Math.floor(status.t / 1000 % 60).toString().padStart(2, '0')}` : '--:--';
  const title = !status ? demo ? 'デモを準備しています' : '有効なステータスを待っています'
    : demo ? copy!.title : `Brain の状態：${current}（${copy!.label}）`;

  return <main>
    <header>
      <a className="brand" href="#"><span className="brand-symbol">≈</span><span>IBUKI<small>息吹 / MOTION MONITOR</small></span></a>
      <div className="header-controls"><div className="mode-switch" role="group" aria-label="データソース">{(['DEMO', 'LIVE'] as const).map(value => <button key={value} aria-pressed={mode === value} onClick={() => onModeChange(value)}>{value}</button>)}</div><span className="demo-badge"><span className="dot"/>{demo ? 'シミュレーションモード' : 'LIVE · WebSocket 受信'}</span></div>
    </header>
    {!demo && <section className={`connection-panel connection-${connection}`} aria-live="polite">
      <strong>{connectionLabels[connection]}</strong><span>ws://localhost:8000/ws</span>
      <small>{connection === 'stale' ? '有効なデータが3秒以上届いていません。現在値と波形を非表示にしています。' : connection === 'disconnected' ? '自動的に再接続します。現在値は表示しません。' : liveReady ? 'Brain から受信した値を表示中。センサーの接続状態・データの出所は送信側で確認してください。' : '有効なステータスの受信前です。測定値は表示しません。'}</small>
    </section>}
    <section className="intro"><div><p className="eyebrow">赤ちゃんの呼吸の動きを、見えるかたちに。</p><h1>小さな動きに、<br className="mobile-break"/>目を向ける。</h1><p className="muted">呼吸の動きを可視化する、IBUKI ダッシュボード。</p></div><div className="session"><span>{mode} SESSION</span><strong>{time}</strong><small>{demo ? '経過時間 · 模擬データ' : '送信側の時刻 t · ms として解釈'}</small></div></section>
    <section className={`status-banner state-${current?.toLowerCase() ?? 'empty'}`} aria-live="polite" aria-atomic="true"><span className="status-icon">{current === 'ALERT' || current === 'CHECK' ? '!' : '≈'}</span><div><span className="eyebrow">{current ? `${current} · ${copy!.label}` : 'WAITING · データ待ち'}</span><h2>{title}</h2><p>{demo ? copy?.description : '状態は Python Brain の受信結果です。ブラウザでは異常判定を行いません。'}</p></div><span className="sim-label">{demo ? '模擬状態' : '受信状態'}</span></section>
    {!demo && alert && <div className="received-event" role="status"><strong>アラートイベント受信</strong><span>理由：{alert.reason} / 継続：{alert.since_s}秒</span><small>受信イベントの記録です。現在の状態は上の表示をご確認ください。電話への通知は送信しません。</small></div>}
    <section className="dashboard-grid">
      <article className="panel waveform">
        <div className="panel-heading"><div><p className="eyebrow">BREATHING MOTION</p><h2>呼吸の動き</h2></div><span className="chart-live"><span className="dot"/>{demo ? '模擬データ · 20 Hz' : liveIntervalMs ? `受信データ · 間隔 ${liveIntervalMs} ms` : '受信データ · 時間間隔は推定'}</span></div>
        <p className="chart-description">胸の変位（mm） · 直近30秒{!demo && !liveIntervalMs ? ' · バッチ時刻から補間（初回は次の受信を待機）' : ''}</p>
        <div className="chart" role="img" aria-label={`${demo ? '模擬' : '受信'}呼吸波形。${copy && status ? `現在の状態は${copy.label}。` : '有効なデータ待ち。'}`}>
          <ResponsiveContainer width="100%" height="100%"><LineChart data={points} margin={{ top: 16, right: 18, bottom: 8, left: 0 }} accessibilityLayer>
            <CartesianGrid stroke="#e6ede9" strokeDasharray="3 6" vertical={false}/>
            <XAxis type="number" dataKey="time" domain={[Math.max(0, (status?.t ?? 0) / 1000 - 30), Math.max(30, (status?.t ?? 0) / 1000)]} tickFormatter={v => `${Math.round(v)}s`} tickLine={false} axisLine={false} minTickGap={40} tick={{ fill: '#7b8983', fontSize: 12 }}/>
            <YAxis type="number" domain={demo ? [-2, 2] : ['auto', 'auto']} tickLine={false} axisLine={false} width={35} tick={{ fill: '#7b8983', fontSize: 12 }}/>
            <Line type="linear" dataKey="displacement" stroke={current === 'ALERT' ? '#c25d50' : '#438b77'} strokeWidth={2.5} dot={false} isAnimationActive={false}/>
          </LineChart></ResponsiveContainer>
        </div>
        <div className="chart-footer"><span><i/>胸の動き · {demo ? 'シミュレーション' : 'WebSocket'}</span><span>{demo ? '5回 / 秒 更新' : '受信ごとに更新'}</span></div>
      </article>
      <aside className="metrics" aria-label={`${readingLabel}の指標`}>
        <article className="panel bpm"><p className="eyebrow">BREATHING RATE · {readingLabel}</p><h2>推定呼吸数</h2><div className="bpm-value">{status?.bpm?.toFixed(1) ?? '—'}<span>回 / 分</span></div><p className="muted">{demo ? 'デモ用の値です。年齢別の判定基準ではありません。' : 'Brain の推定値です。未受信・更新停止・不明の場合は「—」を表示します。'}</p></article>
        <article className="panel metric-row"><div><p className="eyebrow">MOVEMENT · {readingLabel}</p><h2>体動</h2></div><div className="metric-answer"><strong>{!status ? '—' : !status.present ? '対象なし' : demo ? status.motion >= 50 ? '動きあり' : '少ない' : '指標を受信'}</strong><small>動き指標 {status?.motion ?? '—'}</small></div></article>
        <article className="panel metric-row"><div><p className="eyebrow">PRESENCE · {readingLabel}</p><h2>在室</h2></div><strong className="presence">{!status ? '—' : status.present ? '検知あり' : '検知なし'}</strong></article>
      </aside>
    </section>
    {demo && <section className="panel controls"><div><p className="eyebrow">INTERACTIVE DEMO</p><h2>シーンを切り替える</h2><p className="muted">表示したい模擬状態を選択してください。</p></div><div className="state-buttons" role="group" aria-label="模擬状態の選択">{(Object.keys(states) as BreathingState[]).map(state => <button key={state} aria-pressed={selected === state} onClick={() => { mock.setState(state); setSelected(state); }} className={selected === state ? 'selected' : ''}><span>{state}</span><small>{states[state].label}</small></button>)}</div></section>}
    <footer><p><strong>{demo ? 'すべてシミュレーションです。' : 'LIVE はサーバーからの受信データです。'}</strong> {demo ? '実際のセンサーには接続していません。' : 'この通信形式にはデータの出所情報がないため、実センサーによる測定かどうかは送信側で確認してください。'}</p><p>本プロトタイプは医療機器ではなく、診断・安全の保証・SIDS（乳幼児突然死症候群）の予防を目的としません。</p><div><span>IBUKI — JPHACKS 2026</span><span>WEB PROTOTYPE / 02</span></div></footer>
  </main>;
}
