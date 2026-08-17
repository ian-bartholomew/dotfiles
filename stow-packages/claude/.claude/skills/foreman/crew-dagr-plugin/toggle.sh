#!/usr/bin/env bash
# Toggle the crew-dagr pane: if one is already open in this workspace, close it;
# otherwise open it (delegating to open.sh so the open path stays single-sourced).
#
# A crew-dagr pane is identified live by its foreground process running
# crew-dagr.py, the same "read the process, not a state file" approach reviewr
# uses. Parsing is python3 (our runtime), so no jq dependency.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

H="${HERDR_BIN_PATH:-herdr}"
place="${1:-right}"

ws="${HERDR_WORKSPACE_ID:-}"
if [ -z "$ws" ] && [ -n "${HERDR_PLUGIN_CONTEXT_JSON:-}" ]; then
  ws=$(printf '%s' "$HERDR_PLUGIN_CONTEXT_JSON" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("workspace_id",""))' 2>/dev/null || true)
fi
[ -n "$ws" ] || { echo "crew-dagr: no workspace context (invoke from inside herdr)" >&2; exit 1; }

# Print the pane ids in this workspace whose foreground process is crew-dagr.py.
# Exit non-zero (caught below) if herdr cannot be inspected, so a failed read is
# never mistaken for "no pane open" (which would stack duplicates on toggle).
existing=$(H="$H" ws="$ws" python3 <<'PY'
import json, os, subprocess, sys

H, ws = os.environ["H"], os.environ["ws"]

def herdr(*args):
    return subprocess.run([H, *args], capture_output=True, text=True)

pl = herdr("pane", "list", "--workspace", ws)
if pl.returncode != 0:
    sys.exit(3)
try:
    panes = json.loads(pl.stdout or "{}").get("result", {}).get("panes", [])
except json.JSONDecodeError:
    sys.exit(3)

hits = []
for p in panes:
    pid = p.get("pane_id")
    if not pid:
        continue
    pi = herdr("pane", "process-info", "--pane", pid)
    if pi.returncode != 0:
        continue
    try:
        info = json.loads(pi.stdout)
    except json.JSONDecodeError:
        continue
    procs = (info.get("result", {}).get("process_info", {}) or {}).get("foreground_processes", []) or []
    for fp in procs:
        blob = " ".join([str(fp.get("argv0", ""))] + [str(x) for x in (fp.get("argv") or [])])
        if "crew-dagr.py" in blob:
            hits.append(pid)
            break
print("\n".join(hits))
PY
) || { echo "crew-dagr: could not inspect panes" >&2; exit 1; }

if [ -n "$existing" ]; then
  while IFS= read -r p; do
    [ -n "$p" ] && "$H" pane close "$p" >/dev/null 2>&1
  done <<EOF
$existing
EOF
  echo "crew-dagr: pane closed"
else
  exec bash "$(dirname "$0")/open.sh" "$place"
fi
