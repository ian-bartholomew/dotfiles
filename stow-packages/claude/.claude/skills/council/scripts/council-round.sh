#!/usr/bin/env bash
# Run one council round: fan a prompt out to codex, antigravity (agy), and claude-sonnet
# in parallel (read-only), collect each member's answer, and print a manifest.
#
# All judgment (synthesis, convergence) is the chairman's job, done by the calling
# skill. This script is deliberately dumb and deterministic.
#
# Usage: council-round.sh --prompt-file <path> --out-dir <dir>
# Manifest (stdout), one line per member: "<member>\t<status>\t<outfile>"
#   status = ok | failed
# Exit: 0 if at least one member succeeded, 1 if all failed or on usage error.

set -uo pipefail

# Models are pinned to explicit IDs for reproducibility (floating aliases drift across
# CLI versions). Override any of them via the COUNCIL_*_MODEL env vars.
# codex has no stable public model id we pin here; it falls through to the codex CLI
# default unless COUNCIL_CODEX_MODEL is set.
CODEX_MODEL="${COUNCIL_CODEX_MODEL:-}"
# Antigravity (agy) is the third seat, replacing the gemini CLI (which was rate-limited
# and frequently dropped on this account). Let agy use its own default model; override
# via COUNCIL_ANTIGRAVITY_MODEL.
ANTIGRAVITY_MODEL="${COUNCIL_ANTIGRAVITY_MODEL:-}"
# On token/quota exhaustion, the antigravity seat retries once on this model (a separate
# quota pool). Override via COUNCIL_ANTIGRAVITY_FALLBACK; empty disables the fallback.
ANTIGRAVITY_FALLBACK="${COUNCIL_ANTIGRAVITY_FALLBACK:-GPT-OSS 120B (Medium)}"
SONNET_MODEL="${COUNCIL_SONNET_MODEL:-claude-sonnet-4-6}"
TIMEOUT="${COUNCIL_TIMEOUT:-240}"

case "$TIMEOUT" in
  ''|*[!0-9]*) echo "COUNCIL_TIMEOUT must be a positive integer (got '$TIMEOUT')" >&2; exit 1 ;;
esac
[ "$TIMEOUT" -gt 0 ] || { echo "COUNCIL_TIMEOUT must be greater than 0 (got '$TIMEOUT')" >&2; exit 1; }

prompt_file=""
out_dir=""
members_csv="codex,antigravity,sonnet"
while [ $# -gt 0 ]; do
  case "$1" in
    --prompt-file) prompt_file="$2"; shift 2 ;;
    --out-dir)     out_dir="$2"; shift 2 ;;
    --members)     members_csv="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$prompt_file" ] || [ -z "$out_dir" ]; then
  echo "usage: council-round.sh --prompt-file <path> --out-dir <dir> [--members codex,antigravity,sonnet]" >&2
  exit 1
fi

# Resolve and validate the member list (default: all three). Lets /consensus run a
# single model per turn while reusing all the member-invocation logic below.
IFS=',' read -ra MEMBERS <<< "$members_csv"
[ "${#MEMBERS[@]}" -gt 0 ] || { echo "--members is empty" >&2; exit 1; }
member_bin() { case "$1" in codex) echo codex ;; antigravity) echo agy ;; sonnet) echo claude ;; *) echo "" ;; esac; }

missing=""
seen=""
for m in "${MEMBERS[@]}"; do
  bin="$(member_bin "$m")"
  [ -n "$bin" ] || { echo "unknown member: '$m' (valid: codex, antigravity, sonnet)" >&2; exit 1; }
  # Duplicates would run two workers writing the same <member>.out/.exit concurrently.
  case " $seen " in *" $m "*) echo "duplicate member: '$m'" >&2; exit 1 ;; esac
  seen="$seen $m"
  command -v "$bin" >/dev/null 2>&1 || missing="$missing $bin"
done
[ -z "$missing" ] || { echo "missing required CLI(s):$missing" >&2; exit 1; }
command -v pgrep >/dev/null 2>&1 || echo "warning: pgrep not found; timed-out members may leave orphaned child processes" >&2
if [ ! -f "$prompt_file" ]; then
  echo "prompt file not found: $prompt_file" >&2
  exit 1
fi
mkdir -p "$out_dir" || { echo "cannot create out-dir: $out_dir" >&2; exit 1; }
[ -w "$out_dir" ] || { echo "out-dir not writable: $out_dir" >&2; exit 1; }

# ARG_MAX guard: the prompt is passed to members via argv. macOS ARG_MAX is ~1 MB; fail
# well below it with a clear message rather than a cryptic E2BIG from a member CLI.
prompt_bytes="$(wc -c <"$prompt_file" | tr -d ' ')"
if [ "$prompt_bytes" -gt 500000 ]; then
  echo "prompt is ${prompt_bytes} bytes; over the 500000 safety limit for argv passing." >&2
  echo "shorten the prompt (for /consensus, feed only the prior round, not full history)." >&2
  exit 1
fi

# Print all descendant PIDs of $1, deepest first. Called to snapshot the tree BEFORE any
# kill: once a process dies its survivors reparent (to init), so walking down from the
# original parent afterwards can no longer find them. We use `set -m` + group kill is not
# viable here (the member is double-nested in background, never a process-group leader),
# and macOS has no `setsid`, so an explicit PID snapshot is the portable option.
descendants() {
  local pid="$1" kid
  for kid in $(pgrep -P "$pid" 2>/dev/null); do descendants "$kid"; echo "$kid"; done
}

# ponytail: macOS has no `timeout`. Run the command in the background with a watchdog
# that polls once a second and kills it after TIMEOUT seconds. Polling (vs one long
# `sleep $secs`) means killing the watchdog on the success path orphans at most a 1s
# sleep, not a full-timeout one. Returns the command's exit code, or 124 on timeout.
run_with_timeout() {
  local secs="$1"; shift
  "$@" &
  local cmd_pid=$!
  (
    i=0
    while [ "$i" -lt "$secs" ]; do
      kill -0 "$cmd_pid" 2>/dev/null || exit 0
      sleep 1; i=$((i + 1))
    done
    # Snapshot the whole subtree by PID, then TERM, then escalate to KILL. Killing by PID
    # (not `pkill -P`) means a child that traps TERM and outlives its parent, so it
    # reparents away, is still reaped by the KILL pass.
    tree="$(descendants "$cmd_pid") $cmd_pid"
    kill -TERM $tree 2>/dev/null
    sleep 2
    kill -KILL $tree 2>/dev/null
  ) &
  local watch_pid=$!
  wait "$cmd_pid" 2>/dev/null
  local rc=$?
  if [ "$rc" -eq 143 ] || [ "$rc" -eq 137 ]; then
    # Timeout path: let the watchdog complete its TERM->sleep->KILL escalation before
    # reaping it. Killing it here (as the success path does) would cut the escalation
    # short and let a TERM-ignoring child survive. Report the kill as a timeout.
    wait "$watch_pid" 2>/dev/null
    rc=124
  else
    # Success path: the watchdog is still polling; stop it (orphans at most a 1s sleep).
    kill "$watch_pid" 2>/dev/null
    wait "$watch_pid" 2>/dev/null
  fi
  return "$rc"
}

PROMPT="$(cat "$prompt_file")"

# Each member writes its clean answer to <out-dir>/<member>.out and its exit code to
# <member>.exit. Members run read-only and never touch the working tree.
run_codex() {
  local out="$out_dir/codex.out"
  # --ignore-user-config: a council seat must be a fast one-shot proposer, not an agentic
  # crawler. Without it codex loads the user's ~/.codex config (hooks, MCP, skills) -- e.g.
  # a UserPromptSubmit hook that injects a wiki and sends codex off on a multi-round file
  # crawl. That makes codex far slower than the lightweight agy/claude seats, so under
  # /moa's quorum it's the straggler that gets reaped every time and silently drops out.
  local args=(exec --ignore-user-config --skip-git-repo-check -s read-only -o "$out")
  [ -n "$CODEX_MODEL" ] && args+=(-m "$CODEX_MODEL")
  # `--` ends option parsing so a prompt beginning with a dash is treated as the prompt,
  # not misread as a codex flag.
  args+=(-- "$PROMPT")
  # ponytail: </dev/null is load-bearing. Members get the prompt via argv; without
  # closing stdin, claude -p blocks waiting on it and fails under parallel contention.
  run_with_timeout "$TIMEOUT" codex "${args[@]}" >"$out_dir/codex.log" 2>&1 </dev/null
  echo "$?" >"$out_dir/codex.exit"
}

run_antigravity() {
  local out="$out_dir/antigravity.out" log="$out_dir/antigravity.log"
  # agy -p prints the answer to stdout; --sandbox restricts terminal use. No
  # --dangerously-skip-permissions: a council member answers, it does not act.
  local args=(-p "$PROMPT" --sandbox)
  [ -n "$ANTIGRAVITY_MODEL" ] && args+=(--model "$ANTIGRAVITY_MODEL")
  run_with_timeout "$TIMEOUT" agy "${args[@]}" >"$out" 2>"$log" </dev/null
  local rc=$?
  # Token/quota fallback: if the primary model is out of capacity, retry once on the
  # gpt-oss model (separate quota). Gated on an exhaustion signature so timeouts/crashes
  # (which the fallback can't fix) don't burn a second call.
  # ponytail: the pattern is a best-effort match for agy's quota error; widen it if a
  # real exhaustion message slips through. Empty ANTIGRAVITY_FALLBACK disables this.
  if [ -n "$ANTIGRAVITY_FALLBACK" ] && { [ "$rc" -ne 0 ] || [ ! -s "$out" ]; } && \
     grep -qiE 'exhaust|quota|capacity|resource.?exhausted|rate.?limit|429|too many requests|out of (tokens|capacity)' "$log" 2>/dev/null; then
    run_with_timeout "$TIMEOUT" agy -p "$PROMPT" --sandbox --model "$ANTIGRAVITY_FALLBACK" >"$out" 2>>"$log" </dev/null
    rc=$?
  fi
  echo "$rc" >"$out_dir/antigravity.exit"
}

run_sonnet() {
  local out="$out_dir/sonnet.out"
  # No --permission-mode plan: plan mode makes claude explore/plan before answering
  # (~5x slower, measured 321s vs 61s on a hard prompt), which blows the per-member
  # timeout so the sonnet seat gets killed and drops out. Read-only is preserved
  # without it: headless -p can't get interactive approval, so write tools are denied.
  run_with_timeout "$TIMEOUT" claude -p "$PROMPT" --model "$SONNET_MODEL" \
    >"$out" 2>"$out_dir/sonnet.log" </dev/null
  echo "$?" >"$out_dir/sonnet.exit"
}

# Tear the whole member subtree down (TERM, brief grace, KILL) without exiting. Used to
# reap stragglers on an early quorum return and by the interrupt trap below.
reap_children() {
  local tree
  tree="$(descendants $$)"
  if [ -n "$tree" ]; then
    kill -TERM $tree 2>/dev/null
    sleep 1
    kill -KILL $tree 2>/dev/null
  fi
}
# If the round is interrupted, reap now instead of leaving the CLIs orphaned to be killed
# one-by-one when their watchdogs finally time out.
reap_and_exit() {
  local sig="$1"
  reap_children
  trap - INT TERM
  kill "-$sig" $$ 2>/dev/null
}
trap 'reap_and_exit INT' INT
trap 'reap_and_exit TERM' TERM

for m in "${MEMBERS[@]}"; do
  case "$m" in
    codex)       run_codex & ;;
    antigravity) run_antigravity & ;;
    sonnet)      run_sonnet & ;;
  esac
done

# Optional quorum: proceed as soon as COUNCIL_QUORUM members have a good answer and reap the
# stragglers, instead of blocking on the slowest. Opt-in (a caller like /moa may set it);
# default (unset / 0 / >= member count) waits for all, preserving /council's all-opinions
# semantics. Each member has its own watchdog, so the poll loop is bounded by TIMEOUT.
quorum="${COUNCIL_QUORUM:-0}"
case "$quorum" in ''|*[!0-9]*) quorum=0 ;; esac
if [ "$quorum" -gt 0 ] && [ "$quorum" -lt "${#MEMBERS[@]}" ]; then
  while :; do
    ok=0; fin=0
    for member in "${MEMBERS[@]}"; do
      [ -f "$out_dir/$member.exit" ] || continue
      fin=$((fin + 1))
      [ "$(cat "$out_dir/$member.exit" 2>/dev/null)" = "0" ] && [ -s "$out_dir/$member.out" ] && ok=$((ok + 1))
    done
    [ "$ok" -ge "$quorum" ] && break
    [ "$fin" -ge "${#MEMBERS[@]}" ] && break
    sleep 0.2
  done
  reap_children
fi
wait 2>/dev/null

any_ok=1
for member in "${MEMBERS[@]}"; do
  out="$out_dir/$member.out"
  # An empty rc means the exit file was never written: the quorum reap killed the worker
  # subshell after the member flushed its answer but before it recorded its exit code.
  # codex is the usual victim -- it writes its answer via -o mid-run but tears down slowly
  # (MCP/session teardown), so it's still exiting when the two faster seats hit quorum.
  # A reaped member with a non-empty answer file did produce a usable answer; count it ok
  # rather than throwing the work away. An explicit nonzero exit or a timeout still fails.
  rc="$(cat "$out_dir/$member.exit" 2>/dev/null)"
  if { [ "$rc" = "0" ] || [ -z "$rc" ]; } && [ -s "$out" ]; then
    printf '%s\tok\t%s\n' "$member" "$out"
    any_ok=0
  elif [ "$rc" = "124" ]; then
    printf '%s\tfailed(timeout)\t%s\n' "$member" "$out_dir/$member.log"
  else
    printf '%s\tfailed\t%s\n' "$member" "$out_dir/$member.log"
  fi
done

exit "$any_ok"
