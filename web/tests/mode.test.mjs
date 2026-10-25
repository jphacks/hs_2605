import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

const compiled = ts.transpileModule(fs.readFileSync(new URL('../src/data/mode.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { selectDashboardMode, createModeSource } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));

test('ordinary development URLs default to LIVE', () => {
  assert.equal(selectDashboardMode(true, ''), 'LIVE');
  assert.equal(selectDashboardMode(true, '?other=1'), 'LIVE');
});

test('only explicit demo=1 enables development DEMO', () => {
  assert.equal(selectDashboardMode(true, '?demo=1'), 'DEMO');
  assert.equal(selectDashboardMode(true, '?other=x&demo=1'), 'DEMO');
  for (const query of ['?demo', '?demo=0', '?demo=true', '?demo=01', '?demo=1x', '?DEMO=1', '?other=demo%3D1', '?demo=0&demo=1']) {
    assert.equal(selectDashboardMode(true, query), 'LIVE');
  }
});

test('production ignores every DEMO request and saved preference is not an input', () => {
  for (const query of ['', '?demo=1', '?other=x&demo=1', '?demo=true', '?DEMO=1']) {
    assert.equal(selectDashboardMode(false, query), 'LIVE');
  }
});

test('disconnected LIVE never constructs or subscribes to a mock source', () => {
  let liveCreated = 0, liveSubscribed = 0, liveCleaned = 0, mocks = 0;
  const live = { subscribe() { liveSubscribed++; return () => liveCleaned++; } };
  const source = createModeSource(selectDashboardMode(true, ''), {
    live() { liveCreated++; return live; },
    demo() { mocks++; throw Error('LIVE must not use mocks'); },
  });
  const stop = source.subscribe(() => {});
  stop();
  assert.equal(liveCreated, 1); assert.equal(liveSubscribed, 1); assert.equal(liveCleaned, 1); assert.equal(mocks, 0);
});

test('LIVE initialization failures never select DEMO as fallback', () => {
  let mocks = 0;
  assert.throws(() => createModeSource('LIVE', {
    live() { throw Error('unavailable'); }, demo() { mocks++; return {}; },
  }), /unavailable/);
  assert.equal(mocks, 0);
});

test('developer DEMO never constructs a WebSocket source', () => {
  let liveCreated = 0, mockSubscribed = 0;
  const source = createModeSource(selectDashboardMode(true, '?demo=1'), {
    live() { liveCreated++; throw Error('DEMO must not connect'); },
    demo() { return { subscribe() { mockSubscribed++; return () => {}; } }; },
  });
  source.subscribe(() => {})();
  assert.equal(liveCreated, 0); assert.equal(mockSubscribed, 1);
});
