import St from "gi://St";
import * as PanelMenu from "resource:///org/gnome/shell/ui/panelMenu.js";
import * as PopupMenu from "resource:///org/gnome/shell/ui/popupMenu.js";

export class LivePanel {
  constructor({ open, cancel, preferences }) {
    this.button = new PanelMenu.Button(0.0, "Speech2Text Live");
    this.icon = new St.Icon({ icon_name: "radio-checked-symbolic", style_class: "system-status-icon" });
    this.button.add_child(this.icon);
    this._status = new PopupMenu.PopupMenuItem("Ready — use Alt+Super+Space to dictate", { reactive: false });
    this.button.menu.addMenuItem(this._status);
    const openItem = new PopupMenu.PopupMenuItem("Open preview");
    const cancelItem = new PopupMenu.PopupMenuItem("Cancel capture");
    const preferencesItem = new PopupMenu.PopupMenuItem("Preferences");
    openItem.connect("activate", open);
    cancelItem.connect("activate", cancel);
    preferencesItem.connect("activate", preferences);
    this.button.menu.addMenuItem(openItem);
    this.button.menu.addMenuItem(cancelItem);
    this.button.menu.addMenuItem(preferencesItem);
  }

  set(state, message) {
    this.icon.icon_name = state === "finalizing" ? "emblem-synchronizing-symbolic" : "radio-checked-symbolic";
    this.icon.set_style(state === "recording" || state === "failed" ? "color: #ed333b;" : "");
    this._status.label.set_text(message);
  }

  destroy() { this.button.destroy(); }
}
