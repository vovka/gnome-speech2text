"""Private JSONL runner; default absent configuration is unavailable and inert."""
from __future__ import annotations

import json
import os
import select
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

from live.adapters import ContinuousPcmCapture, EndpointFence, HttpWhisperRecognizer, endpoint_marker
from live.readiness import GpuAttestation
from live.session import DictationSession


@dataclass(frozen=True)
class RuntimeConfig:
    endpoint: str
    model: str
    attestation: GpuAttestation
    marker: Path
    identity_reader: Callable[[], tuple[str, str]]


def build_runtime_session(emit: Callable[[dict[str, object]], None], config: RuntimeConfig | None = None,
                          capture_factory=ContinuousPcmCapture, recognizer_factory=HttpWhisperRecognizer,
                          config_provider: Callable[[], RuntimeConfig] | None = None) -> DictationSession:
    if config_provider is not None:
        class Binding:
            current = None
            recognizer = None
            def bind(self):
                try:
                    self.current = config_provider()
                    self.recognizer = build_runtime_session(lambda _: None, self.current,
                        capture_factory, recognizer_factory).recognizer
                except (OSError, ValueError, KeyError, TypeError):
                    self.current = self.recognizer = None
            def ready(self):
                if self.current is None:
                    return False, "GPU disabled: backend configuration unavailable"
                try:
                    latest = config_provider()
                    if (latest.endpoint, latest.model, latest.attestation) != (
                            self.current.endpoint, self.current.model, self.current.attestation):
                        return False, "backend configuration changed during capture"
                    allowed, message = self.current.attestation.validate(
                        self.current.endpoint, self.current.model, self.current.identity_reader)
                    if allowed and self.current.marker.exists():
                        return False, "backend endpoint is busy or requires operator re-verification"
                    return allowed, message
                except (OSError, ValueError, KeyError, TypeError):
                    return False, "GPU disabled: backend configuration unavailable"
            def transcribe(self, wav, done, failed):
                self.recognizer.transcribe(wav, done, failed)
        binding = Binding()
        return DictationSession(capture_factory(), binding, emit, binding.ready, before_begin=binding.bind)
    if config is None:
        class InertCapture:
            def start(self): raise AssertionError("unready session started capture")
            def stop(self): pass
            def pcm(self): return b""
        class InertRecognizer:
            def transcribe(self, _wav, _done, _failed): raise AssertionError("unready session started inference")
        return DictationSession(InertCapture(), InertRecognizer(), emit,
                                lambda: (False, "GPU disabled: no current CUDA operator attestation"))
    def marker_metadata():
        client = os.getpid()
        client_start = Path(f"/proc/{client}/stat").read_text().rsplit(")", 1)[1].split()[19]
        server_id, server_start = config.identity_reader()
        return {"version": 1, "client": {"pid": client, "start": client_start},
                "backend": {"pid": int(server_id.removeprefix("pid:")), "start": server_start, "owned": True}}
    fence = EndpointFence(config.marker, marker_metadata)
    def ready():
        allowed, message = config.attestation.validate(config.endpoint, config.model, config.identity_reader)
        if allowed and not fence.available():
            return False, "backend endpoint is busy or requires operator re-verification"
        return allowed, message
    return DictationSession(capture_factory(), recognizer_factory(config.endpoint, fence=fence), emit, ready)


def load_config(path: Path) -> RuntimeConfig:
    """Reads explicit operator-owned evidence; no default/example can enable a runner."""
    data = json.loads(path.read_text(encoding="utf-8"))
    evidence = data["attestation"]
    attestation = GpuAttestation(data["endpoint"], data["model"], evidence["process_identity"],
                                 evidence["process_started"], evidence["cuda_verified"] is True)
    pid = int(data["server_pid"])
    def identity_reader() -> tuple[str, str]:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat.rsplit(")", 1)[1].split()
        return f"pid:{pid}", fields[19]  # starttime is field 22; fields begin at field 3.
    runtime_dir = Path(f"/run/user/{os.getuid()}/speech2text-live")
    return RuntimeConfig(data["endpoint"], data["model"], attestation,
                         endpoint_marker(runtime_dir, data["endpoint"]), identity_reader)


class CommandPipe:
    """Raw bounded line reader; preserves bursts already buffered after one select wakeup."""
    def __init__(self, descriptor: int) -> None:
        self.descriptor, self.buffer, self.eof = descriptor, bytearray(), False
    def fileno(self) -> int: return self.descriptor
    @property
    def has_line(self) -> bool: return b"\n" in self.buffer or self.eof
    def readline(self) -> str:
        while b"\n" not in self.buffer and not self.eof:
            chunk = os.read(self.descriptor, 4096)
            if not chunk: self.eof = True
            else: self.buffer.extend(chunk)
        if not self.buffer: return ""
        line, separator, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        return (line + separator).decode("utf-8")


def run(session: DictationSession, input_stream: TextIO, clock: Callable[[], float] = time.monotonic,
        wait: Callable[[TextIO, float], bool] | None = None) -> int:
    wait = wait or (lambda stream, timeout: bool(select.select([stream], [], [], timeout)[0]))
    try:
        while True:
            session.tick(clock())
            if not getattr(input_stream, "has_line", False) and not wait(input_stream, 0.05):
                continue
            line = input_stream.readline()
            if not line:
                return 0
            try:
                command = json.loads(line)["command"]
            except (json.JSONDecodeError, KeyError, TypeError):
                emit({"event": "status", "phase": "failed", "message": "invalid command"}); continue
            if command == "begin": session.begin(clock())
            elif command == "release": session.release()
            elif command == "cancel": session.cancel()
            elif command == "shutdown": return 0
            else: emit({"event": "status", "phase": "failed", "message": "invalid command"})
    finally:
        session.cancel()
        session.poll()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    provider = (lambda: load_config(Path(argv[1]))) if len(argv) == 2 and argv[0] == "--config" else None
    return run(build_runtime_session(emit, config_provider=provider), CommandPipe(sys.stdin.fileno()))


def emit(event: dict[str, object]) -> None:
    print(json.dumps(event, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
