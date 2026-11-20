#!/bin/sh
# Deliberate migration step; never run during packaging or extension enablement.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
unit=speech2text-live-preview.service
target="$HOME/.config/systemd/user/$unit"
source="$root/systemd/$unit"
receipt="$root/runtime/live-preview/everyday-unit-receipt"
mkdir -p "$(dirname "$target")"
if [ -e "$target" ]; then
  [ -r "$receipt" ] || { echo "Refusing unowned unit: $target" >&2; exit 1; }
  IFS= read -r recorded < "$receipt"
  expected="$(sha256sum "$target" | awk '{print $1}')  $target"
  [ "$recorded" = "$expected" ] && cmp -s "$source" "$target" || {
    echo "Refusing unknown or mutated unit: $target" >&2; exit 1;
  }
else
  mkdir -p "$(dirname "$receipt")"
  cp "$source" "$target"
  chmod 644 "$target"
fi
systemctl --user daemon-reload
systemctl --user enable "$unit"
printf '%s  %s\n' "$(sha256sum "$target" | awk '{print $1}')" "$target" > "$receipt"
chmod 600 "$receipt"
