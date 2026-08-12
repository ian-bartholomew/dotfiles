#!/usr/bin/env bash
# Self-check for council-round.sh: stub the three member CLIs on PATH (antigravity fails)
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

cat >"$WORK/bin/agy" <<'EOF'
#!/usr/bin/env bash
echo "agy boom" >&2
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

# codex + sonnet ok, antigravity failed
echo "$manifest" | grep -q $'codex\tok' || fail "codex should be ok"
echo "$manifest" | grep -q $'sonnet\tok' || fail "sonnet should be ok"
echo "$manifest" | grep -q $'antigravity\tfailed' || fail "antigravity should be failed"

# answers actually captured
grep -q "optimistic locking" "$WORK/out/codex.out" || fail "codex answer not captured"
grep -q "advisory lock" "$WORK/out/sonnet.out" || fail "sonnet answer not captured"

echo "PASS: fan-out, degradation, and capture all work"

# --- timeout case: a hung member is killed, its child reaped, reported failed(timeout) ---
# Each stub forks a child that ticks a per-member marker file, then hangs. On timeout the
# watchdog must reap the child (pkill -P) so the marker stops growing.
mkdir -p "$WORK/slowbin"
for c in codex agy claude; do
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

# --- antigravity token-exhaustion fallback: primary OOM -> retry on the gpt-oss model ---
mkdir -p "$WORK/aybin"
cat >"$WORK/aybin/agy" <<'EOF'
#!/usr/bin/env bash
# Fallback model present -> answer; otherwise simulate quota/token exhaustion.
if printf '%s\n' "$@" | grep -q 'GPT-OSS'; then
  echo "answer from gpt-oss fallback"; exit 0
fi
echo "Error: you have exhausted your capacity on this model" >&2
exit 1
EOF
chmod +x "$WORK/aybin/agy"

fb_manifest="$(PATH="$WORK/aybin:$PATH" COUNCIL_TIMEOUT=10 \
  bash "$HERE/council-round.sh" --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/out7" --members antigravity)"
[ $? = 0 ] || { echo "FAIL: fallback should make the round succeed"; echo "$fb_manifest"; exit 1; }
echo "$fb_manifest" | grep -q $'antigravity\tok' || { echo "FAIL: antigravity should be ok via fallback"; echo "$fb_manifest"; exit 1; }
grep -q "gpt-oss fallback" "$WORK/out7/antigravity.out" || { echo "FAIL: fallback answer not captured"; exit 1; }
echo "PASS: antigravity falls back to gpt-oss on token exhaustion"

# a non-quota failure must NOT trigger the fallback (the gpt-oss retry can't fix a crash)
cat >"$WORK/aybin/agy" <<'EOF'
#!/usr/bin/env bash
echo "panic: runtime error, nil pointer" >&2
exit 1
EOF
chmod +x "$WORK/aybin/agy"
nofb_manifest="$(PATH="$WORK/aybin:$PATH" COUNCIL_TIMEOUT=10 \
  bash "$HERE/council-round.sh" --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/out8" --members antigravity)"
echo "$nofb_manifest" | grep -q $'antigravity\tfailed' || { echo "FAIL: non-quota failure should stay failed (no fallback)"; echo "$nofb_manifest"; exit 1; }
echo "PASS: non-quota failures do not trigger the gpt-oss fallback"

# --- regression: the sonnet seat must NOT use --permission-mode plan ---
# Plan mode makes claude explore/plan before answering (~5x slower: 321s vs 61s on a
# hard prompt), which blows the per-member timeout so sonnet gets killed and the round
# degrades to "only antigravity returns". Read-only is preserved without it (headless
# -p denies write tools). Pin the flag out so it can't silently return.
grep -v '^[[:space:]]*#' "$HERE/council-round.sh" | grep -q 'permission-mode plan' \
  && { echo "FAIL: sonnet seat reintroduced --permission-mode plan (causes timeout drop-out)"; exit 1; }
echo "PASS: sonnet seat does not use plan mode"

# --- regression: a member slower than the timeout is dropped, faster members survive ---
# The real-world failure shape: one seat over budget, the rest fine, round still succeeds.
mkdir -p "$WORK/mixbin"
cat >"$WORK/mixbin/codex" <<EOF
#!/usr/bin/env bash
out=""; while [ \$# -gt 0 ]; do case "\$1" in -o) out="\$2"; shift 2 ;; *) shift ;; esac; done
[ -n "\$out" ] && echo "codex fast answer" >"\$out"; exit 0
EOF
# sonnet is the slow one: hangs past the 1s timeout
cat >"$WORK/mixbin/claude" <<'EOF'
#!/usr/bin/env bash
sleep 30
EOF
cat >"$WORK/mixbin/agy" <<'EOF'
#!/usr/bin/env bash
echo "agy fast answer"; exit 0
EOF
chmod +x "$WORK/mixbin/"*
mix_manifest="$(PATH="$WORK/mixbin:$PATH" COUNCIL_TIMEOUT=1 \
  bash "$HERE/council-round.sh" --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/out9")"
mix_rc=$?
[ "$mix_rc" = "0" ] || { echo "FAIL: mixed round should exit 0 (2 fast members ok), got $mix_rc"; echo "$mix_manifest"; exit 1; }
echo "$mix_manifest" | grep -q $'codex\tok'                || { echo "FAIL: fast codex should be ok"; echo "$mix_manifest"; exit 1; }
echo "$mix_manifest" | grep -q $'antigravity\tok'          || { echo "FAIL: fast antigravity should be ok"; echo "$mix_manifest"; exit 1; }
echo "$mix_manifest" | grep -q $'sonnet\tfailed(timeout)'  || { echo "FAIL: slow sonnet should be failed(timeout)"; echo "$mix_manifest"; exit 1; }
echo "PASS: a slow member is dropped as timeout while faster members survive"

# --- regression: duplicate --members is rejected (would race two writers on one file) ---
PATH="$WORK/bin:$PATH" bash "$HERE/council-round.sh" \
  --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/outdup" --members codex,codex >/dev/null 2>&1 \
  && { echo "FAIL: duplicate member should be rejected"; exit 1; }
echo "PASS: duplicate --members rejected"

# --- regression: a prompt beginning with a dash reaches codex as the prompt, not a flag ---
# council-round must pass `-- "$PROMPT"` so codex option-parsing can't swallow it.
mkdir -p "$WORK/c5bin"
cat >"$WORK/c5bin/codex" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$@" > "$WORK/cargs"
out=""; while [ \$# -gt 0 ]; do case "\$1" in -o) out="\$2"; shift 2 ;; *) shift ;; esac; done
[ -n "\$out" ] && echo "ok" >"\$out"; exit 0
EOF
chmod +x "$WORK/c5bin/codex"
printf -- '-v danger' > "$WORK/dashprompt.txt"
PATH="$WORK/c5bin:$PATH" COUNCIL_TIMEOUT=10 bash "$HERE/council-round.sh" \
  --prompt-file "$WORK/dashprompt.txt" --out-dir "$WORK/outdash" --members codex >/dev/null 2>&1
grep -A1 '^--$' "$WORK/cargs" | grep -qx -- '-v danger' \
  || { echo "FAIL: dash-prompt not isolated after -- (got:)"; cat "$WORK/cargs"; exit 1; }
echo "PASS: dash-leading prompt is isolated after -- for codex"

# --- regression (C3): a TERM-ignoring child is still SIGKILLed after timeout ---
# The watchdog must snapshot the process tree and escalate to KILL; if it only sends TERM
# (which the child traps away) or is cut off before escalating, the child leaks.
mkdir -p "$WORK/ignbin"
cat >"$WORK/ignbin/agy" <<EOF
#!/usr/bin/env bash
( trap '' TERM; while :; do sleep 0.2; done ) &
echo \$! > "$WORK/ign.childpid"   # \$! (not \$BASHPID: absent in macOS bash 3.2)
wait
EOF
chmod +x "$WORK/ignbin/agy"
PATH="$WORK/ignbin:$PATH" COUNCIL_TIMEOUT=1 bash "$HERE/council-round.sh" \
  --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/outign" --members antigravity >/dev/null 2>&1
sleep 3   # allow TERM(trapped) -> sleep 2 -> KILL escalation to run
ignpid="$(cat "$WORK/ign.childpid" 2>/dev/null)"
if [ -n "$ignpid" ] && kill -0 "$ignpid" 2>/dev/null; then
  kill -KILL "$ignpid" 2>/dev/null   # don't leak the process onto the test host
  echo "FAIL: TERM-ignoring child survived the watchdog (pid $ignpid)"; exit 1
fi
echo "PASS: TERM-ignoring child is escalated to SIGKILL"

# --- regression (C2): SIGTERM to the round reaps its members promptly (no orphan wait) ---
# Without a trap, interrupting the script orphans the member CLIs; they only die when their
# watchdog fires at the full timeout. The trap must tear the subtree down immediately.
mkdir -p "$WORK/trapbin"
for c in codex agy claude; do
  cat >"$WORK/trapbin/$c" <<EOF
#!/usr/bin/env bash
echo \$\$ > "$WORK/${c}.pid"
sleep 30
EOF
done
chmod +x "$WORK/trapbin/"*
rm -f "$WORK/codex.pid" "$WORK/agy.pid" "$WORK/claude.pid"
PATH="$WORK/trapbin:$PATH" COUNCIL_TIMEOUT=30 bash "$HERE/council-round.sh" \
  --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/outtrap" >/dev/null 2>&1 &
crpid=$!
for i in $(seq 1 50); do
  [ -f "$WORK/codex.pid" ] && [ -f "$WORK/agy.pid" ] && [ -f "$WORK/claude.pid" ] && break; sleep 0.1
done
kill -TERM "$crpid" 2>/dev/null
sleep 3
leaked=""
for c in codex agy claude; do p=$(cat "$WORK/${c}.pid" 2>/dev/null); [ -n "$p" ] && kill -0 "$p" 2>/dev/null && leaked="$leaked $c($p)"; done
if [ -n "$leaked" ]; then
  for c in codex agy claude; do p=$(cat "$WORK/${c}.pid" 2>/dev/null); [ -n "$p" ] && kill -KILL "$p" 2>/dev/null; done
  echo "FAIL: members leaked after SIGTERM to the script:$leaked"; exit 1
fi
echo "PASS: SIGTERM to the round reaps its members promptly"

# --- S4: COUNCIL_QUORUM returns on quorum without blocking on the slow member ---
mkdir -p "$WORK/qbin"
cat >"$WORK/qbin/codex" <<EOF
#!/usr/bin/env bash
out=""; while [ \$# -gt 0 ]; do case "\$1" in -o) out="\$2"; shift 2 ;; *) shift ;; esac; done
[ -n "\$out" ] && echo "codex quick" >"\$out"; exit 0
EOF
cat >"$WORK/qbin/agy" <<'EOF'
#!/usr/bin/env bash
echo "agy quick"; exit 0
EOF
cat >"$WORK/qbin/claude" <<'EOF'
#!/usr/bin/env bash
sleep 30
EOF
chmod +x "$WORK/qbin/"*
q_start=$SECONDS
q_manifest="$(PATH="$WORK/qbin:$PATH" COUNCIL_TIMEOUT=30 COUNCIL_QUORUM=2 \
  bash "$HERE/council-round.sh" --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/outq")"
q_el=$((SECONDS - q_start))
[ "$q_el" -lt 10 ] || { echo "FAIL: quorum should return fast (took ${q_el}s; slow member not reaped)"; echo "$q_manifest"; exit 1; }
echo "$q_manifest" | grep -q $'codex\tok'       || { echo "FAIL: codex should be ok"; echo "$q_manifest"; exit 1; }
echo "$q_manifest" | grep -q $'antigravity\tok' || { echo "FAIL: antigravity should be ok"; echo "$q_manifest"; exit 1; }
echo "$q_manifest" | grep -q $'sonnet\tok'      && { echo "FAIL: slow sonnet must not be ok under quorum=2"; echo "$q_manifest"; exit 1; }
echo "PASS: COUNCIL_QUORUM returns on quorum and reaps the slow member"

# --- S5: a member reaped by quorum AFTER writing its answer is ok, not failed ---
# codex writes its answer via -o mid-run but tears down slowly, so quorum reaps its worker
# before it records an exit code. A non-empty answer file must still count as ok.
mkdir -p "$WORK/rbin"
cat >"$WORK/rbin/codex" <<EOF
#!/usr/bin/env bash
out=""; while [ \$# -gt 0 ]; do case "\$1" in -o) out="\$2"; shift 2 ;; *) shift ;; esac; done
[ -n "\$out" ] && echo "codex slow-teardown answer" >"\$out"
sleep 30   # answer already written; slow teardown -> gets reaped before recording exit
EOF
cat >"$WORK/rbin/agy" <<'EOF'
#!/usr/bin/env bash
echo "agy quick"; exit 0
EOF
cat >"$WORK/rbin/claude" <<'EOF'
#!/usr/bin/env bash
echo "sonnet quick"; exit 0
EOF
chmod +x "$WORK/rbin/"*
r_manifest="$(PATH="$WORK/rbin:$PATH" COUNCIL_TIMEOUT=30 COUNCIL_QUORUM=2 \
  bash "$HERE/council-round.sh" --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/outr")"
[ ! -s "$WORK/outr/codex.exit" ] || { echo "FAIL: expected codex reaped before recording exit"; echo "$r_manifest"; exit 1; }
echo "$r_manifest" | grep -q $'codex\tok' || { echo "FAIL: reaped-after-answer codex should be ok"; echo "$r_manifest"; exit 1; }
grep -q "slow-teardown answer" "$WORK/outr/codex.out" || { echo "FAIL: codex answer not captured"; exit 1; }
echo "PASS: member reaped after writing its answer is counted ok"

# default (no COUNCIL_QUORUM) still waits for all (regression guard for /council semantics)
def_manifest="$(PATH="$WORK/bin:$PATH" COUNCIL_TIMEOUT=10 \
  bash "$HERE/council-round.sh" --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/outdef")"
echo "$def_manifest" | grep -q $'codex\tok' && echo "$def_manifest" | grep -q $'sonnet\tok' \
  || { echo "FAIL: default (no quorum) should collect all available members"; echo "$def_manifest"; exit 1; }
echo "PASS: default still waits for all members (quorum off)"
