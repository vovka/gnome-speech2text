import io
import json
import sys
import types
import unittest
import wave
from io import BytesIO

from live.adapters import ContinuousPcmCapture, EndpointFence, HttpWhisperRecognizer
from live.ibus import Lease, PreeditOwner
from live.ibus_controller import IBusController
from live.ibus_engine import engine_class
from live import ffmpeg_child
from live.readiness import GpuAttestation
from live.preview_runner import CommandPipe, RuntimeConfig, build_runtime_session, run
from live.session import DictationSession


class Capture:
    def __init__(self): self.data, self.started, self.stopped = b"", 0, 0; self.on_stop = self.on_start = None
    def start(self):
        self.started += 1
        if self.on_start: self.on_start()
    def stop(self):
        self.stopped += 1
        if self.on_stop: self.on_stop()
    def pcm(self): return self.data


class Recognizer:
    def __init__(self): self.jobs = []; self.raise_error = None
    def transcribe(self, wav, done, failed):
        if self.raise_error: raise self.raise_error
        self.jobs.append((wav, done, failed))
    def finish(self, index, text, twice=False):
        self.jobs[index][1](text)
        if twice: self.jobs[index][1](text + " duplicate")


def ready(): return True, "attested"


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.capture, self.recognizer, self.events = Capture(), Recognizer(), []
        self.session = DictationSession(self.capture, self.recognizer, self.events.append, ready)

    def test_newest_only_full_history_final_suppresses_inflight_partial(self):
        self.session.begin(0)
        self.capture.data = b"\0\0" * 4; self.session.tick(2)
        self.capture.data = b"\0\0" * 8; self.session.release()
        self.recognizer.finish(0, "partial"); self.session.poll()
        with wave.open(BytesIO(self.recognizer.jobs[1][0])) as file:
            self.assertEqual((file.getframerate(), file.getnchannels(), file.getnframes()), (16000, 1, 8))
        self.recognizer.finish(1, "final", twice=True); self.session.poll()
        revisions = [x for x in self.events if x["event"] == "revision"]
        self.assertEqual([(x["text"], x["samples"], x["final"]) for x in revisions], [("final", 8, True)])
        self.assertEqual(self.events[-1], {"event": "status", "phase": "idle",
                                           "message": "Final revision ready; edit or Copy explicitly", "generation": 1})
        self.assertEqual(sum(event.get("final") is True for event in revisions), 1)

    def test_duplicate_begin_stops_once_and_invalidates_old_generation(self):
        self.assertEqual(self.session.begin(0), 1)
        self.capture.data = b"\0\0"; self.session.tick(2)
        self.assertEqual(self.session.begin(3), 2)
        self.assertEqual((self.capture.started, self.capture.stopped), (1, 1))
        self.recognizer.finish(0, "late"); self.session.poll()
        self.assertFalse(any(x.get("text") == "late" for x in self.events))

    def test_stop_callback_cannot_deliver_partial_and_release_before_start_is_safe(self):
        self.session.release()
        self.capture.on_start = self.session.release
        self.session.begin(0)
        self.assertEqual(self.session.phase, "finalizing")
        self.capture.on_start = None
        self.session = DictationSession(self.capture, self.recognizer, self.events.append, ready)
        self.session.begin(0); self.capture.data = b"\0\0"; self.session.tick(2)
        self.capture.on_stop = lambda: self.recognizer.finish(0, "stop-race")
        self.session.release(); self.session.poll()
        self.assertFalse(any(x.get("text") == "stop-race" for x in self.events))

    def test_intermediate_newest_pending_snapshot_replaces_older_pending(self):
        self.session.begin(0)
        self.capture.data = b"\0\0" * 2; self.session.tick(2)
        self.capture.data = b"\0\0" * 4; self.session.tick(4)
        self.capture.data = b"\0\0" * 6; self.session.tick(6)
        self.recognizer.finish(0, "two"); self.session.poll()
        with wave.open(BytesIO(self.recognizer.jobs[1][0])) as file:
            self.assertEqual(file.getnframes(), 6)

    def test_per_dispatch_readiness_loss_stops_before_inference(self):
        allowed = [True]
        session = DictationSession(self.capture, self.recognizer, self.events.append,
                                   lambda: (allowed[0], "server changed"))
        session.begin(0); allowed[0] = False; self.capture.data = b"\0\0"; session.tick(2)
        self.assertEqual(self.recognizer.jobs, [])
        self.assertEqual(self.events[-1]["phase"], "failed")

    def test_cancel_barrier_drops_already_queued_final(self):
        self.session.begin(0); self.capture.data = b"\0\0"; self.session.release()
        self.recognizer.finish(0, "must not commit")
        self.session.cancel(); self.session.poll()
        self.assertFalse(any(event.get("text") == "must not commit" for event in self.events))

    def test_release_when_unavailable_acknowledges_current_terminal_phase(self):
        session = DictationSession(self.capture, self.recognizer, self.events.append,
                                   lambda: (False, "fence requires verification"))
        session.begin(0); session.release()
        self.assertEqual(self.events[-1], {"event": "status", "phase": "disabled",
                                           "message": "release ignored: runner is not recording", "generation": 1})

    def test_disabled_session_rechecks_readiness_on_next_begin(self):
        allowed = [False]
        session = DictationSession(self.capture, self.recognizer, self.events.append,
                                   lambda: (allowed[0], "not ready"))
        session.begin(0); self.assertEqual(self.capture.started, 0)
        allowed[0] = True; session.begin(1)
        self.assertEqual(self.capture.started, 1)

    def test_runtime_config_reload_only_on_explicit_idle_begin(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            state = [None]
            def provider():
                if state[0] is None: raise FileNotFoundError()
                return state[0]
            capture = Capture()
            recognizers = []
            def factory(_endpoint, fence):
                recognizer = Recognizer(); recognizers.append(recognizer); return recognizer
            session = build_runtime_session(self.events.append, capture_factory=lambda: capture,
                                            recognizer_factory=factory, config_provider=provider)
            self.assertEqual(session.begin(0), 1)
            self.assertEqual(self.events[-1]['phase'], 'disabled')
            self.assertEqual(capture.started, 0)
            attestation = GpuAttestation('http://127.0.0.1:18080', 'model', 'pid:2', '20', True)
            state[0] = RuntimeConfig('http://127.0.0.1:18080', 'model', attestation,
                                     Path(directory) / 'fence', lambda: ('pid:2', '20'))
            self.assertEqual(capture.started, 0)
            self.assertEqual(session.begin(1), 2)
            self.assertEqual(capture.started, 1)
            capture.data = b'\0\0'; session.tick(3)
            self.assertEqual(len(recognizers[0].jobs), 1)
            session.cancel()
            state[0] = None
            session.begin(4)
            self.assertEqual(len(recognizers), 1)
            recognizers[0].finish(0, 'late'); session.poll()
            self.assertFalse(any(event.get('text') == 'late' for event in self.events))

    def test_runtime_binding_rejects_config_change_before_dispatch(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            def make_config(start):
                attestation = GpuAttestation('http://127.0.0.1:18080', 'model', 'pid:2', start, True)
                return RuntimeConfig('http://127.0.0.1:18080', 'model', attestation,
                                     Path(directory) / 'fence', lambda: ('pid:2', start))
            state = [make_config('20')]
            session = build_runtime_session(self.events.append, capture_factory=lambda: self.capture,
                                            recognizer_factory=lambda *_args, **_kwargs: self.recognizer,
                                            config_provider=lambda: state[0])
            session.begin(0); state[0] = make_config('21')
            self.capture.data = b'\0\0'; session.tick(2)
            self.assertEqual(self.recognizer.jobs, [])
            self.assertEqual(self.events[-1]['phase'], 'failed')

    def test_unready_and_transcribe_exception_never_capture_or_deliver(self):
        blocked = DictationSession(self.capture, self.recognizer, self.events.append,
                                   lambda: (False, "attestation missing"))
        blocked.begin(0); self.assertEqual(self.capture.started, 0)
        self.recognizer.raise_error = RuntimeError("network failed")
        self.session.begin(1); self.capture.data = b"\0\0"; self.session.tick(3); self.session.poll()
        self.assertEqual(self.events[-1]["phase"], "failed")


class FakeProcess:
    def __init__(self): self.stdout = self; self.bytes = [b"\x01\x00", b"\x02\x00"]; self.alive = True; self.calls = []
    def read1(self, _): return self.bytes.pop(0) if self.bytes else b""
    def poll(self): return None if self.alive else 0
    def terminate(self): self.calls.append("terminate"); self.alive = False
    def wait(self, timeout=None): self.calls.append(("wait", timeout)); return 0
    def kill(self): self.calls.append("kill")


class ChildWrapperTests(unittest.TestCase):
    def test_child_wrapper_arms_pdeath_checks_parent_then_execs(self):
        original_signal, original_parent, original_exec = ffmpeg_child.parent_death_signal, ffmpeg_child.os.getppid, ffmpeg_child.os.execvp
        calls = []
        ffmpeg_child.parent_death_signal = lambda: calls.append("pdeath")
        ffmpeg_child.os.getppid = lambda: 12
        ffmpeg_child.os.execvp = lambda command, argv: calls.append((command, argv))
        try:
            self.assertEqual(ffmpeg_child.main(["12", "ffmpeg", "-version"]), 127)
            self.assertEqual(calls, ["pdeath", ("ffmpeg", ["ffmpeg", "-version"])])
        finally:
            ffmpeg_child.parent_death_signal, ffmpeg_child.os.getppid, ffmpeg_child.os.execvp = original_signal, original_parent, original_exec


class AdapterTests(unittest.TestCase):
    def test_pcm_capture_uses_fixed_command_and_fake_process(self):
        process, calls = FakeProcess(), []
        capture = ContinuousPcmCapture(lambda *argv: calls.append(argv) or process,
                                       read_available=lambda current: current.read1(65536))
        capture.start(); self.assertEqual(calls[0], ContinuousPcmCapture.argv)
        self.assertEqual(capture.pcm(), b"\x01\x00")
        self.assertEqual(capture.pcm(), b"\x01\x00\x02\x00")
        capture.stop(); self.assertEqual(process.calls, ["terminate", ("wait", 1)])

    def test_incomplete_final_pcm_fails_closed(self):
        process = FakeProcess(); process.bytes = [b"\x01"]
        capture = ContinuousPcmCapture(lambda *_: process, read_available=lambda current: current.read1(10))
        capture.start()
        with self.assertRaises(ValueError): capture.stop()

    def test_http_adapter_rejects_non_origins_and_posts_only_injected_request(self):
        for endpoint in ["http://example.com:9", "https://127.0.0.1:9", "http://127.0.0.1:8080",
                         "http://127.0.0.1:8080/", "http://127.0.0.1:9/path",
                         "http://127.0.0.1:9@evil", "http://user@127.0.0.1:9"]:
            with self.assertRaises(ValueError): HttpWhisperRecognizer(endpoint)
        requests, work = [], []
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            adapter = HttpWhisperRecognizer("http://127.0.0.1:18080",
                request=lambda request: requests.append(request) or json.dumps({"text": "ok"}).encode(),
                submit=work.append, fence=EndpointFence(__import__("pathlib").Path(directory) / "fence"))
            output = []; adapter.transcribe(b"wav", output.append, lambda error: self.fail(str(error))); self.assertEqual(requests, [])
            work.pop()(); self.assertEqual((requests[0].full_url, output), ("http://127.0.0.1:18080/inference", ["ok"]))
            self.assertIn(b'name="file"; filename="audio.wav"', requests[0].data)
            self.assertIn(b'name="response_format"', requests[0].data)

    def test_failed_request_leaves_sticky_fence(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            marker = __import__("pathlib").Path(directory) / "fence"
            work, errors = [], []
            adapter = HttpWhisperRecognizer("http://127.0.0.1:18080", request=lambda _: (_ for _ in ()).throw(OSError("lost")),
                                             submit=work.append, fence=EndpointFence(marker))
            adapter.transcribe(b"wav", lambda _: None, errors.append); work.pop()()
            self.assertTrue(marker.exists()); self.assertTrue(errors)
            adapter.transcribe(b"wav", lambda _: None, errors.append)
            self.assertGreaterEqual(len(errors), 2)


class RunnerWiringTests(unittest.TestCase):
    def test_default_runner_session_is_disabled_before_capture_or_network(self):
        events = []; session = build_runtime_session(events.append)
        session.begin(0)
        self.assertEqual(events[-1]["phase"], "disabled")

    def test_attested_runner_wires_injected_concrete_adapters_without_execution(self):
        events, calls = [], []
        attestation = GpuAttestation("http://127.0.0.1:18080", "small", "pid:1", "start:1", True)
        config = RuntimeConfig("http://127.0.0.1:18080", "small", attestation, __import__("pathlib").Path("/tmp/test-live-marker"),
                               lambda: ("pid:1", "start:1"))
        session = build_runtime_session(events.append, config,
            capture_factory=lambda: calls.append("capture") or Capture(),
            recognizer_factory=lambda endpoint, **kwargs: calls.append(endpoint) or Recognizer())
        self.assertEqual(calls, ["capture", "http://127.0.0.1:18080"])
        session.begin(0); self.assertEqual(events[-1]["phase"], "recording")


    def test_runner_pipe_drains_burst_without_second_select_wakeup(self):
        import os
        class LoopSession:
            def __init__(self): self.calls = []
            def tick(self, now): self.calls.append(("tick", now))
            def begin(self, now): self.calls.append(("begin", now))
            def release(self): self.calls.append(("release",))
            def cancel(self): self.calls.append(("cancel",))
            def poll(self): self.calls.append(("poll",))
        read_fd, write_fd = os.pipe(); os.write(write_fd, b'{"command":"begin"}\n{"command":"release"}\n'); os.close(write_fd)
        try:
            session = LoopSession()
            run(session, CommandPipe(read_fd), lambda: 1.0)
            self.assertIn(("begin", 1.0), session.calls); self.assertIn(("release",), session.calls)
        finally:
            os.close(read_fd)

    def test_runner_loop_ticks_and_dispatches_without_subprocess_or_network(self):
        class LoopSession:
            def __init__(self): self.calls = []
            def tick(self, now): self.calls.append(("tick", now))
            def begin(self, now): self.calls.append(("begin", now))
            def release(self): self.calls.append(("release",))
            def cancel(self): self.calls.append(("cancel",))
            def poll(self): self.calls.append(("poll",))
        session, values = LoopSession(), iter([0.0, 2.0, 3.0])
        self.assertEqual(run(session, io.StringIO('{"command":"begin"}\n'), lambda: next(values), lambda *_: True), 0)
        self.assertIn(("begin", 2.0), session.calls)
        self.assertIn(("cancel",), session.calls)


class Bridge:
    def __init__(self, lease): self.lease, self.actions, self.document = lease, [], "αβ😀γ"
    def acquire_lease(self): return self.lease
    def replace_preedit_if_current(self, lease, text, final):
        if lease != self.lease: return False
        self.actions.append(("commit" if final else "preedit", text))
        if final:
            self.document = self.document[:lease.cursor] + text + self.document[lease.cursor:]
        return True
    def clear_preedit_if_current(self, lease):
        if lease != self.lease: return False
        self.actions.append(("clear",)); return True


class ControllerSession:
    def __init__(self): self.calls = []; self.generation = 0
    def begin(self, now): self.calls.append(("begin", now)); self.generation += 1; return self.generation
    def release(self): self.calls.append(("release",))
    def cancel(self): self.calls.append(("cancel",))
    def tick(self, now): self.calls.append(("tick", now))
    def poll(self): self.calls.append(("poll",))


class ControllerOwner:
    def __init__(self): self.calls = []; self.allowed = True
    def begin(self): self.calls.append(("begin",)); return self.allowed
    def revise(self, text): self.calls.append(("revise", text)); return True
    def commit_final(self, text): self.calls.append(("final", text)); return True
    def revoke(self, reason): self.calls.append(("revoke", reason))


class IBusControllerTests(unittest.TestCase):
    def test_f8_routes_session_revisions_and_watchdog_or_manual_keys_revoke(self):
        session, owner, timers, removed = ControllerSession(), ControllerOwner(), {}, []
        controller = IBusController(session, owner, lambda: 2.0,
            lambda seconds, callback: timers.setdefault(1, callback) or 1, removed.append, 8)
        self.assertTrue(controller.key(8, True)); self.assertEqual(session.calls, [("begin", 2.0)])
        controller.event({"event": "revision", "generation": 1, "sequence": 1, "samples": 3, "text": "😀 draft", "final": False})
        controller.key(8, False); self.assertIn(("release",), session.calls)
        controller.event({"event": "revision", "generation": 1, "sequence": 2, "samples": 4, "text": "final", "final": True})
        self.assertEqual(owner.calls[1:3], [("revise", "😀 draft"), ("final", "final")])
        controller.key(8, True); timers[1](); self.assertIn(("revoke", "hold timeout"), owner.calls)
        self.assertIn(("cancel",), session.calls)

    def test_new_hold_is_rejected_while_prior_final_is_pending(self):
        session, owner = ControllerSession(), ControllerOwner()
        controller = IBusController(session, owner, lambda: 0, lambda *_: 1, lambda _: None, 8)
        controller.key(8, True); controller.key(8, False)
        controller.key(8, True)
        self.assertEqual(owner.calls, [("begin",)])
        self.assertEqual([call for call in session.calls if call[0] == "begin"], [("begin", 0)])

    def test_manual_key_after_release_cancels_pending_final_and_latches_repeat_until_release(self):
        session, owner, timers = ControllerSession(), ControllerOwner(), {}
        controller = IBusController(session, owner, lambda: 0,
            lambda _, callback: timers.setdefault(1, callback) or 1, lambda _: None, 8)
        controller.key(8, True); controller.key(8, False)
        controller.key(42, True)
        self.assertIn(("revoke", "manual key"), owner.calls)
        self.assertIn(("cancel",), session.calls)
        controller.key(8, True)
        self.assertEqual([call for call in session.calls if call[0] == "begin"], [("begin", 0), ("begin", 0)])
        timers[1](); controller.key(8, True)
        self.assertEqual([call for call in session.calls if call[0] == "begin"], [("begin", 0), ("begin", 0)])
        controller.key(8, False); controller.key(8, True)
        self.assertEqual(len([call for call in session.calls if call[0] == "begin"]), 3)

    def test_wrong_generation_and_newline_are_never_delivered(self):
        session, owner = ControllerSession(), ControllerOwner()
        controller = IBusController(session, owner, lambda: 0, lambda *_: 1, lambda _: None, 8)
        controller.key(8, True)
        controller.event({"event": "revision", "generation": 2, "sequence": 1, "samples": 1, "text": "wrong", "final": True})
        controller.event({"event": "revision", "generation": 1, "sequence": 1, "samples": 1, "text": "bad\ntext", "final": True})
        self.assertNotIn(("final", "wrong"), owner.calls)
        self.assertIn(("revoke", "unsafe final text"), owner.calls)


class EngineTests(unittest.TestCase):
    def test_lazy_gi_engine_closure_routes_event_and_converts_microseconds(self):
        old_gi, old_repo = sys.modules.get("gi"), sys.modules.get("gi.repository")
        gi, repo = types.ModuleType("gi"), types.ModuleType("gi.repository")
        gi.require_version = lambda *_: None
        timers = {}
        class GLib:
            PRIORITY_DEFAULT = 0; SOURCE_REMOVE = False; SOURCE_CONTINUE = True
            get_monotonic_time = staticmethod(lambda: 2_000_000)
            timeout_add = staticmethod(lambda *_args: len(timers) + 1)
            class Source:
                remove = staticmethod(lambda _id: None)
        class Engine:
            def __init__(self, **_kwargs): pass
            def do_destroy(self): pass
        IBus = types.SimpleNamespace(Engine=Engine, KEY_F8=8,
                                    ModifierType=types.SimpleNamespace(RELEASE_MASK=1))
        repo.GLib, repo.IBus = GLib, IBus; gi.repository = repo
        sys.modules["gi"], sys.modules["gi.repository"] = gi, repo
        try:
            bridge = Bridge(Lease("gtk", 1, 3, True, True, False, 3, 3))
            calls, callback = [], []
            def factory(event):
                callback.append(event); session = ControllerSession(); calls.append(session); return session
            engine = engine_class(lambda: bridge, factory, lambda _: None)()
            engine.do_process_key_event(8, 0, 0)
            self.assertEqual(calls[0].calls, [("begin", 2.0)])
            callback[0]({"event": "revision", "generation": 1, "sequence": 1, "samples": 1, "text": "ok", "final": False})
            self.assertEqual(bridge.actions, [("preedit", "ok")])
            engine.do_set_content_type(None, None)
            self.assertIn(("cancel",), calls[0].calls)
            engine.do_disable()
            engine.do_set_surrounding_text(None, 0, 0)
            engine.do_set_cursor_location(0, 0, 0, 0)
        finally:
            if old_gi is None: sys.modules.pop("gi", None)
            else: sys.modules["gi"] = old_gi
            if old_repo is None: sys.modules.pop("gi.repository", None)
            else: sys.modules["gi.repository"] = old_repo


class IBusTests(unittest.TestCase):
    def test_generic_context_is_rejected(self):
        messages = []; owner = PreeditOwner(None, messages.append)
        self.assertFalse(owner.begin()); self.assertIn("cooperating editor bridge", messages[0])

    def test_unicode_prefilled_cursor_has_atomic_preedit_and_one_final_commit(self):
        bridge, messages = Bridge(Lease("gtk", 1, 3, True, True, False, 3, 3)), []
        owner = PreeditOwner(bridge, messages.append); self.assertTrue(owner.begin())
        self.assertTrue(owner.revise("😀 voice")); self.assertTrue(owner.commit_final("😀 final"))
        self.assertFalse(owner.commit_final("twice"))
        self.assertEqual(bridge.actions, [("preedit", "😀 voice"), ("commit", "😀 final")])
        self.assertEqual(bridge.document, "αβ😀😀 finalγ")

    def test_changed_lease_never_clears_or_commits(self):
        bridge, messages = Bridge(Lease("gtk", 1, 3, True, True, False, 3, 3)), []
        owner = PreeditOwner(bridge, messages.append); owner.begin()
        bridge.lease = Lease("gtk", 2, 3, True, True, False, 3, 3)
        self.assertFalse(owner.revise("must not deliver")); owner.revoke("focus changed")
        self.assertEqual(bridge.actions, [])


class ReadinessTests(unittest.TestCase):
    def test_attestation_rejects_missing_cuda_stale_identity_and_bad_origin(self):
        a = GpuAttestation("http://127.0.0.1:18080", "small.bin", "pid:1", "start:1", True)
        self.assertTrue(a.validate("http://127.0.0.1:18080", "small.bin", lambda: ("pid:1", "start:1"))[0])
        for endpoint in ["http://127.0.0.1:8080/", "http://127.0.0.1:9@evil"]:
            self.assertFalse(a.validate(endpoint, "small.bin", lambda: ("pid:1", "start:1"))[0])
        self.assertFalse(a.validate("http://127.0.0.1:18080", "small.bin", lambda: ("pid:2", "start:2"))[0])


if __name__ == "__main__": unittest.main()
