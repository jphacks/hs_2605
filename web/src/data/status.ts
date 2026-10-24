// Dashboard-local mirror of the existing Brain message. No shared protocol changes.
export type BreathingState = 'EMPTY' | 'OK' | 'STILL' | 'CHECK' | 'ALERT';
export interface BrainStatus {
  t: number;
  disp_mm: number[];
  bpm: number | null;
  motion: number;
  present: boolean;
  state: BreathingState;
}
export interface StatusSource {
  subscribe: (listener: (status: BrainStatus) => void) => () => void;
}
