---
name: terraform-review
description: Review Terraform code on the current branch or a GitHub PR across four lenses — style, module API, security, correctness — with confidence-filtered findings. Two additional whole-diff reviewers run on external CLIs (Codex and Antigravity) for cross-model diversity. Triggers on "/terraform-review", "review my terraform", "tf review", "review this terraform PR", "audit this terraform diff".
arguments:
  - name: target
    description: Optional GitHub PR number (e.g. 1234 or #1234) or PR URL. If omitted, reviews the current branch diff against origin/main.
    required: false
  - name: flags
    description: Optional flags. `--all` shows findings below the default 80-confidence threshold. `--lens <name>` restricts to a single lens (style|module-api|security|correctness).
    required: false
---

# Terraform Review

You are running a multi-lens Terraform code review. The goal is *"is this good Terraform?"* — distinct from `fes-terraform-plan-risk` which answers *"is this safe to merge right now?"*.

The skill fans out to four parallel reviewer agents, one per lens, plus two cross-model reviewers running on external CLIs (**Codex** via `codex exec`, **Antigravity** via `agy`) that review the whole diff across all lenses. All six streams merge into one confidence-filtered report.

## Step 1: Parse arguments

Inputs may be empty, a target, flags, or both.

- **Target** — first positional arg matching:
  - `^#?\d+$` → PR number (PR mode)
  - `https?://github\.com/[^/]+/[^/]+/pull/\d+` → PR URL (PR mode)
  - Anything else → error: `Usage: /terraform-review [<PR-number-or-URL>] [--all] [--lens <name>]`
- **Flags**:
  - `--all` → set `show_all=true` (skip the ≥80 confidence filter)
  - `--lens <name>` where `<name>` ∈ {style, module-api, security, correctness} → set `single_lens=<name>` (only dispatch that lens)
  - Unknown flag → error with usage.

If no target → **branch mode**.

## Step 2: Resolve and load the diff

**Branch mode:**

```bash
git rev-parse --is-inside-work-tree   # else: exit "must be run inside a git repo"
git fetch origin main --quiet         # best-effort; ignore failure
BASE=$(git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD main)
DIFF=$(git diff "$BASE"...HEAD -- '*.tf' '*.tfvars')
FILES=$(git diff --name-only "$BASE"...HEAD -- '*.tf' '*.tfvars')
```

**PR mode:**

```bash
# Verify gh identity per global CLAUDE.md rule.
gh auth status 2>&1 | grep -q 'active account' || { echo "gh not authenticated"; exit; }
ACTIVE_ACCOUNT=$(gh auth status 2>&1 | awk '/Active account: true/{getline; print $NF}' | head -1)

# Determine repo from the PR URL/number; if number-only, default to current repo.
PR_NUM=$(echo "$TARGET" | grep -oE '[0-9]+')
REPO_URL=$(echo "$TARGET" | grep -oE 'github\.com/[^/]+/[^/]+' | sed 's#github.com/##')
REPO_ARG=${REPO_URL:+-R "$REPO_URL"}

# Warn if reviewing a fanatics-gaming repo without ian-at-fes active.
if echo "$REPO_URL" | grep -q '^fanatics-gaming/' && [ "$ACTIVE_ACCOUNT" != "ian-at-fes" ]; then
  echo "WARNING: reviewing fanatics-gaming PR but active gh account is '$ACTIVE_ACCOUNT', not 'ian-at-fes'. Switch with 'gh auth switch -u ian-at-fes' before continuing."
fi

DIFF=$(gh pr diff "$PR_NUM" $REPO_ARG -- '*.tf' '*.tfvars')
FILES=$(gh pr diff "$PR_NUM" $REPO_ARG --name-only -- '*.tf' '*.tfvars')
```

If `FILES` is empty, print `No Terraform changes to review.` and stop.

## Step 3: Run deterministic tools

For each unique directory in `FILES`:

```bash
# Format check — always available with terraform.
terraform fmt -check -diff <dir>     # capture stdout/stderr; non-zero ok
# Validation — best-effort; some configs require init.
terraform -chdir=<dir> validate -no-color 2>&1  # skip silently on init errors
# Lint — if installed.
command -v tflint >/dev/null && tflint --chdir=<dir> --format=json 2>&1
```

Collect outputs into a structured map: `{ tool: <fmt|validate|tflint>, dir: <path>, status: <ok|fail|skipped>, output: <text> }`.

Record which tools were *available* (vs missing) so the final report's footer can disclose it.

## Step 4: Read repo CLAUDE.md (best-effort)

If a `CLAUDE.md` exists at the repo root, read it. Pass relevant snippets to each reviewer in Step 5 so they respect repo-specific conventions.

## Step 5: Dispatch reviewers in parallel

### Step 5a: Prepare the cross-model prompt file

The external CLIs run headless and can't reliably read the skill's reference files, so everything is inlined.

```bash
XMODEL_DIR=$(mktemp -d)
echo "$XMODEL_DIR"   # capture for the Write and Read calls below
```

`Write` `$XMODEL_DIR/prompt.txt` containing, in order:

1. `You are reviewing a Terraform diff. Apply all four lenses: style, module API design, security, correctness.` (If `single_lens` is set, name only that lens.)
2. The contents of `references/output-schema.md`.
3. The repo CLAUDE.md snippet, if any.
4. The deterministic-tool output from Step 3.
5. The full unified diff and `FILES` list.
6. `Return only the YAML findings list matching the schema above. No prose, no headers, no code fences. Set the 'lens' field to whichever of style|module-api|security|correctness fits each finding. If you find nothing at ≥80 confidence, return an empty list ([]).`

### Step 5b: Dispatch all reviewers concurrently

In a **single message**, issue four (or fewer, if `--lens` is set) `Agent` tool calls with `subagent_type: terraform-reviewer`, **plus one `Bash` call** for the two cross-model reviewers:

```bash
COUNCIL_TIMEOUT=300 ~/.claude/skills/council/scripts/council-round.sh \
  --prompt-file "$XMODEL_DIR/prompt.txt" \
  --out-dir "$XMODEL_DIR" \
  --members codex,antigravity
```

The script writes `$XMODEL_DIR/codex.out` and `$XMODEL_DIR/antigravity.out` and prints a tab-separated manifest (`<member>\tok|failed(timeout)|failed\t<path>`). The `Agent` calls and this `Bash` call must go out together — do not dispatch sequentially.

The four lens agents are:

- `style` — pass `references/style.md`, fmt + tflint formatting findings.
- `module-api` — pass `references/module-api.md`, tflint output as context.
- `security` — pass `references/security.md`, tflint security rules.
- `correctness` — pass `references/correctness.md`, tflint output + validate output.

Every call receives:

- The full unified diff (TF diffs are typically small enough to inline).
- The `FILES` list.
- The lens-specific reference file *contents* (Read it from `${SKILL_DIR}/references/<lens>.md`).
- The `references/output-schema.md` contents (rubrics + finding schema).
- The relevant deterministic-tool output for that lens.
- Repo CLAUDE.md snippet, if any.
- Explicit instruction to return findings as a YAML list matching the schema.

Each call's prompt must end with: *"Return only the YAML findings list. No prose, no headers, no commentary. If you find nothing at ≥80 confidence, return an empty list (`[]`)."*

## Step 6: Merge and filter

Parse each agent's YAML output. Then, for each member the manifest marks `ok`, `Read` `$XMODEL_DIR/codex.out` / `$XMODEL_DIR/antigravity.out` and parse the same way — tag those findings `source: codex` and `source: antigravity` (lens agents are `source: local`). A member marked `failed` / `failed(timeout)` produced nothing usable: note it in the report footer and move on; never fabricate its findings. Strip any stray prose or code fences the CLIs wrap around the YAML; if a member's output won't parse, treat it as failed.

Combine into one list, then:

1. **Filter** — drop findings with `confidence < 80` unless `show_all=true`.
2. **Dedupe** — collapse identical `(file, line, rule)` tuples that appear from multiple reviewers. When deduping hits from different lenses *or different models*, bump the surviving finding's confidence by `+10` (capped at 100) and tag with every contributing lens and source.
3. **Group** by lens; within each, sort by severity (`critical` > `high` > `medium` > `low` > `info`) then `file:line`.

## Step 7: Print the report

Format (no emojis, no markdown headers in the chat output — keep it scannable):

```
Terraform Review — <branch-or-PR> (<N> files changed)

[SECURITY]
  HIGH (conf 92)  modules/eks/iam.tf:14  iam-wildcard-action  [local, codex]
    <message>
    > <suggestion>

[CORRECTNESS]
  CRITICAL (conf 95)  services/cognito/main.tf:88  rename-without-moved-block  [local]
    <message>
    > <suggestion>

[MODULE-API]
  MEDIUM (conf 85)  modules/vpc/variables.tf:5  variable-no-type  [antigravity]
    <message>
    > <suggestion>

[STYLE]
  (no findings ≥80 confidence)

Summary: <crit> critical, <high> high, <med> medium, <low> low. <N> lower-confidence findings suppressed (use --all to show).
Reviewers: 4 lens agents + codex, antigravity. (<failed members> unavailable — skipped.)
Tools used: terraform fmt, terraform validate, tflint. (<missing tools> not installed — skipped.)
```

The trailing `[...]` on each finding is its source tag(s) — which reviewers surfaced it. Omit the failed-members note when both cross-model reviewers returned.

If `references` are present on a finding, append them as a trailing line: `refs: [[wiki-page-a]], [[wiki-page-b]]`.

If `show_all=true`, drop the suppressed-findings line and include all findings.

If no findings at all: `Terraform Review — no issues at ≥80 confidence (<lens count> lenses, <N> files). <suppressed> lower-confidence findings available with --all.`

## Notes on calibration

- These reviewer agents are anchored by `references/output-schema.md`'s severity and confidence rubrics. If false-positive rate creeps up, tighten the confidence rubric there (single source of truth).
- The `+10 cross-lens bump` is intentional: when two independent lenses — or two different model families — flag the same line, that's a stronger signal than either alone.
- The cross-model seats reuse `council-round.sh` rather than shelling out to `codex` / `agy` directly. That script owns the timeout watchdog (macOS has no `timeout`), the read-only sandbox flags, stdin handling under parallel contention, and the Antigravity quota fallback. Do not reimplement CLI invocation here.
- Requires the `codex` and `agy` CLIs on `PATH`. If either is absent, `council-round.sh` exits non-zero for the whole call — the review still completes on the four lens agents alone; report the missing seats in the footer.
- Do not invoke `terraform plan` or anything that touches state. This skill is static-analysis only.
