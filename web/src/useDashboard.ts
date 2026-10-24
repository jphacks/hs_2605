import { useEffect, useRef, useState } from 'react';
import type { BrainStatus, StatusSource } from './data/status';
import { appendWaveform } from './data/waveform';
import type { WavePoint } from './data/waveform';
export function useDashboard(source: StatusSource, intervalMs: number | null = null) {
  const interval = useRef(intervalMs);
  // Metadata may arrive after subscription: updating timing must not reconnect.
  useEffect(() => { interval.current = intervalMs; }, [intervalMs]);
  const [data, setData] = useState<{ status: BrainStatus | null; points: WavePoint[] }>({ status: null, points: [] });
  useEffect(() => source.subscribe(status => {
    const spacing = interval.current;
    setData(previous => ({ status, points: appendWaveform(previous.points, previous.status, status, spacing) }));
  }), [source]);
  return data;
}
