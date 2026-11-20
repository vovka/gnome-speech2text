import Adw from "gi://Adw";
import Gtk from "gi://Gtk";
import { ExtensionPreferences } from "resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js";

export default class LivePreferences extends ExtensionPreferences {
  fillPreferencesWindow(window) {
    const settings = this.getSettings();
    const page = new Adw.PreferencesPage({ title: "Live dictation", icon_name: "audio-input-microphone-symbolic" });
    window.add(page);
    const workflow = new Adw.PreferencesGroup({ title: "Safe workflow", description: "Use Alt+Super+Space to dictate, then explicitly Copy and paste yourself." });
    workflow.add(new Adw.ActionRow({ title: "Shortcut", subtitle: "Alt+Super+Space (currently supported shortcut)" }));
    const trigger = new Adw.ComboRow({ title: "Trigger mode" });
    const modes = new Gtk.StringList();
    modes.append("Push-push (press to start, press again to finish)");
    modes.append("Push-to-talk (hold and release Space)");
    trigger.set_model(modes);
    trigger.set_selected(settings.get_string("trigger-mode") === "hold" ? 1 : 0);
    trigger.connect("notify::selected", widget => settings.set_string("trigger-mode", widget.get_selected() === 1 ? "hold" : "toggle"));
    workflow.add(trigger);
    workflow.add(new Adw.ActionRow({ title: "Delivery", subtitle: "Copy-preview only; automatic typing, paste, and submit are unavailable." }));
    workflow.add(new Adw.ActionRow({ title: "IBus", subtitle: "Unavailable: no validated cooperating editor bridge." }));
    page.add(workflow);
    const safety = new Adw.PreferencesGroup({ title: "Safety" });
    const row = new Adw.ActionRow({ title: "Maximum recording duration", subtitle: "Cancels instead of finalizing when time expires." });
    const adjustment = new Gtk.Adjustment({ lower: 5, upper: 300, step_increment: 5, value: settings.get_int("max-hold-seconds") });
    const spin = new Gtk.SpinButton({ adjustment, numeric: true, valign: Gtk.Align.CENTER });
    spin.connect("value-changed", widget => settings.set_int("max-hold-seconds", widget.get_value_as_int()));
    row.add_suffix(spin); row.activatable_widget = spin; safety.add(row); page.add(safety);
    const backend = new Adw.PreferencesGroup({ title: "Local backend", description: "Read-only runtime facts; startup never records audio." });
    backend.add(new Adw.ActionRow({ title: "Endpoint", subtitle: "127.0.0.1:18080 (dedicated loopback)" }));
    backend.add(new Adw.ActionRow({ title: "Model", subtitle: "Local whisper.cpp Small on CUDA GPU" }));
    backend.add(new Adw.ActionRow({ title: "Language", subtitle: "Server default (not configurable here)" }));
    page.add(backend);
  }
}
