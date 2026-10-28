// What the board says about itself (server's BoardHealth). Every part is optional: older
// servers and firmware send less, and a missing part is simply not shown.
export interface BoardHealth {
  restarts?: number;
  last_restart?: { reason: string; unix_s: number } | null;
  climate?: { board_temp_c: number; humidity_pct: number } | null;
  moves?: number;
  last_move?: { mg: number | null; unix_s: number } | null;
  board_ai?: { state: string; event: string; unix_s: number } | null;
  wifi?: { connected: boolean; text: string; alerts_sent: number; unix_s: number } | null;
}
export interface ServerHealth { source: string | null; brain: string; board_connected: boolean; siren?: boolean; pairing?: boolean; board_health?: BoardHealth }
export function parseHealth(value: unknown): ServerHealth | null {
  if (typeof value !== 'object' || value === null) return null;
  const v = value as Record<string, unknown>;
  if (!((typeof v.source === 'string' || v.source === null) && typeof v.brain === 'string' && typeof v.board_connected === 'boolean')) return null;
  return { source: v.source, brain: v.brain, board_connected: v.board_connected,
    ...(typeof v.siren === 'boolean' ? { siren: v.siren } : {}),
    ...(typeof v.pairing === 'boolean' ? { pairing: v.pairing } : {}),
    ...(typeof v.board_health === 'object' && v.board_health !== null ? { board_health: v.board_health as BoardHealth } : {}) };
}
export function resolveEndpoints(pageUrl: string, wsOverride?: string, healthOverride?: string) {
  const ws = new URL(wsOverride || '/ws', pageUrl);
  if (ws.protocol === 'http:') ws.protocol = 'ws:';
  if (ws.protocol === 'https:') ws.protocol = 'wss:';
  if (!['ws:', 'wss:'].includes(ws.protocol)) throw new Error('WebSocket URL must use ws/wss');
  const base = new URL(ws.href);
  base.protocol = ws.protocol === 'wss:' ? 'https:' : 'http:';
  return { ws: ws.href, health: new URL(healthOverride || '/api/health', base).href };
}
// Abort on mode change, timeout each request, never overlap requests or retain a
// formerly confirmed provenance after health fails. Health cannot prove accuracy.
export function watchHealth(url: string, listener: (health: ServerHealth | null) => void, fetcher: typeof fetch = fetch) {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let active: AbortController | undefined;
  async function poll() {
    active = new AbortController();
    const timeout = setTimeout(() => active?.abort(), 2000);
    try {
      const response = await fetcher(url, { signal: active.signal, cache: 'no-store' });
      const health = response.ok ? parseHealth(await response.json()) : null;
      if (!stopped) listener(health);
    } catch { if (!stopped) listener(null); }
    finally { clearTimeout(timeout); if (!stopped) timer = setTimeout(poll, 3000); }
  }
  void poll();
  return () => { stopped = true; clearTimeout(timer); active?.abort(); };
}
