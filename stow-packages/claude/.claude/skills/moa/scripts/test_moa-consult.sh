#!/usr/bin/env bash
# Self-check for moa-consult.sh. Stubs the three member CLIs on PATH and drives the REAL
# council-round.sh through moa-consult.sh, so this exercises the actual composition. The
# stubs emit a different answer when they see the refinement instruction, letting us prove
# that layer N+1 received layer N's proposals. No framework.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
COUNCIL_ROUND="$HERE/../../council/scripts/council-round.sh"
[ -f "$COUNCIL_ROUND" ] || { echo "SKIP: council-round.sh not found at $COUNCIL_ROUND"; exit 0; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export MOA_COUNCIL_ROUND="$COUNCIL_ROUND"
export COUNCIL_TIMEOUT=10

fail() { echo "FAIL: $1"; exit 1; }

# --- stubs: codex + sonnet answer (marking refinement rounds), antigravity always fails ---
mkdir -p "$WORK/bin"
cat >"$WORK/bin/codex" <<'EOF'
#!/usr/bin/env bash
out=""; all="$*"
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac; done
if echo "$all" | grep -q 'candidate responses'; then msg="codex refined"; else msg="codex layer1"; fi
[ -n "$out" ] && echo "$msg" >"$out"
exit 0
EOF
cat >"$WORK/bin/claude" <<'EOF'
#!/usr/bin/env bash
if echo "$*" | grep -q 'candidate responses'; then echo "sonnet refined"; else echo "sonnet layer1"; fi
exit 0
EOF
cat >"$WORK/bin/agy" <<'EOF'
#!/usr/bin/env bash
echo "agy boom" >&2; exit 1
EOF
chmod +x "$WORK/bin/"*

echo "should this helper be bash or python" >"$WORK/prompt.txt"

# --- case 1: two layers, degradation, and layer-2-sees-layer-1 refinement ---
final="$(PATH="$WORK/bin:$PATH" bash "$HERE/moa-consult.sh" \
  --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/o1" --layers 2 | tail -1)"
rc=$?
[ "$rc" = "0" ] || fail "layers=2 should exit 0 (codex+sonnet ok), got $rc"
[ "$final" = "$WORK/o1/moa-final.md" ] || fail "expected moa-final.md path, got '$final'"

grep -q "codex layer1" "$WORK/o1/layer1/codex.out"   || fail "layer1 codex answer missing"
[ -d "$WORK/o1/layer2" ]                             || fail "layer2 dir should exist"
grep -q "codex refined" "$WORK/o1/layer2/codex.out"  || fail "layer2 codex should be the refined answer"

# the refinement prompt must carry the instruction AND layer-1's proposals
grep -q "candidate responses" "$WORK/o1/layer2.prompt" || fail "layer2 prompt missing refine instruction"
grep -q "codex layer1"        "$WORK/o1/layer2.prompt" || fail "layer2 prompt missing codex layer1 proposal"
grep -q "sonnet layer1"       "$WORK/o1/layer2.prompt" || fail "layer2 prompt missing sonnet layer1 proposal"

# moa-final holds the FINAL (refined) proposals under labeled headings; agy absent
grep -q "codex refined"       "$WORK/o1/moa-final.md" || fail "moa-final missing refined codex"
grep -q "sonnet refined"      "$WORK/o1/moa-final.md" || fail "moa-final missing refined sonnet"
grep -q "Response from codex"  "$WORK/o1/moa-final.md" || fail "moa-final missing labeled heading"
echo "PASS: layers stack, layer 2 sees layer 1's proposals, degrades past failed agy"

# --- case 2: layers=1 degenerates to a single fan-out ---
PATH="$WORK/bin:$PATH" bash "$HERE/moa-consult.sh" \
  --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/o2" --layers 1 >/dev/null \
  || fail "layers=1 should exit 0"
[ ! -d "$WORK/o2/layer2" ]         || fail "layers=1 should not create layer2"
grep -q "codex layer1" "$WORK/o2/moa-final.md" || fail "layers=1 moa-final missing codex answer"
echo "PASS: layers=1 is a single council fan-out"

# --- case 3: a layer that fails after a good one falls back to the last good layer ---
# codex here fails ONLY on the refinement round; sonnet always fails -> layer2 has no ok.
mkdir -p "$WORK/bin3"
cat >"$WORK/bin3/codex" <<'EOF'
#!/usr/bin/env bash
out=""; all="$*"
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac; done
echo "$all" | grep -q 'candidate responses' && exit 1
[ -n "$out" ] && echo "codex layer1 only" >"$out"; exit 0
EOF
cat >"$WORK/bin3/claude" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
cp "$WORK/bin/agy" "$WORK/bin3/agy"
chmod +x "$WORK/bin3/"*
PATH="$WORK/bin3:$PATH" bash "$HERE/moa-consult.sh" \
  --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/o3" --layers 2 >/dev/null \
  || fail "fallback to last good layer should exit 0"
grep -q "codex layer1 only" "$WORK/o3/moa-final.md" || fail "moa-final should hold layer1 after layer2 failed"
echo "PASS: a failed layer falls back to the last good layer"

# --- case 4: all layers fail -> exit 1, final names the failure ---
mkdir -p "$WORK/bin4"
for c in codex agy claude; do printf '#!/usr/bin/env bash\nexit 1\n' >"$WORK/bin4/$c"; done
chmod +x "$WORK/bin4/"*
PATH="$WORK/bin4:$PATH" bash "$HERE/moa-consult.sh" \
  --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/o4" --layers 2 >/dev/null \
  && fail "all-fail should exit non-zero"
grep -q "all layers failed" "$WORK/o4/moa-final.md" || fail "moa-final should report total failure"
echo "PASS: total failure exits 1 and is reported"

# --- case 5: bad --layers rejected ---
bash "$HERE/moa-consult.sh" --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/o5" --layers 0 >/dev/null 2>&1 \
  && fail "--layers 0 should be rejected"
bash "$HERE/moa-consult.sh" --prompt-file "$WORK/prompt.txt" --out-dir "$WORK/o6" --layers abc >/dev/null 2>&1 \
  && fail "--layers abc should be rejected"
echo "PASS: invalid --layers rejected"

echo "ALL PASS"
