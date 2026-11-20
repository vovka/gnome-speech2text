"""Unregistered lazy-GI engine factory. Importing this module never imports GI or registers IBus."""
from __future__ import annotations

from typing import Callable

from live.ibus_controller import IBusController


def engine_class(bridge_factory: Callable[[], object | None], session_factory: Callable[[Callable], object],
                 recovery: Callable[[str], None], hold_key_name: str = "F8"):
    import gi
    gi.require_version("GLib", "2.0"); gi.require_version("IBus", "1.0")
    from gi.repository import GLib, IBus

    class LiveEngine(IBus.Engine):
        def __init__(self, **properties):
            super().__init__(**properties)
            controller = [None]
            def recover(reason):
                if controller[0] is not None:
                    controller[0].recover(reason)
                recovery(reason)
            owner = __import__("live.ibus", fromlist=["PreeditOwner"]).PreeditOwner(bridge_factory(), recover)
            def event(event):
                if controller[0] is not None:
                    controller[0].event(event)
            session = session_factory(event)
            self._controller = IBusController(session, owner, lambda: GLib.get_monotonic_time() / 1_000_000,
                lambda seconds, callback: GLib.timeout_add(GLib.PRIORITY_DEFAULT, int(seconds * 1000),
                                                            lambda: (callback(), GLib.SOURCE_REMOVE)[1]),
                GLib.Source.remove, getattr(IBus, f"KEY_{hold_key_name}"))
            controller[0] = self._controller
            self._tick_id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 50, self._tick)

        def _tick(self):
            self._controller.tick()
            return GLib.SOURCE_CONTINUE

        def do_focus_in(self): self._controller.focus_lost()
        def do_focus_out(self): self._controller.focus_lost()
        def do_reset(self): self._controller.reset()
        def do_disable(self): self._controller.disconnect()
        def do_set_content_type(self, _purpose, _hints): self._controller.revoke("content type changed")
        def do_set_surrounding_text(self, _text, _cursor, _anchor): self._controller.revoke("surrounding text changed")
        def do_set_cursor_location(self, _x, _y, _width, _height): self._controller.revoke("caret changed")
        def do_destroy(self):
            self._controller.disconnect()
            GLib.Source.remove(self._tick_id)
            super().do_destroy()
        def do_process_key_event(self, keyval, _keycode, state):
            released = bool(state & IBus.ModifierType.RELEASE_MASK)
            return self._controller.key(keyval, not released)

    return LiveEngine
