import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import { WINDOW_S, glideEnd } from './data/waveform';
import type { WavePoint } from './data/waveform';
import { useReducedMotion } from './useReducedMotion';
import { displayDomain, windowTicks, windowTickLabel } from './chartPresentation';

// The time axis end in seconds, gliding between updates (see glideEnd). Only this chart redraws
// every frame (~30 per second), not the whole dashboard.
function useGlidingEnd(t: number | null, reducedMotion: boolean): number {
  const arrival = useRef({ t: 0, at: 0, step: 0 });
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    if (t === null) return;
    const previous = arrival.current;
    arrival.current = { t, at: performance.now(), step: previous.t > 0 && t > previous.t ? Math.min(t - previous.t, 1000) : 0 };
  }, [t]);
  useEffect(() => {
    if (reducedMotion) return;
    let id = 0, last = 0;
    const tick = (time: number) => {
      if (time - last >= 33) { last = time; setNow(time); }
      id = requestAnimationFrame(tick);
    };
    id = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(id);
  }, [reducedMotion]);
  if (t === null) return 0;
  if (reducedMotion) return t / 1000;
  const { t: lastT, at, step } = arrival.current;
  return lastT === t ? glideEnd(t, step, now - at) : glideEnd(lastT, step, now - at);
}

export default function WaveChart({ points, t, alert, demo, label }:
  { points: WavePoint[]; t: number | null; alert: boolean; demo: boolean; label: string }) {
  const reducedMotion = useReducedMotion();
  const end = Math.max(WINDOW_S, useGlidingEnd(t, reducedMotion));
  const start = end - WINDOW_S;
  const domain = useMemo<[number, number]>(() => demo ? [-2, 2] : displayDomain(points), [demo, points]);
  const gradientId = useId();
  const waveColor = alert ? 'var(--chart-alert)' : 'var(--chart-wave)';
  const hasWaveform = points.some(point => point.displacement !== null);
  return <div className="chart" role="img" aria-label={`${label}。横軸は30秒前から最新を0〜30秒で表示。表示範囲は±${domain[1]}mm。`} data-end={end.toFixed(3)} data-range-mm={domain[1]}>
    <span className="chart-scale">{hasWaveform ? `表示範囲 ±${domain[1]} mm${demo ? '（模擬）' : ' · 自動拡大'}` : '胸の動き（mm）'}</span>
    <ResponsiveContainer width="100%" height="100%"><ComposedChart data={points} margin={{ top: 20, right: 12, bottom: 8, left: 0 }} accessibilityLayer>
      <defs><linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={waveColor} stopOpacity={0.18}/><stop offset="100%" stopColor={waveColor} stopOpacity={0.01}/></linearGradient></defs>
      <CartesianGrid stroke="var(--chart-grid)" strokeWidth={0.8} vertical={true}/>
      {/* allowDataOverflow: the window is fixed; samples just outside it are clipped, not allowed to stretch the axis */}
      <XAxis type="number" dataKey="time" allowDataOverflow domain={[start, end]} ticks={windowTicks(start, end)} tickFormatter={value => windowTickLabel(value, start)} tickLine={false} axisLine={false} tick={{ fill: 'var(--chart-axis)', fontSize: 12 }}/>
      <YAxis type="number" domain={domain} ticks={[domain[0], 0, domain[1]]} padding={{ top: 4, bottom: 4 }} tickFormatter={v => `${Number(Number(v).toFixed(2))}`} tickLine={false} axisLine={false} width={46} tick={hasWaveform ? { fill: 'var(--chart-axis)', fontSize: 12 } : false}/>
      {hasWaveform && <Area type="linear" dataKey="displacement" baseValue="dataMin" fill={`url(#${gradientId})`} stroke={waveColor} strokeWidth={1.7} dot={false} isAnimationActive={false} connectNulls={false}/>}
      <Line type="linear" dataKey="idle" stroke="var(--chart-idle)" strokeWidth={2} strokeDasharray="6 5" dot={false} isAnimationActive={false}/>
    </ComposedChart></ResponsiveContainer>
    {!hasWaveform && <div className="chart-empty"><span>波形データがありません</span></div>}
  </div>;
}
