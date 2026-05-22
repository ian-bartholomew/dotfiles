---
name: start-of-day
description: This skill should be used when the user asks to "start of day", "SOD", "run start of day", "kick off the day", "start of day routine", or runs "/start-of-day".
version: 0.26.0
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

- **`gh` failure:** render the PR section as `_Open Pull Requests — lookup failed: <error>._` and continue.
- **JIRA MCP failure:** render the JIRA section as `_Open JIRA Tickets — lookup failed: <error>._` and continue.
- **`td` failure:** render the Todoist section as `` _Today + Overdue — `td` unavailable: <error>._ `` and continue.
- **`obsidian daily` failure** (most common cause: Obsidian not running): print the error and tell the user `Obsidian doesn't appear to be running — open the app, then re-run /start-of-day. Falling back to terminal output for this run.` Emit the markdown to the terminal as a one-time fallback.
- **Post-write marker count != 1:** print a diagnostic with `$DAILY_NOTE_PATH` and halt — ask the user to inspect the daily note manually before re-running.

## Related Skills

- **/end-of-day** — End-of-day counterpart
- **`obsidian` CLI** — Daily-note ops; full reference at <https://help.obsidian.md/cli>
- **todoist-cli** — `td` CLI syntax reference

## Summary

`/start-of-day` fetches open GitHub PRs, assigned open JIRA tickets, and Todoist due-today + overdue tasks in parallel, then writes three flat bulleted sections into today's Obsidian daily note under a `## Start of Day` heading bracketed by `<!-- sod:begin -->` / `<!-- sod:end -->` markers. The write is idempotent (re-running the same day replaces the section). The skill is one-shot and non-interactive: a single confirmation line prints once the note is written, then the skill invokes `/daily-standup` as its final step to append a `## Daily Standup` section to the same daily note.

From v0.19.0 the markdown is emitted by `render.py` (Python 3 stdlib) rather than composed by the model — the model writes the three fetch results to JSON files, invokes the script, and splices the script's stdout into the daily note. JIRA parent traversal, PR↔JIRA cross-linking, Potential-to-close, and emoji prefixes were intentionally left out of this baseline; they will be added to the script in subsequent versions.
