#!/usr/bin/env bash
# Bug-injection benchmark for /council. For each case in cases.tsv, ask the council a
# forced binary verdict (VERDICT: BUG|CLEAN) on a code snippet, then score deterministically:
#   - recall: buggy cases the council flags BUG
#   - false-positive rate: clean cases the council wrongly flags BUG
#   - right-reason rate: buggy cases flagged for the actual planted reason (regex match)
#   - calibration: verdict accuracy bucketed by the members' stated confidence
#
# Council verdict is reported two ways: any-flag (>=1 member says BUG) and majority (>=2).
# Scoring is on the parsed VERDICT line; member reasoning is kept for inspection.
# bash 3.2 safe: no associative arrays (macOS stock bash lacks them).
#
# Usage: run-bench.sh [case-file ...]   (no args = all cases in cases.tsv)
#   COUNCIL_TIMEOUT (default 150) is passed through to council-round.sh.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
COUNCIL="${COUNCIL_ROUND:-$HOME/.claude/skills/council/scripts/council-round.sh}"
CASES_DIR="$HERE/cases"
MANIFEST="$HERE/cases.tsv"
MEMBERS="codex antigravity sonnet"
export COUNCIL_TIMEOUT="${COUNCIL_TIMEOUT:-150}"

# --- parsing (deterministic; isolated so test_bench.sh can exercise them) ---
# verdict_of <member-out-file> -> BUG | CLEAN | UNPARSEABLE
verdict_of() {
  local line tok
  line="$(grep -iE '^[[:space:]]*VERDICT:' "$1" 2>/dev/null | head -1)"
  tok="$(printf '%s' "$line" | grep -oiE 'bug|clean' | head -1 | tr '[:lower:]' '[:upper:]')"
  case "$tok" in BUG|CLEAN) echo "$tok" ;; *) echo UNPARSEABLE ;; esac
}
# conf_of <member-out-file> -> high | medium | low | unknown
conf_of() {
  local line tok
  line="$(grep -iE '^[[:space:]]*CONFIDENCE:' "$1" 2>/dev/null | head -1)"
  tok="$(printf '%s' "$line" | grep -oiE 'high|medium|low' | head -1 | tr '[:upper:]' '[:lower:]')"
  case "$tok" in high|medium|low) echo "$tok" ;; *) echo unknown ;; esac
}
# reason_matches <member-out-file> <regex> -> 0 if the member's text matches the planted reason
reason_matches() { grep -iqE "$2" "$1" 2>/dev/null; }

# --- bash 3.2-safe counters: dynamic scalar vars instead of associative arrays ---
inc()  { eval "$1=\$(( \${$1:-0} + 1 ))"; }
getv() { eval "printf '%s' \"\${$1:-0}\""; }

# Only run the benchmark when executed directly; sourcing (test_bench.sh) just loads the
# parsing helpers above.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then

[ -x "$COUNCIL" ] || { echo "council-round.sh not found/executable at $COUNCIL" >&2; exit 1; }

buggy=0; clean=0
recall_any=0; recall_maj=0
fp_any=0; fp_maj=0
rightreason=0
unparseable=0
failed=0

printf '%-26s %-7s %-7s %-12s %-7s %-10s %-10s\n' CASE EXPECT codex antigravity sonnet any-flag majority
printf '%-26s %-7s %-7s %-12s %-7s %-10s %-10s\n' "----" "------" "-----" "------" "------" "--------" "--------"

run_case() {
  local file="$1" expected="$2" regex="$3"
  local src="$CASES_DIR/$file"
  [ -f "$src" ] || { echo "missing case file: $src" >&2; return; }

  local pf out_dir
  pf="$(mktemp /tmp/bench-prompt.XXXXXX)"
  out_dir="$(mktemp -d /tmp/bench-round.XXXXXX)"
  {
    echo "Review the bash snippet below for a CORRECTNESS bug (ignore style/portability nitpicks)."
    echo "Respond in EXACTLY this format, nothing before it:"
    echo "VERDICT: BUG   (or)   VERDICT: CLEAN"
    echo "REASON: <one line>"
    echo "CONFIDENCE: high|medium|low"
    echo "BASIS: ran-it|traced-it|pattern-match|guess"
    echo
    echo "----- code -----"
    cat "$src"
  } > "$pf"

  local manifest
  manifest="$(bash "$COUNCIL" --prompt-file "$pf" --out-dir "$out_dir" --members "codex,antigravity,sonnet" 2>/dev/null)"

  local m v conf status n_bug=0
  for m in $MEMBERS; do
    # distinguish "member never answered" (manifest status != ok) from "answered but
    # unparseable format" — they are different failures and were being conflated.
    status="$(printf '%s\n' "$manifest" | awk -F'\t' -v M="$m" '$1==M{print $2}')"
    if [ "$status" != ok ]; then
      eval "vrd_$m=NOANS"; failed=$((failed + 1)); continue
    fi
    v="$(verdict_of "$out_dir/$m.out")"
    eval "vrd_$m=\$v"
    if [ "$v" = UNPARSEABLE ]; then unparseable=$((unparseable + 1)); continue; fi
    conf="$(conf_of "$out_dir/$m.out")"
    [ "$v" = BUG ] && n_bug=$((n_bug + 1))
    inc "mt_$m"; inc "ct_$conf"
    if [ "$v" = "$expected" ]; then inc "mc_$m"; inc "cc_$conf"; fi
  done

  local any maj amark mmark
  [ "$n_bug" -ge 1 ] && any=BUG || any=CLEAN
  [ "$n_bug" -ge 2 ] && maj=BUG || maj=CLEAN
  [ "$any" = "$expected" ] && amark=ok || amark=MISS
  [ "$maj" = "$expected" ] && mmark=ok || mmark=MISS
  printf '%-26s %-7s %-7s %-12s %-7s %-10s %-10s\n' \
    "$file" "$expected" "$(getv vrd_codex)" "$(getv vrd_antigravity)" "$(getv vrd_sonnet)" "$any($amark)" "$maj($mmark)"

  if [ "$expected" = BUG ]; then
    buggy=$((buggy + 1))
    [ "$any" = BUG ] && recall_any=$((recall_any + 1))
    [ "$maj" = BUG ] && recall_maj=$((recall_maj + 1))
    if [ "$any" = BUG ] && [ "$regex" != "-" ]; then
      for m in $MEMBERS; do reason_matches "$out_dir/$m.out" "$regex" && { rightreason=$((rightreason + 1)); break; }; done
    fi
  else
    clean=$((clean + 1))
    [ "$any" = BUG ] && fp_any=$((fp_any + 1))
    [ "$maj" = BUG ] && fp_maj=$((fp_maj + 1))
  fi
  rm -rf "$pf" "$out_dir"
}

want="$*"
while IFS=$'\t' read -r file expected regex; do
  case "$file" in ''|\#*) continue ;; esac
  if [ -n "$want" ]; then case " $want " in *" $file "*) ;; *) continue ;; esac; fi
  run_case "$file" "$expected" "$regex"
done < "$MANIFEST"

pct() { [ "$2" -eq 0 ] && { echo "n/a"; return; }; awk "BEGIN{printf \"%d%%\", ($1/$2)*100}"; }

echo
echo "=== Scorecard ==="
echo "cases: $((buggy + clean))  (buggy $buggy, clean $clean)"
echo "recall    any-flag: $recall_any/$buggy ($(pct $recall_any $buggy))    majority: $recall_maj/$buggy ($(pct $recall_maj $buggy))"
echo "false-pos any-flag: $fp_any/$clean ($(pct $fp_any $clean))    majority: $fp_maj/$clean ($(pct $fp_maj $clean))"
echo "right-reason (of buggy flagged any): $rightreason/$recall_any"
echo "member no-answer (failed/timeout): $failed    unparseable (answered, bad format): $unparseable"
echo "per-member accuracy:"
for m in $MEMBERS; do echo "  $m: $(getv mc_$m)/$(getv mt_$m) ($(pct "$(getv mc_$m)" "$(getv mt_$m)"))"; done
echo "confidence calibration (verdict accuracy by stated confidence):"
for c in high medium low unknown; do
  t="$(getv ct_$c)"; [ "$t" -eq 0 ] && continue
  echo "  $c: $(getv cc_$c)/$t ($(pct "$(getv cc_$c)" "$t"))"
done

fi  # end direct-execution guard