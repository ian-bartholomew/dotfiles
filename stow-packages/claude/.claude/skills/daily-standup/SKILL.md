---
name: daily-standup
description: This skill should be used when the user asks to "daily standup", "standup", "run standup", "make my standup", "generate my standup", or runs "/daily-standup". Synthesizes the classic three-bucket standup (what I did / what I'm going to do / blockers) from the wiki log, project logs, meeting summaries, the Obsidian daily note, JIRA, Todoist (#work), and Slack, then writes it into today's Obsidian daily note. Blockers are inferred from sources — no interactive prompt.
version: 0.3.0
allowed-tools:
  [
    Bash,
    Read,
    mcp__plugin_fbg-core_atlassian__searchJiraIssuesUsingJql,
    mcp__claude_ai_Slack__slack_search_public_and_private,
    Skill,
  ]
---

# Daily Standup Skill

Generate a daily standup section in today's Obsidian daily note. Three
buckets, source-attributed, idempotent — gather from the wiki log,
project logs, meeting summaries, Obsidian daily notes, JIRA, Todoist
(`#work`), and Slack; infer blockers from those sources; emit the section
via a deterministic Python renderer.

## Purpose

The morning standup is always the same shape, but the inputs are scattered
across the wiki, JIRA, Todoist, the daily note, and Slack. This skill stitches
them together so the user can paste a complete standup into Slack (or read it
from Obsidian) without manually correlating sources every morning. Blockers
are inferred from JIRA status, project logs, meeting notes, and the daily
note — the skill never prompts.

Layout is owned by `render.py` next to this file. The model gathers raw data,
normalizes it to a JSON contract, and pipes it to `render.py`. The model
never composes the standup markdown by hand.

Within each bucket, `render.py` groups bullets under bold theme sub-headers
(Tickets, Meetings, Project work, ...) derived from each bullet's `source` —
see §Theming. The model only sets `source`; it never assigns themes.

## When to Use

- User explicitly runs `/daily-standup`
- User says "daily standup", "standup", "run standup", "make my standup", "generate my standup"

## When NOT to Use

- Morning snapshot of PRs + JIRA + all Todoist — use `/start-of-day`
- End-of-day capture (meetings, Slack learnings, wiki compile) — use `/end-of-day`
- Just see today's Todoist — `td today` directly
- Just see open JIRA — Atlassian MCP / board directly

## Pipeline

```
  Compute lookback date  →  Parallel fetch  →  Normalize to JSON
                                                       │
                                                       ▼
                                          render.py → markdown block
                                                       │
                                                       ▼
                                       Upsert into today's daily note
                                                       │
                                                       ▼
                                          One confirmation line, exit
```

## Prerequisites

- **Obsidian app running** with Daily Notes enabled (so `obsidian daily` /
  `obsidian daily:path` work). Full reference: <https://help.obsidian.md/cli>.
- **Atlassian MCP** (`mcp__plugin_fbg-core_atlassian__searchJiraIssuesUsingJql`).
- **Todoist CLI (`td`) authed.** Fallback absolute path: `/opt/homebrew/bin/td`.
- **Slack MCP** (`mcp__claude_ai_Slack__*`). Best-effort — if unauthed, skip
  the Slack signal and continue.
- Working directory is the Obsidian vault (`~/Documents/Work`) so relative
  reads of `wiki/_log.md`, `projects/`, and `meetings/` resolve.

## Process Flow

### Step 1: Compute the lookback window

`today_date` = today (ISO `YYYY-MM-DD`).
`lookback_date` rule:

- If today is **Monday** → previous **Friday** (today minus 3 days).
- Otherwise → today minus 1 day.

Capture both the ISO date and the weekday label (`Friday`, `Monday`, …) so the
section header can read `_Since Friday 2026-05-15 → Monday 2026-05-18_`.

A one-liner that does this:

```bash
python3 -c "
from datetime import date, timedelta
t = date.today()
delta = 3 if t.weekday() == 0 else 1
y = t - timedelta(days=delta)
print(f'lookback_date={y.isoformat()} lookback_label={y.strftime(\"%A\")} today_date={t.isoformat()} today_label={t.strftime(\"%A\")}')
"
```

### Step 2: Locate today's daily note

```bash
obsidian daily                           # ensure today's note exists
DAILY_NOTE_PATH="$(obsidian daily:path)" # canonical absolute path
```

Never construct the path manually. If `obsidian daily` exits non-zero, see §Error Handling.

### Step 3: Fetch all sources in parallel

Issue these in a single assistant message so they execute concurrently.

1. **Bash — wiki activity log:** grep entries dated within the lookback day,
   but **exclude EOD and wiki-ingestion actions** — these are background
   maintenance, not standup-worthy work.

   ```bash
   grep -nE "^## \\[$LOOKBACK_DATE\\]" wiki/_log.md \
     | grep -vE "\\] (end-of-day|compile|ingest|lint|migration) \\|" \
     || true
   ```

   Then read ~30 lines after each match to capture the entry body. Skip any
   matched entry whose action is one of `end-of-day`, `compile`, `ingest`,
   `lint`, or `migration`. Keep `research`, `query`, `archive`, and any other
   user-facing actions.

2. **Bash — project logs:** glob and read each active project's log.

   ```bash
   ls projects/*/log.md 2>/dev/null
   ```

   For each, `grep -nE "^## \\[?$LOOKBACK_DATE" <file>` to find entries from
   the lookback day, then read their bodies. Capture the project name from
   the directory.

3. **Bash — meeting summaries:** glob the lookback day's meeting folders.

   ```bash
   ls -d meetings/$LOOKBACK_DATE-*/ 2>/dev/null
   ```

   For each, read `summary-*.md` (or `summary.md`). Pull the meeting title
   from the folder name and a one-line summary from the file.

4. **Bash — today's daily note:** read `$DAILY_NOTE_PATH`. Look for any
   user-authored "Plan" / "Today" / "Blockers" sections to lift verbatim
   into "Today" / "Blockers" buckets respectively.

5. **Atlassian MCP — JIRA In Progress:**

   ```jql
   assignee = currentUser() AND status = "In Progress" ORDER BY updated DESC
   ```

6. **Atlassian MCP — JIRA Blocked:**

   ```jql
   assignee = currentUser() AND status in (Blocked, "On Hold", Waiting) ORDER BY updated DESC
   ```

7. **Bash — Todoist `#work` due today + overdue:**

   ```bash
   td today --json
   ```

   Filter the result to tasks whose project name is `work` (or any task whose
   `project_id` matches `#work`). Include overdue items.

8. **Slack MCP — messages I sent in the lookback window.** Best-effort: a
   401/permission error means skip, don't fail the run.

   ```text
   slack_search_public_and_private query: "from:@me after:<LOOKBACK_DATE-1d> before:<TODAY_DATE+1d>"
   ```

   Take the top ~20 results; collapse near-duplicates (same channel, same
   thread root) into a single bullet noting the channel and topic.

### Step 4: Normalize to JSON

Produce a single JSON blob matching the contract documented in `render.py`:

```json
{
  "lookback_date":  "2026-05-15",
  "lookback_label": "Friday",
  "today_date":     "2026-05-18",
  "today_label":    "Monday",
  "did":      [ { "text": "...", "ref": "FANDEVX-1234", "url": "https://...", "source": "jira" }, ... ],
  "will_do":  [ ... ],
  "blockers": [ ... ]
}
```

Bucket assignment rules:

- **did** — every wiki log / project log / meeting / Slack signal from the
  lookback window; any closed/resolved JIRA tickets the user worked on.
  Source one of: `jira`, `project`, `log`, `meeting`, `slack`.
- **will_do** — JIRA In Progress assigned to user; Todoist `#work` tasks
  (today + overdue); free-text "Plan" / "Today" section in today's daily
  note; up to 3 LLM-inferred continuations from yesterday's open threads
  (project log "follow-ups", meeting "action items"). Source one of:
  `jira`, `todoist`, `daily-note`, `inferred`.
- **blockers** — JIRA tickets in Blocked / On Hold / Waiting; daily note
  "Blockers" section if present; LLM scan of recent project log / meeting
  notes for "blocker", "waiting on", "stuck on", "blocked by", "depends on",
  "awaiting" language. Source one of: `jira`, `daily-note`, `inferred`.
  All non-JIRA / non-daily-note blockers are inferred — the skill does not
  prompt for manual blocker input.

Cap inferred bullets at **3 per bucket** to keep noise down. Mark every
inferred bullet `source: "inferred"` so the user can prune in Obsidian.

**Theming.** `render.py` sub-groups each bucket under bold theme headers
derived from `source` — you do not set themes, only `source`. The map:

| source            | theme        |
|-------------------|--------------|
| `jira`            | Tickets      |
| `meeting`         | Meetings     |
| `project`, `log`  | Project work |
| `slack`           | Comms        |
| `todoist`         | Todos        |
| `daily-note`, `input` | Notes    |
| `inferred`        | Follow-ups   |
| anything else     | Other        |

A bucket that resolves to a single theme renders flat (no header). Theme
order is fixed: Tickets, Meetings, Project work, Comms, Todos, Notes,
Follow-ups, Other.

**Bullet text must be Slack-brief.** Aim for **≤ 12 words** per `text`.
Prefer verb-led fragments over full sentences:

- Good: `"Shipped Karpenter migration for load-testing env"`
- Bad:  `"I shipped the Karpenter migration for the load-testing environment, which involved …"`

Strip ticket keys from the `text` (they go in `ref`). Strip URLs from the
`text` (they go in `url`). One idea per bullet — split if you find yourself
writing "and".

Write the JSON to `/tmp/daily-standup.json`.

### Step 5: Render

Default is brief mode (Slack-friendly): bare JIRA keys, no source-of-truth
italic suffix, no date-range header. Pass `--verbose` for the Obsidian-only
format with markdown links and source labels.

```bash
# Brief (default — Slack paste)
python3 ~/.claude/skills/daily-standup/render.py --input /tmp/daily-standup.json > /tmp/daily-standup.md

# Verbose (Obsidian-friendly)
python3 ~/.claude/skills/daily-standup/render.py --input /tmp/daily-standup.json --verbose > /tmp/daily-standup.md
```

If the user asks for "verbose" / "with links" / "for obsidian", pass
`--verbose`. Otherwise default to brief.

### Step 6: Upsert into today's daily note

The rendered block is delimited by `<!-- standup:start -->` / `<!-- standup:end -->`.
Idempotent algorithm:

1. Read `$DAILY_NOTE_PATH`.
2. If the file already contains `<!-- standup:start -->`, replace everything
   from the `## Daily Standup` heading through the `<!-- standup:end -->`
   marker with the new block.
3. Otherwise, append the new block to the end of the file (preceded by a
   blank line).
4. Write the file back atomically.

A safe shell implementation:

```bash
python3 - "$DAILY_NOTE_PATH" /tmp/daily-standup.md <<'PY'
import sys, re, pathlib
note = pathlib.Path(sys.argv[1])
new_block = pathlib.Path(sys.argv[2]).read_text().rstrip("\n") + "\n"
text = note.read_text() if note.exists() else ""
pattern = re.compile(r"## Daily Standup\n.*?<!-- standup:end -->\n?", re.DOTALL)
if pattern.search(text):
    text = pattern.sub(new_block, text)
else:
    # Separate existing content from the new block with exactly one blank line.
    text = text.rstrip("\n")
    if text:
        text += "\n\n"
    text += new_block
note.write_text(text)
print(note)
PY
```

### Step 7: Confirm and exit

Print one line:

```
Daily standup written to: /Users/.../05-18-2026.md
```

Do not loop. Do not summarise the buckets. The user reads/edits the section
directly in Obsidian.

## Error Handling

- **`obsidian daily` fails** — print the underlying error verbatim and exit.
  The Obsidian app probably isn't running.
- **Atlassian MCP fails** — render `did` / `will_do` / `blockers` from the
  remaining sources; JIRA-derived bullets simply won't appear.
- **`td today` fails** — same: drop the Todoist source, keep going.
- **Slack MCP fails or returns 401** — same: silently drop the Slack signal.
  Do not prompt the user; auth is out of scope for this skill.
- **No sources matched at all** — the renderer emits empty-state placeholders
  (`_No activity captured for the lookback window._` etc.) so the section
  is still well-formed.

## Out of Scope (v0.1)

- Posting to Slack directly. v0.1 writes only to the daily note; copy/paste
  is the user's call.
- Holiday / PTO-aware lookback. If today is the day after a holiday, the
  lookback may miss work. Re-run with a future `--since YYYY-MM-DD` override
  if that ever becomes a problem.
- Multi-day lookback. v0.1 looks back one workday only.
