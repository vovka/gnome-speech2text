/**
 * A single-key hold with synchronous lifecycle callbacks, each receiving the key.
 * schedule(callback, milliseconds) returns a handle; unschedule(handle) removes it.
 * Cancellation stays latched until matching release. Keys use strict equality.
 * Inject a one-shot scheduler. No platform APIs or timers are used directly.
 */
export class Hold {
  constructor({ begin, release, cancel, schedule, unschedule, watchdogMs = 30000 }) {
    this._callbacks = { begin, release, cancel };
    this._schedule = schedule;
    this._unschedule = unschedule;
    this._watchdogMs = watchdogMs;
    this._held = null;
    this._destroyed = false;
  }

  setWatchdogMs(milliseconds) {
    this._watchdogMs = milliseconds;
  }

  press(key) {
    if (this._destroyed || this._held !== null) return;
    const hold = { key, active: true, timer: null };
    this._held = hold;
    try {
      this._callbacks.begin(key);
      if (hold.active) this._arm(hold);
    } catch (error) {
      this._finish(hold, "cancel");
      throw error;
    }
  }

  release(key) {
    const hold = this._held;
    if (this._destroyed || hold === null || hold.key !== key) return;
    this._held = null;
    this._finish(hold, "release");
  }

  cancel() {
    if (this._held !== null) this._finish(this._held, "cancel");
  }

  destroy() {
    if (this._destroyed) return;
    this._destroyed = true;
    try {
      this.cancel();
    } finally {
      this._held = null;
    }
  }

  _arm(hold) {
    const timer = { ready: false, removed: false, handle: undefined };
    hold.timer = timer;
    timer.handle = this._schedule(
      () => this._finish(hold, "cancel"),
      this._watchdogMs
    );
    timer.ready = true;
    // schedule itself may reenter release/cancel/destroy before returning a handle.
    if (timer.removed) this._unschedule(timer.handle);
  }

  _finish(hold, action) {
    if (!hold.active) return;
    hold.active = false;
    try {
      this._callbacks[action](hold.key);
    } finally {
      const timer = hold.timer;
      hold.timer = null;
      if (timer !== null) {
        timer.removed = true;
        if (timer.ready) this._unschedule(timer.handle);
      }
    }
  }
}
