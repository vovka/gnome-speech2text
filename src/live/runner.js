import Gio from "gi://Gio";
import GLib from "gi://GLib";

// Private pipes only: no environment overrides, D-Bus or backend management.
export class PreviewRunner {
  constructor(root, onEvent, onFailure, config = "") {
    this._closed = false;
    this._pending = Promise.resolve();
    this._readCancel = new Gio.Cancellable();
    this._writeCancel = new Gio.Cancellable();
    this._onFailure = onFailure;
    this._onEvent = onEvent;
    const launcher = new Gio.SubprocessLauncher({
      flags: Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE,
    });
    launcher.set_cwd(root);
    const command = ["python3", "-m", "live.preview_runner"];
    if (config) command.push("--config", config);
    this._process = launcher.spawnv(command);
    this._input = this._process.get_stdin_pipe();
    this._output = new Gio.DataInputStream({ base_stream: this._process.get_stdout_pipe() });
    this._read();
    this._process.wait_async(null, (process, result) => {
      try {
        process.wait_finish(result);
        if (!this._closed) this._fail("Preview runner exited");
      } catch (error) {
        this._fail(error.message);
      }
      this._exited = true;
      if (this._killTimer) GLib.Source.remove(this._killTimer);
      this._killTimer = null;
      this._readCancel.cancel();
      this._writeCancel.cancel();
      this._pending.finally(() => this._input.close(null));
    });
  }

  send(command) {
    if (this._closed) return;
    this._pending = this._pending.then(() => {
      if (this._closed && command !== "shutdown") return;
      return this._write(command);
    }).catch(error => this._fail(error.message));
  }

  _write(command) {
    return new Promise((resolve, reject) => {
      // Bound blocked pipe writes as well as teardown: a stalled runner fails closed.
      let timeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1000, () => {
        timeout = null;
        this._writeCancel.cancel();
        return GLib.SOURCE_REMOVE;
      });
      const bytes = new TextEncoder().encode(`${JSON.stringify({ command })}\n`);
      this._input.write_all_async(bytes, GLib.PRIORITY_DEFAULT, this._writeCancel, (stream, result) => {
        if (timeout) GLib.Source.remove(timeout);
        try {
          stream.write_all_finish(result);
          resolve();
        } catch (error) {
          reject(error);
        }
      });
    });
  }

  _read() {
    this._output.read_line_async(GLib.PRIORITY_DEFAULT, this._readCancel, (stream, result) => {
      try {
        const [line] = stream.read_line_finish_utf8(result);
        if (this._closed) return;
        if (line === null) throw new Error("Preview runner closed stdout");
        const event = JSON.parse(line);
        if (event?.event !== "revision" && event?.event !== "status")
          throw new Error("Unexpected preview runner event");
        this._onEvent(event);
        if (!this._closed) this._read();
      } catch (error) {
        this._fail(error.message);
      } finally {
        if (this._closed) stream.close(null);
      }
    });
  }

  _fail(message) {
    if (this._closed) return;
    this.destroy();
    this._onFailure(message);
  }

  destroy() {
    if (this._closed) return;
    this.send("shutdown");
    this._closed = true;
    this._readCancel.cancel();
    this._killTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1000, () => {
      this._killTimer = null;
      this._writeCancel.cancel();
      if (!this._exited) this._process.force_exit();
      return GLib.SOURCE_REMOVE;
    });
  }
}
