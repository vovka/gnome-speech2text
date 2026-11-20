#!/bin/sh
# Stops/removes only the exact study-owned unit recorded by installation.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
unit=speech2text-live-preview.service
target="$HOME/.config/systemd/user/$unit"
receipt="$root/runtime/live-preview/everyday-unit-receipt"
[ -r "$receipt" ] || { echo "Missing deployment receipt" >&2; exit 1; }
set -- $(cat "$receipt")
[ "$2" = "$target" ] && [ -f "$target" ] || { echo "Receipt path mismatch" >&2; exit 1; }
[ "$1" = "$(sha256sum "$target" | awk '{print $1}')" ] || { echo "Refusing mutated unit" >&2; exit 1; }
systemctl --user disable --now "$unit"
rm "$target" "$receipt"
systemctl --user daemon-reload
