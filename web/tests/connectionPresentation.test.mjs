import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

const compiled = ts.transpileModule(fs.readFileSync(new URL('../src/data/connectionPresentation.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { connectionPresentation, connectionDiagnostics, diagnosticWebSocketUrl } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));
const fresh = { connection: 'connected', board: true, ready: true, health: { source: 'BoardLink', brain: 'Brain', board_connected: true }, placeholder: false, shaking: false, waveAvailable: true };
const view = changes => connectionPresentation({ ...fresh, ...changes });

test('only a progressing, confirmed BoardLink stream gets a positive reception indicator', () => {
  assert.deepEqual(view({}), { key: 'receiving', title: 'データを受信しています', description: '最新の解析データを表示しています', severity: 'positive', provenance: [] });
  assert.equal(view({ ready: false }).key, 'waiting');
  assert.notEqual(view({ ready: false }).severity, 'positive');
});

test('transport boundaries override previously received true or false board events', () => {
  for (const board of [true, false, null]) {
    assert.equal(view({ connection: 'disconnected', board }).title, 'サーバーに接続できません');
    assert.equal(view({ connection: 'connecting', board }).title, 'サーバーに接続しています');
    assert.notEqual(view({ connection: 'disconnected', board }).severity, 'positive');
  }
  const diagnostics = connectionDiagnostics({ ...fresh, connection: 'disconnected' });
  assert.equal(diagnostics.board, '現在は未確認（前回の受信：接続）');
  assert.equal(diagnostics.freshness, '現在のデータは利用できません');
});

test('explicit sensor disconnection outranks stale stream and health board_connected', () => {
  for (const connection of ['connected', 'stale']) {
    const result = view({ connection, board: false });
    assert.equal(result.key, 'sensor-disconnected');
    assert.equal(result.title, 'センサーが接続されていません');
    assert.equal(result.severity, 'caution');
  }
});

test('stale means no fresh statuses, not a closed socket or confirmed sensor loss', () => {
  assert.equal(view({ connection: 'stale' }).key, 'stale');
  assert.equal(view({ connection: 'stale', board: null }).key, 'stale');
  const diagnostics = connectionDiagnostics({ ...fresh, connection: 'stale' });
  assert.equal(diagnostics.transport, '接続あり・データ更新停止');
  assert.equal(diagnostics.waveform, '利用不可');
});

test('unknown board event remains unknown despite successful health and fresh data', () => {
  const result = view({ board: null });
  assert.equal(result.key, 'unknown-input');
  assert.equal(result.title, 'センサーの接続状態を確認中です');
  assert.notEqual(result.severity, 'positive');
  assert.equal(connectionDiagnostics({ ...fresh, board: null }).board, '未確認');
});

test('missing or unsupported health provenance cannot claim live physical measurements', () => {
  assert.equal(view({ health: null }).key, 'unverified-source');
  assert.equal(view({ health: { ...fresh.health, source: 'other' } }).key, 'unverified-source');
  assert.equal(view({ health: { ...fresh.health, source: null } }).key, 'unverified-source');
  assert.equal(connectionDiagnostics({ ...fresh, health: null }).health, '未取得または取得に失敗');
  // Health false also cannot override an explicit true WebSocket event.
  assert.equal(view({ health: { ...fresh.health, board_connected: false } }).key, 'receiving');
});

test('FileReplay including synthetic input is always labeled as recorded, not a live sensor', () => {
  const health = { ...fresh.health, source: 'FileReplay' };
  const result = view({ health });
  assert.equal(result.key, 'replay');
  assert.equal(result.title, '録画データを再生しています');
  assert.notEqual(result.severity, 'positive');
  for (const change of [{}, { board: false }, { board: null }, { ready: false }, { connection: 'stale' }, { connection: 'connecting' }, { connection: 'disconnected' }]) {
    assert.ok(view({ health, ...change }).provenance.includes('録画データ · 実測ライブではありません'));
  }
  assert.equal(view({ health, board: false }).title, '録画データの入力が停止しています');
});

test('placeholder from status or health remains provisional, including combined replay', () => {
  assert.equal(view({ placeholder: true }).key, 'placeholder');
  assert.equal(view({ health: { ...fresh.health, brain: 'placeholder' } }).key, 'placeholder');
  assert.equal(view({ health: null, placeholder: true }).key, 'placeholder');
  const result = view({ health: { ...fresh.health, source: 'FileReplay' }, placeholder: true });
  assert.equal(result.key, 'replay');
  assert.match(result.description, /暫定処理/);
  assert.equal(result.provenance.length, 2);
  for (const connection of ['connecting', 'disconnected', 'stale']) {
    assert.ok(view({ connection, placeholder: true }).provenance.includes('暫定処理 · placeholder'));
  }
});

test('shaking and empty samples do not present a positive available waveform', () => {
  assert.equal(view({ shaking: true }).severity, 'caution');
  assert.equal(view({ shaking: true }).key, 'wave-unavailable');
  assert.equal(view({ waveAvailable: false }).key, 'wave-unavailable');
  assert.equal(connectionDiagnostics({ ...fresh, shaking: true }).waveform, '揺れを検知・利用不可');
  assert.equal(connectionDiagnostics({ ...fresh, waveAvailable: false }).waveform, 'サンプルなし');
});

test('diagnostic URLs remove credentials/query/fragment and safely handle long or invalid URLs', () => {
  assert.equal(diagnosticWebSocketUrl('wss://user:secret@example.com/ws?token=secret#secret'), 'wss://example.com/ws');
  assert.equal(diagnosticWebSocketUrl('ws://localhost:8000/' + 'a'.repeat(500)), 'ws://localhost:8000/' + 'a'.repeat(500));
  assert.equal(diagnosticWebSocketUrl('not a URL'), '未確認');
});

test('presentation is pure and never changes transport, readiness or health input', () => {
  const health = Object.freeze({ ...fresh.health });
  const input = Object.freeze({ ...fresh, health });
  connectionPresentation(input); connectionDiagnostics(input);
  assert.equal(input.health, health);
  assert.equal(input.connection, 'connected');
  assert.equal(input.ready, true);
});
