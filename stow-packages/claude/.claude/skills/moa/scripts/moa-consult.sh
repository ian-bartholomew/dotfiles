#!/usr/bin/env bash
# One Mixture-of-Agents consult: stack N proposer rounds (Together-style layers) by
# re-invoking council-round.sh, feeding each layer's proposer outputs into the next
# layer's prompt. Deterministic and dumb: this script runs the PROPOSER layers only.
# The FINAL aggregation + the resulting action are Claude's job (the aggregator/acting
# model) -- this script just hands back the refined proposals in moa-final.md.
#
# Usage: moa-consult.sh --prompt-file <path> --out-dir <dir> [--layers N] [--members csv]
#   --layers   number of proposer rounds (default 2). 1 == a single council fan-out.
#   --members  csv passed straight through to council-round.sh (default: the trio).
#
# Env: MOA_COUNCIL_ROUND overrides the path to council-round.sh (used by the test).
# Exit: 0 if the final good layer had >=1 proposer answer, 1 if every layer failed.

set -uo pipefail

COUNCIL_ROUND="${MOA_COUNCIL_ROUND:-$HOME/.claude/skills/council/scripts/council-round.sh}"

prompt_file=""
out_dir=""
layers=2
members="codex,antigravity,sonnet"
while [ $# -gt 0 ]; do
  case "$1" in
    --prompt-file) prompt_file="$2"; shift 2 ;;
    --out-dir)     out_dir="$2"; shift 2 ;;
    --layers)      layers="$2"; shift 2 ;;
    --members)     members="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$prompt_file" ] || [ -z "$out_dir" ]; then
  echo "usage: moa-consult.sh --prompt-file <path> --out-dir <dir> [--layers N] [--members csv]" >&2
  exit 1
fi
case "$layers" in
  ''|*[!0-9]*) echo "--layers must be a positive integer (got '$layers')" >&2; exit 1 ;;
esac
[ "$layers" -gt 0 ] || { echo "--layers must be greater than 0 (got '$layers')" >&2; exit 1; }
[ -f "$prompt_file" ] || { echo "prompt file not found: $prompt_file" >&2; exit 1; }
[ -x "$COUNCIL_ROUND" ] || [ -f "$COUNCIL_ROUND" ] || {
  echo "council-round.sh not found at: $COUNCIL_ROUND (set MOA_COUNCIL_ROUND)" >&2; exit 1; }
mkdir -p "$out_dir" || { echo "cannot create out-dir: $out_dir" >&2; exit 1; }

ORIG="$(cat "$prompt_file")"

# Emit each ok proposer's answer from <layer_dir> (per its manifest) under a labeled
# heading. Reads the manifest on stdin (council-round.sh format: member<TAB>status<TAB>path).
proposals_from_manifest() {
  local layer_dir="$1" manifest="$2"
  while IFS=$'\t' read -r member status _; do
    [ "$status" = "ok" ] || continue
    printf '## Response from %s\n\n' "$member"
    cat "$layer_dir/$member.out"
    printf '\n\n'
  done <"$manifest"
}

REFINE_INSTRUCTION='Below are candidate responses from other models to the same query. Use the strongest points, correct any errors you find, and produce an improved, self-contained response. Do not merely copy one of them.'

last_good_dir=""
last_good_manifest=""
declare -a layer_summaries=()

for i in $(seq 1 "$layers"); do
  layer_dir="$out_dir/layer$i"
  manifest="$out_dir/layer$i.manifest"
  mkdir -p "$layer_dir"

  if [ "$i" -eq 1 ]; then
    round_prompt="$prompt_file"
  else
    # Refinement prompt = original question + the immediately-prior layer's proposals.
    round_prompt="$out_dir/layer$i.prompt"
    {
      printf '%s\n\n' "$ORIG"
      printf -- '---\n\n%s\n\n' "$REFINE_INSTRUCTION"
      proposals_from_manifest "$last_good_dir" "$last_good_manifest"
    } >"$round_prompt"
  fi

  # council-round.sh prints the manifest to stdout and exits 0 if >=1 member answered.
  bash "$COUNCIL_ROUND" --prompt-file "$round_prompt" --out-dir "$layer_dir" \
    --members "$members" >"$manifest" 2>"$out_dir/layer$i.log"
  round_rc=$?

  ok_count="$(grep -c $'\tok\t' "$manifest" 2>/dev/null || echo 0)"
  layer_summaries+=("layer$i: rc=$round_rc, ok=$ok_count")

  if [ "$round_rc" -eq 0 ] && [ "$ok_count" -gt 0 ]; then
    last_good_dir="$layer_dir"
    last_good_manifest="$manifest"
  else
    # Whole layer failed: can't refine further. Keep the last good layer as final.
    echo "moa: layer $i produced no answers (rc=$round_rc); using layer output up to $((i-1))" >&2
    break
  fi
done

final="$out_dir/moa-final.md"
if [ -z "$last_good_dir" ]; then
  {
    printf '# MoA consult: all layers failed\n\n'
    printf 'No proposer produced an answer. Per-layer status:\n\n'
    printf -- '- %s\n' "${layer_summaries[@]}"
  } >"$final"
  echo "$final" # still tell the caller where the (empty) result is
  exit 1
fi

{
  printf '# MoA final proposals\n\n'
  # shellcheck disable=SC2016  # backticks are literal markdown, not command substitution
  printf 'Refined proposer responses from the last successful layer (`%s`). ' "$(basename "$last_good_dir")"
  printf 'You are the aggregator: verify load-bearing claims before adopting, then synthesize and act.\n\n'
  printf -- '- %s\n' "${layer_summaries[@]}"
  printf '\n---\n\n'
  proposals_from_manifest "$last_good_dir" "$last_good_manifest"
} >"$final"

echo "$final"
exit 0
