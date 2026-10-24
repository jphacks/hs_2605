import { useState } from 'react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import { createMockSource } from './data/mockSource';
import type { BreathingState } from './data/status';
import { useDashboard } from './useDashboard';

const states: Record<BreathingState, { label: string; title: string; description: string }> = {
  EMPTY: { label: '不在', title: '対象を検知していません', description: '対象がいないシーンをシミュレーションしています。' },
  OK: { label: '動きあり', title: '呼吸の動きを表示中', description: 'ゆるやかな胸の動きを、サンプル波形で表現しています。' },
  STILL: { label: '静止', title: '静かな動きを表示中', description: '呼吸の動きがあり、体動が少ないシーンです。' },
  CHECK: { label: '確認', title: '保護者による確認が必要', description: '動きが小さくなったシーンです。実際の状態判定ではありません。' },
  ALERT: { label: 'アラート', title: 'アラートの表示デモ', description: '保護者へのお知らせ画面を再現しています。外部通知は送信しません。' },
};

export default function App() {
  const [source] = useState(createMockSource);
  const [selected, setSelected] = useState<BreathingState>('OK');
  const { status, points } = useDashboard(source);
  const current = status?.state ?? 'OK';
  const copy = states[current];
  const time = status ? `${Math.floor(status.t / 60000).toString().padStart(2, '0')}:${Math.floor(status.t / 1000 % 60).toString().padStart(2, '0')}` : '--:--';
  return <main>
    <header><a className="brand" href="#"><span className="brand-symbol">≈</span><span>IBUKI<small>息吹 / MOTION MONITOR</small></span></a><span className="demo-badge"><span className="dot"/>シミュレーションモード</span></header>
    <section className="intro"><div><p className="eyebrow">赤ちゃんの呼吸の動きを、見えるかたちに。</p><h1>小さな動きに、<br className="mobile-break"/>目を向ける。</h1><p className="muted">呼吸の動きを可視化する、IBUKI ダッシュボード。</p></div><div className="session"><span>DEMO SESSION</span><strong>{time}</strong><small>経過時間 · 模擬データ</small></div></section>
    <section className={`status-banner state-${current.toLowerCase()}`} aria-live="polite" aria-atomic="true"><span className="status-icon">{current === 'ALERT' || current === 'CHECK' ? '!' : '≈'}</span><div><span className="eyebrow">{current} · {copy.label}</span><h2>{copy.title}</h2><p>{copy.description}</p></div><span className="sim-label">模擬状態</span></section>
    <section className="dashboard-grid">
      <article className="panel waveform"><div className="panel-heading"><div><p className="eyebrow">BREATHING MOTION</p><h2>呼吸の動き</h2></div><span className="chart-live"><span className="dot"/>模擬データ · 20 Hz</span></div><p className="chart-description">胸の変位（mm） · 直近30秒</p>
        <div className="chart" role="img" aria-label={`模擬呼吸波形。現在の模擬状態は${copy.label}。`}><ResponsiveContainer width="100%" height="100%"><LineChart data={points} margin={{ top: 16, right: 18, bottom: 8, left: 0 }} accessibilityLayer><CartesianGrid stroke="#e6ede9" strokeDasharray="3 6" vertical={false}/><XAxis type="number" dataKey="time" domain={[Math.max(0, (status?.t ?? 0) / 1000 - 30), Math.max(30, (status?.t ?? 0) / 1000)]} tickFormatter={v => `${Math.round(v)}s`} tickLine={false} axisLine={false} minTickGap={40} tick={{ fill: '#7b8983', fontSize: 12 }}/><YAxis domain={[-2, 2]} ticks={[-2, -1, 0, 1, 2]} tickLine={false} axisLine={false} width={35} tick={{ fill: '#7b8983', fontSize: 12 }}/><Line type="linear" dataKey="displacement" stroke={current === 'ALERT' ? '#c25d50' : '#438b77'} strokeWidth={2.5} dot={false} isAnimationActive={false}/></LineChart></ResponsiveContainer></div>
        <div className="chart-footer"><span><i/>胸の動き · シミュレーション</span><span>5回 / 秒 更新</span></div>
      </article>
      <aside className="metrics" aria-label="模擬データの指標"><article className="panel bpm"><p className="eyebrow">BREATHING RATE · 模擬値</p><h2>推定呼吸数</h2><div className="bpm-value">{status?.bpm?.toFixed(1) ?? '—'}<span>回 / 分</span></div><p className="muted">デモ用の値です。年齢別の判定基準ではありません。</p></article><article className="panel metric-row"><div><p className="eyebrow">MOVEMENT · 模擬値</p><h2>体動</h2></div><div className="metric-answer"><strong>{!status ? '準備中' : !status.present ? '対象なし' : status.motion >= 50 ? '動きあり' : '少ない'}</strong><small>動き指標 {status?.motion ?? '—'}</small></div></article><article className="panel metric-row"><div><p className="eyebrow">PRESENCE · 模擬値</p><h2>在室</h2></div><strong className="presence">{!status ? '準備中' : status.present ? '検知あり' : '検知なし'}</strong></article></aside>
    </section>
    <section className="panel controls"><div><p className="eyebrow">INTERACTIVE DEMO</p><h2>シーンを切り替える</h2><p className="muted">表示したい模擬状態を選択してください。</p></div><div className="state-buttons" role="group" aria-label="模擬状態の選択">{(Object.keys(states) as BreathingState[]).map(state => <button key={state} aria-pressed={selected === state} onClick={() => { source.setState(state); setSelected(state); }} className={selected === state ? 'selected' : ''}><span>{state}</span><small>{states[state].label}</small></button>)}</div></section>
    <footer><p><strong>すべてシミュレーションです。</strong> 実際のセンサーには接続していません。</p><p>本デモは医療機器ではなく、診断・安全の保証・SIDS（乳幼児突然死症候群）の予防を目的としません。</p><div><span>IBUKI — JPHACKS 2026</span><span>WEB PROTOTYPE / 01</span></div></footer>
  </main>;
}
