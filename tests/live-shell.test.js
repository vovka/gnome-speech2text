import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { Trigger } from "../src/live/trigger.js";
import { PreviewOwnership } from "../src/live/previewOwnership.js";

async function harness(mode = "preview", shortcut = ["<Alt><Super>space"]) {
  const calls = { dialogs: [], runners: [], clipboard: [], timers: new Map() };
  const settings = {
    mode, shortcut, root: "/study/root", config: "", maxHold: 30, triggerMode: "hold",
    get_string: key => key === "mode" ? settings.mode : key === "trigger-mode" ? settings.triggerMode : key === "runner-config" ? settings.config : settings.root,
    get_strv: () => settings.shortcut,
    get_int: () => settings.maxHold,
    connect: (_signal, callback) => { settings.changed = callback; return 1; },
    disconnect: () => { settings.changed = null; },
  };
  class Dialog {
    constructor(options) {
      assert.equal(options.liveOnly, true);
      calls.dialogs.push(this);
      this.value = "";
      this.selection = [0, 0];
      this.focus = 0;
      this.previewTextEntry = {
        get_text: () => this.value,
        get_clutter_text: () => ({
          get_cursor_position: () => this.selection[0],
          get_selection_bound: () => this.selection[1],
          set_cursor_position: value => { this.selection[0] = value; },
          set_selection_bound: value => { this.selection[1] = value; },
        }),
      };
    }
    beginLivePreview() { this.value = ""; this.focus++; this.opened = true; }
    openLivePreview() { this.focus++; this.opened = true; }
    isOpen() { return this.opened === true; }
    replaceLiveText(value) { this.liveWriting = true; this.value = value; this.onLiveChanged(); this.liveWriting = false; }
    setLiveStatus(value) { this.status = value; }
    close() { this.closed = true; }
    destroy() { this.destroyed = true; }
  }
  class Runner {
    constructor(root, onEvent, onFailure) {
      Object.assign(this, { root, onEvent, onFailure, commands: [] });
      calls.runners.push(this);
    }
    send(command) { this.commands.push(command); }
    destroy() { this.destroyed = true; }
  }
  const stage = {
    connect: (_signal, callback) => { stage.captured = callback; return 2; },
    disconnect: id => { assert.equal(id, 2); stage.captured = null; },
  };
  const panel = { added: [], addToStatusArea: (_name, button) => panel.added.push(button) };
  const wm = {
    addKeybinding: (_name, _settings, _flags, _modes, callback) => { wm.press = callback; },
    removeKeybinding: () => { wm.press = null; },
  };
  let timerId = 0;
  const modules = {
    "gi://Clutter": { default: { EventType: { KEY_RELEASE: 2 }, KEY_space: 8, EVENT_PROPAGATE: 0 } },
    "gi://GLib": { default: {
      PRIORITY_DEFAULT: 0, SOURCE_REMOVE: false,
      path_is_absolute: path => path.startsWith("/"),
      timeout_add: (_priority, _ms, callback) => { calls.timers.set(++timerId, callback); return timerId; },
      Source: { remove: id => calls.timers.delete(id) },
    } },
    "gi://Meta": { default: { KeyBindingFlags: { NONE: 0 } } },
    "gi://Shell": { default: { ActionMode: { NORMAL: 1, OVERVIEW: 2 } } },
    "gi://St": { default: { ClipboardType: { CLIPBOARD: 1 }, Clipboard: {
      get_default: () => ({ set_text: (_type, text) => calls.clipboard.push(text) }),
    } } },
    "resource:///org/gnome/shell/ui/main.js": { wm, panel },
    "resource:///org/gnome/shell/extensions/extension.js": { Extension: class {
      getSettings() { return settings; }
    } },
    "./lib/components/modalDialog.js": { ModalDialog: Dialog },
    "./live/trigger.js": { Trigger },
    "./live/previewOwnership.js": { PreviewOwnership },
    "./live/runner.js": { PreviewRunner: Runner },
    "./live/panel.js": { LivePanel: class { constructor(actions) { this.actions = actions; this.button = {}; calls.panel = this; } set(state, message) { this.state = [state, message]; } destroy() { this.destroyed = true; } } },
  };
  const context = vm.createContext({ global: { stage }, log: () => {} });
  const source = await readFile(new URL("../experimental/extension.js", import.meta.url), "utf8");
  const module = new vm.SourceTextModule(source, { context });
  await module.link(specifier => {
    const exports = modules[specifier];
    assert.ok(exports, `Unexpected import ${specifier}`);
    return new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
    }, { context });
  });
  await module.evaluate();
  const extension = new module.namespace.default();
  extension.enable();
  return { calls, settings, wm, panel, stage, extension, release: key => stage.captured(null, {
    type: () => 2, get_key_symbol: () => key,
  }) };
}

const revision = (generation, sequence, text, final = false) => ({
  event: "revision", generation, sequence, samples: sequence * 100, final, text,
});

test("IBus mode never constructs UI, runner or Shell trigger", async () => {
  const h = await harness("ibus");
  assert.equal(h.calls.dialogs.length, 0);
  assert.equal(h.calls.runners.length, 0);
  assert.equal(h.wm.press, undefined);
  assert.equal(h.stage.captured, undefined);
  h.extension.disable();
});

test("missing project root shows a local error without launching a runner", async () => {
  const h = await harness();
  h.settings.root = "";
  h.wm.press();
  assert.equal(h.calls.runners.length, 0);
  assert.equal(h.calls.dialogs[0].isOpen(), true);
  assert.match(h.calls.dialogs[0].status, /absolute project-root/);
  assert.equal(h.calls.timers.size, 0);
  h.extension.disable();
});

test("only exact Alt+Super+Space is accepted, including after a settings change", async () => {
  const h = await harness("preview", ["F9"]);
  assert.equal(h.calls.dialogs.length, 0);
  h.settings.shortcut = ["<Alt><Super>space"];
  h.settings.changed();
  assert.equal(typeof h.wm.press, "function");
  h.settings.shortcut = ["<Super>space"];
  h.settings.changed();
  assert.equal(h.wm.press, null);
  h.extension.disable();
});

test("panel opens the existing preview without clearing text or starting capture", async () => {
  const h = await harness();
  const dialog = h.calls.dialogs[0];
  dialog.value = "manual draft";
  h.calls.panel.actions.open();
  assert.equal(dialog.value, "manual draft");
  assert.equal(h.calls.runners.length, 0);
  assert.equal(h.calls.panel.state[0], "idle");
  h.extension.disable();
});

test("push-push second distinct press finalizes and blocks additional presses while finalizing", async () => {
  const h = await harness();
  h.settings.triggerMode = "toggle";
  h.settings.changed(h.settings, "trigger-mode");
  h.wm.press(); h.wm.press(); h.release(8);
  const runner = h.calls.runners[0];
  h.wm.press(); h.wm.press(); h.release(8); h.wm.press(); h.release(8);
  assert.deepEqual(runner.commands, ["begin", "release"]);
  h.extension.disable();
});

test("stale previous status cannot unlock a new finalizing generation", async () => {
  const h = await harness();
  h.settings.triggerMode = "toggle"; h.settings.changed(h.settings, "trigger-mode");
  h.wm.press(); h.release(8); h.wm.press(); h.release(8);
  const runner = h.calls.runners[0];
  runner.onEvent(revision(1, 1, "first", true));
  runner.onEvent({ event: "status", generation: 1, phase: "idle", message: "first ready" });
  h.wm.press(); h.release(8); h.wm.press(); h.release(8);
  const status = h.calls.dialogs[0].status;
  runner.onEvent({ event: "status", generation: 1, phase: "cancelled", message: "stale" });
  runner.onEvent({ event: "status", generation: 1, phase: "idle", message: "stale idle" });
  h.wm.press(); h.release(8);
  assert.equal(h.calls.dialogs[0].status, status);
  assert.equal(runner.commands.filter(command => command === "begin").length, 2);
  h.extension.disable();
});

test("changing trigger mode preserves edited draft and status", async () => {
  const h = await harness();
  const dialog = h.calls.dialogs[0];
  dialog.value = "manual draft"; dialog.status = "keep status";
  h.settings.triggerMode = "toggle";
  h.settings.changed(h.settings, "trigger-mode");
  assert.equal(dialog.value, "manual draft");
  assert.equal(dialog.status, "keep status");
  assert.equal(h.calls.dialogs.length, 1);
  assert.equal(h.calls.runners.length, 0);
  h.extension.disable();
});

test("hold revisions preserve focus and selection; Copy is explicit and cancels delivery", async () => {
  const h = await harness();
  h.wm.press();
  h.wm.press();
  const runner = h.calls.runners[0];
  const dialog = h.calls.dialogs[0];
  assert.equal(runner.root, "/study/root");
  assert.deepEqual(runner.commands, ["begin"]);
  runner.onEvent(revision(1, 1, "hello"));
  dialog.selection = [3, 1];
  runner.onEvent(revision(1, 2, "hello world"));
  assert.deepEqual(dialog.selection, [3, 1]);
  assert.equal(dialog.focus, 1);
  assert.deepEqual(h.calls.clipboard, []);
  assert.equal(dialog.onInsert, undefined);
  dialog.onCopy(dialog.value);
  assert.deepEqual(h.calls.clipboard, ["hello world"]);
  assert.deepEqual(runner.commands, ["begin", "cancel"]);
  runner.onEvent(revision(1, 3, "late"));
  assert.equal(dialog.value, "hello world");
  h.release(8);
  assert.deepEqual(runner.commands, ["begin", "cancel"]);
  h.extension.disable();
});

test("runner-disabled status revokes delivery and cannot be overwritten by release Finalizing text", async () => {
  const h = await harness();
  h.wm.press();
  const runner = h.calls.runners[0];
  const dialog = h.calls.dialogs[0];
  runner.onEvent({ event: "status", generation: 1, phase: "disabled", message: "GPU fence requires verification" });
  assert.equal(dialog.status, "GPU fence requires verification");
  assert.deepEqual(runner.commands, ["begin"]);
  runner.onEvent({ event: "status", generation: 1, phase: "cancelled", message: "delivery revoked" });
  h.release(8);
  assert.equal(dialog.status, "GPU fence requires verification");
  assert.equal(dialog.status.includes("Finalizing"), false);
  h.extension.disable();
});

test("a matching release finalizes once and a subsequent hold owns only its generation", async () => {
  const h = await harness();
  h.release(8); // no actual activation yet
  h.wm.press();
  const runner = h.calls.runners[0];
  h.release(9);
  assert.deepEqual(runner.commands, ["begin"]);
  h.release(8);
  h.release(8);
  runner.onEvent(revision(1, 1, "done", true));
  runner.onEvent({ event: "status", generation: 1, phase: "idle", message: "Final ready" });
  h.wm.press();
  assert.deepEqual(runner.commands, ["begin", "release", "cancel", "begin"]);
  runner.onEvent(revision(1, 2, "stale", true));
  runner.onEvent(revision(2, 1, "new"));
  assert.equal(h.calls.dialogs[0].value, "new");
  h.release(8);
  assert.equal(runner.commands.at(-1), "release");
  h.extension.disable();
});

for (const signal of ["before", "fallback"]) {
  test(`user edit cancels capture (${signal}) and holds remain latched until release`, async () => {
    const h = await harness();
    h.wm.press();
    const runner = h.calls.runners[0];
    const dialog = h.calls.dialogs[0];
    runner.onEvent(revision(1, 1, "owned"));
    if (signal === "before") dialog.onLiveEdit();
    dialog.value = "manual";
    dialog.onLiveChanged();
    assert.deepEqual(runner.commands, ["begin", "cancel"]);
    h.wm.press();
    runner.onEvent(revision(1, 2, "overwrite"));
    assert.equal(dialog.value, "manual");
    h.release(8);
    assert.deepEqual(runner.commands, ["begin", "cancel"]);
    h.extension.disable();
  });
}

test("fallback text-changed mutation preserves recovery text for the next hold", async () => {
  const h = await harness();
  h.wm.press();
  const dialog = h.calls.dialogs[0];
  dialog.value = "fallback manual";
  dialog.onLiveChanged();
  h.release(8);
  h.wm.press();
  assert.equal(dialog.value, "fallback manual");
  assert.match(dialog.status, /Clear or explicitly discard/);
  h.extension.disable();
});

test("a nonempty manually edited recovery draft blocks a later hold until explicitly cleared", async () => {
  const h = await harness();
  h.wm.press();
  const runner = h.calls.runners[0];
  const dialog = h.calls.dialogs[0];
  runner.onEvent(revision(1, 1, "owned"));
  dialog.onLiveEdit();
  dialog.value = "manual recovery";
  dialog.onLiveChanged();
  h.release(8);
  h.wm.press();
  assert.equal(dialog.value, "manual recovery");
  assert.deepEqual(runner.commands, ["begin", "cancel"]);
  assert.match(dialog.status, /Clear or explicitly discard/);
  h.release(8);
  dialog.value = "";
  dialog.onLiveChanged();
  h.wm.press();
  assert.deepEqual(runner.commands, ["begin", "cancel", "begin"]);
  h.extension.disable();
});

test("late runner callbacks after disable are ignored", async () => {
  const h = await harness();
  h.wm.press();
  const runner = h.calls.runners[0];
  const dialog = h.calls.dialogs[0];
  h.extension.disable();
  const status = dialog.status;
  runner.onEvent(revision(1, 1, "late"));
  runner.onFailure("late failure");
  assert.equal(dialog.value, "");
  assert.equal(dialog.status, status);
});

test("lost-release watchdog cancels, never finalizes; teardown disconnects and destroys", async () => {
  const h = await harness();
  h.wm.press();
  [...h.calls.timers.values()][0]();
  const runner = h.calls.runners[0];
  assert.deepEqual(runner.commands, ["begin", "cancel"]);
  h.wm.press();
  assert.deepEqual(runner.commands, ["begin", "cancel"]);
  h.settings.mode = "ibus";
  h.settings.changed();
  assert.equal(h.calls.dialogs[0].destroyed, true);
  assert.equal(runner.destroyed, true);
  assert.equal(h.stage.captured, null);
  assert.equal(h.wm.press, null);
  assert.equal(h.calls.timers.size, 0);
  h.extension.disable();
});
