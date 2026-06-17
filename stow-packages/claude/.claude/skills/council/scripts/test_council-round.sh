#!/usr/bin/env bash
# Self-check for council-round.sh: stub the three member CLIs on PATH (gemini fails)
# and assert fan-out, degradation, and manifest behavior. No framework.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- stubs ---
mkdir -p "$WORK/bin"

cat >"$WORK/bin/codex" <<'EOF'
#!/usr/bin/env bash
# codex exec ... -o <file> <prompt>  -> write clean answer to the -o file
out=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac
done
[ -n "$out" ] && echo "codex says: optimistic locking" >"$out"
exit 0
EOF

cat >"$WORK/bin/gemini" <<'EOF'
#!/usr/bin/env bash
echo "gemini boom" >&2
exit 1
EOF

cat >"$WORK/bin/claude" <<'EOF'
#!/usr/bin/env bash
echo "sonnet says: advisory lock"
exit 0
EOF

chmod +x "$WORK/bin/"*

# --- run ---
echo "pick a locking strategy" >"$WORK/prompt.txt"
manifest="$(PATH="$WORK/bin:$PATH" COUNCIL_TIMEOUT=10 \
  bash "$HERE/council-round.sh" --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/out")"
rc=$?

fail() { echo "FAIL: $1"; echo "--- manifest ---"; echo "$manifest"; exit 1; }

# at least one member ok -> exit 0
[ "$rc" = "0" ] || fail "expected exit 0 (2 members ok), got $rc"

# codex + sonnet ok, gemini failed
echo "$manifest" | grep -q $'codex\tok' || fail "codex should be ok"
echo "$manifest" | grep -q $'sonnet\tok' || fail "sonnet should be ok"
echo "$manifest" | grep -q $'gemini\tfailed' || fail "gemini should be failed"

# answers actually captured
grep -q "optimistic locking" "$WORK/out/codex.out" || fail "codex answer not captured"
grep -q "advisory lock" "$WORK/out/sonnet.out" || fail "sonnet answer not captured"

echo "PASS: fan-out, degradation, and capture all work"

# --- timeout case: a hung member is killed, its child reaped, reported failed(timeout) ---
# Each stub forks a child that ticks a per-member marker file, then hangs. On timeout the
# watchdog must reap the child (pkill -P) so the marker stops growing.
mkdir -p "$WORK/slowbin"
for c in codex gemini claude; do
  cat >"$WORK/slowbin/$c" <<EOF
#!/usr/bin/env bash
( while :; do echo tick >>"$WORK/${c}.tick"; sleep 0.2; done ) &
sleep 30
EOF
done
chmod +x "$WORK/slowbin/"*

to_manifest="$(PATH="$WORK/slowbin:$PATH" COUNCIL_TIMEOUT=1 \
  bash "$HERE/council-round.sh" --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/out2")"
to_rc=$?

[ "$to_rc" = "1" ] || { echo "FAIL: expected exit 1 (all timed out), got $to_rc"; exit 1; }
echo "$to_manifest" | grep -q 'failed(timeout)' || {
  echo "FAIL: expected a failed(timeout) line"; echo "$to_manifest"; exit 1; }

echo "PASS: hung members are killed and reported as failed(timeout)"

# child must be reaped: marker stops growing after the kill
sleep 1
a=$(wc -l <"$WORK/codex.tick" 2>/dev/null || echo 0)
sleep 1
b=$(wc -l <"$WORK/codex.tick" 2>/dev/null || echo 0)
[ "$a" = "$b" ] || { echo "FAIL: timed-out member's child survived (ticks $a -> $b)"; exit 1; }

echo "PASS: timed-out member's child process is reaped"

# --- guard rails: bad timeout and missing binary fail fast ---
PATH="$WORK/bin:$PATH" COUNCIL_TIMEOUT=0 bash "$HERE/council-round.sh" \
  --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/out3" >/dev/null 2>&1 \
  && { echo "FAIL: TIMEOUT=0 should be rejected"; exit 1; }

# PATH without the member stubs -> preflight should fail naming the missing binary
miss_err="$(PATH="/usr/bin:/bin" bash "$HERE/council-round.sh" \
  --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/out4" 2>&1)" \
  && { echo "FAIL: missing CLIs should fail preflight"; exit 1; }
echo "$miss_err" | grep -q 'missing required CLI' || {
  echo "FAIL: expected missing-CLI message, got: $miss_err"; exit 1; }

echo "PASS: TIMEOUT=0 and missing-binary preflight both fail fast"

# --- --members filter: a single-member run yields only that member (used by /consensus) ---
mem_manifest="$(PATH="$WORK/bin:$PATH" COUNCIL_TIMEOUT=10 \
  bash "$HERE/council-round.sh" --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/out5" --members codex)"
mem_rc=$?
[ "$mem_rc" = "0" ] || { echo "FAIL: single-member run should exit 0"; echo "$mem_manifest"; exit 1; }
[ "$(echo "$mem_manifest" | grep -c .)" = "1" ] || { echo "FAIL: expected exactly one manifest line"; echo "$mem_manifest"; exit 1; }
echo "$mem_manifest" | grep -q $'codex\tok' || { echo "FAIL: expected codex ok"; echo "$mem_manifest"; exit 1; }

# unknown member rejected
PATH="$WORK/bin:$PATH" bash "$HERE/council-round.sh" \
  --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/out6" --members bogus >/dev/null 2>&1 \
  && { echo "FAIL: unknown member should be rejected"; exit 1; }

echo "PASS: --members runs a single model and rejects unknown members"
