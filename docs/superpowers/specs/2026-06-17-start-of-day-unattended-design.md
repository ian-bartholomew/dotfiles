# Design: `/start-of-day --unattended`

Date: 2026-06-17
Status: approved (post-adversarial-review + consensus; ready to implement)

## Goal

Let `/start-of-day` run as an unsupervised Desktop routine (no human present, no
prompts). Reuse the `/end-of-day --unattended` pattern, but adapt the failure
model to SOD's actual dependency graph rather than copying EOD's gate wholesale.

## Constraints

- Manual `/start-of-day` (no token) must be byte-for-byte unchanged.
- The two modes share every step body; they differ ONLY at named points.
- `work-board` gets a small, in-scope text change so it owns its OWN no-prompt
  path (see below). `daily-standup` is unchanged (already non-interactive). The
  `Skill` tool loads a sub-skill into the SAME conversation/context as SOD (it
  is not an isolated subagent), so SOD's unattended rules remain in-context —
  but consensus review flagged that relying on a caller-side instruction to
  override work-board's own "ask when unsure" directive is an unverified
  instruction-conflict, not a guarantee. The fix removes the conflict at the
  source rather than shouting over it.

### work-board no-prompt path (consensus-driven, in scope)

work-board/SKILL.md step 2 already documents the exact fallback we want:
"In `--dry-run` mode never ask - use the best guess and note it in the report."
But `--dry-run` (passed to the sync script) also disables live moves, so it is
the wrong lever for SOD. Change: widen that one rule so it also fires for an
unattended caller, with live sync still enabled. Concretely, line ~54 becomes:
"In `--dry-run` mode, OR when the caller invokes work-board unattended (it will
say so), never ask - use the best guess and note it in the report." SOD's
Step 2.5 invocation states "this is an unattended invocation." After this,
work-board no longer instructs a prompt on the unattended path, so there is no
competing directive for the model to resolve.

- Auth/permission mode (auto mode, per commit a96bf7d) is the routine's config,
  not this skill. This spec is skill-side only, but the run performs a
  best-effort permission self-check (Observability).

## Architecture

One skill, two modes. Add to `start-of-day/SKILL.md`:

- `argument-hint: "[--unattended]"` in frontmatter, and bump `version`
  (0.28.0 -> 0.29.0).
- Add `PushNotification` to `allowed-tools`. Do NOT add `AskUserQuestion`: the
  unattended contract is "never prompt", so granting the prompt tool would only
  invite a hang. Manual mode does not call it either (the skill is already
  one-shot/non-interactive); work-board's prompt is suppressed by instruction,
  not by SOD holding the tool.
- A single `## Unattended Mode` section, placed after `## When NOT to Use`
  (same position as end-of-day). If the `--unattended` token is absent, the
  section is ignored and behavior is unchanged.

The scheduled wrapper `~/.claude/scheduled-tasks/start-of-day/SKILL.md` body
changes from `Run the /start-of-day skill` to `/start-of-day --unattended`.

### Note on EOD/SOD drift

The unattended boilerplate (lock, run-date, observability, permission
self-check) is ~90% identical to end-of-day's. We accept copy-paste here (two
call sites, no skill-include mechanism) but add a `<!-- keep-in-sync-with:
end-of-day Unattended Mode -->` marker comment at the top of SOD's section so a
future reader knows the two must track each other.

## `## Unattended Mode` section contents

### Global rules (unattended)

- No interactive prompts of any kind. Never call `AskUserQuestion`. Every place
  the flow (or an invoked sub-skill) would ask, take the documented auto-default
  and continue.
- Best-effort: a mid-run sub-skill or step failure is logged and the run
  continues degraded. The exceptions are the hard gates in Step 0 (Atlassian,
  gh) and the post-write integrity check (Step 4), which still halt.
- Every existing *halt* path in the skill (render.py nonzero at Step 3,
  marker-count != 1 at Step 4, `obsidian daily` failure at Step 1, Step 0 gate)
  must, before exiting, send the end-of-run notification AND release the lock.
  There is no silent halt in unattended mode.
- End every run with a `PushNotification` and a durable `wiki/_log.md` entry
  (Observability, below).

### Concurrency lock (first action, unattended)

Acquire `/tmp/sod.lock` before Step 0 so a catch-up run and a manual run cannot
double-write the shared `/tmp/sod-*.json` caches and the daily note:

```bash
# clear an orphaned lock older than 6h, then acquire atomically:
if [ -d /tmp/sod.lock ] && find /tmp/sod.lock -maxdepth 0 -mmin +360 | grep -q .; then
  rmdir /tmp/sod.lock 2>/dev/null
fi
if ! mkdir /tmp/sod.lock 2>/dev/null; then
  echo "start-of-day already running (/tmp/sod.lock present); aborting."; exit 0
fi
echo "$$ $(date -u +%FT%TZ)" > /tmp/sod.lock/owner   # liveness/debug record
```

Release rules:

- `rmdir /tmp/sod.lock` (after removing `owner`) is the final action on EVERY
  exit path: clean completion, Step 0 gate halt, render halt (Step 3),
  marker-count halt (Step 4), obsidian halt (Step 1), and any standup/work-board
  failure that ends the run.
- There is no shell-level `finally` across the multi-tool run. If the agent or
  host is killed mid-run the lock persists; the 6h staleness sweep above is the
  only recovery. **Cascade to call out:** a stuck lock blocks every subsequent
  scheduled run (and the same-day catch-up) until the 6h window elapses — e.g. a
  hang at 6am blocks SOD until noon. This is an accepted limitation, recorded
  here so a stuck morning has a known cause and a known fix
  (`rmdir /tmp/sod.lock`). The kill-switch section documents the manual clear.
- SOD and EOD use independent locks (`/tmp/sod.lock` vs `/tmp/eod.lock`). They
  write disjoint marker blocks in the daily note, but to avoid a concurrent
  `Write` to the same file, the daily-note upsert (Step 4) re-reads immediately
  before writing so the last writer preserves the other's block.

### Run-date under catch-up

A Desktop task missed because the Mac was asleep fires a single catch-up. The
cached `date +%Y-%m-%d` reflects the actual run day. All sources are
point-in-time snapshots, so this is expected, not an error; the daily-note
section, the notification, and the `wiki/_log.md` entry are labeled with the
run day.

### Per-step deltas (unattended)

- **Step 0 (pre-flight), split gate:**
  - **Hard gates (still HALT):** Atlassian MCP probe, `gh auth`, and the
    **open-PR** `gh search prs` query. SOD's own core output (JIRA + PR
    sections) cannot be produced without these; writing a snapshot missing them
    poisons downstream consumers. The **merged-PR** query is NOT a hard gate: it
    is already best-effort in the skill (on failure the "Potential to close"
    flag simply does not fire), so a merged-query failure degrades, it does not
    halt. On halt: send the failure notification, write the `wiki/_log.md`
    halted entry, release the lock, exit.
  - **Soft gates (degrade, do NOT halt):** Slack MCP and Zoom MCP probes. These
    are needed only by the chained `/daily-standup` (Step 6), not by SOD's own
    snapshot. On probe failure: record the gap, SKIP the Step 6 standup chain,
    and explicitly mark the run `degraded` with a `standup skipped: <reason>`
    note in BOTH the notification and the `wiki/_log.md` entry — never imply a
    partial or successful standup. Continue writing the PR/JIRA/Todoist
    snapshot.
  - The `lookup failed` graceful-degrade path for `td`/`obsidian` is unchanged.
- **Step 1 (`obsidian daily`):** on failure, do NOT use the interactive
  terminal-fallback (no human to read it). Record a degradation, include the
  rendered section in the notification body if short enough, send the
  notification + `wiki/_log.md` entry, release the lock, and stop (no daily-note
  write is possible without the path).
- **Step 2.5 (work-board, live):** invoke work-board live AND tell it this is an
  unattended invocation, so its own (now-widened) step-2 rule takes the
  best-guess-and-log path instead of `AskUserQuestion`. SOD's invocation line:
  "Run work-board live, unattended — do not prompt; for any unsure new-card
  priority use the best guess (or `p3` if no signal) and log each
  `(ticket -> p?)`." The no-prompt behavior is owned by work-board's text (see
  Constraints), not by this instruction alone. Include the sync summary in the
  notification.
- **Step 2.6 (EOD heartbeat):** runs unchanged (it is already a non-interactive
  bash check). Its warning string, if present, is carried into the notification
  and the `wiki/_log.md` entry.
- **Step 3 (render.py):** render degrades a malformed/missing source file to a
  `lookup failed:` line and exits 0 (verified) — it does NOT halt on bad source
  JSON. Only a genuine nonzero exit (bad CLI args / unexpected crash) is a halt;
  unattended that halt = notify + `wiki/_log.md` entry + release lock. If the
  output carries any `lookup failed:` sentinel, mark the run `degraded`.
- **Step 4 (daily-note upsert):** auto-write the rendered section (local,
  reversible, idempotent). Re-read the note immediately before writing (see lock
  rules) so a concurrent EOD block is preserved. The
  `grep -c '<!-- sod:begin'` post-write check is unchanged and still halts on
  `0`/`>1` (integrity gate) — but unattended halt = notify + log + release lock.
- **Step 5 (confirmation):** the terminal line still prints (it lands in the run
  log) but is not load-bearing unattended; the durable record is the
  `wiki/_log.md` entry.
- **Step 6 (daily-standup):** invoked as the final step ONLY if the Slack/Zoom
  soft gates passed. No flag passed; `/daily-standup` is already non-interactive
  (it infers blockers, never prompts) — pin this assumption in the spec and
  re-verify during the build by grepping daily-standup for `AskUserQuestion`. A
  standup failure is logged + folded into the notification; it does not undo the
  already-written `## Start of Day` section.

### Observability (unattended)

- **Durable run record.** Append a `wiki/_log.md` entry
  (`## [<date>] start-of-day`) every run, mirroring EOD. This is the durable
  record (PushNotification may silently fail in headless mode) and the future
  heartbeat hook so a missed SOD run is itself detectable.
- **End-of-run notification.** Send a `PushNotification` with a one-line status:
  `start-of-day <date>: ok` / `start-of-day <date>: degraded (<N> failures)` /
  `start-of-day <date>: halted (<gate>)`. Body carries PR/JIRA/Todoist counts,
  the work-board sync summary, the EOD-heartbeat warning if present, the
  standup-skipped note if a soft gate failed, and any step failures.
- **Permission self-check (best-effort).** If any tool call returns blocked or
  times out waiting on a permission decision (symptom of the Desktop auth mode
  having reverted), mark the run `degraded` and say so. Acknowledged limitation:
  if the run is fully wedged at a prompt it may never reach the notification
  code; the `wiki/_log.md` entry's absence is then the backstop signal.

### Kill-switch / rollback

Documented in the section: to disable the routine, revert the wrapper
(`~/.claude/scheduled-tasks/start-of-day/SKILL.md`) body to
`Run the /start-of-day skill` (or delete the scheduled task). To clear a stuck
lock after a killed run: `rmdir /tmp/sod.lock`.

### Success checklist (what "ran cleanly" means)

Hard gates passed; soft gates passed or standup cleanly skipped; all four
sources fetched or rendered an honest `lookup failed`; the daily-note section
was written and the post-write check returned exactly 1; work-board sync
reported created/moved/completed with no prompt; standup appended its section
(or was skipped on a soft-gate miss); a `wiki/_log.md` entry was written; the
notification status is `ok` (or `degraded` with the reason named).

## Out of scope (YAGNI)

- No `--unattended` flag in `daily-standup` (already non-interactive). work-board
  gets only the minimal text widening described in Constraints, not a new flag.
- No auth/permission-mode config change (only a runtime self-check).
- No shared skill-include mechanism for the EOD/SOD boilerplate (accepted
  copy-paste with a keep-in-sync marker).
- No new render.py behavior.

## Testing

Two gates; the first is necessary but not sufficient.

1. **Interactive E2E (in-session, this build).** Invoke
   `/start-of-day --unattended` and confirm: zero `AskUserQuestion` calls; the
   daily-note section written with post-write check == 1; work-board synced with
   priorities auto-assigned and logged; a `wiki/_log.md` entry written; the
   end-of-run notification fired with an `ok`/`degraded` status. **Ambiguous-card
   probe (consensus-required):** stage at least one new assigned ticket whose
   priority is genuinely ambiguous (JIRA Medium / no signal, no existing card)
   so work-board's step-2 prompt branch WOULD fire interactively, then confirm
   the unattended run assigns a best-guess priority and logs it WITHOUT a prompt,
   while the live sync still creates the card. A clean run with only
   unambiguous cards does not exercise this path. Force a failure path: a
   malformed `/tmp/sod-jira.json` degrades to a `lookup failed:` line (exit 0,
   verified) and marks the run `degraded` — it does NOT halt; the genuine halt
   path is a nonzero render exit (bad args / crash). Confirm a degraded source
   is surfaced in the notification and the lock is still released. Regression: invoke manual
   `/start-of-day` (no token) and confirm the Unattended Mode section is
   ignored. Limitation, stated plainly: an interactive session has a human +
   live auth and CANNOT reproduce the scheduler's no-TTY / overnight-token-expiry
   context — which is why gate 2 exists.
2. **First live scheduled run (post-merge gate).** Inspect the first real
   scheduled run's `PushNotification` and `wiki/_log.md` entry. Ship is not
   "done" until one clean live `ok` (or an understood `degraded`) is observed.
