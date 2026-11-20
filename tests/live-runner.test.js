import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const flush = () => new Promise(resolve => setImmediate(resolve));

async function harness() {
  const calls = { writes: [], timers: new Map(), events: [], failures: [], killed: 0 };
  let timerId = 0;
  class Cancellable {
    callbacks = [];
    cancelled = false;
    cancel() {
      if (this.cancelled) return;
      this.cancelled = true;
      for (const callback of this.callbacks) queueMicrotask(callback);
    }
  }
  const input = {
    write_all_async(bytes, _priority, cancellable, callback) {
      let finished = false;
      const write = { command: JSON.parse(new TextDecoder().decode(bytes)), finish(error = false) {
        if (finished) return;
        finished = true;
        callback(input, { error });
      } };
      calls.writes.push(write);
      cancellable.callbacks.push(() => write.finish(true));
    },
    write_all_finish(result) { if (result.error) throw new Error("cancelled write"); return [true, 1]; },
    close() { calls.inputClosed = true; },
  };
  const output = {
    read_line_async(_priority, cancellable, callback) {
      let finished = false;
      const finish = result => {
        if (finished) return;
        finished = true;
        callback(output, result);
      };
      calls.read = line => finish({ line });
      cancellable.callbacks.push(() => finish({ error: true }));
    },
    read_line_finish_utf8(result) {
      calls.readFinished = (calls.readFinished ?? 0) + 1;
      if (result.error) throw new Error("cancelled read");
      return [result.line, 0];
    },
    close() { calls.outputClosed = true; },
  };
  const process = {
    get_stdin_pipe: () => input, get_stdout_pipe: () => output,
    wait_async: (_cancel, callback) => { calls.exit = () => callback(process, {}); },
    wait_finish: () => {}, force_exit: () => { calls.killed++; },
  };
  const Gio = {
    Cancellable, SubprocessFlags: { STDIN_PIPE: 1, STDOUT_PIPE: 2 },
    SubprocessLauncher: class {
      constructor(options) { calls.options = options; }
      set_cwd(root) { calls.root = root; }
      spawnv(args) { calls.args = args; return process; }
    },
    DataInputStream: class { constructor() { return output; } },
  };
  const GLib = {
    PRIORITY_DEFAULT: 0, SOURCE_REMOVE: false,
    timeout_add: (_priority, _ms, callback) => { calls.timers.set(++timerId, callback); return timerId; },
    Source: { remove: id => calls.timers.delete(id) },
  };
  const context = vm.createContext({ TextEncoder });
  const module = new vm.SourceTextModule(
    await readFile(new URL("../src/live/runner.js", import.meta.url), "utf8"), { context });
  await module.link(specifier => new vm.SyntheticModule(["default"], function () {
    this.setExport("default", specifier === "gi://Gio" ? Gio : GLib);
  }, { context }));
  await module.evaluate();
  const runner = new module.namespace.PreviewRunner("/study/root", event => calls.events.push(event),
    message => calls.failures.push(message));
  return { calls, runner };
}

test("runner launches only the private module in project cwd and serializes JSON commands", async () => {
  const { calls, runner } = await harness();
  assert.equal(calls.root, "/study/root");
  assert.deepEqual(Array.from(calls.args), ["python3", "-m", "live.preview_runner"]);
  assert.equal(calls.options.flags, 3);
  runner.send("begin");
  runner.send("release");
  runner.send("cancel");
  await flush();
  assert.deepEqual(calls.writes.map(write => write.command), [{ command: "begin" }]);
  calls.writes[0].finish();
  await flush();
  calls.writes[1].finish();
  await flush();
  calls.writes[2].finish();
  await flush();
  assert.deepEqual(calls.writes.map(write => write.command), [
    { command: "begin" }, { command: "release" }, { command: "cancel" },
  ]);
  calls.read(JSON.stringify({ event: "status", phase: "disabled", message: "GPU disabled" }));
  assert.equal(calls.events[0].message, "GPU disabled");
  runner.destroy();
  await flush();
  assert.deepEqual(calls.writes.at(-1).command, { command: "shutdown" });
  calls.writes.at(-1).finish();
  calls.exit();
  await flush();
  assert.equal(calls.inputClosed, true);
  assert.equal(calls.outputClosed, true);
  assert.equal(calls.timers.size, 0);
});

test("teardown drops queued non-shutdown commands and finishes cancelled reads", async () => {
  const { calls, runner } = await harness();
  runner.send("begin");
  runner.send("release");
  runner.destroy();
  await flush();
  assert.deepEqual(calls.writes.map(write => write.command), [{ command: "shutdown" }]);
  assert.equal(calls.readFinished, 1);
  assert.equal(calls.outputClosed, true);
  calls.writes[0].finish();
  await flush();
  [...calls.timers.values()][0]();
  assert.equal(calls.killed, 1);
  calls.exit();
  await flush();
  assert.deepEqual(calls.failures, []);
});

test("invalid stdout revokes transport and reports failure once", async () => {
  const { calls, runner } = await harness();
  calls.read("not json");
  await flush();
  assert.equal(calls.failures.length, 1);
  runner.send("begin");
  assert.deepEqual(calls.writes.map(write => write.command), [{ command: "shutdown" }]);
  calls.writes[0].finish();
  calls.exit();
  await flush();
  assert.equal(calls.failures.length, 1);
});

test("stalled pipe writes time out, cancel delivery, and leave a bounded kill timer", async () => {
  const { calls, runner } = await harness();
  runner.send("begin");
  await flush();
  const [id, timeout] = [...calls.timers.entries()][0];
  calls.timers.delete(id); // One-shot GLib source is removed by the scheduler.
  timeout();
  await flush();
  assert.equal(calls.failures.length, 1);
  assert.equal(calls.outputClosed, true);
  assert.equal(calls.timers.size >= 1, true);
  const [killId, kill] = [...calls.timers.entries()][0];
  calls.timers.delete(killId);
  kill();
  assert.equal(calls.killed, 1);
  calls.exit();
  await flush();
});
