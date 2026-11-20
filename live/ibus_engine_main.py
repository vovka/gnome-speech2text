"""Explicit-only IBus host entry. Importing it is inert; no installer invokes it."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

from live.ibus_engine import engine_class
from live.preview_runner import build_runtime_session, load_config


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 5 or argv[0] != "--enable-host" or argv[1] != "--config" or argv[3] != "--bridge-module":
        print("explicit --enable-host --config FILE --bridge-module MODULE required", file=sys.stderr)
        return 2
    config = load_config(Path(argv[2]))
    bridge_factory = getattr(importlib.import_module(argv[4]), "create_bridge")
    import gi
    gi.require_version("IBus", "1.0")
    from gi.repository import IBus
    bus = IBus.Bus()
    if not bus.is_connected():
        raise RuntimeError("IBus bus is unavailable")
    component = IBus.Component.new("org.gnome.Shell.Extensions.Speech2TextLiveStudy", "", "", "", "", "", "", "")
    engine = engine_class(bridge_factory, lambda emit: build_runtime_session(emit, config), lambda _reason: None)
    factory = IBus.Factory.new(bus.get_connection())
    factory.add_engine("speech2text-live-study", engine)
    component.add_engine(IBus.EngineDesc.new("speech2text-live-study", "Speech2Text Live Study", "Guarded study engine",
                                             "en", "MIT", "Local study", "", "us"))
    bus.register_component(component)
    IBus.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
