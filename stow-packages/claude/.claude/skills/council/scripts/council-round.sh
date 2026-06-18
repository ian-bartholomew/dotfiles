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
TIMEOUT="${COUNCIL_TIMEOUT:-180}"

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
for m in "${MEMBERS[@]}"; do
  bin="$(member_bin "$m")"
  [ -n "$bin" ] || { echo "unknown member: '$m' (valid: codex, antigravity, sonnet)" >&2; exit 1; }
  command -v "$bin" >/dev/null 2>&1 || missing="$missing $bin"
done
[ -z "$missing" ] || { echo "missing required CLI(s):$missing" >&2; exit 1; }
command -v pkill >/dev/null 2>&1 || echo "warning: pkill not found; timed-out members may leave orphaned child processes" >&2
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

# ponytail: macOS has no `timeout`. Run the command in the background with a watchdog
# that polls once a second and kills it after TIMEOUT seconds. Polling (vs one long
# `sleep $secs`) means killing the watchdog on the success path orphans at most a 1s
# sleep, not a full-timeout one. Returns the command's exit code, or 124 on timeout.
#
# On timeout we kill the member's direct children (pkill -P) before the member itself,
# so a CLI that forked a worker (node/python wrappers) doesn't leave it orphaned. We do
# NOT use `set -m` + `kill -- -$pid` (the obvious process-group approach): in this
# script the member is double-nested in background, so it never becomes its own
# process-group leader and the group-kill silently kills nothing (verified). pkill -P
# reaps one level of children, which covers the common case; deeper trees are the known
# ceiling, acceptable since members are read-only and short-lived.
run_with_timeout() {
  local secs="$1"; shift
  "$@" &
  local cmd_pid=$!
  (
    local i=0
    while [ "$i" -lt "$secs" ]; do
      kill -0 "$cmd_pid" 2>/dev/null || exit 0
      sleep 1; i=$((i + 1))
    done
    pkill -TERM -P "$cmd_pid" 2>/dev/null; kill -TERM "$cmd_pid" 2>/dev/null
    sleep 2
    pkill -KILL -P "$cmd_pid" 2>/dev/null; kill -KILL "$cmd_pid" 2>/dev/null
  ) &
  local watch_pid=$!
  wait "$cmd_pid" 2>/dev/null
  local rc=$?
  kill "$watch_pid" 2>/dev/null
  wait "$watch_pid" 2>/dev/null
  # A kill from our watchdog surfaces as 143 (TERM) or 137 (KILL); report as timeout.
  if [ "$rc" -eq 143 ] || [ "$rc" -eq 137 ]; then rc=124; fi
  return "$rc"
}

PROMPT="$(cat "$prompt_file")"

# Each member writes its clean answer to <out-dir>/<member>.out and its exit code to
# <member>.exit. Members run read-only and never touch the working tree.
run_codex() {
  local out="$out_dir/codex.out"
  local args=(exec --skip-git-repo-check -s read-only -o "$out")
  [ -n "$CODEX_MODEL" ] && args+=(-m "$CODEX_MODEL")
  args+=("$PROMPT")
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
  run_with_timeout "$TIMEOUT" claude -p "$PROMPT" --model "$SONNET_MODEL" --permission-mode plan \
    >"$out" 2>"$out_dir/sonnet.log" </dev/null
  echo "$?" >"$out_dir/sonnet.exit"
}

for m in "${MEMBERS[@]}"; do
  case "$m" in
    codex)       run_codex & ;;
    antigravity) run_antigravity & ;;
    sonnet)      run_sonnet & ;;
  esac
done
wait

any_ok=1
for member in "${MEMBERS[@]}"; do
  out="$out_dir/$member.out"
  rc="$(cat "$out_dir/$member.exit" 2>/dev/null || echo 1)"
  if [ "$rc" = "0" ] && [ -s "$out" ]; then
    printf '%s\tok\t%s\n' "$member" "$out"
    any_ok=0
  elif [ "$rc" = "124" ]; then
    printf '%s\tfailed(timeout)\t%s\n' "$member" "$out_dir/$member.log"
  else
    printf '%s\tfailed\t%s\n' "$member" "$out_dir/$member.log"
  fi
done

exit "$any_ok"
