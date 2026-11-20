"""Guarded IBus preedit ownership. Generic IBus contexts are intentionally rejected."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Lease:
    """An opaque lease issued by a cooperating editor, never inferred from IBus signals."""
    context_id: str
    focus_epoch: int
    cursor: int
    collapsed: bool
    editable: bool
    sensitive: bool
    selection_start: int = 0
    selection_end: int = 0
    composition_active: bool = False
    known_context: bool = True


class AtomicBridge(Protocol):
    """The editor must atomically validate lease + mutate its own preedit at this seam."""
    def acquire_lease(self) -> Lease | None: ...
    def replace_preedit_if_current(self, lease: Lease, text: str, final: bool) -> bool: ...
    def clear_preedit_if_current(self, lease: Lease) -> bool: ...


class PreeditOwner:
    """No surrounding-text operations; the bridge, not a boolean, establishes atomicity."""
    def __init__(self, bridge: AtomicBridge | None, recovery) -> None:
        self.bridge, self.recovery, self.lease, self.final = bridge, recovery, None, False

    def begin(self) -> bool:
        if self.bridge is None:
            self.recovery("IBus unavailable: no validated cooperating editor bridge")
            return False
        lease = self.bridge.acquire_lease()
        if lease is None or not (lease.known_context and lease.collapsed and lease.editable and not lease.sensitive
                                 and not lease.composition_active and lease.selection_start == lease.cursor
                                 and lease.selection_end == lease.cursor):
            self.recovery("IBus unavailable: invalid target")
            return False
        self.lease, self.final = lease, False
        return True

    def revise(self, text: str) -> bool:
        return self._replace(text, False)

    def commit_final(self, text: str) -> bool:
        if not self._replace(text, True):
            return False
        self.final, self.lease = True, None
        return True

    def revoke(self, reason: str) -> None:
        lease, self.lease = self.lease, None
        if lease is not None and self.bridge is not None:
            self.bridge.clear_preedit_if_current(lease)
        self.recovery(reason)

    def _replace(self, text: str, final: bool) -> bool:
        if final and any(ord(character) < 32 for character in text):
            self.revoke("IBus delivery revoked: control text")
            return False
        if self.lease is None or self.bridge is None or self.final:
            return False
        if not self.bridge.replace_preedit_if_current(self.lease, text, final):
            self.lease = None
            self.recovery("IBus delivery revoked: editor lease changed")
            return False
        return True
