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
const { parseHealth } = await load('../src/data/health.ts');

test('health carries the siren state only when the server sends it', () => {
  const base = { source: 'BoardLink', brain: 'brain', board_connected: true };
  assert.deepEqual(parseHealth(base), base);
  assert.equal(parseHealth({ ...base, siren: true }).siren, true);
  assert.equal(parseHealth({ ...base, siren: false }).siren, false);
  assert.equal(parseHealth({ ...base, siren: 'yes' }).siren, undefined);
});
