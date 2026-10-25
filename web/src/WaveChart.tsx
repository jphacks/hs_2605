import { useEffect, useRef, useState } from 'react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import { WINDOW_S, glideEnd } from './data/waveform';
import type { WavePoint } from './data/waveform';

// The time axis end in seconds, gliding between updates (see glideEnd). Only this chart redraws
// every frame (~30 per second), not the whole dashboard.
function useGlidingEnd(t: number | null): number {
  const arrival = useRef({ t: 0, at: 0, step: 0 });
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    if (t === null) return;
    const previous = arrival.current;
    arrival.current = { t, at: performance.now(), step: previous.t > 0 && t > previous.t ? Math.min(t - previous.t, 1000) : 0 };
  }, [t]);
  useEffect(() => {
    let id = 0, last = 0;
    const tick = (time: number) => {
      if (time - last >= 33) { last = time; setNow(time); }
      id = requestAnimationFrame(tick);
    };
    id = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(id);
  }, []);
  if (t === null) return 0;
  const { t: lastT, at, step } = arrival.current;
  return lastT === t ? glideEnd(t, step, now - at) : glideEnd(lastT, step, now - at);
}

export default function WaveChart({ points, t, alert, demo, label }:
  { points: WavePoint[]; t: number | null; alert: boolean; demo: boolean; label: string }) {
  const end = Math.max(WINDOW_S, useGlidingEnd(t));
  return <div className="chart" role="img" aria-label={label} data-end={end.toFixed(3)}>
    <ResponsiveContainer width="100%" height="100%"><LineChart data={points} margin={{ top: 16, right: 18, bottom: 8, left: 0 }} accessibilityLayer>
      <CartesianGrid stroke="#e6ede9" strokeDasharray="3 6" vertical={false}/>
      {/* allowDataOverflow: the window is fixed; samples just outside it are clipped, not allowed to stretch the axis */}
      <XAxis type="number" dataKey="time" allowDataOverflow domain={[end - WINDOW_S, end]} ticks={[end - 30, end - 20, end - 10, end]} tickFormatter={v => Math.round(v - end) === 0 ? '今' : `${Math.round(v - end)}秒`} tickLine={false} axisLine={false} tick={{ fill: '#7b8983', fontSize: 12 }}/>
      <YAxis type="number" domain={demo ? [-2, 2] : ['auto', 'auto']} tickFormatter={v => `${Number(Number(v).toFixed(2))}`} tickLine={false} axisLine={false} width={46} tick={{ fill: '#7b8983', fontSize: 12 }}/>
      <Line type="linear" dataKey="idle" stroke="#a9b3ae" strokeWidth={2} strokeDasharray="6 5" dot={false} isAnimationActive={false}/>
      <Line type="linear" dataKey="displacement" stroke={alert ? '#c25d50' : '#438b77'} strokeWidth={2.5} dot={false} isAnimationActive={false}/>
    </LineChart></ResponsiveContainer>
  </div>;
}
