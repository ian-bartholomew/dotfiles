#!/usr/bin/env bash
# Self-check for run-bench.sh's deterministic parsing (the fragile part). Sources the
# harness (guarded, so only the helpers load) and feeds synthetic member outputs. No CLIs.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/run-bench.sh"

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
fail() { echo "FAIL: $1"; exit 1; }

printf 'VERDICT: BUG\nREASON: the pipe runs the loop in a subshell\nCONFIDENCE: high\nBASIS: ran-it\n' >"$W/a"
[ "$(verdict_of "$W/a")" = BUG ]    || fail "verdict BUG"
[ "$(conf_of "$W/a")"    = high ]   || fail "conf high"
reason_matches "$W/a" 'subshell|pipe' || fail "reason regex should match"

# the trap: a CLEAN verdict whose REASON contains the word 'bug' must still parse CLEAN
printf 'VERDICT: CLEAN\nREASON: looks correct, no bug here\nCONFIDENCE: medium\n' >"$W/b"
[ "$(verdict_of "$W/b")" = CLEAN ]  || fail "must read the VERDICT line, not the word 'bug' in REASON"
[ "$(conf_of "$W/b")"    = medium ] || fail "conf medium"

# no verdict line at all
printf 'some prose, model ignored the format\n' >"$W/c"
[ "$(verdict_of "$W/c")" = UNPARSEABLE ] || fail "missing verdict -> UNPARSEABLE"
[ "$(conf_of "$W/c")"    = unknown ]     || fail "missing confidence -> unknown"

# lowercase + extra spacing tolerance
printf '  verdict:   clean\nconfidence: LOW\n' >"$W/d"
[ "$(verdict_of "$W/d")" = CLEAN ] || fail "lowercase/spaced verdict"
[ "$(conf_of "$W/d")"    = low ]   || fail "uppercase LOW confidence"

echo "PASS: bench parsing handles format, case, spacing, and the 'no bug' false-match trap"