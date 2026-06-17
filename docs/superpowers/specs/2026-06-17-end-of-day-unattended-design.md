# End-of-Day Unattended — Design

Date: 2026-06-17
Status: Draft for review (revised after adversarial review round 1)

## Goal

Make `/end-of-day` (currently v0.3.0, a 14-step interactive pipeline) able to
run unsupervised as a local Claude Code Desktop scheduled task, with zero
permission prompts and zero in-skill approval gates, while leaving the manual
interactive experience unchanged. Meeting action items are auto-created, but
deduped against the live Todoist list first so a scheduled run never creates a
todo that already exists, and never silently buries a real commitment.

## Background — why the current setup stalls

Forensics over 2026-06-03 → 2026-06-17 (12 EOD runs, but only **one** real
scheduled run, on 06-09 — small-n for the actual target environment) show the
unsupervised stalls are **harness permission prompts**, not in-skill content
gates:

1. `Bash(rm -rf /tmp/eod-cache-threads ... /tmp/eod-fes-support-cache.json)` —
   the Step 1.5 cache reset. `rm` is in no allowlist. Denied in the 06-09
   scheduled run, which blocked the Slack steps. Top offender.
2. MCP calls under rotating server-UUID identities that do not match the
   friendly-name allowlist entries. Jira search failed twice with "permission
   stream closed".
3. Step-0 pre-flight MCP probes.

Separately, the in-skill `AskUserQuestion` gates (Steps 3, 4, 5, 6, 9 and the
error-handling continue/retry/halt prompts) cannot be answered unattended.

## Execution model (decided)

EOD moves off the hand-rolled `com.ian.eod` LaunchAgent onto a **local Claude
Code Desktop scheduled task** (`~/.claude/scheduled-tasks/end-of-day/`).

Accepted tradeoff: Desktop tasks fire only while the Desktop app is open and the
Mac is awake; missed runs get one catch-up within 7 days. Reliability downgrade
from the always-on LaunchAgent, accepted deliberately.

**Prerequisite (must verify, not assume):** the Desktop app is installed, set to
launch-at-login, and the `end-of-day` task is registered on the right schedule
(weekdays 16:30) and exposes a per-task permission mode in its editor. If any of
these is not true, the migration does not start.

## Authorization design (decided) — three layers + threat model

Local Desktop tasks read `~/.claude/settings.json` allow/deny rules AND carry a
per-task permission mode set in the Desktop UI. In the default `ask` mode an
unpermitted tool stalls indefinitely.

Because Slack/Zoom/Atlassian are claude.ai **connectors** (not local `.mcp.json`
servers), their tool names resolve to rotating UUIDs that cannot be durably
allowlisted — allow rules cannot wildcard the server segment.

> **UPDATE (post-implementation, 2026-06-17):** the chosen Layer 1 is **auto
> mode**, not `bypassPermissions`. The Desktop app did not offer
> `bypassPermissions` for the task; it offered "auto". Auto is a better fit
> anyway: it keeps a background safety classifier that vets tool calls (blocking
> escalation / hostile-content-driven actions) instead of blanket-approving
> everything, which directly mitigates the prompt-injection surface this job
> carries. Auto auto-approves read-only actions, working-dir edits, and
> allow-rule matches; routes other Bash/MCP through the classifier; and still
> honors deny rules. It is NOT a guaranteed zero-prompt for connector MCP calls
> (the classifier can route one to a stall), so the settings allowlist (Layer 2)
> and the observability backstops (notification + start-of-day dead-man's-switch)
> remain load-bearing. Net posture: auto + allowlist + observability. The
> supervised e2e on 2026-06-17 ran clean under this posture. Known UI bug #53569:
> setting auto on a task can hide "auto" from the mode picker elsewhere.

- **Layer 1 — auto mode per-task (primary).** Set in the Desktop UI. Vets tool
  calls via a safety classifier; auto-approves read-only + working-dir edits +
  allow-rule matches; honors deny rules. (Originally specced as
  `bypassPermissions`; see UPDATE above for why auto was chosen.)
- **Layer 2 — `settings.json` allow + deny.** Allow the deterministic,
  non-rotating Bash so they never stall even if Desktop silently reverts to
  `acceptEdits` (a known bug):
  - `Bash(rm -rf /tmp/eod-cache-threads:*)`
  - `Bash(rm -f /tmp/eod-fes-support-cache.json:*)`
  - confirm `Bash(td:*)`, `Bash(python3:*)` present.
  Deny rules still apply under `bypassPermissions` — see threat model.
- **Layer 3 — `--unattended` skill flag.** Removes in-skill AskUserQuestion
  gates and makes externally-visible writes draft/dry-run only (below).

### Threat model for `bypassPermissions` (new)

`bypassPermissions` auto-approves *every* tool call on a job that ingests
untrusted content (Slack messages, Zoom transcripts, Jira tickets). That content
is a prompt-injection surface: a crafted message could induce a destructive or
exfiltrating `Bash`/MCP call with no human to catch it. Mitigations, layered:

1. **Deny-list as the real containment.** `settings.json` `deny` rules are
   honored even under `bypassPermissions`. The existing deny-list already blocks
   `rm -rf /`, `mkfs`, `dd`, force-push to main, curl|bash, etc. Audit it and
   add any EOD-relevant destructive patterns. This is the hard floor.
2. **External writes are draft/dry-run only unattended** (Layer 3) — limits the
   blast radius of any injected instruction to local, reviewable artifacts.
3. **Known reversion bug.** Layer 1 may silently revert to `acceptEdits`
   mid-run. The supervised-first-run gate (below) and the post-run self-check
   detect this rather than discovering it after a bad run.

### Durable alternative, honestly sized (was dismissed too fast)

Migrating Slack/Zoom/Atlassian from claude.ai connectors to local `.mcp.json`
servers would make the allowlist the durable lever (pinned server names, no UUID
rotation, no need for blanket bypass). Rough size: one community/official MCP
server per service (Slack, Zoom, Atlassian) wired in `~/.claude.json` with
tokens in env, plus rewriting the skill's hardcoded `mcp__claude_ai_*` tool
names to the pinned names — call it a 1-2 day spike with token-management and
re-test risk. Deferred, not free; revisit if `bypassPermissions` proves too
unreliable in practice. Tracked as a follow-up, not silently dropped.

## The `--unattended` flag (decided: flag-gated)

`/end-of-day` accepts an optional `--unattended` argument. Manual `/end-of-day`
is unchanged. The scheduled task body becomes `/end-of-day --unattended`. To
limit interactive/unattended drift, the two modes share every step's body and
differ ONLY at the documented decision points below.

### Concurrency guard (new, runs before anything)

First action in `--unattended`: acquire an exclusive lock
(`flock`/`mkdir` sentinel on `/tmp/eod.lock`). If held, abort immediately with a
logged "already running" — prevents a catch-up run and a manual run from
double-writing the shared `/tmp` caches and `_action-item-state.json`. Release
on exit (including failure).

### Run-date resolution under catch-up (new)

`date +%Y-%m-%d` at run start defines "today" (already specified). A delayed
catch-up therefore labels and processes against the **actual run day**, not the
missed day. All source steps are "since last run", so no data is lost; the
daily-note section is written for the run day. State this explicitly so a
Saturday catch-up is understood, not surprising.

### Per-step behavior in `--unattended`

- **Step 0 (pre-flight MCP):** unchanged. On failure it HALTS — correct even
  unattended; records the failure to the report + `wiki/_log.md` + the failure
  notification (Observability).
- **Step 1 (meeting-ingest, bg subagent):** unchanged.
- **Step 1.5 (thread cache, `rm` + Slack reads):** unchanged logic; `rm`
  covered by Layers 1/2.
- **Step 2 (fes-support → Confluence, bg subagent):** **draft/hold only.** Do
  NOT publish live Confluence pages unsupervised. Either publish as DRAFT or
  write the would-be content to a local hold file for next-interactive review.
  (Verify the fes-support-learnings skill's draft capability during
  implementation; if it cannot draft, defer the publish entirely and surface in
  the report.)
- **Steps 3, 4 (support / internal learnings → `raw/`):** non-interactive with
  the SAME explicit auto-default convention Step 2 already documents
  (classification → `knowledge`, resolution → `unresolved`, domain → keyword-map
  else `general`, duplicate → `skip`, other → skip/no-action). These write to
  local `raw/` (not external), compiled later — acceptable unsupervised. Record
  every auto-default for the report.
- **Step 4.5 (join):** unchanged.
- **Step 5 (meeting action items):** see Todoist dedup below. Inline, no
  AskUserQuestion.
- **Step 6 (project-log gate):** audit only (detection). Do NOT auto-write
  `log.md` entries; record gaps in the report and surface in the Step 9 daily
  note Follow-ups for next interactive session.
- **Step 7 (compile):** non-interactive. Verify the compile sub-skill actually
  supports a non-interactive path during implementation; do not assume.
- **Step 8 (verify-status):** read-only, unchanged.
- **Step 8.5 (work-board dry-run):** read-only report, unchanged.
- **Step 9 (daily-note synthesis):** auto-write the drafted section (local,
  idempotent upsert, post-write-verified by the existing `grep -c` check). The
  daily note is the one external-ish surface intentionally auto-written because
  it is local, reversible, and the user reads it. On the post-write check
  failing (`0` or `>1`), do NOT proceed silently — log a failure marker and
  include it in the notification.
- **Step 10 (report):** append run summary to `wiki/_log.md` AND emit the
  end-of-run notification (Observability), including resolved date,
  auto-defaults applied, deduped/deferred items, Step-6 gaps deferred, and any
  step failures.

**Error handling in `--unattended`:** no continue/retry/halt prompts. Mid-run
sub-skill failures are logged, the pipeline continues best-effort, and the
failure is recorded in the report AND the notification. Step 0 pre-flight is the
one hard halt. Pipeline-interruption handling is interactive-only.

## Observability (new) — no silent failures

1. **End-of-run notification.** Every `--unattended` run ends by sending a
   `PushNotification` (tool available) with a one-line status: `ok` /
   `degraded (N step failures)` / `halted (pre-flight)` plus the resolved date.
   Degraded and halted are loud.
2. **Dead-man's-switch via start-of-day.** "Run never fired" produces no
   `wiki/_log.md` entry — a non-event nothing notices. Cheapest mitigation that
   needs no new daemon: `/start-of-day` checks whether the prior business day
   has an `end-of-day` `wiki/_log.md` entry and flags its absence in the morning.
   (Small follow-up edit to start-of-day; in scope as a one-line check.)
3. **Post-run permission self-check.** At the end of the run, assert the run did
   not encounter a stall/timeout (proxy for `bypassPermissions` having reverted)
   and include the result in the notification.

## Todoist dedup (decided: revised after review)

The "equals-or-is-contained-by" rule is removed — substring containment caused
silent data loss ("Email Bob" ⊂ "Email Bob about Q3"). Replaced with two
coherent, non-destructive layers that share ONE terminal-state authority.

**Single terminal-state authority: the script (`meeting_action_items.py`).**
The model semantic pass may only *downgrade* a candidate `todo → skip`; it can
never write a permanent `dismiss`. Both layers reduce to the same outcome for a
live-todo match: **not created this run, not permanently suppressed,
re-evaluated next run.** This removes the precedence conflict the review flagged.

- **Script backstop (in `apply`, protects every caller):**
  - Before creating, query `td task list --project Work --json` once per
    `apply` (cached for the batch).
  - **Match rule:** normalized-equality OR token-set overlap ratio ≥ threshold
    (default 0.85), NOT substring containment. Normalization is specified
    precisely with test vectors (lowercase; NFKC unicode fold; strip leading
    checkbox/bullet markers; collapse internal whitespace; strip trailing
    punctuation; drop a small stop-word set). Emoji and unicode cases covered by
    vectors.
  - On match: skip creation, return a `duplicate` result carrying the matched
    task URL, and **do not record terminal state** (so if the matched live todo
    is later completed and the action item still applies, it resurfaces).
  - "Open todo" scope is explicit: `--project Work`, top-level + subtasks,
    excludes completed/archived and other projects. Documented in the test.
  - Batch edge cases covered by tests: two candidates matching the same existing
    todo; two candidates matching each other; candidate matching nothing.
- **Model semantic pass (Step 5, unattended only):** catches reworded dupes the
  string rule misses. Marks such candidates `skip` (non-terminal). A confidence
  floor applies; low-confidence matches are NOT skipped — they go through as
  todos (creating a near-dupe is recoverable; dropping a real commitment is
  not). Each skip is logged with the matched todo for the report.
- **Surfacing:** dropped/deferred candidates appear in the failure-visible
  report and notification, not only an append-only log nobody reads.

Existing per-item state (`<meeting_dir>::<sha256(normalized)[:12]>` → todoed |
dismissed) is unchanged and remains the meeting+item-keyed dedup for items the
user explicitly actioned in interactive runs.

## Scheduling migration (decided) — safe sequencing

Reordered so EOD is never left non-functional:

1. **Backup** `com.ian.eod.plist` (and sod/standup, untouched) to a timestamped
   dir.
2. **Verify Desktop prerequisites** (app installed, launch-at-login, task
   registered, schedule correct, permission-mode setting present).
3. **Configure:** set `~/.claude/scheduled-tasks/end-of-day/SKILL.md` body to
   `/end-of-day --unattended`; user sets the task permission mode to
   `bypassPermissions` in the Desktop UI.
4. **Supervised first-run gate:** run `/end-of-day --unattended` manually
   (watching) and walk the success checklist (below). Fix anything; repeat.
5. **Prove it scheduled:** let the Desktop task fire on its own and confirm a
   clean run via the checklist + notification. Require **2 consecutive clean
   scheduled runs**.
6. **Only then** `launchctl bootout` + delete `com.ian.eod.plist`, AND
   `com.ian.sod.plist` + `com.ian.standup.plist` (decided: remove all three).

**sod/standup (decided: remove all three now).** Their LaunchAgents are removed
in this change alongside EOD's. Accepted risk, explicitly: start-of-day and
daily-standup have NOT received `--unattended` treatment, so their Desktop
scheduled equivalents may stall on their own in-skill prompts until converted.
Tracked follow-up: give start-of-day and daily-standup the same `--unattended`
conversion. Until then, run them manually if a scheduled run stalls.

**Rollback:** if the Desktop task proves unreliable after step 6, restore the
EOD plist from backup (`launchctl bootstrap`) to return to the always-on runner.
Note: restoring the runner does not undo a bad run's local writes — but because
external writes are draft/dry-run only unattended, reversal is limited to local
artifacts (daily note section re-upserts cleanly; Todoist state is
non-terminal).

## Scope summary + Definition of Done

Version-controlled (2 PRs):

1. `.dotfiles`: `stow-packages/claude/.claude/skills/end-of-day/SKILL.md` —
   `argument-hint`, `--unattended` mode, concurrency lock, draft/dry-run
   external writes, observability hooks, per-step behavior.
   - Plus a one-line `start-of-day` dead-man's-switch check (same repo).
2. `lyt-assistant`: `meeting_action_items.py` dedup backstop + tests (match
   rule, normalization vectors, batch edge cases, non-terminal state, `--dry-run`
   stays green).

Local machine setup (not PRs):

1. `~/.claude/settings.json` — allow backstop + deny-list audit (direct edit;
   not stowed).
2. `~/.claude/scheduled-tasks/end-of-day/SKILL.md` — `/end-of-day --unattended`.
3. Remove `com.ian.eod`, `com.ian.sod`, `com.ian.standup` LaunchAgents (backup +
   unload + delete) — EOD's **only after** 2 clean scheduled runs.
4. Desktop UI permission mode → `bypassPermissions` (user manual step).

**Cross-cutting DoD + ordering:** dedup PR merged → skill PR merged → settings
allow/deny edited → scheduled-task body updated → permission mode set →
supervised first run green → 2 clean scheduled runs → EOD plist removed. EOD is
not "done" until the last item.

**"Successful unsupervised run" checklist (the success criterion):**

- Pre-flight passed (not halted).
- Slack steps (2/3/4) reached and produced output (or honest nothing-to-do).
- No auto-default on any critical-publish step (Confluence stayed draft/deferred).
- Step 5 dedup ran; report lists created vs deduped-vs-deferred; no silent drop.
- Daily note section written and post-write check returned exactly 1.
- End-of-run notification received with status `ok`.
- `wiki/_log.md` has the run entry for the resolved date.

## Out of scope (with honest notes)

- start-of-day / daily-standup `--unattended` treatment (follow-up; their
  LaunchAgents are removed in this change, so until the follow-up lands their
  scheduled runs may stall — run manually if so).
- Connector → local `.mcp.json` migration (sized above; deferred follow-up).
- Auto-writing project `log.md` entries unsupervised.

## Verification

- `meeting_action_items.py`: `python3 test_meeting_action_items.py` green,
  including new dedup tests (match rule, normalization vectors, batch edge
  cases, non-terminal state) and the existing `--dry-run` path.
- Skill: the unattended section documents zero AskUserQuestion calls and the
  draft/dry-run external-write behavior; the supervised first run (migration
  step 4) is the integration test. Honest limitation: a markdown prompt's
  runtime behavior cannot be unit-tested without executing the model — the
  supervised gate + minimized interactive/unattended divergence are the
  controls.
- settings.json: allow entries present; deny-list audited.
- Observability: trigger a deliberate failure in the supervised run and confirm
  the notification fires `degraded`/`halted`.
- LaunchAgents: after removal, `launchctl list | grep com.ian.eod` returns
  nothing; sod/standup still present; backups exist.
