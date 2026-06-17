---
name: end-of-day
description: This skill should be used when the user asks to "end of day", "EOD", "run end of day", "wrap up the day", or runs "/end-of-day". Captures the day's Zoom + Slack signal into the wiki (with FES support learnings published to Confluence), reviews meeting action items into Todoist, audits project log.md files for today's entries, runs a verify-status snapshot, and synthesizes today's daily-note EOD section. Friday adds a weekly retrospective.
version: 0.4.0
argument-hint: "[--unattended]"
allowed-tools: [Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion, Skill, Agent, TaskStop, PushNotification]
---

# End-of-Day Skill

Run the full end-of-day pipeline: pull the day's meetings, extract Slack learnings (FES support to Confluence, support + internal channels to `raw/`), review meeting action items into Todoist, audit project logs for today's work, compile everything into the wiki, run a verify-status snapshot, and synthesize today's daily-note EOD section.

## Purpose

Provide a single command that runs the complete daily wrap-up workflow with interactive gates at every step. Chains together meeting ingestion, three Slack-learning extractions, meeting action item review, a project-log audit (enforcing the CLAUDE.md rule that any project work must produce a `log.md` entry), the full compile pipeline, a read-only verify-status snapshot, and finally a daily-note synthesis pass — so the user doesn't have to remember to run each one manually at end of day.

## When to Use

Invoke this skill when:

- User explicitly runs `/end-of-day`
- User mentions "end of day", "EOD", "wrap up the day", "end of day routine"
- User wants to capture the day's meetings + Slack activity and roll it into the wiki in one pass

## When NOT to Use

- If the user only wants to pull meetings: use `/lyt-assistant:meeting-ingest`
- If the user only wants FES support learnings to Confluence: use `/fes-support-learnings:fes-support-learnings`
- If the user only wants the support channel into `raw/`: use `/lyt-assistant:support-learnings`
- If the user only wants the internal channel into `raw/`: use `/lyt-assistant:internal-channel-learnings`
- If the user only wants to review meeting action items into Todoist: use `/lyt-assistant:meeting-action-items`
- If the user only wants to compile already-collected sources: use `/lyt-assistant:compile`

## Unattended Mode

When invoked as `/end-of-day --unattended` (the scheduled Desktop task uses
this), the pipeline runs with no human present. Manual `/end-of-day` is
unchanged. Both modes share every step body; they differ ONLY at the points
listed here. If the `--unattended` token is absent, ignore this entire section.

### Global rules (unattended)

- **No interactive prompts of any kind. No continue/retry/halt prompts.** Every place
  the interactive flow would ask, take the documented auto-default and continue.
- **Best-effort.** A mid-run sub-skill failure is logged to the report and the
  run continues. The single exception is Step 0 pre-flight, which still HALTS.
- **External/published writes are draft/dry-run only** (see Step 2). The local
  daily note (Step 9) is the one surface auto-written, because it is local,
  reversible, and idempotently upserted.
- **End every run with a notification** (Observability, below).

### Concurrency lock (first action, unattended)

Before Step 0, acquire an exclusive lock so a catch-up run and a manual run
cannot double-write the shared `/tmp` caches and `_action-item-state.json`:

```bash
if ! mkdir /tmp/eod.lock 2>/dev/null; then
  echo "end-of-day already running (/tmp/eod.lock present); aborting."; exit 0
fi
```

Remove `/tmp/eod.lock` (`rmdir /tmp/eod.lock`) as the final action of the run,
including on any failure path that ends the run early.

### Run-date under catch-up

A Desktop task missed because the Mac was asleep fires a single catch-up within
7 days. The cached `date +%Y-%m-%d` therefore reflects the **actual run day**,
not the missed day. All source steps are "since last run", so nothing is lost;
the daily-note section and report are labeled with the run day. This is expected
behavior, not an error.

### Per-step deltas (unattended)

- **Step 0 (pre-flight):** unchanged; still HALTS on failure. On halt, send the
  failure notification and release the lock before exiting.
- **Step 2 (FES support -> Confluence):** the background subagent must run
  **draft/hold only** -- do NOT publish a live Confluence page. Instruct it: if
  the fes-support-learnings skill supports draft pages, publish as DRAFT;
  otherwise write the would-be page content to
  `raw/support_learnings/_pending_confluence/<date>.md` and report it as
  deferred. Live Confluence publish is for interactive runs.
- **Steps 3, 4 (support / internal -> raw/):** run non-interactively using the
  exact auto-default convention Step 2 already documents (classification ->
  `knowledge`, resolution -> `unresolved`, domain -> keyword-map else
  `general`, duplicate -> `skip`, any other -> the skip/no-action option).
  Record every auto-default for the report. (These write to local `raw/`, which
  is reviewable and compiled later, so auto-defaulting is acceptable.)
- **Step 5 (meeting action items):** run inline, no interactive prompts:
  1. `python3 <skill-dir>/meeting_action_items.py list` -> candidates with
     suggested title/due/priority.
  2. `td task list --project Work --all --json` -> existing open todos.
  3. Semantic pass: for each candidate that MEANS THE SAME as an existing open
     todo (reworded duplicate the string layer would miss), mark it `skip`
     (NON-terminal -- never `dismiss`). Only skip on high confidence; when
     unsure, let it through as a `todo` (a near-dupe is recoverable; a dropped
     commitment is not). Log each skip with the matched todo title.
  4. Build the decisions JSON: surviving candidates -> `todo` with the script's
     suggested due/priority; semantic dupes -> `skip`.
  5. `python3 <skill-dir>/meeting_action_items.py apply --input <file>`. The
     script independently dedups each `todo` against the live Work project and
     returns `duplicate` (non-terminal) for any it catches -- this is the
     deterministic backstop beneath the semantic pass.
  Report created vs `skip` (semantic dupe) vs `duplicate` (script backstop);
  never silently drop. (`<skill-dir>` is the meeting-action-items skill dir
  announced when that skill loads.)
- **Step 6 (project-log gate):** run the audit (detection) ONLY. Do NOT
  auto-write `log.md` entries. Record each gap in the report and add it to the
  Step 9 daily-note Follow-ups for the next interactive session.
- **Step 7 (compile):** run non-interactively. (If the compile sub-skill exposes
  any prompt, take its default.)
- **Step 9 (daily-note synthesis):** auto-approve and write the drafted section.
  The upsert and `grep -c '<!-- eod:begin'` post-write check are unchanged. If
  the check returns `0` or `>1`, do NOT proceed silently: record a failure
  marker and include it in the notification.
- **Step 10 (report):** after writing the `wiki/_log.md` entry, send the
  end-of-run notification (below), then release the lock.

### Observability (unattended)

- **End-of-run notification.** Send a `PushNotification` with a one-line status:
  `end-of-day <date>: ok` / `end-of-day <date>: degraded (<N> step failures)` /
  `end-of-day <date>: halted (pre-flight)`. Include the deduped/deferred counts
  and any step failures in the body.
- **Permission self-check.** If any tool call in the run was blocked or timed
  out waiting on a permission decision (the symptom of the Desktop
  `bypassPermissions` mode having silently reverted), treat the run as
  `degraded` and say so in the notification -- this is how a reverted permission
  mode gets surfaced instead of discovered later.

### Success checklist (what "ran cleanly" means)

A clean unattended run satisfies all of: pre-flight passed; Slack steps 2/3/4
reached and produced output or an honest nothing-to-do; Confluence stayed
draft/deferred; Step 5 reported created vs skip vs duplicate with no silent
drop; the daily-note section was written and the post-write check returned
exactly 1; the notification status is `ok`; and `wiki/_log.md` has the run entry
for the resolved date.

## Pipeline Overview

```
Step 1: Meetings (background)     ──┐  Pull Zoom transcripts into meetings/<date>/
                                    │  (meeting-ingest, background subagent)
                                    │
Step 1.5: Thread cache (fg)       ──┤  Fetch new #fes-platform-support thread
                                    │  bodies once to /tmp/eod-fes-support-cache.json
                                    │  (consumed by Steps 2 and 3)
                                    │
Step 2: FES Support (background)  ──┤  Extract #fes-platform-support threads to Confluence
                                    │  (fes-support-learnings:fes-support-learnings,
                                    │   background subagent, non-interactive)
                                    │
Step 3: Support                     │  Extract support channel threads into raw/support_learnings/
                                    │  (support-learnings, interactive)
                                    │
Step 4: Internal                    │  Extract #fes-platform-internal threads into raw/internal_learnings/
                                    │  (internal-channel-learnings, interactive)
                                    │
Step 4.5: Join ─────────────────────┘  Wait for Steps 1 and 2 background subagents
                                        to finish before running the remaining steps.

Step 5: Meeting Action Items          Interactive review of the day's meeting action
                                      items; creates Todoist tasks in the Work project
                                      (meeting-action-items — consumes Step 1 output)

Step 6: Project-Log Gate              Audit projects/*/log.md for today's entries against
                                      today's git/PR activity; invoke /project-log-entry
                                      interactively for each gap (consumes Step 1
                                      project mentions; runs before compile so any
                                      added log entries are picked up by Step 7)

Step 7: Compile                       Ingest raw/ into wiki/ + validate + discover links
                                      (compile workflow — consumes Steps 3 & 4 output)

Step 8: Verify-Status                 Run /verify-status read-only snapshot — feeds the
                                      synthesis step with live JIRA / PR / git state

Step 8.5: Work-board drift report     Run /work-board --dry-run --stale-days 7 — surface pending
                                      moves, manual overrides, orphans, stale + sectionless cards

Step 9: Daily-Note Synthesis          Draft today's EOD section into the daily note:
                                      accomplishments, decisions, follow-ups, tomorrow,
                                      blockers. On Fridays, add weekly retrospective +
                                      "pick up Monday" list. On Mondays, reconcile
                                      against last Friday's "pick up Monday" list and
                                      flag drift. (interactive: approve / edit / skip)

Step 10: Report                       Summary of full pipeline run, appended to wiki/_log.md
```

Why this shape:

- **Step 1 in parallel:** `meeting-ingest` writes to `meetings/` (not `raw/`), uses Zoom MCP (not Slack), and nothing downstream within Steps 2–4 reads its output. Fully independent, runs as a background subagent and overlaps with everything else.
- **Step 2 in parallel:** `fes-support-learnings` reads `#fes-platform-support` and publishes to Confluence — the *same channel* Step 3 (`support-learnings`) reads. Because the user will already be reviewing those threads interactively in Step 3, Step 2 can run unattended in the background — any classification it would otherwise prompt on will get a second look during Step 3's review. Confluence output is reviewable post-hoc.
- **Step 1.5 in the foreground:** the single thread fetch must complete before Step 2's subagent is dispatched so the cache exists when the subagent starts; the cost is a short serial block, the win is that each new thread's body is fetched once instead of once per consumer.
- **Steps 3 and 4 sequential:** these two retain interactive per-thread review (classify / resolve / dismiss). They share the user's attention, so they run one at a time.
- **Step 5 after the join:** `meeting-action-items` reads from `meetings/`, which Step 1 populates. Placing it after the join guarantees Step 1's background subagent has finished without pulling Step 1 foreground and killing the parallelism with Steps 2–4.
- **Step 6 (project-log gate) before compile:** auditing `projects/*/log.md` against today's PR/JIRA/git activity belongs before compile so any new log entries created at this gate get indexed by Step 7's compile. The user's attention is already on today's work after Step 5's action-item review, so this is the natural moment to flag missing log entries. Enforces the CLAUDE.md rule.
- **Step 7 (compile) after Steps 3, 4, 6:** `compile` reads from `raw/` (filled by Steps 3 + 4) and indirectly from `projects/*/log.md` (touched by Step 6). Running it after both means the wiki indexes a complete picture.
- **Step 8 (verify-status) after compile:** read-only snapshot of JIRA / PR / git state. Feeds Step 9 synthesis with live data. Placed after compile because synthesis is the consumer; running verify-status earlier wouldn't change its output and would interleave with the user-attention steps.
- **Step 9 (synthesis) after verify-status:** the daily-note EOD section draws from both the captured signal (Steps 1, 3, 4, 5) and the live state from Step 8. Interactive gate (approve / edit / skip) before writing to the daily note. Friday and Monday variants live inside this step.
- **Step 10 (report) last:** unchanged role — write `wiki/_log.md` entry summarizing the run.

## Prerequisites

Each sub-skill has its own requirements:

- **Zoom MCP** (`claude.ai Zoom for Claude`) — for Step 1
- **Slack MCP** (`claude.ai Slack`) — for Steps 2, 3, 4
- **Atlassian MCP** — for Step 2 (publishes to Confluence)
- **Todoist CLI (`td`) authenticated** — for Step 5 (creates Todoist tasks)

The three MCPs (Atlassian, Zoom, Slack) are verified up-front by Step 0 below. The pipeline halts loudly if any probe fails — the wiki compile downstream cannot recover from silently-missing Slack/Zoom/Atlassian data, so partial-data runs are refused by design. The Todoist CLI and `obsidian` CLI are not probed: `td` is used only in Step 5 and fails loudly without poisoning the wiki, and `obsidian` is used only by Step 9's daily-note write, which degrades to terminal output when Obsidian is not running.

## Process Flow

For each step: announce it, invoke the sub-skill, capture a one-line status (`ok` / `nothing-to-do` / `skipped` / `failed`) plus a short artifact summary for the final report. On failure, prompt the user — do not silently halt or barrel past.

### "Today" scope

Several steps reference "today" — for example "PR merged today", "JIRA ticket transitioned today", "meeting summary from today", "log entry dated today". Define "today" as the user's local-timezone calendar day, from local 00:00:00 to 23:59:59. Resolve via `date +%Y-%m-%d` (which honors `$TZ` / system TZ) at the start of the pipeline and cache the date string for the whole run — don't re-evaluate per step, otherwise a long-running pipeline could straddle midnight and produce inconsistent results across steps.

For `gh search` / `gh pr` queries, format date filters as `merged:YYYY-MM-DD` or `closed:>=YYYY-MM-DD` using that cached value. For JIRA, use `updated >= startOfDay()` (Jira's built-in function honors the user's account TZ). For git, use `--since=midnight` against the local repo.

Pin the resolved date string in the final report so the user can confirm which calendar day the run covered.

### Step 0: Pre-Flight MCP Auth Check (MANDATORY)

Before dispatching any subagents or invoking any sub-skills, probe each MCP server this pipeline depends on. If any probe fails, **HALT the pipeline immediately** — do not proceed with partial data, do not skip the failing dependency, do not offer a continue/skip path. Surface which probe(s) failed and tell the user to fix permissions (typically via `/plugin` to reauthorize), then re-run `/end-of-day`.

Run these three probes **in parallel** (single assistant message, three tool calls):

1. **Atlassian MCP** — `mcp__plugin_fbg-core_atlassian__atlassianUserInfo` (no args). Success = returns user info. Failure = auth error, tool unavailable, or permission denied.
2. **Zoom MCP** — `mcp__claude_ai_Zoom_for_Claude__recordings_list` with `page_size: 1` (and any required date arg set to today). Success = returns a recordings response (empty list is fine). Failure = auth error, tool unavailable, or permission denied.
3. **Slack MCP** — `mcp__claude_ai_Slack__slack_search_users` with a minimal query (e.g. `query: "ian"`, `count: 1`). Success = returns a user search response. Failure = auth error, tool unavailable, or permission denied.

If all three succeed, print one line — `Pre-flight: Atlassian / Zoom / Slack OK` — and proceed to Step 1.

If any fail, halt with this message shape (fill in the actual status per row):

```
End-of-Day pre-flight FAILED. The pipeline depends on three MCPs:

  Atlassian MCP — [OK | FAILED: <error>]
  Zoom MCP      — [OK | FAILED: <error>]
  Slack MCP     — [OK | FAILED: <error>]

Fix the failing MCP(s) (typically: run /plugin, reauthorize the affected
server, then retry) and re-run /end-of-day. The pipeline will not run
with partial data — meeting-ingest, fes-support-learnings, support-
learnings, and internal-channel-learnings all depend on these MCPs, and
silently degrading their output would corrupt the wiki compile in Step 6.
```

Do **not** offer "continue without this MCP" or "skip the failing step." The whole purpose of this gate is to refuse partial-data runs. The continue/retry/halt prompt in the per-step Error Handling section below applies only to *mid-run* failures of an MCP that passed pre-flight (e.g. transient API errors, rate limits) — not to pre-flight failures.

If the user fixes permissions and explicitly asks to "continue from Step 1" without re-running pre-flight, **still re-run Step 0 first**. Authorization can lapse mid-conversation, and the cost of one extra probe call is trivial compared to running the pipeline blind.

**Step 0 is not negotiable under time pressure.** If the user invokes `/end-of-day` with "go fast", "skip checks", "I just want to wrap up", or any time-pressure framing, pre-flight still runs in full. The user's actual goal under that framing is a clean wrap-up — running with a silently-broken MCP corrupts the wiki compile and creates more cleanup the next morning, not less. A 1-2 second parallel probe block is cheaper than the cleanup.

**Pre-flight failures are gates, not graceful-degrade sources.** The continue/retry/halt prompt in the per-step Error Handling section is for *mid-run* failures only. The pre-flight three (Atlassian, Zoom, Slack) are explicit gates — there is no error-sentinel render path for them, no "skip this step" affordance, no `lookup failed: <error>` fallback. If you find yourself reasoning "other parts of this pipeline degrade gracefully, so I can let this probe failure through" — stop. That reasoning is wrong. The boundary is intentional: `td` and `obsidian` degrade; Atlassian/Zoom/Slack gate.

### Rationalization counters

| Excuse | Reality |
|--------|---------|
| "User said go fast — that overrides MANDATORY." | No. User's goal is a clean wrap-up; partial-data EOD corrupts the wiki and creates more morning cleanup. Pre-flight runs in full. |
| "I'll skip the probe and let the sub-skill fail naturally." | The whole point of Step 0 is to fail BEFORE dispatching subagents and writing partial output. Letting the sub-skill fail wastes the parallel run and may leave half-written files behind. |
| "Only Step 2 needs Atlassian — if Atlassian is down I'll just skip Step 2 and continue." | No. Step 2 is parallel with the user-attention Steps 3 and 4 by design. Silently dropping it means the FES support channel is unprocessed today and the user won't notice until Confluence is missing the day's threads. Halt and fix. |
| "User verbally hinted Zoom auth is flaky — but the skill probes anyway, so I'll skip the probe to save a call." | The probe IS the signal. A user warning is additional evidence the probe will catch something, not a reason to skip it. |

### Step 1: Meeting Ingest (background subagent)

Dispatch meeting-ingest as a background subagent so it runs in parallel with Steps 2–4. Use the `Agent` tool with `subagent_type: general-purpose` (no specialized agent is needed — the work is "run this skill end-to-end and report a summary").

Agent prompt template:

> Run the `lyt-assistant:meeting-ingest` skill end-to-end with no arguments. It pulls the past 5 days of Zoom transcripts into `~/Documents/Work/meetings/<date-slug>/`, skipping meetings that already have folders. The Zoom MCP (`mcp__claude_ai_Zoom_for_Claude__*`) must be authenticated — if it isn't, stop and report that, do not try to authenticate. When the skill finishes, report a single block: number of meetings ingested, list of new folder paths, and any meetings skipped because they already existed. Under 150 words.

Set `run_in_background: true` so the orchestrator does not block on this. Note the agent ID so it can be joined later (at Step 4.5).

Do NOT poll or sleep waiting on it — the runtime notifies on completion. Proceed straight to Step 1.5.

Track (recorded at the join in Step 4.5): meetings ingested, meetings skipped.

Record status as one of:

- `ok` — N new meetings ingested
- `nothing-to-do` — all meetings already ingested
- `failed` — see failure-handling below

### Step 1.5: Fetch fes-support threads to the shared cache (foreground)

Both Step 2 (Confluence) and Step 3 (raw/) read the same `#fes-platform-support`
threads. Fetch the thread bodies once here so each consumer classifies from the
cache instead of re-reading Slack.

1. Start clean: `rm -rf /tmp/eod-cache-threads && mkdir -p /tmp/eod-cache-threads`
   and `rm -f /tmp/eod-fes-support-cache.json`.
2. Compute the widest window either consumer needs: read
   `raw/support_learnings/_metadata.yml` and find the most recent
   `date_processed`. Compute two Unix timestamps: (a) local midnight at the
   start of that date via `date -j -f "%Y-%m-%d %H:%M:%S" "<date> 00:00:00" "+%s"`,
   and (b) now minus 7 days via `date -v-7d +%s`. Use the smaller (older) of
   the two as `oldest`.
3. Run `mcp__claude_ai_Slack__slack_read_channel` (channel `C06PUG6V6NT`,
   that `oldest`).
4. For each threaded message whose `ts` is NOT already in
   `raw/support_learnings/_metadata.yml`: read it with
   `mcp__claude_ai_Slack__slack_read_thread`, then IMMEDIATELY write that one
   thread to `/tmp/eod-cache-threads/<ts>.json` via the Write tool as
   `{"parent": {"author", "ts", "text", "reactions"?}, "replies": [{"author",
   "ts", "text"}, ...]}` (verbatim text; reactions optional). One file per
   thread; never hand-assemble the combined JSON.
5. Assemble and validate:

   ```bash
   python3 ~/.claude/skills/end-of-day/build_cache.py /tmp/eod-cache-threads \
     -o /tmp/eod-fes-support-cache.json \
     --channel C06PUG6V6NT --window-oldest <oldest>
   ```

   On a non-zero exit, delete `/tmp/eod-fes-support-cache.json` if present and
   continue WITHOUT a cache: both consumers fall back to live fetches. Never
   halt the pipeline over the cache.

Track: threads cached, cache path, or `no-cache (reason)`.

### Step 2: FES Support Learnings → Confluence (background subagent)

Dispatch fes-support-learnings as a second background subagent so it runs in parallel with meeting-ingest and Steps 3–4. It lives in the `fes-support-learnings` plugin (separate from `lyt-assistant`) — use the fully-qualified skill name.

Agent prompt template:

> Run the `fes-support-learnings:fes-support-learnings` skill end-to-end with no arguments. It extracts threads from `#fes-platform-support` (default 7-day lookback) and publishes domain-grouped pages to Confluence. The Slack MCP and Atlassian MCP must both be authenticated — if either is missing, stop and report that, do not try to authenticate.
>
> A thread cache may exist at `/tmp/eod-fes-support-cache.json`. Before reading
> any thread from Slack, run `python3 <skill-dir>/fes_support_cache.py check`
> (the helper ships with the fes-support-learnings skill) (its base directory
> is announced when that skill loads; substitute it for `<skill-dir>`); if trusted, take
> thread bodies from the cache (`get <ts>`) and only call slack_read_thread for
> threads the cache is missing. If untrusted, fetch live exactly as before.
>
> **Run non-interactively** using these explicit auto-defaults so behavior is consistent run-to-run and the user knows what to spot-check at the join:
>
> - Per-thread classification prompt → default to `knowledge` (the safest catch-all category; classifying as `incident` or `decision` requires evidence the auto-pass shouldn't infer).
> - Per-thread resolution prompt → default to `unresolved` (let the foreground `support-learnings` step in Step 3 capture the resolution if there is one; do not guess from the thread body).
> - Domain assignment prompt → default to the domain inferred from the thread's first message via the skill's own keyword map; if no map match, default to `general`.
> - Duplicate-thread detection prompt → default to `skip` (do not overwrite or merge; surface as an auto-default).
> - Any other prompt the skill exposes → default to the option labeled "skip" or "no action".
>
> Record every auto-default applied (thread ID + prompt name + chosen default) so the user can spot-check at the Step 4.5 join. Do not block waiting for user input under any circumstance.
>
> The user is independently reviewing the same channel's threads in the interactive `support-learnings` step running in the foreground, so anything ambiguous will get a second look there. When the skill finishes, report a single block: new threads processed, unresolved threads re-evaluated, Confluence page URL(s) created or updated, and the full list of auto-defaulted prompts. Under 250 words.

Set `run_in_background: true`. Note the agent ID for the Step 4.5 join.

Do NOT poll or sleep. Proceed straight to Step 3.

Track (recorded at the join): new threads processed, unresolved threads re-evaluated, Confluence page URL(s), list of auto-defaulted prompts.

### Step 3: Support Learnings → `raw/`

Invoke the (lyt-assistant) support-learnings skill, which writes to `~/Documents/Work/raw/support_learnings/`:

```
Skill: lyt-assistant:support-learnings
Args: (none — process new threads since last run)
```

A thread cache may exist at `/tmp/eod-fes-support-cache.json` (written by
Step 1.5). The support-learnings skill consults it automatically; nothing to
pass.

Track: number of new threads processed, output file path(s). These files become inputs for Step 6 (Compile).

### Step 4: Internal Channel Learnings → `raw/`

Invoke the internal-channel-learnings skill, which writes to `~/Documents/Work/raw/internal_learnings/`:

```
Skill: lyt-assistant:internal-channel-learnings
Args: (none — process new threads since last run)
```

Track: number of new threads processed and their categories (decision / incident / knowledge / process-change / discussion), output file path(s). These files also feed Step 6 (Compile).

### Step 4.5: Join — Wait for Background Subagents

Before starting compile, ensure both Step 1 (meeting-ingest) and Step 2 (fes-support-learnings) background subagents have completed.

- For each subagent: if the runtime has already delivered its completion notification, read its result block and record status + summary. If still running, wait for the completion notification — do NOT poll, sleep, or proactively check; the runtime notifies on completion.
- The two foreground interactive steps (3 and 4) almost always take longer than the background subagents, so this wait is usually instant.
- **Missing-notification fallback:** if a subagent is still running 5 minutes after Steps 3 + 4 have both completed (or 10 minutes from initial dispatch, whichever is later) and no completion notification has arrived (e.g. the runtime dropped the event, or the subagent crashed silently without reporting), surface this to the user, treat the subagent as failed, and apply the continue / retry / halt prompt below. Do not hang Step 4.5 indefinitely. These thresholds are deliberately generous — `meeting-ingest` and `fes-support-learnings` typically finish well under 5 minutes, so a 5-minute post-Steps-3+4 budget catches genuine hangs without false positives.
- If either subagent reported a failure (e.g. Zoom MCP unauthed, Atlassian MCP unauthed), apply the same continue / retry / halt prompt described in Error Handling below. Treat each subagent's failure independently — one can succeed while the other fails.
- For fes-support-learnings specifically: surface its list of auto-defaulted prompts to the user as part of the join. They are not errors, but the user may want to spot-check them.

### Step 5: Meeting Action Items

Invoke the meeting-action-items skill with no arguments (its default lookback is "since last run, fallback 2 days"). Step 1 has finished by this point, so any meetings ingested in Step 1 are now visible to this step.

```
Skill: lyt-assistant:meeting-action-items
Args: (none)
```

**Must run interactively in the foreground session.** Invoke via the `Skill` tool in the main conversation — do NOT dispatch via `Agent` (background or foreground) and do NOT instruct it to "run non-interactively" like Step 2. The skill drives a per-item prompt loop that requires the user at the keyboard: `[t]` make todo / `[d]` dismiss / `[s]` skip / `[q]` quit, plus a bulk-triage shortcut. A subagent has no way to surface those prompts to the user, so dispatching it that way would either hang, auto-default every item, or silently dismiss work that should have become a todo.

In `--unattended` mode this step does NOT run interactively -- see Unattended
Mode > Per-step deltas > Step 5 for the inline auto-todo + dedup flow.

Track for the run: number of items reviewed, number of new todos created, number dismissed, number skipped.

Record status:

- `ok` — items reviewed and any todos created
- `nothing-new` — no unhandled action items in the lookback window
- `quit-early` — user quit mid-loop; still proceed to Step 6 with partial progress
- `failed` — see Error Handling

### Step 6: Project-Log Gate

Audit `~/Documents/Work/projects/*/log.md` to find projects that were touched today but lack today's log entry. Enforces the CLAUDE.md rule that any project work must produce a `log.md` entry.

**Detection signals (any one is sufficient evidence of "touched today"):**

- A PR I authored merged today, referencing a JIRA key that appears in a project's README or recent log entries
- A new branch matching `<TICKET-KEY>-*` created today (via `gh search` from earlier steps' data)
- A JIRA ticket transitioned today, assigned to me, referenced in a project's README or recent log entries
- A meeting summary from today (from Step 1) names a project explicitly, **or** references a JIRA key / repo / branch / topic that maps unambiguously to one project's scope (cross-check meeting Decisions and Action Items sections against each project README's "Goals" / "Scope" / "Focus" headings — pure-discussion project work without commits still counts as "touched")
- A `~/Dev/<repo>/` clone has commits authored by me today, where the repo is named in a project's README

A project that scores on any one signal but has no `log.md` entry dated today is flagged. If the scope-match heuristic produces an ambiguous result (a meeting topic plausibly maps to two projects), flag both — `project-log-entry` runs interactively per project, so the user decides which deserves the entry.

For each project flagged as "touched today but no `log.md` entry dated today":

```
Skill: project-log-entry
Args: project=<name>, audit-mode=true, source-summary=<detected-signals>
```

`project-log-entry` runs interactively per project — the user can approve a proposed entry, edit, or skip. Surface the detected signals so the user has context for what would go in the entry.

**Must run interactively in the foreground session.** Invoke via the `Skill` tool; do NOT dispatch via `Agent`. The user approves each entry.

Track for the run: number of projects audited, number with no today-entry, number where a new entry was created, number skipped.

Record status:

- `ok` — audit ran; report counts above
- `nothing-to-do` — no project work detected today
- `quit-early` — user skipped through some / all gaps; partial progress
- `failed` — see Error Handling

**Why this step runs before Compile (Step 7):** any new log entries written here become inputs for the compile step's link-discovery pass. Running this after compile would mean today's new log content doesn't get indexed until tomorrow's run.

### Step 7: Compile

Invoke the compile skill, which itself chains ingest → validate → discover-links:

```
Skill: lyt-assistant:compile
Args: (none)
```

Track: articles created, articles updated, stubs created, validation fixes applied, new connections added. The compile skill handles its own logging to `wiki/_log.md`.

### Step 8: Verify-Status (read-only snapshot)

Invoke the `verify-status` skill to produce a read-only snapshot of live JIRA / PR / git state. The snapshot feeds Step 9's synthesis.

```
Skill: verify-status
Args: (none)
```

`verify-status` is itself a multi-step pipeline (identity check, git fetch on active-work repos, JIRA In Progress/Blocked, GitHub open PRs, reconcile, NEXT: recommendation). Capture its full output verbatim — Step 9 reads it as input.

Track: did verify-status run cleanly? Did it emit a `NEXT:` line? Capture any drift findings it produced — they get surfaced in Step 9's synthesis.

Record status:

- `ok` — snapshot captured
- `failed` — verify-status itself errored; continue to Step 9 with reduced input (synthesis falls back to captured signal only)

### Step 8.5: Work-board drift report (dry-run)

Run the `work-board` skill with `--dry-run --stale-days 7`. Do not execute moves. Surface in the EOD
summary: pending moves the morning sync will make, manual overrides (cards Ian parked
deliberately), orphans needing a decision, stale manual cards (`stale` actions: cards in
Next Up / In Progress with no Todoist activity in 7 days - ask whether each is still real),
and any sectionless cards in the Work project
(`td task list --project "Work" --json` entries with null sectionId) as filing candidates.

### Step 9: Daily-Note Synthesis

Draft today's end-of-day section into the daily note. Interactive: present the draft for approve / edit / skip before writing.

**Inputs:**

- Captured signal from Steps 1, 3, 4, 5 (meetings ingested, threads extracted, action items reviewed)
- Live state from Step 8 verify-status (today's PR merges, JIRA transitions, drift findings, the NEXT: recommendation)
- Project-log audit results from Step 6 (which projects got new entries today)
- **Stale-PR scan:** any open PR I authored where `updatedAt` is older than 5 days ago (computed from verify-status output — if absent, run `gh search prs --author=ian-at-fes --state=open --json number,title,url,updatedAt` inline). These feed the Follow-ups section as nudge candidates.
- **Todoist overdue audit:** run `td list --filter "(overdue | today) & #work" --json` to surface today/overdue items in the work project. Items still open at EOD feed the Follow-ups section so they're explicit in tomorrow's planning rather than implicit-in-Todoist.
- **Tomorrow's calendar:** query the Google Calendar MCP (`mcp__claude_ai_Google_Calendar__*`) for events scheduled tomorrow between 00:00 and 23:59 local time. The first meeting's start time and topic feed the Tomorrow section so the priorities respect the day's shape. If the MCP is unavailable or unauthed, skip this input — don't block the synthesis.

**Daily-note path:** Resolve via the Obsidian CLI, identically to /start-of-day and /daily-standup:

    obsidian daily                              # ensure today's note exists
    DAILY_NOTE_PATH="$(obsidian daily:path)"    # canonical absolute path

Never construct the path manually. Do NOT write to `raw/daily/<date>.md`: that directory remains in use by memory-flush hooks (session logs) and by lyt-assistant's compile (`/compile daily` source), but the EOD section no longer lives there. Pre-2026-06-09 EOD sections remain in `raw/daily/` and are not migrated.

**Obsidian-not-running fallback:** if `obsidian daily` or `obsidian daily:path` exits non-zero, emit the fully rendered EOD section to the terminal as a one-time fallback, skip the file write, and record Step 9 status as `ok (terminal fallback)`. Do not fall back to the old `raw/daily/` path. Re-running Step 9 after opening Obsidian upserts cleanly.

**Default section template (append to existing daily note):**

```markdown
## End of Day — <YYYY-MM-DD>

<!-- eod:begin generated=<ISO-8601 UTC timestamp> -->

### Accomplishments
- <PRs merged today (from verify-status)>
- <JIRA tickets transitioned today (from verify-status)>
- <Meetings attended (from Step 1)>
- <Project log entries written today (from Step 6)>

### Decisions
<Pulled from today's meeting summaries via meeting-section-extract or inline summarization>

### Follow-ups
- <Open action items created in Step 5>
- <verify-status drift findings>
- <Stale PRs (open, mine, updatedAt > 5 days ago) — nudge or close>
- <Overdue or due-today Todoist #work items still open — snooze, complete, or carry forward>

### Tomorrow
<Top 2-3 priorities, drawn from verify-status NEXT: + In-Progress JIRA tickets + tomorrow's first-meeting topic if calendar available. Anchor priorities around the calendar shape — e.g. if first meeting is at 09:00, surface what needs to be done before vs after it.>

### Blockers
<Any JIRA tickets in Blocked state from verify-status, with the most recent blocker comment>

<!-- eod:end -->
```

**Friday variant (when `date +%A` is `Friday`):** After the standard sections, append a weekly retrospective block:

```markdown
### Weekly Retrospective — Week of <Monday's date>
**Highlights:** <pulled from Mon-Fri daily-note Accomplishments sections (canonical notes at `raw/daily_notes/YYYY/MM/<date>-<Weekday>.md`; pre-2026-06-09 EOD sections live in `raw/daily/<date>.md`) + this week's PR merges>
**Lowlights:** <pulled from this week's Blockers + carried-over Follow-ups>
**Pick up Monday:** <2-4 items, explicit and concrete — these become Monday's reconcile input>
```

**Highlights dedupe rule:** PRs typically appear in both the daily-note Accomplishments (per-day) and the `gh search prs --author=ian-at-fes --merged:<monday>..<friday>` rollup. Dedupe by PR number (the `#<N>` token). When the same PR is mentioned in both sources, prefer the daily-note phrasing — it captures the user's framing for that day, while the `gh` rollup is just title + URL. Apply the same dedupe to JIRA keys (`[A-Z]+-\d+`) appearing in both daily-note entries and the week's JIRA transition history. Lowlights and Pick-up-Monday rarely have overlap, but if a Blocker carries across days, list it once with the earliest date the blocker was first noted.

**Monday variant (when `date +%A` is `Monday`):** Before drafting the standard sections, read last Friday's daily note (canonical: `raw/daily_notes/YYYY/MM/<last-friday>-Friday.md`; if it has no EOD section and the date is before 2026-06-09, fall back to `raw/daily/<last-friday>.md`) and locate the `### Pick up Monday` block. For each item:

- Mark `[x] picked up` if a JIRA ticket / PR / branch / commit from today touches it
- Mark `[ ] not picked up` if today shows no activity on it

Prepend a `### Friday → Today` block to the EOD section reporting the reconciled list. Then proceed with the standard sections.

**Interactive gate:** Show the full drafted section to the user before writing. Options: `[a]` approve / `[e]` edit (open in `$EDITOR`) / `[s]` skip (don't append to daily note).

**Upsert (idempotent):** Write the approved section via this exact pattern (mirrors /daily-standup). The rendered section, including the `## End of Day` heading and both markers, must first be saved to `/tmp/eod-section.md`.

```bash
python3 - "$DAILY_NOTE_PATH" /tmp/eod-section.md <<'PY'
import sys, re, pathlib
note = pathlib.Path(sys.argv[1])
new_block = pathlib.Path(sys.argv[2]).read_text().rstrip("\n") + "\n"
text = note.read_text() if note.exists() else ""
pattern = re.compile(r"## End of Day[^\n]*\n.*?<!-- eod:end -->\n?", re.DOTALL)
matches = pattern.findall(text)
if len(matches) > 1:
    sys.exit(f"REFUSING: {len(matches)} End of Day blocks already in {note} - inspect manually")
if matches:
    text = pattern.sub(new_block, text, count=1)
else:
    text = text.rstrip("\n")
    if text:
        text += "\n\n"
    text += new_block
note.write_text(text)
print(note)
PY
```

The splice runs from the `## End of Day` heading line through and including `<!-- eod:end -->` (the heading precedes the begin marker, so both markers are removed and re-inserted together).

**Post-write verification:** `grep -c '<!-- eod:begin' "$DAILY_NOTE_PATH"` must return exactly `1`. On `0` or `>1`, print a diagnostic with the path and halt Step 9 (do not proceed to Step 10 silently).

Track: daily-note path written, sections included (standard / + weekly retro / + Friday→Today reconcile), and the user's choice.

Record status:

- `ok` — section appended to daily note
- `skipped` — user skipped the synthesis
- `failed` — see Error Handling

### Step 10: Final Report

Summarize the full end-of-day run in one block:

```
End-of-Day Complete

  Step 1 — Meetings (parallel):
    New meetings: 3
    Skipped (already present): 2

  Step 2 — FES Support → Confluence (parallel):
    New threads: 4
    Unresolved revisited: 2
    Confluence page: https://.../2026-05-12-learnings
    Auto-defaulted prompts: 1 (thread T1234 classified as "knowledge" by default)

  Step 3 — Support → raw/:
    New threads: 4
    Files written: 1 (raw/support_learnings/2026-05-12.md)

  Step 4 — Internal → raw/:
    New threads: 6 (2 decision, 1 incident, 3 knowledge)
    Files written: 1 (raw/internal_learnings/2026-05-12.md)

  Step 5 — Meeting Action Items:
    Items reviewed: 5
    Todos created: 3
    Dismissed: 1
    Skipped: 1

  Step 6 — Project-Log Gate:
    Projects audited: 4
    Missing today-entry: 2
    New entries created: 2
    Skipped: 0

  Step 7 — Compile:
    Articles created: 4
    Articles updated: 2
    Stubs created: 1
    Validation fixes: 1
    New connections: 7

  Step 8 — Verify-Status:
    NEXT: merge PR #1842 (approved, clean, mine)
    Drift findings: 1 (load-testing-environment/log.md stale-pr)

  Step 9 — Daily-Note Synthesis:
    Daily note: <output of obsidian daily:path>
    Sections written: standard + Friday weekly retro
    User action: approved

  Activity logged to: wiki/_log.md
```

Then append an end-of-day block to `wiki/_log.md`:

```markdown
## [2026-05-12] end-of-day | Daily Pipeline

- Meetings ingested: 3
- FES support threads → Confluence: 4 new, 2 revisited
- Support threads → raw/: 4
- Internal threads → raw/: 6 (2 decision, 1 incident, 3 knowledge)
- Meeting action items: 5 reviewed, 3 created, 1 dismissed, 1 skipped
- Project-log gate: 4 audited, 2 missing entries, 2 created
- Compile: 4 created, 2 updated, 7 new connections
- Verify-status: NEXT: merge PR #1842; 1 drift finding
- Daily-note synthesis: standard + Friday weekly retro (approved)
- Step failures: none
```

If any steps failed or were skipped, list them in a `- Step failures:` line.

## Error Handling

### A Sub-Skill Reports Its MCP Isn't Authed

The sub-skill will tell the user to authenticate. Don't try to authenticate on its behalf. Surface the message and prompt:

```
Step 2 (FES Support) couldn't run: Atlassian MCP not authenticated.

The sub-skill suggests running `/plugin` and authenticating Atlassian, then retrying.

  [c] Continue — skip this step and move to Step 3
  [r] Retry — user authenticates now, then retry Step 2
  [h] Halt — stop the pipeline here

Choice?
```

Default behavior is to ask, not assume. One auth glitch should not abort the whole run.

### A Sub-Skill Errors Mid-Run

If a sub-skill starts but errors part-way through (e.g. Slack API rate limit, a thread the skill can't classify, a write failure), surface the error and prompt the same continue / retry / halt choice. Capture whatever partial output the sub-skill produced in the per-step status.

### A Sub-Skill Has Nothing to Do

This is not an error. Mark the step `nothing-to-do` and continue without prompting.

### `meeting-action-items` Quits Early

If the user hits `[q]` partway through Step 5's per-item loop, treat that as `ok` (partial-progress), report how far the run got, and continue to Step 6 — compile and the final report are still useful.

### Pipeline Interruption

If the user cancels mid-pipeline, also stop any background subagents (Steps 1 and 2) that are still running — use `TaskStop` on each agent ID. Then report what was completed and what remains:

```
End-of-Day interrupted after Step 3 (Support Learnings).

Completed:
  - Step 1: Meetings — 3 new (background subagent finished before interrupt)
  - Step 2: FES Support — 4 new threads → Confluence (background subagent finished before interrupt)
  - Step 3: Support → raw/ — 4 new threads

Not yet run:
  - Step 4: Internal Channel — run /lyt-assistant:internal-channel-learnings
  - Step 5: Meeting Action Items — run /lyt-assistant:meeting-action-items
  - Step 6: Project-Log Gate — run /project-log-entry per affected project
  - Step 7: Compile — run /lyt-assistant:compile (will pick up Step 3 output plus Step 4 if you run it)
  - Step 8: Verify-Status — run /verify-status
  - Step 9: Daily-Note Synthesis — manually draft today's EOD section in the canonical daily note (`obsidian daily:path`)
```

If a background subagent was still running when the interrupt happened, mark it `cancelled` instead of `ok` and note what its last reported progress was, if available.

## Related Skills

- **/lyt-assistant:meeting-ingest** — Pull Zoom transcripts (Step 1)
- **/fes-support-learnings:fes-support-learnings** — FES support → Confluence (Step 2; lives in the `fes-support-learnings` plugin)
- **/lyt-assistant:support-learnings** — Support channel → `raw/support_learnings/` (Step 3)
- **/lyt-assistant:internal-channel-learnings** — Internal channel → `raw/internal_learnings/` (Step 4)
- **/lyt-assistant:meeting-action-items** — Interactive review of meeting action items into Todoist (Step 5)
- **/lyt-assistant:compile** — Full compilation pipeline (Step 6; itself chains `/lyt-assistant:ingest` → `/lyt-assistant:lint` → `/lyt-assistant:discover-links`)
- **/start-of-day** — Morning counterpart; lists today + overdue Todoist tasks with an inline edit loop (user-private skill at `~/.claude/skills/start-of-day/`)

## Summary

The end-of-day skill runs the full daily wrap-up: pull the day's Zoom meetings, extract three Slack channels' worth of learnings (FES support to Confluence, support and internal channels to `raw/`), review the day's meeting action items into Todoist, then compile `raw/` into the wiki. It chains six skills in order so the day's commitments and Slack-derived notes get captured and into the wiki the same day rather than waiting until the next compile. Use `/end-of-day` as the single command to wrap up the workday.
