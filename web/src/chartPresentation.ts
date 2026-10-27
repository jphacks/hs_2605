import type { WavePoint } from './data/waveform';

// Display scale only, never a breathing/health threshold. A zero-centred ±2 mm
// floor avoids magnifying tiny motion. Expand in powers of two with 10% headroom.
// The existing rolling history keeps an excursion in this scale until it leaves
// the window; there is no smoothing or rewriting of incoming samples.
export function displayDomain(points: readonly WavePoint[]): [number, number] {
  let peak = 0;
  for (const point of points) {
    peak = Math.max(peak, Math.abs(point.displacement ?? 0), Math.abs(point.idle ?? 0));
  }
  let range = 2;
  while (peak > range * 0.9) {
    const next = range * 2;
    if (!Number.isFinite(next)) { range = Number.MAX_VALUE; break; }
    range = next;
  }
  return [-range, range];
}

// Tick positions stay in the original elapsed-time coordinates. Only their
// displayed labels count from the beginning of the same rolling window.
export function windowTicks(start: number, end: number): number[] {
  return Array.from({ length: 7 }, (_, index) => start + (end - start) * index / 6);
}

export function windowTickLabel(value: number, start: number): string {
  return `${Math.round(value - start)}秒`;
}
