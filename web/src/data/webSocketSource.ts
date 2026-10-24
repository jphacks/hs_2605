import type { BrainStatus, StatusSource } from './status';

export type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'stale';
export interface AlertEvent { type: 'alert'; reason: string; since_s: number; t?: number }
export interface LiveOptions {
  url?: string;
  staleAfterMs?: number;
  onConnection?: (state: ConnectionState) => void;
  onAlert?: (event: AlertEvent) => void;
  onBoard?: (connected: boolean) => void;
}
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const record = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
export function isBrainStatus(v: unknown): v is BrainStatus {
  return record(v) && !('type' in v) && finite(v.t) && v.t >= 0 &&
    Array.isArray(v.disp_mm) && v.disp_mm.length <= 4096 && v.disp_mm.every(finite) &&
    (v.bpm === null || finite(v.bpm) && v.bpm >= 0) && finite(v.motion) &&
    typeof v.present === 'boolean' && typeof v.state === 'string' &&
    ['EMPTY', 'OK', 'STILL', 'CHECK', 'ALERT'].includes(v.state) &&
    (v.dt_ms === undefined || finite(v.dt_ms) && v.dt_ms > 0) &&
    (v.source === undefined || typeof v.source === 'string') &&
    (v.shaking === undefined || typeof v.shaking === 'boolean');
}
function isAlert(v: unknown): v is AlertEvent {
  return record(v) && v.type === 'alert' && typeof v.reason === 'string' && v.reason.length <= 256 &&
    finite(v.since_s) && v.since_s >= 0 && (v.t === undefined || finite(v.t) && v.t >= 0);
}

// A subscription owns its socket and timers; teardown is safe under React StrictMode.
export function createWebSocketSource(options: LiveOptions = {}): StatusSource {
  return { subscribe(listener) {
    let stopped = false;
    let socket: WebSocket | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let staleTimer: ReturnType<typeof setTimeout> | undefined;
    let delay = 1000;
    const staleAfterMs = options.staleAfterMs ?? 3000;
    const clearStale = () => { clearTimeout(staleTimer); staleTimer = undefined; };
    const armStale = () => {
      clearStale();
      staleTimer = setTimeout(() => { if (!stopped) options.onConnection?.('stale'); }, staleAfterMs);
    };
    function reconnect() {
      if (stopped) return;
      clearStale();
      options.onConnection?.('disconnected');
      retry = setTimeout(connect, delay);
      delay = Math.min(delay * 2, 10000);
    }
    function connect() {
      if (stopped) return;
      options.onConnection?.('connecting');
      try {
        const defaultUrl = new URL('/ws', globalThis.location?.href ?? 'http://localhost:8000');
        defaultUrl.protocol = defaultUrl.protocol === 'https:' ? 'wss:' : 'ws:';
        socket = new WebSocket(options.url ?? defaultUrl.href);
      }
      catch { reconnect(); return; }
      const active = socket;
      const isCurrent = () => !stopped && socket === active;
      active.onopen = () => {
        if (!isCurrent()) return;
        options.onConnection?.('connected');
        armStale(); // An open socket without valid statuses also becomes stale.
      };
      active.onmessage = event => {
        if (!isCurrent() || typeof event.data !== 'string' || event.data.length > 1_000_000) return;
        let value: unknown;
        try { value = JSON.parse(event.data); } catch { return; }
        if (isBrainStatus(value)) {
          delay = 1000;
          options.onConnection?.('connected');
          armStale();
          listener(value);
        } else if (isAlert(value)) {
          options.onAlert?.(value); // Events never masquerade as waveform/status updates.
        } else if (record(value) && value.type === 'board' && typeof value.connected === 'boolean') {
          options.onBoard?.(value.connected); // Does not change WebSocket freshness.
        }
      };
      active.onclose = () => { if (isCurrent()) { socket = undefined; reconnect(); } };
      active.onerror = () => { if (isCurrent()) active.close(); };
    }
    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      clearStale();
      if (socket) {
        socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
        socket.close();
      }
    };
  } };
}
