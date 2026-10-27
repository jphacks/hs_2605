import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

const code = ts.transpileModule(fs.readFileSync(new URL('../src/chartPresentation.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { displayDomain, windowTicks, windowTickLabel } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const points = values => values.map((displacement, time) => ({ time, displacement }));

test('empty, zero and missing samples use a display floor without creating readings', () => {
  const missing = Object.freeze([Object.freeze({ time: 1, displacement: null, idle: 0 })]);
  assert.deepEqual(displayDomain([]), [-2, 2]);
  assert.deepEqual(displayDomain(points([0])), [-2, 2]);
  assert.deepEqual(displayDomain(missing), [-2, 2]);
  assert.deepEqual(missing, [{ time: 1, displacement: null, idle: 0 }]);
});

test('the 32/min fixture retains all 16 cycles, values and timestamps', () => {
  const samples = Object.freeze(Array.from({ length: 501 }, (_, index) => Object.freeze({
    time: 170 + index * .06, displacement: .9 * Math.sin(index * .06 * 2 * Math.PI * 32 / 60),
  })));
  const original = structuredClone(samples);
  const risingCrossings = samples.filter((sample, index) => index && samples[index - 1].displacement <= 0 && sample.displacement > 0).length;
  assert.equal(risingCrossings, 16);
  assert.deepEqual(displayDomain(samples), [-2, 2]);
  assert.deepEqual(samples, original);
  assert.equal(samples.length, 501);
});

test('zero-centred tiers stay stable for small changes and leave visible headroom', () => {
  for (const peak of [.01, .4, .9, 1.6, 1.8]) assert.deepEqual(displayDomain(points([-peak, peak])), [-2, 2]);
  assert.deepEqual(displayDomain(points([1.81])), [-4, 4]);
  assert.deepEqual(displayDomain(points([-3.2, .5])), [-4, 4]);
  assert.deepEqual(displayDomain(points([-4.3, 1])), [-8, 8]);
});

test('larger positive/negative excursions and the missing-data trace are never clipped', () => {
  for (const values of [[-12, 8], [-.1, 99], [0, -101], [1e200, -1e200]]) {
    const domain = displayDomain(points(values));
    assert.ok(domain.every(Number.isFinite));
    assert.equal(domain[0], -domain[1]);
    for (const value of values) assert.ok(value >= domain[0] && value <= domain[1]);
  }
  assert.deepEqual(displayDomain([{ time: 1, displacement: null, idle: 3 }]), [-4, 4]);
});

test('large finite values do not overflow the display range', () => {
  assert.deepEqual(displayDomain(points([Number.MAX_VALUE])), [-Number.MAX_VALUE, Number.MAX_VALUE]);
});

test('0–30 second labels preserve the absolute rolling-window coordinates', () => {
  for (const end of [30, 200, 12345.375]) {
    const start = end - 30;
    const ticks = windowTicks(start, end);
    assert.equal(ticks[0], start);
    assert.equal(ticks.at(-1), end);
    assert.deepEqual(ticks.map(tick => windowTickLabel(tick, start)), ['0秒', '5秒', '10秒', '15秒', '20秒', '25秒', '30秒']);
    assert.ok(ticks.every((tick, index) => !index || Math.abs(tick - ticks[index - 1] - 5) < 1e-8));
  }
});
