import { useEffect, useRef, useState } from 'react';
import type { BrainStatus, StatusSource } from './data/status';
import { appendWaveform } from './data/waveform';
import type { WavePoint } from './data/waveform';
export function useDashboard(source: StatusSource, intervalMs: number | null = null, resetKey = 0) {
  const interval = useRef(intervalMs);
  // Metadata may arrive after subscription: updating timing must not reconnect.
  const generation = useRef(resetKey);
  useEffect(() => { interval.current = intervalMs; generation.current = resetKey; }, [intervalMs, resetKey]);
  const [data, setData] = useState<{ status: BrainStatus | null; points: WavePoint[]; generation: number }>({ status: null, points: [], generation: resetKey });
  useEffect(() => source.subscribe(status => {
    const spacing = interval.current;
    const version = generation.current;
    setData(previous => ({ status, generation: version, points: appendWaveform(previous.generation === version ? previous.points : [], previous.generation === version ? previous.status : null, status, spacing) }));
  }), [source]);
  return data.generation === resetKey ? data : { status: null, points: [] };
}
