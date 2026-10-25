import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

const code = ts.transpileModule(fs.readFileSync(new URL('../src/data/notifications.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { createNotificationState, reduceNotifications: reduce, notificationPresentation: view, notificationDetails,
  notificationMessages, notificationLevels } =
  await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));

const base = { t: 1000, disp_mm: [0.2, 0.3], bpm: 16.2, motion: 1, present: true, state: 'ALERT' };
const noBreath = { type: 'alert', reason: 'no_breath', since_s: 10 };
const leftBed = { type: 'alert', reason: 'left_bed', since_s: 0 };
const wokeUp = { type: 'info', reason: 'woke_up', since_s: 0 };
const event = (state, value, receivedAt = 1234567890) => reduce(state, { type: 'event', event: value, receivedAt });
const status = (state, values) => reduce(state, { type: 'status', status: { ...base, ...values } });
function fresh(values = {}) {
  let state = reduce(createNotificationState(), { type: 'connection', value: 'connected' });
  state = reduce(state, { type: 'board', connected: true });
  state = status(state, { t: 800, ...values });
  return status(state, { t: 1000, ...values });
}

test('consecutive/repeated alert and info events have unique receipts; newest 20 are retained', () => {
  let state = fresh();
  for (let i = 0; i < 25; i++) state = event(state, i % 2 ? wokeUp : noBreath, i);
  assert.equal(state.history.length, 20);
  assert.deepEqual(state.history.map(entry => entry.id), Array.from({ length: 20 }, (_, i) => 25 - i));
  assert.equal(new Set(state.history.map(entry => entry.id)).size, 20);
  assert.equal(state.history[0].receivedAt, 24);
  assert.equal(state.history[1].receivedAt, 23);
});

test('original fields and missing server timestamps are preserved without fabrication', () => {
  const input = Object.freeze({ ...noBreath, bpm: 54.2, t: 1234.5 });
  let state = event(fresh(), input, 9876543210);
  assert.deepEqual(state.history[0].event, input);
  assert.notEqual(state.history[0].event, input);
  state = event(state, Object.freeze(wokeUp), 9876543211);
  assert.equal(Object.hasOwn(state.history[0].event, 't'), false);
  assert.equal(state.history[0].event.since_s, 0);
  assert.deepEqual(notificationDetails(wokeUp), []);
  assert.deepEqual(notificationDetails(leftBed), []);
  assert.deepEqual(notificationDetails(noBreath), ['10秒続いたとき']);
  // the board's millisecond clock is not something a parent can use, so it is not shown
  assert.deepEqual(notificationDetails(input), ['10秒続いたとき', '呼吸数 54.2回／分']);
});

test('titles and urgency labels are the ones the phone gets (pc/ibuki/server.py)', () => {
  assert.deepEqual(notificationMessages, { no_breath: '呼吸が確認できません', left_bed: 'ベッドにいません',
    woke_up: '起きました', fast_breathing: '呼吸が速い状態が続いています' });
  // priority 5 / 4 / 2 on ntfy
  assert.deepEqual(notificationLevels, { no_breath: '緊急', left_bed: '注意', fast_breathing: '注意', woke_up: 'お知らせ' });
});

test('status followed immediately by event cannot dismiss the new warning in the same cycle', () => {
  let state = event(fresh({ state: 'STILL' }), noBreath);
  assert.equal(view(state).current.length, 1);
  state = status(state, { t: 1000, state: 'STILL' }); // old/same t
  assert.equal(view(state).current.length, 1);
  assert.equal(view(state).current[0].indication, 'unknown');
  state = status(state, { t: 1120, state: 'STILL' });
  assert.equal(view(state).current.length, 0);
  assert.equal(view(state).message, '現在の解析状態では継続していません');
  assert.deepEqual(state.history.map(entry => entry.event), [noBreath]);
});

test('event before a fresh status stays in history after STILL stops the indication', () => {
  let state = event(fresh(), noBreath);
  state = status(state, { t: 1120, state: 'ALERT' });
  assert.equal(state.warnings.no_breath.indication, 'indicated');
  state = status(state, { t: 1240, state: 'CHECK' });
  assert.equal(view(state).current.length, 1);
  state = status(state, { t: 1360, state: 'STILL' });
  assert.equal(view(state).current.length, 0);
  assert.equal(state.history[0].event.reason, 'no_breath');
});

test('ordinary EMPTY never invents a departure; received left_bed persists until presence returns', () => {
  let state = fresh({ state: 'EMPTY', present: false, disp_mm: [] });
  assert.equal(view(state).current.length, 0); assert.equal(state.history.length, 0);
  state = event(state, leftBed);
  state = status(state, { t: 1120, state: 'EMPTY', present: false, disp_mm: [] });
  assert.equal(state.warnings.left_bed.indication, 'indicated');
  state = status(state, { t: 1240, state: 'OK', present: true });
  assert.equal(view(state).current.length, 0);
  assert.equal(state.history[0].event.reason, 'left_bed');
});

test('two warning reasons remain independent; EMPTY cannot establish a prior breathing condition', () => {
  let state = event(event(fresh(), noBreath), leftBed);
  assert.deepEqual(view(state).current.map(warning => warning.entry.event.reason), ['left_bed', 'no_breath']);
  state = status(state, { t: 1120, state: 'EMPTY', present: false, disp_mm: [] });
  assert.equal(state.warnings.left_bed.indication, 'indicated');
  assert.equal(state.warnings.no_breath.indication, 'unknown');
  assert.equal(view(state).message, '現在の状態は確認できません');
  state = status(state, { t: 1240, state: 'STILL' });
  assert.equal(view(state).current.length, 0); assert.equal(state.history.length, 2);
});

test('OK only changes analysis indication and never declares verified breathing or safety', () => {
  let state = event(fresh(), noBreath);
  state = status(state, { t: 1120, state: 'OK', bpm: null, disp_mm: [] });
  assert.equal(view(state).message, '現在の解析状態では継続していません');
  assert.equal(view(state).current.length, 0);
  assert.equal(state.history.length, 1);
  assert.equal(Object.hasOwn(state, 'resolved'), false);
});

test('disconnected, stale, sensor disconnected, shaking and placeholder remain unknown', () => {
  const initial = event(fresh(), noBreath);
  const interruptions = [
    { type: 'connection', value: 'disconnected' }, { type: 'connection', value: 'stale' },
    { type: 'board', connected: false },
    { type: 'status', status: { ...base, t: 1120, state: 'OK', shaking: true } },
    { type: 'status', status: { ...base, t: 1120, state: 'STILL', source: 'placeholder' } },
  ];
  for (const action of interruptions) {
    const state = reduce(initial, action);
    assert.equal(view(state).current.length, 1);
    assert.equal(state.warnings.no_breath.indication, 'unknown');
    assert.equal(view(state).message, '現在の状態は確認できません');
    assert.equal(state.history, initial.history);
  }
});

test('reconnection keeps history and ignores cached status until new timestamps progress', () => {
  let state = event(fresh(), noBreath);
  const history = state.history;
  state = reduce(state, { type: 'connection', value: 'disconnected' });
  state = status(state, { t: 1120, state: 'OK' }); // no transport
  state = reduce(state, { type: 'connection', value: 'connecting' });
  state = reduce(state, { type: 'connection', value: 'connected' });
  state = reduce(state, { type: 'board', connected: true });
  state = status(state, { t: 1000, state: 'STILL' }); // server's cached hub.latest
  assert.equal(state.ready, false);
  assert.equal(view(state).current[0].indication, 'unknown');
  assert.equal(state.history, history);
  state = status(state, { t: 1120, state: 'STILL' });
  assert.equal(view(state).current.length, 0);
  assert.equal(state.history, history);
});

test('optional event timestamp forms an ordering barrier; a clock reset requires new progress', () => {
  let state = event(fresh(), { ...noBreath, t: 1500 });
  state = status(state, { t: 1120, state: 'STILL' });
  assert.equal(view(state).current.length, 1);
  state = status(state, { t: 1500, state: 'STILL' });
  assert.equal(view(state).current.length, 1);
  state = status(state, { t: 1600, state: 'STILL' });
  assert.equal(view(state).current.length, 0);
  state = event(state, { ...noBreath, t: 1700 });
  state = status(state, { t: 100, state: 'STILL' });
  assert.equal(state.ready, false); assert.equal(view(state).current.length, 1);
  state = status(state, { t: 200, state: 'STILL' });
  assert.equal(view(state).current.length, 0);
});

test('info events never activate warnings or alter freshness bookkeeping', () => {
  let state = fresh();
  for (const value of [wokeUp, { type: 'info', reason: 'fast_breathing', since_s: 30, bpm: 54.2 }]) {
    const before = state;
    state = event(state, value);
    assert.equal(view(state).current.length, 0);
    assert.equal(state.warnings, before.warnings);
    assert.equal(state.lastStatusT, before.lastStatusT); assert.equal(state.ready, before.ready);
    assert.equal(state.history[0].event.type, 'info');
  }
});

test('PR30 CHECK status alone never invents a browser notification or warning receipt', () => {
  const state = status(fresh({ state: 'STILL' }), { t: 1120, state: 'CHECK', bpm: null });
  assert.equal(state.history.length, 0);
  assert.equal(view(state).current.length, 0);
});

test('PR31 departure indication follows received event and presence, independent of movement', () => {
  let state = fresh({ state: 'EMPTY', present: false, motion: 0, disp_mm: [] });
  assert.equal(view(state).current.length, 0);
  state = event(state, leftBed);
  state = status(state, { t: 1120, state: 'EMPTY', present: false, motion: 0, disp_mm: [] });
  assert.equal(state.warnings.left_bed.indication, 'indicated');
  state = status(state, { t: 1240, state: 'STILL', present: true, motion: 0 });
  assert.equal(view(state).current.length, 0);
  assert.equal(state.history[0].event.reason, 'left_bed');
});

test('history eviction does not remove an older current warning; reducer inputs stay immutable', () => {
  const sample = Object.freeze({ ...base, disp_mm: Object.freeze([0.2, 0.3]) });
  let state = status(fresh(), sample);
  state = event(state, Object.freeze(noBreath));
  const previous = structuredClone(state);
  for (let i = 0; i < 25; i++) state = event(state, wokeUp, i);
  assert.equal(state.history.length, 20);
  assert.equal(view(state).current[0].entry.event.reason, 'no_breath');
  assert.deepEqual(sample.disp_mm, [0.2, 0.3]); assert.equal(sample.bpm, 16.2);
  assert.equal(previous.history.length, 1);
});
