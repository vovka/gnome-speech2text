import Clutter from "gi://Clutter";
import GLib from "gi://GLib";
import Meta from "gi://Meta";
import Shell from "gi://Shell";
import St from "gi://St";
import * as Main from "resource:///org/gnome/shell/ui/main.js";
import { Extension } from "resource:///org/gnome/shell/extensions/extension.js";
import { ModalDialog } from "./lib/components/modalDialog.js";
import { Trigger } from "./live/trigger.js";
import { PreviewOwnership } from "./live/previewOwnership.js";
import { PreviewRunner } from "./live/runner.js";
import { LivePanel } from "./live/panel.js";

export default class LivePreviewExtension extends Extension {
  enable() {
    this._settings = this.getSettings();
    this._settingsSignal = this._settings.connect("changed", (_settings, key) => {
      if (key === "max-hold-seconds") {
        this._hold?.setWatchdogMs(this._settings.get_int(key) * 1000);
        return;
      }
      if (key === "trigger-mode") {
        this._hold?.setMode(this._settings.get_string(key));
        return;
      }
      this._stop();
      this._start();
    });
    this._start();
  }

  _start() {
    // IBus owns focus, trigger and delivery. Shell must remain entirely passive.
    if (this._settings.get_string("mode") !== "preview") return;
    this._active = true;
    this._epoch = (this._epoch ?? 0) + 1;
    this._manualDraft = false;
    const shortcut = this._settings.get_strv("preview-trigger");
    if (shortcut.length !== 1 || shortcut[0] !== "<Alt><Super>space") {
      log("Live dictation: only Alt+Super+Space is supported; preview disabled");
      return;
    }
    this._generation = 0;
    this._failed = false;
    this._dialog = new ModalDialog({ liveOnly: true });
    this._panel = new LivePanel({
      open: () => this._openPreview(),
      cancel: () => { this._owner.revoke(); this._cancelCapture(); },
      preferences: () => this.openPreferences(),
    });
    Main.panel.addToStatusArea("speech2text-live", this._panel.button);
    this._panel.set("idle", "Ready — use Alt+Super+Space to dictate");
    const text = this._dialog.previewTextEntry.get_clutter_text();
    this._owner = new PreviewOwnership({
      getText: () => this._dialog.previewTextEntry.get_text(),
      setText: value => this._dialog.replaceLiveText(value),
      getSelection: () => [text.get_cursor_position(), text.get_selection_bound()],
      setSelection: ([cursor, bound]) => {
        text.set_cursor_position(cursor);
        text.set_selection_bound(bound);
      },
      cancel: () => this._cancelCapture(),
    });
    this._hold = new Trigger({
      mode: this._settings.get_string("trigger-mode"),
      begin: () => this._begin(),
      release: () => {
        if (["disabled", "failed", "cancelled"].includes(this._runnerPhase)) return;
        this._runner?.send("release");
        this._dialog.setLiveStatus("Release requested; awaiting runner confirmation");
        this._panel.set("finalizing", "Finalizing");
      },
      cancel: () => {
        this._owner.revoke();
        if (!["disabled", "failed"].includes(this._runnerPhase))
          this._dialog.setLiveStatus("Capture cancelled (edit, error or hold timeout)");
      },
      schedule: (callback, ms) => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
        callback();
        return GLib.SOURCE_REMOVE;
      }),
      unschedule: id => GLib.Source.remove(id),
      watchdogMs: this._settings.get_int("max-hold-seconds") * 1000,
    });
    this._dialog.onLiveEdit = () => {
      this._manualDraft = true;
      this._owner.beforeEdit();
    };
    this._dialog.onLiveChanged = () => {
      // Programmatic revisions emit the same signal while ModalDialog.liveWriting is true.
      if (!this._dialog.liveWriting) this._manualDraft = true;
      this._owner.changed();
      // A user may explicitly clear their recovery draft before another hold.
      if (this._manualDraft && this._dialog.previewTextEntry.get_text() === "")
        this._manualDraft = false;
    };
    this._dialog.onCancel = () => {
      this._owner.revoke();
      this._hold.cancel();
      this._dialog.close();
    };
    this._dialog.onCopy = value => {
      this._owner.revoke();
      St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, value);
      this._dialog.setLiveStatus("Copied explicitly");
    };
    this._sessionModeSignal = Main.sessionMode?.connect?.("updated", () => {
      // GNOME's lock/unlock transition invalidates a live delivery lease immediately.
      if (Main.sessionMode.currentMode === "unlock-dialog") {
        this._owner.revoke();
        this._hold.cancel();
      }
    });
    this._captured = global.stage.connect("captured-event", (_actor, event) => {
      if (event.type() === Clutter.EventType.KEY_RELEASE && event.get_key_symbol() === Clutter.KEY_space)
        this._hold.releaseKey();
      return Clutter.EVENT_PROPAGATE;
    });
    Main.wm.addKeybinding("preview-trigger", this._settings, Meta.KeyBindingFlags.NONE,
      Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW, () => this._hold.press());
  }

  _begin() {
    if (this._failed || !this._active) return;
    if (this._manualDraft && this._dialog.previewTextEntry.get_text() !== "") {
      this._hold.cancel();
      this._dialog.setLiveStatus("Clear or explicitly discard the edited draft before another hold");
      return;
    }
    try {
      const root = this._settings.get_string("project-root");
      if (!GLib.path_is_absolute(root)) throw new Error("Configure an absolute project-root first");
      if (!this._runner) {
        const config = this._settings.get_string("runner-config");
        const epoch = this._epoch;
        if (config && !GLib.path_is_absolute(config)) throw new Error("Configure an absolute runner-config or leave it empty");
        this._runner = new PreviewRunner(root, event => {
          if (!this._active || this._epoch !== epoch) return;
          if (event.event === "revision") {
            this._owner.revision(event);
          } else if (event.event === "status" && typeof event.phase === "string") {
            if (event.generation !== this._generation) return;
            this._runnerPhase = event.phase;
            if (["idle", "disabled", "failed", "cancelled"].includes(event.phase)) this._hold.terminal();
            if (["disabled", "failed"].includes(event.phase) && typeof event.message === "string")
              this._runnerTerminalMessage = event.message;
            if (!(event.phase === "cancelled" && this._runnerTerminalMessage))
              this._panel.set(event.phase, event.message ?? "Live dictation status");
            if (typeof event.message === "string" &&
                !(event.phase === "cancelled" && this._runnerTerminalMessage))
              this._dialog.setLiveStatus(event.message);
            if (["disabled", "failed"].includes(event.phase)) {
              this._owner.revoke();
              this._hold.cancel();
            }
          }
        }, message => {
          if (!this._active || this._epoch !== epoch) return;
          this._failed = true;
          this._owner.revoke();
          this._hold.cancel();
          this._dialog.setLiveStatus(message);
          this._panel.set("failed", message);
        }, config);
      }
      this._starting = true;
      try {
        this._owner.revoke();
      } finally {
        this._starting = false;
      }
      this._dialog.beginLivePreview();
      this._runnerPhase = "starting";
      this._runnerTerminalMessage = null;
      // Private runner contract: a fresh process starts at zero; every begin increments once.
      this._owner.begin(++this._generation);
      this._dialog.setLiveStatus(this._settings.get_string("trigger-mode") === "hold"
        ? "Hold Alt+Super+Space; release Space to finalize" : "Press Alt+Super+Space again to finalize");
      this._runner.send("begin");
    } catch (error) {
      this._hold.cancel();
      // No automatic retry after a transport failure; re-enable explicitly.
      if (!this._dialog.isOpen()) this._dialog.beginLivePreview();
      this._dialog.setLiveStatus(error.message);
    }
  }

  _openPreview() {
    this._dialog.openLivePreview();
    if (!this._runnerPhase || this._runnerPhase === "idle")
      this._dialog.setLiveStatus("Ready — use Alt+Super+Space to dictate");
  }

  _cancelCapture() {
    if (!["disabled", "failed"].includes(this._runnerPhase)) this._runner?.send("cancel");
    if (!this._starting) this._hold.cancel();
  }

  _stop() {
    this._active = false;
    if (!this._dialog) return;
    Main.wm.removeKeybinding("preview-trigger");
    global.stage.disconnect(this._captured);
    if (this._sessionModeSignal) Main.sessionMode.disconnect(this._sessionModeSignal);
    this._sessionModeSignal = null;
    this._owner.revoke();
    this._hold.destroy();
    this._runner?.destroy();
    this._runner = null;
    this._panel.destroy();
    this._panel = null;
    this._dialog.destroy();
    this._dialog = null;
  }

  disable() {
    this._settings.disconnect(this._settingsSignal);
    this._stop();
    this._settings = null;
  }
}
