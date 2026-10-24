import type { BrainStatus } from './status';
export interface WavePoint { time: number; displacement: number }
// t is milliseconds and marks the last sample of a batch (pending Brain agreement).
// Without an agreed interval, estimate spacing from consecutive batch timestamps.
export function appendWaveform(points: WavePoint[], previous: BrainStatus | null, status: BrainStatus, intervalMs: number | null): WavePoint[] {
  if (status.shaking || status.disp_mm.length === 0) return [];
  const delta = previous ? status.t - previous.t : null;
  if (delta !== null && delta <= 0) return []; // Reset/duplicate/out-of-order timestamp.
  const gap = delta !== null && delta > 3000;
  const previousReliable = previous && !previous.shaking && previous.disp_mm.length > 0;
  const spacing = status.dt_ms ?? intervalMs ?? (delta !== null && !gap && previousReliable ? delta / status.disp_mm.length : null);
  if (spacing === null || !Number.isFinite(spacing) || spacing <= 0) return gap ? [] : points;
  const samples = status.disp_mm.map((displacement, i) => ({
    time: (status.t - (status.disp_mm.length - 1 - i) * spacing) / 1000, displacement,
  })).filter(p => p.time >= 0);
  const firstTime = samples[0]?.time ?? Infinity;
  return [...(gap ? [] : points).filter(p => p.time < firstTime), ...samples]
    .filter(p => p.time >= status.t / 1000 - 30).slice(-4096);
}
