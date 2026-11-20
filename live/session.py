"""Single-owner-thread dictation lifecycle with queued recognizer completions."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from io import BytesIO
import wave
from typing import Callable, Protocol

RATE = 16_000


class Capture(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def pcm(self) -> bytes: ...


class Recognizer(Protocol):
    def transcribe(self, wav: bytes, done: Callable[[str], None], failed: Callable[[Exception], None]) -> None: ...


@dataclass(frozen=True)
class Revision:
    generation: int
    sequence: int
    samples: int
    final: bool
    text: str

    def event(self) -> dict[str, object]:
        return {"event": "revision", **self.__dict__}


class DictationSession:
    """Owner calls methods/poll; capture and recognition callbacks only enqueue work."""
    def __init__(self, capture: Capture, recognizer: Recognizer, emit: Callable[[dict[str, object]], None],
                 ready: Callable[[], tuple[bool, str]], cadence_seconds: float = 2.0,
                 before_begin: Callable[[], None] | None = None) -> None:
        self.capture, self.recognizer, self.emit, self.ready = capture, recognizer, emit, ready
        self.cadence, self.generation, self.sequence = cadence_seconds, 0, 0
        self.phase, self.last_tick, self.inflight, self.pending = "idle", 0.0, False, None
        self._job, self._events = 0, deque()
        self.before_begin = before_begin

    def begin(self, now: float) -> int:
        self.generation += 1
        self.sequence, self.pending = 0, None
        if self.phase in {"cancelled", "disabled", "failed"} and not self.inflight:
            self.phase = "idle"
        try:
            if self.phase == "idle" and not self.inflight and self.before_begin is not None:
                self.before_begin()
            allowed, message = self.ready()
        except Exception as error:
            self._fail(error)
            return self.generation
        if self.phase != "idle" or self.inflight:
            self._revoke("session already active")
            return self.generation
        if not allowed:
            self.phase = "disabled"
            self._emit({"event": "status", "phase": "disabled", "message": message})
            return self.generation
        try:
            self.phase, self.last_tick = "recording", now
            self.capture.start()
            if self.phase == "recording":
                self._emit({"event": "status", "phase": "recording", "message": "capture started"})
        except Exception as error:
            self._fail(error)
        return self.generation

    def tick(self, now: float) -> None:
        self.poll()
        if self.phase == "recording" and now - self.last_tick >= self.cadence:
            self.last_tick = now
            self._queue(False)

    def release(self) -> None:
        if self.phase != "recording":
            self._emit({"event": "status", "phase": self.phase,
                        "message": "release ignored: runner is not recording"})
            return
        self.phase, self.pending = "finalizing", None
        try:
            self.capture.stop()
            self._emit({"event": "status", "phase": "finalizing", "message": "finalizing; draining final snapshot"})
            self._queue(True)
        except Exception as error:
            self._fail(error)

    def cancel(self) -> None:
        self._revoke("delivery revoked")

    def poll(self) -> None:
        while self._events:
            job_id, job, text, error = self._events.popleft()
            if job_id != self._job:
                continue
            self.inflight = False
            if error is not None:
                self._fail(error)
                continue
            _wav, samples, final, generation = job
            expected = "finalizing" if final else "recording"
            if generation == self.generation and self.phase == expected:
                self.sequence += 1
                self._emit(Revision(generation, self.sequence, samples, final, text).event())
                if final:
                    self.phase = "idle"
                    self._emit({"event": "status", "phase": "idle",
                                "message": "Final revision ready; edit or Copy explicitly"})
            queued, self.pending = self.pending, None
            if queued is not None and self.phase in {"recording", "finalizing"}:
                self._start(queued)
            elif not self.inflight and self.phase in {"cancelled", "disabled", "failed"}:
                self.phase = "idle"

    def _revoke(self, message: str) -> None:
        if self.phase in {"recording", "finalizing"}:
            self.phase, self.pending = "cancelled", None
            try:
                self.capture.stop()
            except Exception as error:
                self._fail(error)
                return
        elif self.phase != "idle":
            self.phase, self.pending = "cancelled", None
        self._emit({"event": "status", "phase": "cancelled", "message": message})
        if not self.inflight:
            self.phase = "idle"

    def _queue(self, final: bool) -> None:
        try:
            pcm = self.capture.pcm()
        except Exception as error:
            self._fail(error)
            return
        if len(pcm) % 2:
            self._fail(ValueError("invalid mono PCM"))
            return
        job = (self._wav(pcm), len(pcm) // 2, final, self.generation)
        if self.inflight:
            self.pending = job
        else:
            self._start(job)

    def _start(self, job: tuple[bytes, int, bool, int]) -> None:
        try:
            allowed, message = self.ready()
        except Exception as error:
            self._fail(error)
            return
        if not allowed:
            self._fail(RuntimeError(message))
            return
        self.inflight, self._job = True, self._job + 1
        job_id, completed = self._job, False
        def done(text: str) -> None:
            nonlocal completed
            if not completed:
                completed = True
                self._events.append((job_id, job, text, None))
        def failed(error: Exception) -> None:
            nonlocal completed
            if not completed:
                completed = True
                self._events.append((job_id, job, "", error))
        try:
            self.recognizer.transcribe(job[0], done, failed)
        except Exception as error:
            failed(error)

    def _fail(self, error: Exception) -> None:
        self.pending, self.phase = None, "failed"
        try:
            self.capture.stop()
        except Exception:
            pass
        self._emit({"event": "status", "phase": "failed", "message": str(error)})
        if not self.inflight:
            self.phase = "idle"

    def _emit(self, event: dict[str, object]) -> None:
        if event.get("event") == "status":
            event = {**event, "generation": self.generation}
        try:
            self.emit(event)
        except Exception:
            self.pending, self.phase = None, "failed"
            try:
                self.capture.stop()
            except Exception:
                pass

    @staticmethod
    def _wav(pcm: bytes) -> bytes:
        output = BytesIO()
        with wave.open(output, "wb") as file:
            file.setnchannels(1); file.setsampwidth(2); file.setframerate(RATE); file.writeframes(pcm)
        return output.getvalue()
