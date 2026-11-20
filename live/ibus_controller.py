"""Plain, fake-testable IBus lifecycle controller; GI engine only delegates here."""
from __future__ import annotations

from typing import Callable


class IBusController:
    def __init__(self, session, owner, clock: Callable[[], float], schedule, unschedule,
                 hold_key: int, watchdog_seconds: float = 30) -> None:
        self.session, self.owner, self.clock = session, owner, clock
        self.schedule, self.unschedule, self.hold_key, self.watchdog_seconds = schedule, unschedule, hold_key, watchdog_seconds
        self.held = self.latched = self.active = False
        self.generation = self.sequence = self.samples = 0
        self.final, self.timer, self.recovery_text = False, None, ""

    def key(self, keyval: int, pressed: bool) -> bool:
        if keyval == self.hold_key:
            if pressed: self.begin()
            else: self.release()
            return True
        if pressed and self.active:
            self.revoke("manual key")
        return False

    def begin(self) -> None:
        if self.latched or self.active or not self.owner.begin():
            return
        self.latched = self.held = self.active = True
        self.generation = self.session.begin(self.clock())
        self.sequence = self.samples = 0
        self.final = False
        self.timer = self.schedule(self.watchdog_seconds, lambda: self.revoke("hold timeout"))

    def release(self) -> None:
        if not self.latched:
            return
        self.latched, self.held = False, False
        self._remove_timer()
        if self.active:
            self.session.release()

    def tick(self) -> None:
        self.session.tick(self.clock())
        self.session.poll()

    def event(self, event: dict[str, object]) -> None:
        if event.get("event") != "revision" or not self.active or self.final:
            return
        generation, sequence, samples = event.get("generation"), event.get("sequence"), event.get("samples")
        text, final = event.get("text"), event.get("final")
        if (generation != self.generation or not isinstance(sequence, int) or sequence <= self.sequence
                or not isinstance(samples, int) or samples < self.samples or not isinstance(text, str)
                or not isinstance(final, bool)):
            return
        if "\n" in text or "\r" in text:
            self.revoke("unsafe final text")
            return
        self.recovery_text = text
        delivered = self.owner.commit_final(text) if final else self.owner.revise(text)
        if not delivered:
            self.revoke("bridge delivery rejected")
            return
        self.sequence, self.samples, self.final = sequence, samples, final
        if final:
            self.active = False
            self._remove_timer()

    def recover(self, _reason: str) -> None:
        """Retain only current in-memory recognition; never focus/copy/log it."""

    def revoke(self, reason: str) -> None:
        self.held = self.active = False
        self._remove_timer()
        self.owner.revoke(reason)
        self.session.cancel()

    def focus_lost(self) -> None: self.revoke("focus lost")
    def reset(self) -> None: self.revoke("input method reset")
    def disconnect(self) -> None: self.revoke("engine disconnected")

    def _remove_timer(self) -> None:
        if self.timer is not None:
            self.unschedule(self.timer)
            self.timer = None
