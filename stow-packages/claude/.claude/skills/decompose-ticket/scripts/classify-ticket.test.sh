#!/usr/bin/env bash
# Red/green test for classify-ticket.sh — the mechanical decision core of the
# decompose-ticket skill. Locks the parent->child mapping, the creation routing,
# and the decomposability guards so they never regress to the "Story -> Task"
# mistake a capable agent might still make on a bad day.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SUT="$HERE/classify-ticket.sh"
pass=0
fail=0

# assert_kv <parent-type> <expected key=value line>
# passes when the script exits 0 and prints that exact line
assert_kv() {
  local input="$1" expect="$2" out rc
  out="$(bash "$SUT" "$input" 2>/dev/null)"
  rc=$?
  if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -qxF "$expect"; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    printf 'FAIL: classify %-10s -> want "%s" (rc 0); got rc=%s:\n%s\n' \
      "$input" "$expect" "$rc" "$out" >&2
  fi
}

# assert_refuse <parent-type>
# passes when the script exits 2 (not decomposable / unsupported / missing)
assert_refuse() {
  local input="$1" out rc
  out="$(bash "$SUT" "$input" 2>/dev/null)"
  rc=$?
  if [ "$rc" -eq 2 ]; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    printf 'FAIL: classify %-10s -> want refusal (rc 2); got rc=%s:\n%s\n' \
      "$input" "$rc" "$out" >&2
  fi
}

# --- the contract ---
assert_kv     "Feature"  "child_type=Epic"
assert_kv     "Feature"  "creator=jira-skill"
assert_kv     "Epic"     "child_type=Story"
assert_kv     "epic"     "creator=jira-skill"          # case-insensitive
assert_kv     "Story"    "child_type=Sub-task"         # the historically mis-specified case
assert_kv     "Story"    "creator=direct-mcp-subtask"  # sub-tasks bypass the jira-tickets skill
assert_refuse "Bug"                                    # leaf, nothing to decompose
assert_refuse "Sub-task"                               # already lowest level
assert_refuse "Task"                                   # out of scope (level-0, not Feature/Epic/Story)
assert_refuse ""                                       # nothing supplied
assert_refuse "Widget"                                 # unknown type

printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
