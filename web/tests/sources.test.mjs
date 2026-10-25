import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

async function load(relative) {
  const code = ts.transpileModule(fs.readFileSync(new URL(relative, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
}
const { createWebSocketSource, isBrainStatus } = await load('../src/data/webSocketSource.ts');
const { appendWaveform } = await load('../src/data/waveform.ts');
const { createMockSource } = await load('../src/data/mockSource.ts');
const { deriveLiveView } = await load('../src/data/liveView.ts');
const { resolveEndpoints, parseHealth, watchHealth } = await load('../src/data/health.ts');
const { createNotificationState, reduceNotifications, notificationPresentation } = await load('../src/data/notifications.ts');
const status = { t: 12345, disp_mm: [0.81, 0.95, 1.02, 1.07], bpm: 16.2, motion: 120, present: true, state: 'OK' };

function sandbox(run) {
  const originals = { WebSocket: globalThis.WebSocket, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout, window: globalThis.window };
  const sockets = [];
  const tasks = new Map();
  let now = 0, id = 0;
  globalThis.setTimeout = (fn, delay) => { tasks.set(++id, { fn, due: now + delay }); return id; };
  globalThis.clearTimeout = key => tasks.delete(key);
  class Socket {
    constructor(url) { this.url = url; sockets.push(this); }
    open() { this.onopen?.({}); }
    message(value) { this.onmessage?.({ data: typeof value === 'string' ? value : JSON.stringify(value) }); }
    close() { this.closed = true; this.onclose?.({}); }
  }
  globalThis.WebSocket = Socket;
  function tick(ms) {
    const end = now + ms;
    for (;;) {
      const entry = [...tasks].sort((a, b) => a[1].due - b[1].due)[0];
      if (!entry || entry[1].due > end) break;
      now = entry[1].due; tasks.delete(entry[0]); entry[1].fn();
    }
    now = end;
  }
  try { run({ sockets, tasks, tick }); } finally {
    Object.assign(globalThis, originals);
  }
}

test('validates status fields and rejects malformed/invalid messages', () => {
  assert.ok(isBrainStatus(status));
  assert.ok(isBrainStatus({ ...status, bpm: null }));
  for (const value of [null, [], {}, { ...status, t: '123' }, { ...status, t: -1 },
    { ...status, disp_mm: [NaN] }, { ...status, dt_ms: 0 }, { ...status, dt_ms: '60' }, { ...status, shaking: 1 }, { ...status, bpm: Infinity },
    { ...status, state: 'UNKNOWN' }, { ...status, present: 1 }, { ...status, type: 'alert' }]) {
    assert.equal(isBrainStatus(value), false);
  }
});

test('routes statuses and alert events separately; invalid traffic cannot refresh stale status', () => sandbox(({ sockets, tick }) => {
  const states = [], messages = [], alerts = [];
  const stop = createWebSocketSource({ onConnection: s => states.push(s), onAlert: e => alerts.push(e) }).subscribe(s => messages.push(s));
  assert.equal(states.at(-1), 'connecting');
  assert.equal(sockets[0].url, 'ws://localhost:8000/ws');
  sockets[0].open();
  assert.equal(states.at(-1), 'connected');
  sockets[0].message(status);
  tick(2000);
  sockets[0].message('{bad');
  sockets[0].message({ ...status, disp_mm: ['bad'] });
  sockets[0].message({ type: 'alert', reason: 'no_breath', since_s: 10, t: 23456 });
  sockets[0].message({ type: 'alert', reason: 'bad', since_s: '10' });
  sockets[0].onmessage({ data: new ArrayBuffer(1) });
  tick(1000);
  assert.equal(states.at(-1), 'stale');
  assert.equal(messages.length, 1);
  assert.equal(alerts.length, 1);
  sockets[0].message({ ...status, t: 12545 });
  assert.equal(states.at(-1), 'connected');
  assert.equal(messages.length, 2);
  stop();
}));

// Actual Brain events; server.handle_output supplies since_s=0 when absent.
const notificationEvents = [
  { type: 'alert', reason: 'no_breath', since_s: 10 },
  { type: 'alert', reason: 'left_bed', since_s: 0 },
  { type: 'info', reason: 'woke_up', since_s: 0 },
  { type: 'info', reason: 'fast_breathing', since_s: 30, bpm: 54.2 },
];

test('all four server notifications route by category and preserve optional BPM and t', () => sandbox(({ sockets }) => {
  const alerts = [], infos = [], statuses = [];
  const stop = createWebSocketSource({ onAlert: e => alerts.push(e), onInfo: e => infos.push(e) }).subscribe(s => statuses.push(s));
  sockets[0].open();
  for (const event of notificationEvents) sockets[0].message(event);
  assert.deepEqual(alerts, notificationEvents.slice(0, 2));
  assert.deepEqual(infos, notificationEvents.slice(2));
  const extended = notificationEvents.map(event => ({ ...event, t: 12345.5, bpm: 0 }));
  for (const event of extended) sockets[0].message(event);
  assert.deepEqual(alerts.slice(2), extended.slice(0, 2));
  assert.deepEqual(infos.slice(2), extended.slice(2));
  // BPM is optional even for fast_breathing; the server's category still applies.
  sockets[0].message({ type: 'info', reason: 'fast_breathing', since_s: 0 });
  assert.equal(infos.at(-1).bpm, undefined);
  assert.equal(statuses.length, 0);
  stop();
}));

test('malformed and unsupported notification payloads are ignored in both categories', () => sandbox(({ sockets }) => {
  const alerts = [], infos = [], statuses = [];
  const stop = createWebSocketSource({ onAlert: e => alerts.push(e), onInfo: e => infos.push(e) }).subscribe(s => statuses.push(s));
  sockets[0].open();
  const invalid = [null, [], {}, { type: 'info' },
    // PR30's CHECK nudge is push-only, not a new WebSocket event category.
    { type: 'info', reason: 'check', since_s: 0 },
    { type: 'alert', reason: 'check', since_s: 0 },
    { type: 'alert', reason: 'woke_up', since_s: 0 },
    { type: 'info', reason: 'no_breath', since_s: 10 },
    { type: 'info', reason: 'unsupported', since_s: 0 },
    { type: 'alert', reason: 'unsupported', since_s: 0 },
    { type: 'notification', reason: 'left_bed', since_s: 0 }];
  for (const event of notificationEvents) {
    const missingDuration = { ...event }; delete missingDuration.since_s;
    invalid.push(missingDuration);
    for (const since_s of [-1, '10', null, true, Infinity]) invalid.push({ ...event, since_s });
    for (const bpm of [-1, '54', null, true, Infinity]) invalid.push({ ...event, bpm });
    for (const t of [-1, '12345', null, true, Infinity]) invalid.push({ ...event, t });
  }
  for (const event of invalid) sockets[0].message(event);
  sockets[0].message('{malformed JSON');
  sockets[0].message('{"type":"info","reason":"fast_breathing","since_s":0,"bpm":1e999}');
  assert.deepEqual(alerts, []); assert.deepEqual(infos, []); assert.deepEqual(statuses, []);
  sockets[0].message(status); assert.equal(statuses.length, 1);
  stop();
}));

test('notification receipt never changes status or waveform samples', () => sandbox(({ sockets }) => {
  let current = null, points = [], statusCount = 0;
  const alerts = [], infos = [];
  const stop = createWebSocketSource({ onAlert: e => alerts.push(e), onInfo: e => infos.push(e) }).subscribe(next => {
    points = appendWaveform(points, current, next, 50); current = next; statusCount++;
  });
  sockets[0].open(); sockets[0].message(status);
  const snapshot = structuredClone({ current, points });
  for (const event of notificationEvents) sockets[0].message(event);
  assert.equal(statusCount, 1);
  assert.deepEqual({ current, points }, snapshot);
  assert.equal(alerts.length, 2); assert.equal(infos.length, 2);
  stop();
}));

test('each notification category/reason leaves the status stale deadline unchanged', () => {
  for (const event of notificationEvents) sandbox(({ sockets, tick }) => {
    const connections = [];
    const stop = createWebSocketSource({ onConnection: state => connections.push(state), onAlert: () => {}, onInfo: () => {} }).subscribe(() => {});
    sockets[0].open(); sockets[0].message(status);
    tick(2999); sockets[0].message(event);
    assert.equal(connections.at(-1), 'connected');
    tick(1); assert.equal(connections.at(-1), 'stale');
    sockets[0].message(event); assert.equal(connections.at(-1), 'stale');
    sockets[0].message({ ...status, t: status.t + 200 });
    assert.equal(connections.at(-1), 'connected');
    stop();
  });
});

test('late notifications from closed or unsubscribed sockets are ignored during reconnect/cleanup', () => sandbox(({ sockets, tasks, tick }) => {
  const alerts = [], infos = [];
  const stop = createWebSocketSource({ onAlert: e => alerts.push(e), onInfo: e => infos.push(e) }).subscribe(() => {});
  sockets[0].open();
  const oldCallback = sockets[0].onmessage;
  sockets[0].close(); tick(1000);
  assert.equal(sockets.length, 2);
  sockets[1].open();
  const currentCallback = sockets[1].onmessage;
  for (const event of notificationEvents) oldCallback({ data: JSON.stringify(event) });
  assert.equal(alerts.length + infos.length, 0);
  for (const event of notificationEvents) sockets[1].message(event);
  assert.equal(alerts.length, 2); assert.equal(infos.length, 2);
  stop();
  for (const event of notificationEvents) currentCallback({ data: JSON.stringify(event) });
  assert.equal(alerts.length, 2); assert.equal(infos.length, 2);
  assert.equal(tasks.size, 0); assert.equal(sockets[1].closed, true);
}));

test('wire lifecycle keeps session history across reconnect and never feeds events into wave/status', () => sandbox(({ sockets, tasks, tick }) => {
  let notifications = createNotificationState(), current = null, points = [], statusCount = 0;
  const dispatch = action => { notifications = reduceNotifications(notifications, action); };
  const receive = event => dispatch({ type: 'event', event, receivedAt: 1234567890 });
  const source = createWebSocketSource({ onConnection: value => dispatch({ type: 'connection', value }),
    onBoard: connected => dispatch({ type: 'board', connected }), onAlert: receive, onInfo: receive });
  const stop = source.subscribe(next => {
    dispatch({ type: 'status', status: next });
    points = appendWaveform(points, current, next, 50); current = next; statusCount++;
  });
  sockets[0].open(); sockets[0].message({ type: 'board', connected: true });
  sockets[0].message(status); sockets[0].message({ ...status, t: status.t + 200 });
  const wave = structuredClone({ current, points, statusCount });
  for (const event of notificationEvents) sockets[0].message(event);
  assert.deepEqual({ current, points, statusCount }, wave);
  assert.equal(notifications.history.length, 4);
  assert.equal(notificationPresentation(notifications).current.length, 2);
  tick(3000); assert.equal(notifications.connection, 'stale');
  assert.ok(notificationPresentation(notifications).current.every(warning => warning.indication === 'unknown'));
  sockets[0].close(); tick(1000); sockets[1].open();
  sockets[1].message({ type: 'board', connected: true });
  sockets[1].message({ ...status, t: status.t + 200, state: 'STILL' });
  assert.equal(notifications.ready, false);
  assert.equal(notificationPresentation(notifications).current.length, 2);
  sockets[1].message({ ...status, t: status.t + 400, state: 'STILL' });
  assert.equal(notificationPresentation(notifications).current.length, 0);
  assert.equal(notifications.history.length, 4);
  const late = sockets[1].onmessage;
  stop(); late({ data: JSON.stringify(notificationEvents[0]) });
  assert.equal(notifications.history.length, 4); assert.equal(tasks.size, 0);
  // StrictMode effect cleanup/re-subscribe retains the reducer's session state.
  const stopAgain = source.subscribe(() => {});
  assert.equal(notifications.history.length, 4); stopAgain();
  assert.equal(tasks.size, 0);
}));

test('stale detection also applies to an open connection that never sends a status', () => sandbox(({ sockets, tick }) => {
  let connection;
  const stop = createWebSocketSource({ onConnection: s => { connection = s; } }).subscribe(() => {});
  sockets[0].open(); tick(3000);
  assert.equal(connection, 'stale');
  stop();
}));

test('board events are independent; unsupported source messages cannot refresh freshness', () => sandbox(({ sockets, tick }) => {
  const boards = [], statuses = [], states = [];
  const stop = createWebSocketSource({ onBoard: s => boards.push(s), onConnection: s => states.push(s) }).subscribe(s => statuses.push(s));
  sockets[0].open();
  sockets[0].message({ type: 'source', simulated: true });
  sockets[0].message({ type: 'board', connected: false });
  assert.deepEqual(boards, [false]);
  assert.equal(states.at(-1), 'connected');
  assert.equal(statuses.length, 0);
  sockets[0].message({ type: 'board', connected: 'true' });
  sockets[0].message({ type: 'board', connected: true });
  assert.deepEqual(boards, [false, true]);
  tick(3000); assert.equal(states.at(-1), 'stale');
  sockets[0].message({ ...status, disp_mm: [], source: 'placeholder', shaking: true, dt_ms: 60 });
  assert.equal(statuses.length, 1); assert.equal(states.at(-1), 'connected');
  stop();
}));

test('real placeholder status, empty arrays, and optional fields validate', () => {
  assert.ok(isBrainStatus({ ...status, bpm: null, disp_mm: [], dt_ms: 60, source: 'placeholder', shaking: true, chest_cm: null }));
  assert.ok(isBrainStatus({ ...status, disp_mm: [] }));
});

test('dt_ms has priority; empty/shaking statuses clear history without fabricated samples', () => {
  const actual = { ...status, dt_ms: 60 };
  const points = appendWaveform([], null, actual, 50);
  assert.equal(points.length, 4);
  assert.ok(Math.abs(points[0].time - (status.t - 180) / 1000) < 1e-8);
  assert.deepEqual(appendWaveform(points, actual, { ...actual, t: status.t + 120, disp_mm: [] }, null), []);
  assert.deepEqual(appendWaveform(points, actual, { ...actual, t: status.t + 120, shaking: true }, null), []);
  // No interpolation across an unavailable batch when dt_ms is absent.
  assert.deepEqual(appendWaveform([], { ...status, disp_mm: [] }, { ...status, t: status.t + 120 }, null), []);
});

test('disconnected/stale/cached/shaking readings are unavailable; placeholder never reports BPM', () => {
  const health = { source: 'FileReplay', brain: 'placeholder', board_connected: true };
  const replay = deriveLiveView({ ...status, source: 'placeholder' }, 'connected', true, true, health);
  assert.equal(replay.bpm, null); assert.equal(replay.placeholder, true); assert.equal(replay.replay, true);
  assert.equal(replay.waveAvailable, true);
  for (const [connection, board, ready] of [['connected', false, true], ['disconnected', true, true], ['stale', true, true], ['connected', true, false]]) {
    const view = deriveLiveView(status, connection, board, ready, health);
    assert.equal(view.status, null); assert.equal(view.bpm, null); assert.equal(view.waveAvailable, false);
  }
  for (const next of [{ ...status, shaking: true }, { ...status, disp_mm: [] }]) {
    const view = deriveLiveView(next, 'connected', true, true, null);
    assert.equal(view.waveAvailable, false); assert.equal(view.bpm, null);
  }
  assert.equal(deriveLiveView(status, 'connected', true, true, null).replay, false);
});

test('endpoint resolution follows page host, TLS, IPv6 and optional development overrides', () => {
  assert.deepEqual(resolveEndpoints('http://192.168.1.20:8000/'), { ws: 'ws://192.168.1.20:8000/ws', health: 'http://192.168.1.20:8000/api/health' });
  assert.equal(resolveEndpoints('https://ibuki.example/page').ws, 'wss://ibuki.example/ws');
  assert.equal(resolveEndpoints('http://[::1]:8000/').ws, 'ws://[::1]:8000/ws');
  assert.equal(resolveEndpoints('http://localhost:5173', 'ws://localhost:8000/ws').health, 'http://localhost:8000/api/health');
  assert.equal(resolveEndpoints('http://localhost:5173', undefined, '/health-test').health, 'http://localhost:5173/health-test');
});

test('health polling validates data, handles HTTP/network/JSON failure, and cleans up', async () => {
  const health = { source: 'BoardLink', brain: 'placeholder', board_connected: true };
  assert.deepEqual(parseHealth(health), health); assert.equal(parseHealth({}), null);
  for (const fetcher of [async () => ({ ok: true, json: async () => health }),
    async () => ({ ok: false }), async () => { throw new Error('offline'); },
    async () => ({ ok: true, json: async () => { throw new Error('invalid JSON'); } })]) {
    const received = [];
    const stop = watchHealth('/api/health', h => received.push(h), fetcher);
    await new Promise(resolve => setImmediate(resolve));
    stop(); assert.equal(received.length, 1);
    assert.ok(received[0] === null || received[0].source === 'BoardLink');
  }
  let signal, finish;
  const received = [];
  const stop = watchHealth('/api/health', h => received.push(h), async (_url, options) => {
    signal = options.signal;
    await new Promise(resolve => { finish = resolve; });
    return { ok: true, json: async () => health };
  });
  stop(); assert.equal(signal.aborted, true); finish();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(received.length, 0);
});

test('reconnects with backoff; cleans sockets/timers and ignores late callbacks', () => sandbox(({ sockets, tasks, tick }) => {
  const messages = [];
  const source = createWebSocketSource();
  const stop = source.subscribe(s => messages.push(s));
  const late = sockets[0].onmessage;
  sockets[0].close();
  tick(999); assert.equal(sockets.length, 1);
  tick(1); assert.equal(sockets.length, 2);
  late({ data: JSON.stringify(status) }); assert.equal(messages.length, 0);
  sockets[1].onerror();
  tick(1999); assert.equal(sockets.length, 2);
  tick(1); assert.equal(sockets.length, 3);
  sockets[2].open(); sockets[2].message(status);
  assert.equal(messages.length, 1);
  stop(); assert.equal(tasks.size, 0); assert.equal(sockets[2].closed, true);
  tick(20000); assert.equal(sockets.length, 3);
  // React StrictMode: a new subscription after cleanup is independent.
  const stopAgain = source.subscribe(() => {});
  assert.equal(sockets.length, 4); stopAgain(); assert.equal(tasks.size, 0);
}));

test('socket constructor failures retry and cleanup cancels pending retries', () => sandbox(({ tasks, tick }) => {
  let attempts = 0;
  globalThis.WebSocket = class { constructor() { attempts++; throw new Error('unavailable'); } };
  const stop = createWebSocketSource().subscribe(() => {});
  tick(1000); assert.equal(attempts, 2);
  stop(); assert.equal(tasks.size, 0);
}));

test('waveform uses batch timing or configured spacing, handles resets/gaps and bounds history', () => {
  assert.deepEqual(appendWaveform([], null, status, null), []);
  const next = { ...status, t: status.t + 240 };
  const points = appendWaveform([], status, next, null);
  assert.equal(points.length, 4);
  assert.ok(Math.abs(points[1].time - points[0].time - 0.060) < 1e-8);
  const configured = appendWaveform([], null, status, 75);
  assert.ok(Math.abs(configured[1].time - configured[0].time - 0.075) < 1e-8);
  assert.deepEqual(appendWaveform(points, next, status, null), []);
  assert.deepEqual(appendWaveform(points, next, { ...status, t: 30000 }, null), []);
  let history = [], previous = null;
  for (let t = 200; t <= 40000; t += 200) {
    const message = { ...status, t };
    history = appendWaveform(history, previous, message, 50); previous = message;
  }
  assert.ok(history.length <= 4096);
  assert.ok(history.every(p => p.time >= 10 && p.time <= 40));
});

test('DEMO retains all five states and the exact status message structure', () => sandbox(() => {
  let tick, cleared = false;
  globalThis.window = { setInterval(fn, ms) { assert.equal(ms, 200); tick = fn; return 1; }, clearInterval() { cleared = true; } };
  const source = createMockSource();
  let received;
  const stop = source.subscribe(s => { received = s; });
  for (const state of ['EMPTY', 'OK', 'STILL', 'CHECK', 'ALERT']) {
    source.setState(state); tick();
    assert.ok(isBrainStatus(received));
    assert.deepEqual(Object.keys(received).sort(), Object.keys(status).sort());
    assert.equal(received.state, state);
    assert.equal(received.present, state !== 'EMPTY');
    assert.equal(received.bpm, ['OK', 'STILL'].includes(state) ? 16.2 : null);
  }
  stop(); assert.ok(cleared);
}));
