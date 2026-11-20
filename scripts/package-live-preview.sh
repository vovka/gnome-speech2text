#!/bin/sh
# Build an isolated archive only. Never install, register, enable or run a backend.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output="$root/dist/speech2text-live-study@local.invalid.zip"
if [ -e "$output" ]; then
    echo "Refusing to overwrite $output; remove it explicitly to rebuild" >&2
    exit 1
fi
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT HUP INT TERM
mkdir -p "$stage/lib/components" "$stage/live" "$stage/schemas" "$root/dist"
cp "$root/experimental/extension.js" "$root/experimental/prefs.js" "$root/experimental/metadata.json" "$root/experimental/README.md" "$root/experimental/PUBLIC-SETUP.md" "$stage/"
cp "$root/experimental/schemas/"*.xml "$stage/schemas/"
cp "$root/src/lib/components/modalDialog.js" "$stage/lib/components/"
cp "$root/src/lib/constants.js" "$root/src/lib/logger.js" "$root/src/lib/uiUtils.js" "$stage/lib/"
cp "$root/src/live/"*.js "$stage/live/"
cp "$root/experimental/live/panel.js" "$stage/live/"
cp "$root/LICENSE" "$stage/"
glib-compile-schemas --strict "$stage/schemas"
(cd "$stage" && zip -qr "$output" .)
printf '%s\n' "$output"
