---
name: end-of-day
description: This skill should be used when the user asks to "end of day", "EOD", "run end of day", "wrap up the day", "end of day routine", or runs "/end-of-day".
version: 0.1.0
allowed-tools: [Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion, Skill, Agent, TaskStop]
---

# End-of-Day Skill

Run the full end-of-day capture pipeline: pull the day's meetings, extract Slack learnings (FES support to Confluence, support + internal channels to `raw/`), review the day's meeting action items into Todoist, then compile everything into the wiki.

## Purpose

Provide a single command that runs the complete daily wrap-up workflow with interactive gates at every step. Chains together meeting ingestion, three Slack-learning extractions, an interactive review of the day's meeting action items, and the full compile pipeline so the user doesn't have to remember to run each one manually at end of day.

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

## Pipeline Overview

```
Step 1: Meetings (background)     ──┐  Pull Zoom transcripts into meetings/<date>/
                                    │  (meeting-ingest, background subagent)
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

Step 6: Compile                       Ingest raw/ into wiki/ + validate + discover links
                                      (compile workflow — consumes Steps 3 & 4 output)

Step 7: Report                        Summary of full pipeline run, appended to wiki/_log.md
```

Why this shape:

- **Step 1 in parallel:** `meeting-ingest` writes to `meetings/` (not `raw/`), uses Zoom MCP (not Slack), and nothing downstream within Steps 2–4 reads its output. Fully independent, runs as a background subagent and overlaps with everything else.
- **Step 2 in parallel:** `fes-support-learnings` reads `#fes-platform-support` and publishes to Confluence — the *same channel* Step 3 (`support-learnings`) reads. Because the user will already be reviewing those threads interactively in Step 3, Step 2 can run unattended in the background — any classification it would otherwise prompt on will get a second look during Step 3's review. Confluence output is reviewable post-hoc.
- **Steps 3 and 4 sequential:** these two retain interactive per-thread review (classify / resolve / dismiss). They share the user's attention, so they run one at a time.
- **Step 5 after the join:** `meeting-action-items` reads from `meetings/`, which Step 1 populates. Placing it after the join guarantees Step 1's background subagent has finished without pulling Step 1 foreground and killing the parallelism with Steps 2–4.
- **Step 6 after Step 5:** `compile` reads from `raw/`, so it must run after Steps 3 and 4. It does not strictly depend on Steps 1, 2, or 5, but running it after the interactive steps keeps all user-attention work upstream of the final report.

## Prerequisites

Each sub-skill has its own requirements:

- **Zoom MCP** (`claude.ai Zoom for Claude`) — for Step 1
- **Slack MCP** (`claude.ai Slack`) — for Steps 2, 3, 4
- **Atlassian MCP** — for Step 2 (publishes to Confluence)
- **Todoist CLI (`td`) authenticated** — for Step 5 (creates Todoist tasks)

Don't pre-flight-check all of these. Let each step fail naturally if its MCP or CLI is unauthed — the sub-skill itself reports the auth gap clearly. The failure-handling flow below covers that case.

## Process Flow

For each step: announce it, invoke the sub-skill, capture a one-line status (`ok` / `nothing-to-do` / `skipped` / `failed`) plus a short artifact summary for the final report. On failure, prompt the user — do not silently halt or barrel past.

### Step 1: Meeting Ingest (background subagent)

Dispatch meeting-ingest as a background subagent so it runs in parallel with Steps 2–4. Use the `Agent` tool with `subagent_type: general-purpose` (no specialized agent is needed — the work is "run this skill end-to-end and report a summary").

Agent prompt template:

> Run the `lyt-assistant:meeting-ingest` skill end-to-end with no arguments. It pulls the past 5 days of Zoom transcripts into `~/Documents/Work/meetings/<date-slug>/`, skipping meetings that already have folders. The Zoom MCP (`mcp__claude_ai_Zoom_for_Claude__*`) must be authenticated — if it isn't, stop and report that, do not try to authenticate. When the skill finishes, report a single block: number of meetings ingested, list of new folder paths, and any meetings skipped because they already existed. Under 150 words.

Set `run_in_background: true` so the orchestrator does not block on this. Note the agent ID so it can be joined later (at Step 4.5).

Do NOT poll or sleep waiting on it — the runtime notifies on completion. Proceed straight to Step 2.

Track (recorded at the join in Step 4.5): meetings ingested, meetings skipped.

Record status as one of:

- `ok` — N new meetings ingested
- `nothing-to-do` — all meetings already ingested
- `failed` — see failure-handling below

### Step 2: FES Support Learnings → Confluence (background subagent)

Dispatch fes-support-learnings as a second background subagent so it runs in parallel with meeting-ingest and Steps 3–4. It lives in the `fes-support-learnings` plugin (separate from `lyt-assistant`) — use the fully-qualified skill name.

Agent prompt template:

> Run the `fes-support-learnings:fes-support-learnings` skill end-to-end with no arguments. It extracts threads from `#fes-platform-support` (default 7-day lookback) and publishes domain-grouped pages to Confluence. The Slack MCP and Atlassian MCP must both be authenticated — if either is missing, stop and report that, do not try to authenticate. **Run non-interactively**: for any per-thread classification or resolution prompt, pick the most reasonable default and continue; do not block waiting for user input. The user is independently reviewing the same channel's threads in the interactive `support-learnings` step running in the foreground, so anything ambiguous will get a second look there. When the skill finishes, report a single block: new threads processed, unresolved threads re-evaluated, Confluence page URL(s) created or updated, and any prompts you auto-defaulted (so the user can spot-check them). Under 200 words.

Set `run_in_background: true`. Note the agent ID for the Step 4.5 join.

Do NOT poll or sleep. Proceed straight to Step 3.

Track (recorded at the join): new threads processed, unresolved threads re-evaluated, Confluence page URL(s), list of auto-defaulted prompts.

### Step 3: Support Learnings → `raw/`

Invoke the (lyt-assistant) support-learnings skill, which writes to `~/Documents/Work/raw/support_learnings/`:

```
Skill: lyt-assistant:support-learnings
Args: (none — process new threads since last run)
```

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
- **Missing-notification fallback:** if a subagent has been running noticeably longer than expected and no completion notification has arrived (e.g. the runtime dropped the event, or the subagent crashed silently without reporting), surface this to the user, treat the subagent as failed, and apply the continue / retry / halt prompt below. Do not hang Step 4.5 indefinitely.
- If either subagent reported a failure (e.g. Zoom MCP unauthed, Atlassian MCP unauthed), apply the same continue / retry / halt prompt described in Error Handling below. Treat each subagent's failure independently — one can succeed while the other fails.
- For fes-support-learnings specifically: surface its list of auto-defaulted prompts to the user as part of the join. They are not errors, but the user may want to spot-check them.

### Step 5: Meeting Action Items

Invoke the meeting-action-items skill with no arguments (its default lookback is "since last run, fallback 2 days"). Step 1 has finished by this point, so any meetings ingested in Step 1 are now visible to this step.

```
Skill: lyt-assistant:meeting-action-items
Args: (none)
```

**Must run interactively in the foreground session.** Invoke via the `Skill` tool in the main conversation — do NOT dispatch via `Agent` (background or foreground) and do NOT instruct it to "run non-interactively" like Step 2. The skill drives a per-item prompt loop that requires the user at the keyboard: `[t]` make todo / `[d]` dismiss / `[s]` skip / `[q]` quit, plus a bulk-triage shortcut. A subagent has no way to surface those prompts to the user, so dispatching it that way would either hang, auto-default every item, or silently dismiss work that should have become a todo.

Track for the run: number of items reviewed, number of new todos created, number dismissed, number skipped.

Record status:

- `ok` — items reviewed and any todos created
- `nothing-new` — no unhandled action items in the lookback window
- `quit-early` — user quit mid-loop; still proceed to Step 6 with partial progress
- `failed` — see Error Handling

### Step 6: Compile

Invoke the compile skill, which itself chains ingest → validate → discover-links:

```
Skill: lyt-assistant:compile
Args: (none)
```

Track: articles created, articles updated, stubs created, validation fixes applied, new connections added. The compile skill handles its own logging to `wiki/_log.md`.

### Step 7: Final Report

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

  Step 6 — Compile:
    Articles created: 4
    Articles updated: 2
    Stubs created: 1
    Validation fixes: 1
    New connections: 7

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
- Compile: 4 created, 2 updated, 7 new connections
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
  - Step 6: Compile — run /lyt-assistant:compile (will pick up Step 3 output plus Step 4 if you run it)
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
