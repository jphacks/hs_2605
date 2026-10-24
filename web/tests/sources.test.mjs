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
    { ...status, disp_mm: [NaN] }, { ...status, disp_mm: [] }, { ...status, bpm: Infinity },
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

test('stale detection also applies to an open connection that never sends a status', () => sandbox(({ sockets, tick }) => {
  let connection;
  const stop = createWebSocketSource({ onConnection: s => { connection = s; } }).subscribe(() => {});
  sockets[0].open(); tick(3000);
  assert.equal(connection, 'stale');
  stop();
}));

test('source metadata labels simulated transport without becoming status or refreshing freshness', () => sandbox(({ sockets, tick }) => {
  const sources = [], statuses = [], states = [];
  const stop = createWebSocketSource({ onSource: s => sources.push(s), onConnection: s => states.push(s) }).subscribe(s => statuses.push(s));
  sockets[0].open();
  const source = { type: 'source', name: 'IBUKI SIMULATED Brain', simulated: true, timestamp_unit: 'ms', sample_interval_ms: 60 };
  sockets[0].message(source);
  assert.deepEqual(sources, [source]);
  assert.equal(statuses.length, 0);
  sockets[0].message({ ...source, simulated: 'true' });
  sockets[0].message({ ...source, timestamp_unit: 'seconds' });
  sockets[0].message({ ...source, sample_interval_ms: 0 });
  assert.equal(sources.length, 1);
  tick(3000); assert.equal(states.at(-1), 'stale');
  sockets[0].message(status); assert.equal(statuses.length, 1);
  stop();
}));

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
