import { Hold } from './hold.js';

// Physical key edges are separate from the virtual hold that bounds a capture.
export class Trigger {
  constructor({ mode, begin, release, cancel, schedule, unschedule, watchdogMs }) {
    this.mode = mode;
    this.cancelDelivery = cancel;
    this.down = false;
    this.phase = 'idle';
    this.destroyed = false;
    this.cancelling = false;
    this.hold = new Hold({
      begin: () => { this.phase = 'recording'; begin(); },
      release: () => { this.phase = 'finalizing'; release(); },
      cancel: () => { this.phase = 'idle'; cancel(); this.hold.release('capture'); },
      schedule, unschedule, watchdogMs,
    });
  }
  press() {
    if (this.destroyed || this.down) return;
    this.down = true;
    if (this.phase === 'finalizing' || this.phase === 'cancelled') return;
    if (this.phase === 'idle') this.hold.press('capture');
    else if (this.mode === 'toggle') this.hold.release('capture');
  }
  releaseKey() {
    if (!this.down) return;
    this.down = false;
    if (this.mode === 'hold' && this.phase === 'recording') this.hold.release('capture');
  }
  release() { this.releaseKey(); }
  cancel() {
    if (this.cancelling) return;
    this.cancelling = true;
    try {
      if (this.phase === 'finalizing') {
        this.phase = 'cancelled';
        this.cancelDelivery();
      } else this.hold.cancel();
    } finally { this.cancelling = false; }
  }
  terminal() { if (['finalizing', 'cancelled'].includes(this.phase)) this.phase = 'idle'; }
  setMode(mode) { if (mode !== this.mode) { this.cancel(); this.mode = mode; } }
  setWatchdogMs(ms) { this.hold.setWatchdogMs(ms); }
  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    this.cancel();
    this.hold.destroy();
  }
}
