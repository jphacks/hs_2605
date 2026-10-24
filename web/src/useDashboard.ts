import { useEffect, useState } from 'react';
import type { BrainStatus, StatusSource } from './data/status';
export interface WavePoint { time: number; displacement: number }
export function useDashboard(source: StatusSource) {
  const [data, setData] = useState<{ status: BrainStatus | null; points: WavePoint[] }>({ status: null, points: [] });
  useEffect(() => source.subscribe(status => {
    setData(previous => ({ status, points: [...previous.points, ...status.disp_mm.map((displacement, i) => ({
      time: (status.t - (status.disp_mm.length - 1 - i) * 50) / 1000, displacement,
    }))].filter(point => point.time >= status.t / 1000 - 30).slice(-600) }));
  }), [source]);
  return data;
}
