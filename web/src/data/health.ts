export interface ServerHealth { source: string | null; brain: string; board_connected: boolean; siren?: boolean }
export function parseHealth(value: unknown): ServerHealth | null {
  if (typeof value !== 'object' || value === null) return null;
  const v = value as Record<string, unknown>;
  return (typeof v.source === 'string' || v.source === null) && typeof v.brain === 'string' && typeof v.board_connected === 'boolean'
    ? { source: v.source, brain: v.brain, board_connected: v.board_connected, ...(typeof v.siren === 'boolean' ? { siren: v.siren } : {}) } : null;
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
