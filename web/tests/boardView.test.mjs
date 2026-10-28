import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

const compiled = ts.transpileModule(fs.readFileSync(new URL('../src/data/boardView.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { boardItems } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));
const base = { source: 'BoardLink', brain: 'state_model', board_connected: true };
const keys = items => items.map(i => i.key);

test('an older server without board_health shows only who decides', () => {
  assert.deepEqual(keys(boardItems(base)), ['brain']);
  assert.equal(boardItems(base)[0].value, 'AI モデル');
  assert.deepEqual(boardItems(null), []);
});

test('the board AI, Wi-Fi, climate and board state in plain Japanese', () => {
  const items = boardItems({ ...base, pairing: true, board_health: {
    restarts: 0, last_restart: null, moves: 0, last_move: null, climate: { board_temp_c: 31.24, humidity_pct: 40.6 },
    board_ai: { state: 'breathing', event: '', unix_s: 100 }, wifi: { connected: true, text: 'alert sent to the phone', alerts_sent: 2, unix_s: 100 } } }, 200);
  assert.deepEqual(keys(items), ['brain', 'board-ai', 'wifi', 'climate', 'board', 'pairing']);
  const by = Object.fromEntries(items.map(i => [i.key, i]));
  assert.equal(by['board-ai'].value, '呼吸しています');
  assert.equal(by.wifi.value, '接続中・通知 2 回送信');
  assert.equal(by.climate.value, '31.2℃・41%');
  assert.equal(by.board.value, '正常');
});

test('a recent restart or move is mentioned, an old one is not; never more than caution', () => {
  const restart = { restarts: 1, last_restart: { reason: 'fifo overflow', unix_s: 1000 }, moves: 0, last_move: null };
  assert.match(boardItems({ ...base, board_health: restart }, 1100).at(-1).value, /再起動しました.*fifo overflow/);
  assert.equal(boardItems({ ...base, board_health: restart }, 1000 + 11 * 60).at(-1).value, '正常');
  const moved = { restarts: 0, moves: 1, last_move: { mg: 77, unix_s: 1000 } };
  assert.match(boardItems({ ...base, board_health: moved }, 1010).at(-1).value, /動かされました/);
  assert.ok(boardItems({ ...base, board_health: moved }, 1010).every(i => i.tone !== 'alert'));
});
