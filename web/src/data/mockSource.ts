import type { BrainStatus, BreathingState, StatusSource } from './status';

// 20 samples/s, delivered as four new points per 200 ms message.
// Illustrative signals only: these values are not infant thresholds or measurements.
export function createMockSource(): StatusSource & { setState: (state: BreathingState) => void } {
  let state: BreathingState = 'OK';
  let sample = 0;
  return {
    setState(next) { state = next; },
    subscribe(listener) {
      const timer = window.setInterval(() => {
        const present = state !== 'EMPTY';
        const breathing = state === 'OK' || state === 'STILL';
        const disp_mm = Array.from({ length: 4 }, () => {
          const seconds = ++sample / 20;
          const ripple = Math.sin(seconds * 13) * 0.025;
          return Number((breathing ? Math.sin(seconds * 2 * Math.PI * 16.2 / 60) * 1.1 + ripple : present ? ripple : 0).toFixed(3));
        });
        listener({ t: sample * 50, disp_mm, bpm: breathing ? 16.2 : null,
          motion: state === 'OK' ? 120 : state === 'STILL' ? 8 : present ? 2 : 0,
          present, state });
      }, 200);
      return () => window.clearInterval(timer);
    },
  };
}
