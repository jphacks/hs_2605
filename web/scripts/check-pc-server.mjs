// Actual frontend source + team server, not a replacement Python gateway.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

async function load(path) {
  const code = ts.transpileModule(fs.readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
}
const { createWebSocketSource } = await load('../src/data/webSocketSource.ts');
const { appendWaveform } = await load('../src/data/waveform.ts');
const { deriveLiveView } = await load('../src/data/liveView.ts');
const url = new URL(process.argv[2] || 'http://127.0.0.1:8000');
const health = await (await fetch(new URL('/api/health', url))).json();
assert.equal(health.source, 'FileReplay');
assert.equal(health.brain, 'placeholder');
assert.equal(health.ntfy, false);
const ws = new URL('/ws', url); ws.protocol = 'ws:';
let board = null, previous = null, points = [], count = 0;
let empty = false, shaking = false, waveform = false;
const requireShake = process.argv.includes('--require-shake');
await new Promise((resolve, reject) => {
  const timeout = setTimeout(() => finish(new Error('Timed out waiting for team replay statuses')), 18000);
  const finish = error => { clearTimeout(timeout); stop(); error ? reject(error) : resolve(); };
  const stop = createWebSocketSource({ url: ws.href, onBoard: value => { board = value; } }).subscribe(status => {
    try {
      count++;
      assert.equal(status.source, 'placeholder');
      assert.equal(status.bpm, null);
      assert.ok(status.dt_ms > 0);
      points = appendWaveform(points, previous, status, null);
      previous = status;
      const view = deriveLiveView(status, 'connected', board, true, health);
      assert.equal(view.placeholder, true);
      assert.equal(view.replay, true);
      assert.equal(view.bpm, null);
      empty ||= status.disp_mm.length === 0;
      shaking ||= status.shaking === true;
      waveform ||= points.length > 0;
      if (status.shaking || status.disp_mm.length === 0) assert.equal(points.length, 0);
      assert.equal(deriveLiveView(status, 'connected', false, true, health).waveAvailable, false);
      if (count >= 20 && waveform && (!requireShake || empty && shaking)) {
        assert.equal(board, true);
        finish();
      }
    } catch (error) { finish(error); }
  });
});
console.log(`PASS team server -> frontend: ${count} statuses; FileReplay, placeholder, dt_ms, board events, waveform; empty=${empty}, shaking=${shaking}; ntfy off`);
