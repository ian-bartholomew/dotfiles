---
name: adversarial-review
description: |
  Run a skeptical red-team review of a plan using three parallel personas
  (Staff Platform Engineer, Staff Software Architect, Technical Project
  Manager). Use when the user asks to "adversarially review", "red-team",
  "critique", "pressure-test", or "stress-test" a plan, or runs
  "/adversarial-review". Personas are explicitly told to assume the plan
  is flawed and hunt for failure modes, unexamined assumptions, and weak
  spots. Findings are merged, presented as a numbered list, and the user
  picks which to fold back into the plan file. Loops until verdicts
  converge or the user stops. Two additional skeptics run on external
  models (Codex and Antigravity) for cross-model diversity.
version: 1.1.0
argument-hint: "[path-to-plan.md]"
allowed-tools: [Read, Edit, Write, Glob, Bash, Agent, AskUserQuestion]
---

# Adversarial Review Skill

Stress-test a plan with three skeptical, role-specific personas running in parallel: **Staff Platform Engineer**, **Staff Software Architect**, **Technical Project Manager**. Two more skeptics run on external model families — **Codex** (OpenAI) and **Antigravity** (Google, via the `agy` CLI) — reviewing the whole plan as independent red-teamers, for a total of five reviewers per round. Reviewers are instructed to assume the plan is flawed and hunt for failure modes — this is **not** a balanced peer review. Findings are merged, the user picks which to incorporate, the plan file is edited in place, and the loop repeats until verdicts converge or the user stops.

The three personas run as Claude `Agent` subagents; the two model skeptics are dispatched through the reused `~/.claude/skills/council/scripts/council-round.sh` fan-out script (which owns all CLI plumbing: timeouts, sandboxing, quota fallback). All five run concurrently.

## When to Use

Invoke this skill when:

- User runs `/adversarial-review` (with or without a path argument)
- User says "adversarially review", "red-team", "critique", "pressure-test", or "stress-test" a plan
- User has just finished `/superpowers:writing-plans` (or similar) and wants to break the result before executing it

This skill never commits to git, never modifies anything outside the plan file (and an optional sidecar review file). It is read-only on the codebase.

## Workflow

### Step 1 — Resolve the plan path

If the user passed a path as the argument, use it directly. Expand `~` and resolve relative paths against CWD. If the file does not exist, fail fast:

```
No plan at <path>. Pass an existing file or omit the argument to pick from recent plans.
```

If no argument was passed, build a candidate list from three locations:

```bash
CANDIDATES=$(
  { ls -t ~/.claude/plans/*.md 2>/dev/null
    ls -t "$(pwd)/docs/superpowers/specs/"*.md 2>/dev/null
    ls -t "$(pwd)/docs/plans/"*.md 2>/dev/null
  } | head -10
)
```

For each candidate, capture mtime (e.g. `2026-05-27 14:03`) and the first non-frontmatter `#` or `##` heading.

Present the candidates via `AskUserQuestion` (single-select). Label = relative path from `$HOME`; description = `<mtime> · <first heading>`. If no candidates exist, exit with:

```
No plans found in ~/.claude/plans/, ./docs/superpowers/specs/, or ./docs/plans/.
Pass a path explicitly: /adversarial-review <path-to-plan.md>
```

Bind the chosen path to `$PLAN_PATH` for the rest of the workflow.

### Step 2 — Pre-flight checks

```bash
WORDS=$(wc -w < "$PLAN_PATH" | tr -d ' ')
```

If `WORDS < 50` or the file is empty, refuse:

```
Plan looks too short to meaningfully review (<N> words).
Run /adversarial-review after the plan has substantive content.
```

### Step 3 — Extract optional context

`Read` the plan file in full. Scan for a second-level heading matching any of (case-insensitive):
`## Context`, `## Background`, `## Problem`, `## Summary`, `## Overview`. If more than one is
present, pick the first in that priority order (Context > Background > Problem > Summary > Overview).
Extract the body from the chosen heading up to the next `##` heading (or end of file).

If none of those sections is present, set `CONTEXT = "Not provided in plan."` and proceed. **Do not prompt the user** — the design intentionally relies on auto-detection only.

Hold the extracted context as a string for the persona prompts.

### Step 4 — Initialize round counter

```
ROUND=1
INCORPORATED_LOG=""   # accumulates "Round N: <finding>" lines across the session
```

### Step 5 — Parallel review (three personas + two model skeptics)

**Step 5a — Prepare the model-skeptic prompt file.** The two external CLIs run sandboxed (`codex exec -s read-only`, `agy -p --sandbox`) and cannot reliably read `$PLAN_PATH` at an arbitrary absolute path, so the plan is **inlined** into their prompt. Create a working dir and write the skeptic prompt (skeptic persona block + shared response contract + full plan text) to it:

```bash
SKEPTIC_DIR=$(mktemp -d)
echo "$SKEPTIC_DIR"   # capture for the Write and Read calls below
```

Use `Write` to create `$SKEPTIC_DIR/prompt.txt` containing the **Model skeptic** prompt below (with `<CONTEXT>` and the full plan content substituted in). Both CLIs receive the same file; the `## <ROLE>` heading distinguishes their reports.

**Step 5b — Dispatch all five reviewers concurrently.** In **a single message**, emit **three `Agent` tool calls** (one per persona, `subagent_type: "general-purpose"`) **plus one `Bash` call** that runs the council fan-out for the two model skeptics:

```bash
COUNCIL_TIMEOUT=300 ~/.claude/skills/council/scripts/council-round.sh \
  --prompt-file "$SKEPTIC_DIR/prompt.txt" \
  --out-dir "$SKEPTIC_DIR" \
  --members codex,antigravity
```

The script writes `$SKEPTIC_DIR/codex.out` and `$SKEPTIC_DIR/antigravity.out` and prints a tab-separated manifest (`<member>\tok|failed(timeout)|failed\t<path>`). The three `Agent` calls and this `Bash` call run in parallel — do not dispatch sequentially.

Each persona agent receives:

- The absolute plan path (for `Read` access)
- The extracted `CONTEXT` excerpt (or the "Not provided" placeholder)
- A persona-specific concerns list
- The shared response contract

**Shared response contract** (append to every persona prompt — replace `<ROLE>` with the persona's title):

```
Return ONLY this format:

## <ROLE>

### Critical issues
1. <issue — what's wrong and why it matters>
2. <issue>
3. <issue>
(3–6 items; severity-ordered, most damaging first)

### Missing or unaddressed
- <gap>
- <gap>

### Assumptions to challenge
- <assumption stated or implied that may not hold>

### Suggested changes
- <plan section heading>: <concrete edit — what to add, remove, or change>
- <plan section heading>: <concrete edit>

### Verdict: reject | accept-with-changes | approve

Keep your response under 350 words. Do not rewrite the plan; critique it.
ASSUME THE PLAN IS FLAWED. Do NOT list strengths. Be specific, concrete,
and uncharitable about ambiguity.
```

**Persona 1 — Staff Platform Engineer:**

```
You are a Staff Platform Engineer conducting an adversarial red-team review
of the plan at <PLAN_PATH>.

ASSUME THE PLAN IS FLAWED. Your job is to find failure modes, unexamined
assumptions, and weak spots — not balanced peer review. Do NOT list
strengths.

Original problem context (if available):
<CONTEXT>

Your concerns as a Staff Platform Engineer:
- Operability, on-call burden, blast radius if this goes wrong
- Failure modes, day-2 ops, runbook gaps
- Observability gaps (metrics, logs, traces, alerts, SLOs)
- Rollback / rollforward story — is reversal even possible?
- Infra cost (compute, storage, egress, idle capacity, surprise bills)
- AWS Well-Architected Framework alignment: operational excellence,
  security, reliability, performance efficiency, cost optimization,
  sustainability
- Cloud architecture and cloud-native infrastructure best practices
- Terraform best practices (module design, state, drift, blast radius
  of apply, plan reviewability)
- Modern GitOps workflow alignment (declarative, versioned, pull-based,
  observable)
- Secrets handling, IAM least-privilege, compliance / audit traces

Read the plan in full, then return the response format described below.

[shared response contract with <ROLE> = "Staff Platform Engineer"]
```

**Persona 2 — Staff Software Architect:**

```
You are a Staff Software Architect conducting an adversarial red-team
review of the plan at <PLAN_PATH>.

ASSUME THE PLAN IS FLAWED. Your job is to find failure modes, unexamined
assumptions, and weak spots — not balanced peer review. Do NOT list
strengths.

Original problem context (if available):
<CONTEXT>

Your concerns as a Staff Software Architect:
- Design integrity, coupling, cohesion, boundary placement
- Scaling characteristics (load, data volume, fan-out, hotspots)
- Abstraction quality — is the right thing being abstracted? Are the
  abstractions premature? Are they leaky?
- Alternatives not considered, or dismissed without evidence
- Hidden complexity, accidental complexity, premature optimization
- Contract / API stability (versioning, backward compat, deprecation paths)
- Data model concerns (schema evolution, consistency guarantees, integrity)
- Test-strategy adequacy (unit / integration / e2e coverage, test data,
  flakiness risk, what's untestable)
- Maintainability over time — who owns this in 5 years? Will it rot?
- Refactor risk if core assumptions change

Read the plan in full, then return the response format described below.

[shared response contract with <ROLE> = "Staff Software Architect"]
```

**Persona 3 — Technical Project Manager:**

```
You are a Technical Project Manager conducting an adversarial red-team
review of the plan at <PLAN_PATH>.

ASSUME THE PLAN IS FLAWED. Your job is to find failure modes, unexamined
assumptions, and weak spots — not balanced peer review. Do NOT list
strengths.

Original problem context (if available):
<CONTEXT>

Your concerns as a Technical Project Manager:
- Scope clarity — is what's IN and what's OUT explicit?
- Sequencing, critical path, parallelizable vs serial work
- Dependencies — internal teams, external vendors, infra, tooling,
  approvals that aren't named
- Timeline risk, slip vectors, estimation gaps
- Stakeholder gaps — who's missing from the plan? Who hasn't signed off?
- Success criteria — measurable, verifiable, agreed in advance?
- Hidden assumptions buried in passive voice or vague wording
- Definition of done
- Rollout / comms plan, change management, training needs

Read the plan in full, then return the response format described below.

[shared response contract with <ROLE> = "Technical Project Manager"]
```

**Model skeptics — Codex & Antigravity** (written to `$SKEPTIC_DIR/prompt.txt` in Step 5a; both CLIs get this same prompt):

```
You are an independent senior engineer conducting an adversarial red-team
review of the plan included below.

ASSUME THE PLAN IS FLAWED. Your job is to find failure modes, unexamined
assumptions, and weak spots across ANY dimension — technical, operational,
architectural, delivery, security — not balanced peer review. Do NOT list
strengths.

Original problem context (if available):
<CONTEXT>

Review the full plan text below, then return the response format described
at the end.

--- BEGIN PLAN ---
<full plan content — inlined verbatim from $PLAN_PATH>
--- END PLAN ---

[shared response contract — set <ROLE> to your own model name plus
" — Independent Skeptic", i.e. codex emits "## Codex — Independent Skeptic"
and antigravity emits "## Antigravity — Independent Skeptic"]
```

Because both CLIs receive the identical file, the `## <ROLE>` heading is what
separates the two reports when you read them back. Set the role from the
output filename: `codex.out` → `Codex — Independent Skeptic`, `antigravity.out`
→ `Antigravity — Independent Skeptic`.

### Step 6 — Aggregate and report

First, collect the two model-skeptic reports. From the `council-round.sh` manifest, for each member marked `ok` with a non-empty `.out`, `Read` `$SKEPTIC_DIR/codex.out` and `$SKEPTIC_DIR/antigravity.out`. A member marked `failed` or `failed(timeout)` produced no usable report — treat it as an absent reviewer with verdict `unknown` (see failure handling below); do not fabricate its output.

Print a round header, then each report verbatim, separated by `---` (personas first, then the model skeptics that succeeded):

```
# Round <ROUND>

[Staff Platform Engineer report]

---

[Staff Software Architect report]

---

[Technical Project Manager report]

---

[Codex — Independent Skeptic report, if ok]

---

[Antigravity — Independent Skeptic report, if ok]
```

Then merge the **Suggested changes** sections across all reporting reviewers into a single numbered list. Substantively-similar findings are deduplicated and tagged with each source's abbreviation: `PE` (Platform Engineer), `ARCH` (Architect), `TPM`, `CODEX`, `AGY` (Antigravity). Two findings are "substantively the same" when they target the same plan section AND propose changes that overlap meaningfully — when in doubt, keep them separate.

```
## Consolidated findings (Round <ROUND>)

1. [PE, ARCH, CODEX] <plan section>: <merged edit description>
2. [TPM] <plan section>: <edit description>
3. [AGY] <plan section>: <edit description>
...
```

Then a one-line verdict summary covering all five reviewers (use `unknown` for any that failed, timed out, or returned malformed output):

```
Verdicts — PE: accept-with-changes | ARCH: reject | TPM: accept-with-changes | CODEX: reject | AGY: unknown
```

If any reviewer's output is malformed (missing `### Verdict:` line, missing `### Suggested changes` section) or a model skeptic failed / timed out, show the raw output where available, mark its verdict as `unknown` in the summary, and exclude it from the consolidated list (you'll have nothing to merge from it). A model-skeptic failure is non-fatal: proceed with whatever reviewers reported.

**Convergence** (skip Step 7, jump to Step 9): the three Claude personas must have succeeded and ALL reporting reviewers (the three personas plus any model skeptic that returned `ok`) must return `approve` with no suggested changes. A model skeptic stuck in `unknown` (persistent failure) does not by itself block convergence — but if any reviewer that *did* report wants changes, the loop continues.

### Step 7 — Incorporation prompt

Ask via `AskUserQuestion`:

| Option | Behavior |
|--------|----------|
| **Incorporate all** | Apply every consolidated finding as a plan-file edit |
| **Cherry-pick** | User types finding numbers (e.g. `1,3,5`) in the "Other" field |
| **None / done** | Skip edits; exit loop and jump to Step 9 |

If the user picks **Cherry-pick** and the response is unparseable (no integers, all numbers out of range, etc.), re-prompt once with the same `AskUserQuestion`. On a second unparseable response, treat it as **None / done** and proceed.

### Step 8 — Apply edits and re-loop

For each accepted finding number, apply the edit to `$PLAN_PATH`:

1. Identify the target section heading from the finding's `[section: edit]` form.
2. Use `Edit` to apply the change:
   - **Additive changes** (default): locate the section heading via a unique `old_string` (the heading line plus its first line of content), and replace with the heading + first line + new content appended below. Prefer appending at the *end* of the section unless the finding specifically calls for restructuring.
   - **Replacement changes**: when the finding asks to modify existing text, use the most specific unique `old_string` available to do a surgical `old_string` → `new_string` replacement.
3. If `Edit` fails (e.g. `old_string` not unique), retry with a longer context window. If that still fails, print a warning, skip the finding, and continue with the next one.

After all accepted edits applied, append entries to `INCORPORATED_LOG`:

```
Round <ROUND>:
  - [PE, ARCH] <plan section>: <merged edit description>
  - [TPM] <plan section>: <edit description>
```

`Read` the updated plan back (this confirms edits landed and gives fresh content for the next round). Increment `ROUND` and loop to Step 5 — Step 5a rebuilds `$SKEPTIC_DIR/prompt.txt` with the revised plan inlined (a fresh `mktemp -d` per round), so the model skeptics review the current version, not the original.

### Step 9 — Loop termination

The loop exits when any of:

1. **Convergence** — The three Claude personas succeeded and all reporting reviewers (personas + any `ok` model skeptic) returned `approve` with no suggested changes (Step 6).
2. **User stop** — User picked **None / done** at Step 7.
3. **Round limit** — `ROUND == 3` (and we're about to enter round 4). At this point, ask via `AskUserQuestion`:

   ```
   Three rounds complete. Continue iterating, save the review and exit,
   or discard remaining findings and exit?

   [Continue / Save and exit / Discard and exit]
   ```

   - **Continue** — keep looping (no further round-limit prompts).
   - **Save and exit** — jump to Step 10 with the save prompt.
   - **Discard and exit** — jump to Step 10 with the save prompt anyway (the *findings* aren't discarded — the user is opting out of further incorporation).

Note which termination condition fired (used for the final summary).

### Step 10 — Offer to save the review artifact

Ask via `AskUserQuestion`:

| Option | Behavior |
|--------|----------|
| **Save review** | Write the final-round persona reports + consolidated findings + verdict summary + a header noting which findings were incorporated this session |
| **Skip save** | Exit silently |

On **Save review**, derive the output path:

```bash
PLAN_DIR=$(dirname "$PLAN_PATH")
PLAN_STEM=$(basename "$PLAN_PATH" .md)
TODAY=$(date +%Y-%m-%d)
REVIEW_PATH="$PLAN_DIR/$PLAN_STEM-review-$TODAY.md"
```

If `REVIEW_PATH` already exists (re-running the skill on the same day), append `-2`, `-3`, etc. until a free name is found.

Write the file with this structure:

```markdown
# Adversarial review: <plan title or filename>

- **Plan:** <relative or absolute path to $PLAN_PATH>
- **Reviewed:** <YYYY-MM-DD>
- **Rounds:** <N>
- **Termination:** convergence | user-stop | round-limit-continue | round-limit-save | round-limit-discard

## Findings incorporated this session

<contents of $INCORPORATED_LOG, or "None." if empty>

## Final round persona reports

<Round N persona reports verbatim, separated by ---->

## Final consolidated findings

<numbered list from final round>

## Final verdicts

<one-line verdict summary>
```

### Step 11 — Final summary

Print to chat:

```
Adversarial review complete.

  Plan:               <relative path>
  Rounds:             <N>
  Findings applied:   <count> across <N> rounds
  Final verdicts:     PE: <v> | ARCH: <v> | TPM: <v> | CODEX: <v> | AGY: <v>
  Review saved:       <REVIEW_PATH or "skipped">
```

## Edge Cases

| Condition | Behavior |
|-----------|----------|
| `$PLAN_PATH` does not exist | Fail fast at Step 1 with a clear message. No agents dispatched. |
| Plan file < 50 words | Refuse at Step 2 with "too short to meaningfully review". |
| No `## Context` / `## Background` / `## Problem` / `## Summary` / `## Overview` section | Pass `"Not provided in plan."` to personas. Do not prompt user. |
| No candidates for smart default | Exit Step 1 with instructions to pass a path explicitly. |
| Persona agent returns malformed output | Show raw output, mark verdict as `unknown`, exclude from consolidated list. Does NOT count as `approve` for convergence. |
| All reporting reviewers approve on round 1 with no suggestions | Skip Step 7. Jump to Step 9 → Step 10. |
| A model skeptic (`codex` / `antigravity`) fails or times out | Manifest marks it `failed` / `failed(timeout)`. Non-fatal: mark verdict `unknown`, exclude from consolidated list, proceed with the remaining reviewers. Does NOT block convergence on its own. |
| Both model skeptics fail | Review degrades gracefully to the original three-persona behavior for that round. Note it in the round output. |
| `agy` hits its quota | `council-round.sh` auto-retries once on its fallback model (`COUNCIL_ANTIGRAVITY_FALLBACK`); no action needed here. |
| User picks Cherry-pick with unparseable numbers | Re-prompt once; on second failure, treat as None / done. |
| `Edit` fails on a specific finding (e.g. ambiguous `old_string`) | Warn, skip that finding, continue with the rest. The skipped finding is not added to `INCORPORATED_LOG`. |
| User picks "Discard and exit" at round 3 | Findings from rounds 1–N are already in `INCORPORATED_LOG`. The "discard" only means no further incorporation — the save artifact (if user opts in) still includes the full session. |
| `$REVIEW_PATH` exists | Append `-2`, `-3`, etc. before writing. |
| Plan path contains spaces or special chars | All shell commands quote `$PLAN_PATH`. |

## Examples

The examples below predate the model skeptics and show only the three persona reports; a live round also includes `CODEX` and `AGY` reports and verdicts (see Step 6). They remain accurate for the persona flow, loop mechanics, and incorporation prompts.

### Example 1 — Happy path, two rounds, partial cherry-pick

```
User: /adversarial-review

Pick the plan to review:
  ~/.claude/plans/fizzy-sprouting-simon.md       2026-05-27 14:03 · Plan: /adversarial-review user skill
  ~/.claude/plans/quiet-evening-tide.md          2026-05-26 10:17 · Plan: Migrate fes-platform off ext-dns helm
  ~/Documents/Work/docs/plans/karpenter-rollout.md  2026-05-25 18:42 · Karpenter org-wide rollout
> ~/.claude/plans/fizzy-sprouting-simon.md

Dispatching 3 reviewers in parallel...

# Round 1

[PE report — finds: no rollback story, blast radius unclear, IAM model missing]
---
[ARCH report — finds: test strategy thin, abstraction premature, no schema versioning]
---
[TPM report — finds: success criteria vague, no stakeholder list, sequencing unclear]

## Consolidated findings (Round 1)
1. [PE] Step 8 — Apply edits: add explicit rollback procedure for failed Edit operations
2. [PE, TPM] Verification: define measurable success criteria before implementation
3. [ARCH] Persona prompt skeleton: tighten the abstraction or document why three personas not more
4. [TPM] Critical files: name the stakeholders who'll consume the saved review
5. [ARCH] Verification: expand test coverage for malformed agent output cases

Verdicts — PE: accept-with-changes | ARCH: accept-with-changes | TPM: accept-with-changes

[Incorporate all / Cherry-pick / None / done] > Cherry-pick
Which findings? > 1,2,5

Applied 3 edits to ~/.claude/plans/fizzy-sprouting-simon.md.

Re-dispatching reviewers on revised plan...

# Round 2

[PE report — approve]
---
[ARCH report — approve]
---
[TPM report — accept-with-changes, finds: rollout comms still missing]

## Consolidated findings (Round 2)
1. [TPM] Step 11 — Final summary: add a comms-plan line if the plan is for a multi-team rollout

Verdicts — PE: approve | ARCH: approve | TPM: accept-with-changes

[Incorporate all / Cherry-pick / None / done] > None / done

[Save review / Skip save] > Save review

Adversarial review complete.
  Plan:               ~/.claude/plans/fizzy-sprouting-simon.md
  Rounds:             2
  Findings applied:   3 across 2 rounds
  Final verdicts:     PE: approve | ARCH: approve | TPM: accept-with-changes
  Review saved:       ~/.claude/plans/fizzy-sprouting-simon-review-2026-05-27.md
```

### Example 2 — Round 1 convergence

```
User: /adversarial-review ~/.claude/plans/clean-tide.md

Dispatching 3 reviewers in parallel...

# Round 1

[three reports, all approve, no suggested changes]

Verdicts — PE: approve | ARCH: approve | TPM: approve

All three personas approve with no suggested changes. Loop terminates.

[Save review / Skip save] > Skip save

Adversarial review complete.
  Plan:               ~/.claude/plans/clean-tide.md
  Rounds:             1
  Findings applied:   0
  Final verdicts:     PE: approve | ARCH: approve | TPM: approve
  Review saved:       skipped
```

### Example 3 — Path argument, short plan refused

```
User: /adversarial-review ./scratch.md

Plan looks too short to meaningfully review (12 words).
Run /adversarial-review after the plan has substantive content.
```

## Notes

- The skill never commits to git. The user does that.
- The skill never modifies anything outside `$PLAN_PATH` and the optional `$REVIEW_PATH`.
- The persona response contracts are strict on tone (`Do NOT list strengths`) — this is intentional. If the user wants balanced peer review, they should use a different skill (e.g. extend `/one-pager` semantics to plans, or write a new `/peer-review-plan` skill).
- Subagent dispatch uses `general-purpose` to match the precedent in `one-pager` and `rfc`. If a future dedicated `plan-reviewer` agent is added, swap the `subagent_type`.
- The model skeptics reuse `council-round.sh` rather than shelling out to `codex` / `agy` directly. That script owns the timeout watchdog (macOS has no `timeout`), sandbox flags, stdin handling under parallel contention, and the Antigravity quota fallback. Do not reimplement CLI invocation here — call the script.
- The plan is inlined into the skeptic prompt because the CLIs run sandboxed and can't reliably read `$PLAN_PATH` (often outside the repo, e.g. `~/.claude/plans/`). The Claude personas read the file directly via their `Read` tool — that asymmetry is deliberate.
- Requires the `codex` and `agy` CLIs on `PATH`. If either is absent, that skeptic's `council-round.sh` member fails and is treated as `unknown` — the review still runs on the remaining reviewers.

## Related

- `~/.claude/skills/council/SKILL.md` — source of `council-round.sh`, the CLI fan-out engine reused here.
- `~/.claude/skills/one-pager/SKILL.md` — closest structural template (3 personas, parallel dispatch, iterative loop, response contract).
- `~/.claude/skills/rfc/SKILL.md` — 2-persona variant with verdict-gated approval.
- `~/.claude/skills/terraform-review/SKILL.md` — confidence-filtered, lens-based parallel dispatch; uses a custom `terraform-reviewer` subagent (intentionally not adopted here).
- `/superpowers:writing-plans` — the upstream skill that produces the plans this skill reviews.
