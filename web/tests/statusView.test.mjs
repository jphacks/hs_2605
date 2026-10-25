import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

const code = ts.transpileModule(fs.readFileSync(new URL('../src/data/statusView.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { describeStatus } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const view = (state, extra = {}) => describeStatus({ state, shaking: false, placeholder: false, waiting: '接続を待っています', ...extra });

test('each state is shown in the same plain words as the README table and the phone', () => {
  // was "Brain の受信状態：STILL": the code word, not something a parent (or a judge) can read
  assert.equal(view('STILL').title, '眠っています');
  assert.equal(view('OK').title, '起きています');
  assert.equal(view('CHECK').title, '呼吸を確認中');
  assert.equal(view('ALERT').title, '呼吸が確認できません');
  assert.equal(view('EMPTY').title, 'ベッドにいません');
  assert.deepEqual(['STILL', 'OK', 'CHECK', 'ALERT', 'EMPTY'].map(s => view(s).tone), ['still', 'ok', 'check', 'alert', 'empty']);
});

test('shaking, waiting and placeholder are said plainly; an alarm stays on screen while shaking', () => {
  assert.equal(view('STILL', { shaking: true }).title, 'センサーが揺れています');
  assert.match(view('STILL', { shaking: true }).detail, /呼吸が止まったという意味ではありません/);
  assert.equal(view('ALERT', { shaking: true }).title, '呼吸が確認できません');
  assert.deepEqual(view(null), { tone: 'wait', title: 'データを待っています', detail: '接続を待っています' });
  assert.equal(view('STILL', { placeholder: true }).title, '呼吸は判定していません');
});
