---
name: terraform-review
description: Review Terraform code on the current branch or a GitHub PR across four lenses — style, module API, security, correctness — with confidence-filtered findings. Triggers on "/terraform-review", "review my terraform", "tf review", "review this terraform PR", "audit this terraform diff".
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

The skill fans out to four parallel reviewer agents, one per lens, then merges and filters findings before printing.

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

## Step 5: Dispatch reviewer agents in parallel

In a **single message**, issue four (or fewer, if `--lens` is set) `Agent` tool calls with `subagent_type: terraform-reviewer`. The four lenses are:

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

Parse each agent's YAML output. Combine into one list, then:

1. **Filter** — drop findings with `confidence < 80` unless `show_all=true`.
2. **Dedupe** — collapse identical `(file, line, rule)` tuples that appear from multiple agents. When deduping cross-lens hits, bump the surviving finding's confidence by `+10` (capped at 100) and tag with both lenses.
3. **Group** by lens; within each, sort by severity (`critical` > `high` > `medium` > `low` > `info`) then `file:line`.

## Step 7: Print the report

Format (no emojis, no markdown headers in the chat output — keep it scannable):

```
Terraform Review — <branch-or-PR> (<N> files changed)

[SECURITY]
  HIGH (conf 92)  modules/eks/iam.tf:14  iam-wildcard-action
    <message>
    > <suggestion>

[CORRECTNESS]
  CRITICAL (conf 95)  services/cognito/main.tf:88  rename-without-moved-block
    <message>
    > <suggestion>

[MODULE-API]
  MEDIUM (conf 85)  modules/vpc/variables.tf:5  variable-no-type
    <message>
    > <suggestion>

[STYLE]
  (no findings ≥80 confidence)

Summary: <crit> critical, <high> high, <med> medium, <low> low. <N> lower-confidence findings suppressed (use --all to show).
Tools used: terraform fmt, terraform validate, tflint. (<missing tools> not installed — skipped.)
```

If `references` are present on a finding, append them as a trailing line: `refs: [[wiki-page-a]], [[wiki-page-b]]`.

If `show_all=true`, drop the suppressed-findings line and include all findings.

If no findings at all: `Terraform Review — no issues at ≥80 confidence (<lens count> lenses, <N> files). <suppressed> lower-confidence findings available with --all.`

## Notes on calibration

- These reviewer agents are anchored by `references/output-schema.md`'s severity and confidence rubrics. If false-positive rate creeps up, tighten the confidence rubric there (single source of truth).
- The `+10 cross-lens bump` is intentional: when two independent lenses flag the same line, that's a stronger signal than either alone.
- Do not invoke `terraform plan` or anything that touches state. This skill is static-analysis only.
