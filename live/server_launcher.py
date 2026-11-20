"""Owned backend process with startup-only, sanitized stderr evidence."""
from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Sequence


@dataclass(frozen=True)
class OwnedServer:
    pid: int
    pgid: int
    process: subprocess.Popen[bytes]


class StartupEvidenceDrainer:
    """Persist whitelisted startup facts until sealed; discard every later byte."""
    _facts = {
        "use_gpu": re.compile(rb"whisper_init_with_params_no_state: use gpu\s*=\s*1"),
        "gpu_device_0": re.compile(rb"whisper_init_with_params_no_state: gpu_device\s*=\s*0"),
        "device_0": re.compile(rb"Device 0:"),
        "cuda0_backend": re.compile(rb"using CUDA0 backend"),
        "model_loaded": re.compile(rb"whisper_model_load: model size"),
    }

    def __init__(self, stream: BinaryIO, evidence: Path) -> None:
        self._stream, self._evidence = stream, evidence
        self._sealed, self._seen, self._lock = threading.Event(), set[str](), threading.Lock()
        self._output: BinaryIO | None = None
        self._thread = threading.Thread(target=self._drain, daemon=True)

    def start(self) -> None:
        self._evidence.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._output = self._evidence.open("wb")
        self._thread.start()

    def facts(self) -> set[str]:
        with self._lock:
            return set(self._seen)

    def seal(self) -> set[str]:
        self._sealed.set()
        with self._lock:
            if self._output is not None:
                self._output.close()
                self._output = None
        self._evidence.chmod(0o600)
        return set(self._seen)

    def close(self) -> None:
        self.seal()
        self._thread.join(timeout=1)
        self._stream.close()

    def _drain(self) -> None:
        while chunk := self._stream.readline():
            if self._sealed.is_set():
                continue
            for fact, pattern in self._facts.items():
                if pattern.search(chunk):
                    with self._lock:
                        if self._output is not None and fact not in self._seen:
                            self._seen.add(fact)
                            self._output.write(f"{fact}\n".encode())
                            self._output.flush()


def start_owned_server(argv: Sequence[str]) -> OwnedServer:
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, start_new_session=True)
    return OwnedServer(process.pid, process.pid, process)
