import type { BrainStatus } from './status';
// displacement: a measured sample (null breaks the line). idle: 0 marks a moment with no reading
// (shaking, learning, nobody there), drawn as a grey dashed line, never as breathing.
export interface WavePoint { time: number; displacement: number | null; idle?: number }
export const WINDOW_S = 30;
// Updates arrive every ~120 ms. Moving the time axis only when one arrives made the wave jump
// 8 times a second. The axis glides instead: one update (step ms) behind the newest sample t,
// moving toward it as time passes, and never past it (no running ahead of the data).
export function glideEnd(t: number, step: number, sinceArrivalMs: number): number {
  return (t - step + Math.min(Math.max(sinceArrivalMs, 0), step)) / 1000;
}
// t is milliseconds and marks the last sample of a batch (pending Brain agreement).
// Without an agreed interval, estimate spacing from consecutive batch timestamps.
export function appendWaveform(points: WavePoint[], previous: BrainStatus | null, status: BrainStatus, intervalMs: number | null): WavePoint[] {
  const delta = previous ? status.t - previous.t : null;
  if (delta !== null && delta < 0) return []; // The board's clock went back: it restarted.
  if (delta === 0) return points; // A repeated status adds nothing.
  const recent = (list: WavePoint[]) => list.filter(p => p.time >= status.t / 1000 - WINDOW_S).slice(-4096);
  // No reading now: keep what is drawn (the graph pauses instead of starting again) and mark the
  // moment as "no reading". No displacement is made up.
  if (status.shaking || status.disp_mm.length === 0) return recent([...points, { time: status.t / 1000, displacement: null, idle: 0 }]);
  const gap = delta !== null && delta > 3000;
  const previousReliable = previous && !previous.shaking && previous.disp_mm.length > 0;
  const spacing = status.dt_ms ?? intervalMs ?? (delta !== null && !gap && previousReliable ? delta / status.disp_mm.length : null);
  if (spacing === null || !Number.isFinite(spacing) || spacing <= 0) return recent(points);
  const samples: WavePoint[] = status.disp_mm.map((displacement, i) => ({
    time: (status.t - (status.disp_mm.length - 1 - i) * spacing) / 1000, displacement,
  })).filter(p => p.time >= 0);
  const firstTime = samples[0]?.time ?? Infinity;
  // After a time gap, a null point keeps the line from joining across missing data.
  const breakLine: WavePoint[] = gap ? [{ time: firstTime, displacement: null }] : [];
  return recent([...points.filter(p => p.time < firstTime), ...breakLine, ...samples]);
}
