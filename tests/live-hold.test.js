import assert from "node:assert/strict";
import test from "node:test";
import { Hold } from "../src/live/hold.js";

function fixture(callbacks = {}) {
  const events = [];
  const timers = new Map();
  const removed = [];
  let next = 0;
  const hold = new Hold({
    begin: (key) => events.push(["begin", key]),
    release: (key) => events.push(["release", key]),
    cancel: (key) => events.push(["cancel", key]),
    schedule: (callback, ms) => {
      const id = next++;
      timers.set(id, { callback, ms });
      return id;
    },
    unschedule: (id) => {
      removed.push(id);
      timers.delete(id);
    },
    watchdogMs: 123,
    ...callbacks,
  });
  return { hold, events, timers, removed };
}

test("only a matching release finalizes an actual press; repeats are ignored", () => {
  const { hold, events, timers, removed } = fixture();
  hold.release("a");
  hold.press("a");
  hold.press("a");
  hold.press("b");
  hold.release("b");
  assert.equal(timers.size, 1);
  assert.equal(timers.get(0).ms, 123);
  hold.release("a");
  hold.release("a");
  assert.deepEqual(events, [["begin", "a"], ["release", "a"]]);
  assert.equal(timers.size, 0);
  assert.deepEqual(removed, [0]);
});

for (const reason of ["watchdog", "explicit"]) {
  test(`${reason} cancellation blocks repeats until the matching release`, () => {
    const { hold, events, timers } = fixture();
    hold.press("a");
    const stale = timers.get(0).callback;
    if (reason === "watchdog") stale();
    else hold.cancel();
    hold.cancel();
    hold.press("a");
    hold.press("b");
    hold.release("b");
    hold.press("a");
    stale();
    assert.deepEqual(events, [["begin", "a"], ["cancel", "a"]]);
    assert.equal(timers.size, 0);
    hold.release("a");
    hold.press("a");
    stale();
    hold.release("a");
    assert.deepEqual(events.slice(2), [["begin", "a"], ["release", "a"]]);
    assert.equal(timers.size, 0);
  });
}

test("destroy cancels once, cleans timers, and permanently disables the hold", () => {
  const { hold, events, timers } = fixture();
  hold.press("a");
  const stale = timers.get(0).callback;
  hold.destroy();
  hold.destroy();
  stale();
  hold.release("a");
  hold.press("b");
  hold.cancel();
  assert.deepEqual(events, [["begin", "a"], ["cancel", "a"]]);
  assert.equal(timers.size, 0);
});

for (const action of ["release", "cancel", "destroy"]) {
  test(`begin may reenter ${action} without leaving a watchdog`, () => {
    let hold;
    const f = fixture({ begin: (key) => hold[action](key) });
    hold = f.hold;
    hold.press("a");
    assert.deepEqual(f.events, [[action === "release" ? "release" : "cancel", "a"]]);
    assert.equal(f.timers.size, 0);
  });
}

test("release callback may start another hold; old cleanup cannot remove its timer", () => {
  let hold;
  const f = fixture({ release: () => hold.press("b") });
  hold = f.hold;
  hold.press("a");
  const stale = f.timers.get(0).callback;
  hold.release("a");
  stale();
  assert.deepEqual(f.events, [["begin", "a"], ["begin", "b"]]);
  assert.deepEqual([...f.timers.keys()], [1]);
  hold.destroy();
});

test("cancel callback reentrancy cannot finish twice or restart the held key", () => {
  let hold;
  let calls = 0;
  const f = fixture({ cancel: () => {
    calls++;
    hold.cancel();
    hold.press("a");
    hold.destroy();
  } });
  hold = f.hold;
  hold.press("a");
  hold.cancel();
  assert.equal(calls, 1);
  assert.equal(f.timers.size, 0);
});

for (const action of ["release", "cancel", "destroy"]) {
  test(`${action} cleans up even when its lifecycle callback throws`, () => {
    const error = new Error("callback failed");
    const f = fixture({ [action === "destroy" ? "cancel" : action]: () => { throw error; } });
    f.hold.press("a");
    assert.throws(() => f.hold[action]("a"), (actual) => actual === error);
    assert.equal(f.timers.size, 0);
    f.hold.destroy();
  });
}

test("begin failure cancels and remains latched until release", () => {
  const error = new Error("begin failed");
  const f = fixture({ begin: () => { throw error; } });
  assert.throws(() => f.hold.press("a"), (actual) => actual === error);
  f.hold.press("a");
  assert.deepEqual(f.events, [["cancel", "a"]]);
  assert.equal(f.timers.size, 0);
  f.hold.release("a");
  assert.throws(() => f.hold.press("a"), (actual) => actual === error);
});

test("scheduling may synchronously cancel; its returned handle is still cleaned up", () => {
  let hold;
  const removed = [];
  hold = new Hold({
    begin() {}, release() {}, cancel() {},
    schedule(callback) { callback(); return 0; },
    unschedule: (id) => removed.push(id),
  });
  hold.press("a");
  assert.deepEqual(removed, [0]);
  hold.destroy();
});

test("keys match by identity, not by their string representation", () => {
  const f = fixture();
  const key = { code: "a" };
  f.hold.press(key);
  f.hold.release({ code: "a" });
  assert.equal(f.events.length, 1);
  f.hold.release(key);
  assert.deepEqual(f.events, [["begin", key], ["release", key]]);
});

test("timer removal can reenter without reviving or finishing the old hold", () => {
  let hold;
  const removed = [];
  const f = fixture({ unschedule: (id) => {
    removed.push(id);
    hold.release("a");
    hold.press("b");
  } });
  hold = f.hold;
  hold.press("a");
  hold.cancel();
  assert.deepEqual(f.events, [["begin", "a"], ["cancel", "a"], ["begin", "b"]]);
  hold.destroy();
  assert.deepEqual(removed, [0, 1]);
});

test("destroying an idle or already cancelled hold does not cancel twice", () => {
  const idle = fixture();
  idle.hold.cancel();
  idle.hold.destroy();
  idle.hold.press("a");
  assert.deepEqual(idle.events, []);
  const f = fixture();
  f.hold.press("a");
  f.hold.cancel();
  f.hold.destroy();
  assert.deepEqual(f.events, [["begin", "a"], ["cancel", "a"]]);
});

test("scheduler failure cancels the hold", () => {
  const error = new Error("schedule failed");
  const f = fixture({ schedule: () => { throw error; } });
  assert.throws(() => f.hold.press("a"), (actual) => actual === error);
  f.hold.release("a");
  assert.deepEqual(f.events, [["begin", "a"], ["cancel", "a"]]);
});
