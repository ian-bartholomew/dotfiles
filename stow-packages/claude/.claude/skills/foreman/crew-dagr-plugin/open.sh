#!/usr/bin/env bash
# Launcher for the crew-dagr pane. herdr actions run a command (there is no
# declarative "open this pane" field), so this shells back into the herdr CLI
# and opens the [[panes]] entrypoint as a split. No cwd resolution: the run
# file is a fixed global path, so the pane is the same regardless of where the
# action fired.
set -uo pipefail

herdr_bin="${HERDR_BIN_PATH:-herdr}"
plugin_id="${HERDR_PLUGIN_ID:-crew-dagr}"

# herdr splits go right/down natively; left/up open then swap across.
place="${1:-right}"
case "$place" in
  right) direction="right"; swap="" ;;
  down)  direction="down";  swap="" ;;
  left)  direction="right"; swap="left" ;;
  up)    direction="down";  swap="up" ;;
  *)     direction="right"; swap="" ;;
esac

args=(plugin pane open
  --plugin "$plugin_id"
  --entrypoint dagr
  --placement split
  --direction "$direction"
  --focus)

if [ -z "$swap" ]; then
  exec "$herdr_bin" "${args[@]}"
fi

# left/up: capture the opened pane id from the JSON reply, then swap it across.
# No jq on purpose; first "pane_id" wins.
out=$("$herdr_bin" "${args[@]}") || exit 1
pane_id=$(printf '%s' "$out" | sed -nE 's/.*"pane_id":"([^"]+)".*/\1/p' | head -1)
if [ -n "$pane_id" ]; then
  "$herdr_bin" pane swap --pane "$pane_id" --direction "$swap" >/dev/null
fi
