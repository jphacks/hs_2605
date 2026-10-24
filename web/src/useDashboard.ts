import { useEffect, useState } from 'react';
import type { BrainStatus, StatusSource } from './data/status';
import { appendWaveform } from './data/waveform';
import type { WavePoint } from './data/waveform';
export function useDashboard(source: StatusSource, intervalMs: number | null = null) {
  const [data, setData] = useState<{ status: BrainStatus | null; points: WavePoint[] }>({ status: null, points: [] });
  useEffect(() => source.subscribe(status => {
    setData(previous => ({ status, points: appendWaveform(previous.points, previous.status, status, intervalMs) }));
  }), [source, intervalMs]);
  return data;
}
