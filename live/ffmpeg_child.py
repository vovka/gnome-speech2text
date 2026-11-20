"""Minimal Linux exec wrapper that arms parent-death termination before FFmpeg starts."""
from __future__ import annotations

import os
import sys

from live.adapters import parent_death_signal


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        return 127
    expected_parent = int(argv[0])
    parent_death_signal()
    if os.getppid() != expected_parent:
        return 127
    os.execvp(argv[1], argv[1:])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
