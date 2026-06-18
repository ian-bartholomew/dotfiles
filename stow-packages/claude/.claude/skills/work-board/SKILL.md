---
name: work-board
description: Sync the Todoist Work project's kanban board with assigned JIRA tickets and open PRs. Triggers on "sync my board", "work board", "update my kanban", "/work-board", or as a step inside /start-of-day (live), /end-of-day (dry-run), and /finish-work (single-card). Deterministic script does all moves; the LLM fetches JIRA via MCP, infers priority for new cards (asking when unsure), and resolves orphans.
---

# Work Board Sync

Keeps the Todoist **Work** project board (Backlog | Next Up | In Progress | In Review |
Waiting on Others | Blocked) in sync with live JIRA/PR state.

Card contract: JIRA-linked cards are titled `<KEY> <summary>`; the description ends with a
machine-owned line `sync: jira-status=<status> | synced=<date>`. Cards without a key prefix
are never touched. A card whose column disagrees with JIRA while its recorded status is
unchanged was moved by Ian on purpose - the script reports it as `manual-override` and leaves it.

## Steps

### 1. Fetch assigned tickets (MCP)

Call `mcp__plugin_fbg-core_atlassian__searchJiraIssuesUsingJql` with
`assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC`
(fields: summary, status, priority, duedate). Write `/tmp/work-board-tickets.json`:

```json
[{"key": "FANDEVX-2471", "summary": "...", "status": "In Progress",
  "statusCategory": "indeterminate",
  "url": "https://betfanatics.atlassian.net/browse/FANDEVX-2471"}]
```

`status` = live status NAME; `statusCategory` = its statusCategory key (new/indeterminate/done).
Keep the JIRA priority name and due date handy for step 2; they don't go in the JSON.

### 2. Infer priority for NEW cards (LLM)

Find which tickets will become new cards: run
`td task list --project Work --json --all`, extract JIRA keys from card content
(same prefix rule as the script), and take the tickets whose key has no card.
Skip this step entirely if every ticket already has a card.

For each new ticket, infer a Todoist priority and add `"priority": "p1".."p4"`
to its object in the tickets JSON (p1 = most urgent):

| Signal | Priority |
| --- | --- |
| JIRA Highest/Blocker, prod incident, security issue, due within 2 days | p1 |
| JIRA High, blocking someone else, due this week | p2 |
| JIRA Medium set deliberately, normal sprint work | p3 |
| JIRA Low/Lowest, chores, cleanup, docs, no urgency signal | p4 |

JIRA Medium is usually the untouched default - treat it as no signal and
infer from summary, due date, and what you know of current project state
instead. If the signals conflict or there is nothing to go on, ask via
AskUserQuestion (batch all unsure tickets into one call, options p1-p4 with
your best guess recommended first). In `--dry-run` mode, OR when the caller
invokes work-board unattended (it will say so), never ask - use the best guess
and note it in the report. Unattended differs from `--dry-run` only here: the
sync still runs live (no `--dry-run` flag on the script); only the prompt is
suppressed.

Priority is applied on create only; the script never changes priority on
existing cards, so manual priority edits are always preserved.

### 3. Run the script

```bash
python3 ~/.claude/skills/work-board/scripts/sync.py \
  --tickets-file /tmp/work-board-tickets.json [--dry-run]
```

Live mode for /start-of-day and direct invocations; `--dry-run` for /end-of-day drift reports.
Add `--stale-days 7` to also report `stale` actions: manual (non-JIRA) cards sitting in
Next Up / In Progress with no Todoist activity in the window (report-only, never acted on;
JIRA-linked cards are exempt - their freshness is JIRA state).
The script prints one JSON action per line: create / move / update-state / complete /
manual-override / orphan. It exits FATAL if the six board sections don't exist.

### 4. Resolve orphans (LLM)

For each `orphan` action (JIRA-linked card whose key wasn't in the assigned-open set), call
`getJiraIssue` for the key:

- done-category status -> `td task complete id:<card_id>`
- still open but assigned to someone else -> report ("re-assigned to X - complete or keep?");
  do NOT auto-complete.
- MCP failure -> report the orphan as unverified; do not guess.

If the script was run with `--dry-run` (e.g. from /end-of-day), do NOT execute any td
commands for orphans - list them in the report only. Orphan completions are live-mode only.

### 5. Report

Summarize in one short block: created N (with assigned priority), moved N
(from -> to with keys), completed N, manual overrides (key: column vs mapped -
informational), orphans resolved/flagged.
In dry-run, prefix with "drift report - no changes made".

## Setup (one-time, idempotent)

```bash
td section create --project "Work" --name "In Review"
td section create --project "Work" --name "Waiting on Others"
```

Column ORDER cannot be set via td - drag columns in the Todoist app once:
Backlog | Next Up | In Progress | In Review | Waiting on Others | Blocked.
