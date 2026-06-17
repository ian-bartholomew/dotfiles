---
name: start-of-day
description: This skill should be used when the user asks to "start of day", "SOD", "run start of day", "kick off the day", "start of day routine", or runs "/start-of-day".
version: 0.28.0
allowed-tools:
  [
    Bash,
    mcp__plugin_fbg-core_atlassian__searchJiraIssuesUsingJql,
    mcp__plugin_fbg-core_atlassian__getJiraIssue,
    Skill,
  ]
---

# Start-of-Day Skill

Gather the morning signal — open GitHub PRs, open JIRA tickets assigned to you, and Todoist tasks due today + overdue — and write it as three flat sections into today's Obsidian daily note.

Layout is owned by a deterministic Python render script (`render.py` next to this file), so the model never composes the markdown by hand. The script:

- Builds a nested JIRA forest from assigned tickets plus their reverse-traversed parent chain (root → child → grandchild …) and renders each ticket as a bulleted item with an embedded `####`/`#####`/`######` heading (capped at h6). Ancestor tickets that aren't assigned to you get a `· _(not assigned to you)_` suffix.
- Extracts `FANDEVX-\d+` / `FESFEAT-\d+` keys from PR titles to render a bi-directional PR ↔ JIRA cross-link (`· JIRA: [KEY](url)` on PR rows, `· PRs: [#N](url)` on matching JIRA rows).
- Flags assigned tickets as `· **🟢 Potential to close**` when the merged-PR set names the key but no open PR does — i.e., shipped work that no longer has open PRs. Ancestors never flag.
- Prefixes JIRA status and priority with an emoji for at-a-glance scanning (📋/🚧/👀/🛑/✅/❌ for status; 🔥/🔴/🟡/🔵/⚪ for priority). 🟢 is reserved for "Potential to close". Unrecognised values render without a prefix.

The skill now carries the full v0.17.0 feature set, end-to-end deterministic and owned by `render.py`.

## Purpose

The TUI is the wrong place for the morning snapshot — colours are flaky, the output scrolls away, and there's no record after the session ends. The snapshot is written into today's Obsidian daily note, which becomes the durable, searchable record of what was on the plate that morning.

The skill is one-shot and non-interactive: fetch, write, confirm path, then hand off to `/daily-standup` to append the standup section in the same daily note. The user reviews and edits items in Obsidian (or via `td task update` / `gh` / JIRA directly) — this skill does not loop.

## When to Use

- User explicitly runs `/start-of-day`
- User mentions "start of day", "SOD", "kick off the day", "start of day routine"

## When NOT to Use

- Only want today's todos: run `td today` directly
- Want to edit one task: `td task update <ref>` directly
- Only want open PRs or JIRA tickets: `gh search prs --author=ian-at-fes --state=open` or the Atlassian MCP search directly

## Pipeline

```
  Fetch (parallel)  →  Render three flat sections  →  Upsert into today's
  PRs / JIRA /         under a single ## Start          daily note (idempotent
  Todoist              of Day heading                   via <!-- sod:* --> markers)
                                                              ↓
                                                  one confirmation line
                                                              ↓
                                                  invoke /daily-standup
                                                  (appends ## Daily Standup
                                                   section to same note)
```

## Prerequisites

- **`gh` CLI authed as `ian-at-fes`.** The skill runs `gh auth switch --user ian-at-fes` before any `gh` call. Pin `--author=ian-at-fes` explicitly — never `--author @me` (it silently falls back to the personal account and hides org PRs).
- **Atlassian MCP available** (`mcp__plugin_fbg-core_atlassian__searchJiraIssuesUsingJql`).
- **Todoist CLI (`td`) authed.** Fallback absolute path: `/opt/homebrew/bin/td`.
- **Obsidian app running** with Daily Notes enabled. The `obsidian` CLI talks to the live app and exits non-zero otherwise. Full reference: <https://help.obsidian.md/cli>.

## Step 0: Pre-Flight Verification (MANDATORY)

Run these checks **before fetching any data or writing the snapshot**. `/start-of-day` doesn't make recommendations directly, but its snapshot is consumed downstream by `/what-next` and `/daily-standup` — feeding them stale or partial data poisons the rest of the day's signal. **Fail loudly and halt if any check cannot execute.**

The MCP probes (#3, #5, #6 below) are non-negotiable: the snapshot section itself depends on Atlassian, and `/daily-standup` invoked in Step 6 depends on Slack and Zoom. A missing MCP at the start of the day means stale signal for every downstream skill that reads the daily note. Do not skip a failing probe; do not offer the user a "continue without this MCP" path. Halt, surface the failure, ask the user to fix permissions (`/plugin` → reauthorize), and re-run.

### Step 0 does NOT degrade gracefully

The skill's per-source `lookup failed: <error>` fallback (see Error Handling) applies **only** to `td` and `obsidian`. It does **NOT** generalize to Atlassian, Slack, or Zoom. Those three are pre-flight gates, not graceful-degrade sources. The line is:

- **Gate (halt on failure):** Atlassian MCP, Slack MCP, Zoom MCP, `gh` CLI, `gh search prs` queries.
- **Graceful-degrade (render `lookup failed`, continue):** Todoist (`td`), Obsidian write failure (one-time terminal fallback).

If you find yourself reasoning "the skill degrades gracefully elsewhere, so I can let this MCP failure through too" — stop. That reasoning is wrong. The gate exists precisely because downstream consumers (`/what-next`, `/daily-standup`) cannot recover from a poisoned snapshot or a missing standup section the way they can recover from a missing Todoist line.

### Step 0 is not negotiable under time pressure

If the user invokes `/start-of-day` with "go fast", "skip checks", "I'm late for standup", or any time-pressure framing — **pre-flight still runs in full**. The user's actual goal under that framing is a usable snapshot for downstream synthesis. Running the pipeline blind defeats that goal: the snapshot lands but is silently partial, `/daily-standup` fails or runs degraded, and `/what-next` reads stale data for the rest of the day. A 1-2 second parallel probe block is cheaper than the cleanup.

If a probe fails under time pressure, the right response is **not** "skip and ship" — it's "halt now and surface so the user can `/plugin` reauthorize in 10 seconds and re-run before standup." That is faster than discovering the gap mid-standup.

### Rationalization counters

| Excuse | Reality |
|--------|---------|
| "User said go fast — that overrides MANDATORY." | No. User's goal is usable signal; partial signal is worse than 10s of re-auth. Pre-flight runs in full. |
| "The skill degrades gracefully on `td`, so MCP failures should too." | No. `td`/`obsidian` are graceful-degrade by design; Atlassian/Slack/Zoom are explicit gates. The boundary is intentional. |
| "User verbally said Slack might be flaky, but the skill doesn't say to probe — so I won't." | The skill DOES say to probe (#5). A user warning is additional evidence the probe will catch something, not a reason to skip it. |
| "Probing Slack/Zoom is duplicate work — the downstream call will fail anyway." | Downstream failure happens AFTER a partial write. Probe failure happens BEFORE any write. Order matters — half-written daily notes pollute every downstream consumer for the rest of the day. |
| "I'll skip probes and let `lookup failed: <error>` lines render." | Only `td` renders `lookup failed`. Atlassian/Slack/Zoom failures halt — there is no error-sentinel render path for them. |

### 1. Sync local git state (when in a repo)

If cwd is inside a git repo, sync the default branch and surface staleness:

```bash
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git fetch origin --quiet \
    || { echo "FATAL: git fetch origin failed. Halting."; exit 1; }
  DEFAULT_BRANCH="$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')"
  DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"
  BEHIND="$(git rev-list --count "$DEFAULT_BRANCH..origin/$DEFAULT_BRANCH" 2>/dev/null || echo 0)"
  [ "$BEHIND" -gt 0 ] && echo "NOTE: local $DEFAULT_BRANCH is $BEHIND commits behind origin/$DEFAULT_BRANCH."
fi
```

If cwd is not in a repo, skip silently — `/start-of-day` is commonly run from `~` or another non-repo path.

### 2. GitHub identity must be `ian-at-fes` and reachable

```bash
gh auth switch --user ian-at-fes 2>&1 \
  || { echo "FATAL: cannot switch gh CLI to ian-at-fes. Halting."; exit 1; }
gh auth status 2>&1 | grep -q "Active account: true" \
  || { echo "FATAL: gh auth status check failed. Halting."; exit 1; }
```

A failure here halts the skill — do **not** proceed to write a snapshot with a missing or wrong-account PR section. This is a deliberate change from prior versions, which rendered an error sentinel and continued.

### 3. Atlassian MCP must be available

The skill cannot produce a useful daily-note JIRA section without a live MCP call. Probe the Atlassian MCP up-front with `mcp__plugin_fbg-core_atlassian__atlassianUserInfo` (no args, minimal side-effect-free auth probe). If the probe fails — auth error, tool unavailable, permission denied — halt before writing:

```
FATAL: Atlassian MCP unavailable — cannot fetch assigned tickets. Halting.
Fix: run /plugin, reauthorize the Atlassian server, then re-run /start-of-day.
```

The functional JQL call in Step 2 (`searchJiraIssuesUsingJql`) doubles as a second auth check at fetch time, but probing here keeps the failure mode upstream of the parallel-fetch block so the user isn't left with half-written temp files.

Earlier versions rendered an error sentinel into the JIRA section. That behavior is removed — `/what-next` and `/daily-standup` cannot meaningfully recover from a poisoned JIRA section, so write nothing rather than write garbage.

### 4. Live PR merge state via `gh search prs`

Both the `--state=open` and `--state=closed --merged` searches in Step 2 must succeed. If either fails, halt:

```
FATAL: gh search prs failed (<open|merged>). Halting.
```

The merged-PRs query feeds the "🟢 Potential to close" flag downstream consumers read — a missing merged-PR set silently downgrades signal quality across the day. No partial writes.

### 5. Slack MCP must be available

`/start-of-day` doesn't read Slack directly, but it chains to `/daily-standup` (Step 6), which does. Probe up-front so the user fixes auth before the snapshot is written and the standup attempt fails halfway through.

Call `mcp__claude_ai_Slack__slack_search_users` with a minimal query (e.g. `query: "ian"`, `count: 1`). Success = returns a user search response. Failure (auth error, tool unavailable, permission denied) halts:

```
FATAL: Slack MCP unavailable — /daily-standup will not have Slack signal. Halting.
Fix: run /plugin, reauthorize the Slack server, then re-run /start-of-day.
```

### 6. Zoom MCP must be available

Same reasoning as Slack — `/daily-standup` and other downstream consumers of the daily note may pull from Zoom transcripts, so a missing Zoom MCP at the start of the day means stale signal. Probe with `mcp__claude_ai_Zoom_for_Claude__recordings_list` (`page_size: 1`, today's date for any required date arg). Success = returns a recordings response (empty list is fine). Failure halts:

```
FATAL: Zoom MCP unavailable — meeting context will be missing downstream. Halting.
Fix: run /plugin, reauthorize the Zoom server, then re-run /start-of-day.
```

### Pre-flight summary

If all six checks pass, print one line — `Pre-flight: git / gh / Atlassian / gh-prs / Slack / Zoom OK` — and proceed to Step 1. If any failed, halt and surface every probe's status in the failure message so the user can fix all gaps in a single round-trip rather than re-running and discovering them one at a time.

Run probes #3, #5, and #6 (the three MCPs) **in parallel** — single assistant message, three tool calls — to keep pre-flight under one round-trip when everything is healthy. The bash checks (#1, #2, #4) can run in the same message too.

## Process Flow

### Step 1: Locate today's daily note

```bash
obsidian daily                              # ensure today's note exists
DAILY_NOTE_PATH="$(obsidian daily:path)"    # canonical absolute path
```

Never construct the path manually. Obsidian's Daily Notes / Periodic Notes plugin owns the location and filename format — whatever `obsidian daily:path` returns is the only correct destination. If `obsidian daily` exits non-zero, see §Error Handling.

### Step 2: Fetch all sources in parallel

Issue these tool uses in a single assistant message so they execute concurrently.

1. **Bash — GitHub PRs (open):**

   ```bash
   gh auth switch --user ian-at-fes && \
   gh search prs --author=ian-at-fes --state=open \
     --json number,title,url,repository,isDraft,updatedAt \
     --limit 500
   ```

1a. **Bash — GitHub PRs (merged), for the "🟢 Potential to close" flag:**

```bash
gh search prs --involves=ian-at-fes --state=closed --merged \
  --json number,title,url,repository \
  --limit 500
```

The merged query uses `--involves` (not `--author`) on purpose: the flag is meant to catch shipped work that closed a ticket assigned to you, regardless of who authored the PR. A teammate's merged PR that names `FANDEVX-NNNN` in its title should still flag the corresponding assigned ticket as closeable. The open-PR query above keeps `--author=ian-at-fes` because the PR section of the daily note is "what's on _your_ plate" — reviewer-only PRs there would just add noise.

This call is best-effort: if it fails, write `{"error": "<msg>"}` to `/tmp/sod-merged-prs.json` and the flag simply won't fire.

1. **Atlassian MCP — JIRA tickets + parent-chain traversal:**

   First, search for assigned tickets:

   - **Tool:** `mcp__plugin_fbg-core_atlassian__searchJiraIssuesUsingJql`
   - **cloudId:** `efc5fcb9-cd3f-4ee1-8d0d-255a135bf4e8`
   - **JQL:** `assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC`
   - **Fields:** `summary, status, priority, updated, issuetype, parent`

   Then walk each ticket's `parent` chain up to the topmost ancestor — even when those ancestors are not assigned to you — so the nested rendering can show the epic / feature context. Algorithm:

   1. Seed a map `nodes_by_key` from the search result. Mark each as `assignedToMe: true`.
   2. For every ticket with a `parent.key` not already in the map, call `mcp__plugin_fbg-core_atlassian__getJiraIssue` (same cloudId, same `fields` list) to fetch that ancestor. Mark fetched ancestors as `assignedToMe: false`. Add them to the map.
   3. Repeat for the ancestor's own `parent.key`, and so on. **Dedup aggressively — never fetch the same key twice in one run.** Different assigned tickets often share ancestor chains.
   4. **Depth cap: 6 hops.** If traversal would go deeper, stop and accept that some far ancestors won't appear.

   Write the **merged** map to `/tmp/sod-jira.json` as `{"issues": [...]}` — one entry per ticket (assigned + ancestor), each with `key`, `assignedToMe`, and a `fields` object containing `summary, status, priority, issuetype, updated, parent`. The render script reads this shape and builds the forest itself; no traversal logic in the script.

2. **Bash — Todoist:**

   ```bash
   td today --json
   ```

After all three fetches return (or fail), persist each result to a temp JSON file for Step 3 to consume:

- **Open PRs (success):** write the raw `gh search prs --state=open --json …` output to `/tmp/sod-prs.json`.
- **Merged PRs (success):** write the raw `gh search prs --state=closed --merged --json …` output to `/tmp/sod-merged-prs.json`.
- **JIRA (success):** write the raw Atlassian MCP response (object with an `issues` array) to `/tmp/sod-jira.json`.
- **Todoist (success):** write the raw `td today --json` output to `/tmp/sod-todoist.json`.
- **Any source fails:** write `{"error": "<message>"}` to that source's file instead of a payload. Other sources still write their normal output.

The render script in Step 3 reads these three files and emits the corresponding section text — including the `_… lookup failed: <error>._` line for any source whose file contains an `error` shape.

### Step 2.5: Work-board sync (live)

Run the `work-board` skill in live mode, reusing the assigned-open JIRA result already
fetched in Step 2 (write it to `/tmp/work-board-tickets.json` in the shape the skill
documents; do not re-query JIRA). Include the sync summary (created / moved / completed /
manual overrides / orphans) in the Step 5 terminal confirmation. A sync failure is
non-fatal to start-of-day: report it and continue - the board is a view, not a gate.

### Step 2.6: End-of-day heartbeat check

Confirm the previous business day actually ran its end-of-day pipeline. A
missed unattended EOD leaves no `wiki/_log.md` entry - a silent non-event.
Surface its absence in the morning.

```bash
PREV=$(date -v-1d +%Y-%m-%d)         # Mon-Thu; on Monday use Friday:
[ "$(date +%u)" = "1" ] && PREV=$(date -v-3d +%Y-%m-%d)
grep -q "^## \[$PREV\] end-of-day" ~/Documents/Work/wiki/_log.md \
  && echo "EOD heartbeat: $PREV ok" \
  || echo "EOD heartbeat: WARNING no end-of-day entry for $PREV"
```

If the check warns, add a one-line "Yesterday's end-of-day did not run -- check
the Desktop scheduled task" note to the start-of-day terminal summary (Step 5).
Do not block the morning routine on it.

(This may emit a false warning the day after a US holiday, when the prior
business day legitimately had no run. It is non-blocking, so that is acceptable.)

### Step 3: Invoke the render script

The skill no longer composes markdown by hand. A deterministic Python script at `~/.claude/skills/start-of-day/render.py` reads the three JSON files written in Step 2 and emits the entire `## Start of Day` … `<!-- sod:end -->` block on stdout. The script owns whitespace and structure; the model captures stdout and uses it verbatim in Step 4.

```bash
python3 ~/.claude/skills/start-of-day/render.py \
  --prs /tmp/sod-prs.json \
  --jira /tmp/sod-jira.json \
  --todoist /tmp/sod-todoist.json \
  --merged-prs /tmp/sod-merged-prs.json \
  --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

The script uses Python 3 stdlib only — no `pip install`, no extra setup. It works against whatever Python 3 ships on the system. Optional `--now <iso>` overrides the reference time used for "updated Nh/Nd ago" math (useful only for fixture-based testing).

The captured stdout is opaque to the model — do **not** edit it before Step 4. The byte stream from `python3 render.py …` is exactly what goes into the daily note.

If the script exits non-zero, halt and surface the stderr to the user — that signals a malformed JSON file from Step 2.

Test fixtures live at `~/.claude/skills/start-of-day/test-fixtures/`. To verify the script standalone:

```bash
cd ~/.claude/skills/start-of-day
./render.py --prs test-fixtures/sample-prs.json \
            --jira test-fixtures/sample-jira.json \
            --todoist test-fixtures/sample-todoist.json \
            --generated-at 2026-05-13T22:40:20Z \
            --now 2026-05-13T22:40:20Z
```

The output shape is unchanged from v0.18.0 — three flat sections, `_None._` for empty lists (or the Todoist-specific empty line), `_<Section> — lookup failed: <message>._` for an error source.

### Step 4: Upsert the section into today's daily note

Idempotent — re-running the same day MUST replace the previous section, not duplicate it.

**Case A — first run today (markers absent):** append via `obsidian daily:append`:

```bash
obsidian daily:append content="$(cat <<'EOF'

## Start of Day

<!-- sod:begin generated=… -->
...full rendered section...
<!-- sod:end -->
EOF
)"
```

The leading blank line in the heredoc separates the new section from whatever ended the daily note already.

**Case B — re-run today (markers present):** read → splice → write. Use `$DAILY_NOTE_PATH` from Step 1:

1. Read the current note via the `Read` tool against `$DAILY_NOTE_PATH` (or `obsidian daily:read`).
2. Find the `## Start of Day` heading and the matching `<!-- sod:begin … -->` / `<!-- sod:end -->` markers. Delete everything from the heading line through and including the `<!-- sod:end -->` line.
3. Splice the freshly-rendered section into the same position.
4. Write the result back to `$DAILY_NOTE_PATH` via `Write` or `Edit`.

Detection: if `obsidian daily:read` contains the literal `<!-- sod:begin`, you're in Case B; otherwise Case A.

**Never** edit by line number or by blind regex on the whole file. The `<!-- sod:begin` / `<!-- sod:end -->` markers are the only contract; anything outside them is the user's.

After the write, verify: `obsidian daily:read | grep -c '<!-- sod:begin'` must return `1`. If it returns `0` or `>1`, see §Error Handling.

### Step 5: Confirm the start-of-day write in the terminal

Print one short, plain-text line before proceeding to the standup step. No edit loop:

```
Start of Day written to: <absolute path>
  PRs: <N>  ·  JIRA: <N>  ·  Todoist: <N>
```

### Step 6: Invoke `/daily-standup`

After the start-of-day section is written and confirmed, invoke `/daily-standup` as the final step. This appends the `## Daily Standup` section (bracketed by `<!-- standup:start -->` / `<!-- standup:end -->`) to the same daily note. Both sections coexist; their markers do not collide.

Call the `Skill` tool with `skill: "daily-standup"` — no arguments. `/daily-standup` runs with its own defaults (brief mode, today's date) and prints its own confirmation line on completion.

If `/daily-standup` fails (Slack MCP down, JIRA timeout, etc.), the already-written `## Start of Day` section remains intact. Surface the failure to the user inline; do not retry. The user can re-run `/daily-standup` directly — both skills are idempotent.

## Error Handling

`gh`, Atlassian/JIRA MCP, merged-PR, Slack MCP, and Zoom MCP failures are now governed by the Step 0 Pre-Flight Verification block — they halt the skill loudly before any write happens. The "render error sentinel and continue" behavior for those sources was deliberately removed in v0.27.0 (and extended in v0.28.0 to cover Slack/Zoom): downstream consumers (`/what-next`, `/daily-standup`) cannot recover from a poisoned snapshot or a missing standup section, so writing nothing is better than writing garbage. Pre-flight failures must be fixed (typically `/plugin` → reauthorize) and the skill re-run; do not offer a continue-without-MCP path.

Failures that **do not** halt:

- **`td` failure:** render the Todoist section as `` _Today + Overdue — `td` unavailable: <error>._ `` and continue. Todoist is a lower-stakes signal — a missing section doesn't poison the rest of the day's downstream synthesis.
- **`obsidian daily` failure** (most common cause: Obsidian not running): print the error and tell the user `Obsidian doesn't appear to be running — open the app, then re-run /start-of-day. Falling back to terminal output for this run.` Emit the rendered markdown to the terminal as a one-time fallback.
- **Post-write marker count != 1:** print a diagnostic with `$DAILY_NOTE_PATH` and halt — ask the user to inspect the daily note manually before re-running. This is a structural integrity check on the write itself, not a pre-flight check.

## Related Skills

- **/end-of-day** — End-of-day counterpart
- **`obsidian` CLI** — Daily-note ops; full reference at <https://help.obsidian.md/cli>
- **todoist-cli** — `td` CLI syntax reference

## Summary

`/start-of-day` fetches open GitHub PRs, assigned open JIRA tickets, and Todoist due-today + overdue tasks in parallel, then writes three flat bulleted sections into today's Obsidian daily note under a `## Start of Day` heading bracketed by `<!-- sod:begin -->` / `<!-- sod:end -->` markers. The write is idempotent (re-running the same day replaces the section). The skill is one-shot and non-interactive: a single confirmation line prints once the note is written, then the skill invokes `/daily-standup` as its final step to append a `## Daily Standup` section to the same daily note.

From v0.19.0 the markdown is emitted by `render.py` (Python 3 stdlib) rather than composed by the model — the model writes the three fetch results to JSON files, invokes the script, and splices the script's stdout into the daily note. JIRA parent traversal, PR↔JIRA cross-linking, Potential-to-close, and emoji prefixes were intentionally left out of this baseline; they will be added to the script in subsequent versions.
