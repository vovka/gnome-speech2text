import test from 'node:test';
import assert from 'node:assert/strict';
import { Trigger } from '../src/live/trigger.js';

function fixture(mode) {
  const events = [], timers = new Map(); let next = 0;
  const trigger = new Trigger({ mode, begin: () => events.push('begin'), release: () => events.push('release'),
    cancel: () => events.push('cancel'), schedule: (fn, ms) => { timers.set(++next, {fn, ms}); return next; },
    unschedule: id => timers.delete(id) });
  return { trigger, events, timers };
}

test('push-push ignores repeats, releases rearm, and finalizing blocks presses', () => {
  const { trigger: t, events } = fixture('toggle');
  t.press(); t.press(); t.releaseKey(); t.press(); t.press(); t.releaseKey(); t.press();
  assert.deepEqual(events, ['begin', 'release']);
  t.releaseKey(); t.terminal(); t.press();
  assert.deepEqual(events, ['begin', 'release', 'begin']);
});

test('push-to-talk release finalizes; watchdog cancels and stays latched', () => {
  const { trigger: t, events, timers } = fixture('hold');
  t.press(); t.releaseKey(); assert.deepEqual(events, ['begin', 'release']);
  t.terminal(); t.press(); [...timers.values()][0].fn(); t.press();
  assert.deepEqual(events, ['begin', 'release', 'begin', 'cancel']);
  t.releaseKey(); t.terminal(); t.press(); assert.equal(events.at(-1), 'begin');
});

test('finalizing cancel and destroy are reentrant-safe and prevent new capture', () => {
  let t, cancellations = 0;
  t = new Trigger({mode: 'toggle', begin() {}, release() {}, cancel() { cancellations++; t.cancel(); },
    schedule() { return 1; }, unschedule() {}});
  t.press(); t.releaseKey(); t.press(); t.releaseKey();
  t.destroy(); t.destroy(); t.terminal(); t.press();
  assert.equal(cancellations, 1);
});

test('mode change cancels current capture without starting another', () => {
  const { trigger: t, events } = fixture('toggle');
  t.press(); t.releaseKey(); t.setMode('hold');
  assert.deepEqual(events, ['begin', 'cancel']);
});
