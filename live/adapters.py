"""Concrete, injected capture/inference adapters. Construction does not activate them."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class Process(Protocol):
    stdout: object
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def kill(self) -> None: ...


def parent_death_signal() -> None:
    """Runs only in a spawned Linux child: SIGTERM if the runner dies."""
    if ctypes.CDLL(None).prctl(1, signal.SIGTERM) != 0:  # PR_SET_PDEATHSIG
        raise OSError("could not set parent-death signal")


class ContinuousPcmCapture:
    argv = ("ffmpeg", "-nostdin", "-loglevel", "error", "-f", "pulse", "-i", "default",
            "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1")
    max_samples = 16_000 * 300

    def __init__(self, process_factory: Callable[..., Process] | None = None,
                 read_available: Callable[[Process], bytes] | None = None) -> None:
        self._factory, self._read = process_factory or self._spawn, read_available
        self._process: Process | None = None
        self._pcm, self._tail, self._error = bytearray(), b"", None
        self._lock, self._reader = threading.Lock(), None

    @staticmethod
    def _spawn(*argv: str) -> Process:
        command = (sys.executable, "-m", "live.ffmpeg_child", str(os.getpid()), *argv)
        return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                start_new_session=True)  # type: ignore[return-value]

    def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("capture already started")
        self._pcm, self._tail, self._error = bytearray(), b"", None
        self._process = self._factory(*self.argv)
        if self._read is None:
            self._reader = threading.Thread(target=self._drain, args=(self._process,), daemon=False)
            self._reader.start()

    def _append(self, chunk: bytes) -> None:
        chunk = self._tail + chunk
        self._tail = chunk[-1:] if len(chunk) % 2 else b""
        chunk = chunk[:-1] if self._tail else chunk
        if len(self._pcm) + len(chunk) > self.max_samples * 2:
            self._error = ValueError("capture duration limit exceeded")
            return
        self._pcm.extend(chunk)

    def _drain(self, process: Process) -> None:
        try:
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    return
                with self._lock:
                    self._append(chunk)
        except Exception as error:
            with self._lock:
                self._error = error

    def pcm(self) -> bytes:
        if self._process is not None and self._read is not None:
            chunk = self._read(self._process)
            if chunk:
                with self._lock:
                    self._append(chunk)
        with self._lock:
            if self._error is not None:
                raise self._error
            return bytes(self._pcm)

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=1)
        if self._reader is not None:
            self._reader.join(timeout=1)
            if self._reader.is_alive():
                raise RuntimeError("capture reader did not stop")
            self._reader = None
        if self._read is not None:
            chunk = self._read(process)
            if chunk:
                with self._lock:
                    self._append(chunk)
        close = getattr(process.stdout, "close", None)
        if close is not None:
            close()
        with self._lock:
            if self._tail:
                self._error = ValueError("capture ended with incomplete PCM sample")
            if self._error is not None:
                raise self._error


def endpoint_marker(directory: Path, endpoint: str) -> Path:
    return directory / f"whisper-{hashlib.sha256(endpoint.encode()).hexdigest()[:16]}.inflight"


class EndpointFence:
    """Persistent marker prevents reuse after ambiguous/crashed inference until re-verification."""
    def __init__(self, marker: Path, metadata: dict | None = None) -> None:
        self.marker, self.metadata, self.active = marker, metadata, False

    def begin(self) -> None:
        if self.active:
            raise RuntimeError("backend endpoint is busy or requires operator re-verification")
        self.marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise RuntimeError("backend endpoint is busy or requires operator re-verification") from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            metadata = self.metadata() if callable(self.metadata) else self.metadata
            file.write(json.dumps(metadata) if metadata else "inflight\n")
        self.active = True

    def available(self) -> bool:
        return not self.active and not self.marker.exists()

    def success(self) -> None:
        if self.active:
            self.marker.unlink(missing_ok=True)
            self.active = False

    def poison(self) -> None:
        self.active = False


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise RuntimeError("redirect rejected")


class HttpWhisperRecognizer:
    def __init__(self, endpoint: str, request: Callable[[Request], bytes] | None = None,
                 submit: Callable[[Callable[[], None]], None] | None = None, fence: EndpointFence | None = None) -> None:
        if fence is None:
            raise ValueError("an explicit endpoint fence is required")
        self.endpoint = self._validate_endpoint(endpoint)
        self._request, self._submit, self._fence = request or self._send, submit or self._thread, fence

    @staticmethod
    def _validate_endpoint(endpoint: str) -> str:
        try:
            parsed, port = urlsplit(endpoint), urlsplit(endpoint).port
        except ValueError as error:
            raise ValueError("invalid loopback endpoint") from error
        if (parsed.scheme != "http" or parsed.username is not None or parsed.password is not None
                or parsed.hostname not in {"127.0.0.1", "::1"} or port is None or port == 0 or port == 8080
                or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("endpoint must be exact dedicated literal loopback origin")
        host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
        return f"http://{host}:{port}"

    @staticmethod
    def _send(request: Request) -> bytes:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=30) as response:
            return response.read()

    @staticmethod
    def _thread(work: Callable[[], None]) -> None:
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _multipart(wav: bytes) -> tuple[bytes, str]:
        boundary = "----speech2text-live-boundary"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\n"
                "Content-Type: audio/wav\r\n\r\n").encode() + wav + f"\r\n--{boundary}--\r\n".encode()
        return body, boundary

    def transcribe(self, wav: bytes, done: Callable[[str], None], failed: Callable[[Exception], None]) -> None:
        try:
            self._fence.begin()
        except Exception as error:
            failed(error); return
        def work() -> None:
            try:
                body, boundary = self._multipart(wav)
                request = Request(f"{self.endpoint}/inference", data=body, method="POST",
                                  headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"})
                response = json.loads(self._request(request).decode("utf-8"))
                text = response.get("text")
                if not isinstance(text, str):
                    raise ValueError("whisper response lacks text")
                self._fence.success(); done(text)
            except Exception as error:
                self._fence.poison(); failed(error)
        try:
            self._submit(work)
        except Exception as error:
            self._fence.poison()
            failed(error)
