---
name: what-next
description: This skill should be used when the user asks "what next", "what-next", "what should I work on", "what's next", or otherwise wants a recommendation for the next piece of work to pick up. Synthesizes across active projects, project logs, the wiki log, and today's daily note to surface a single recommended task plus a triage view of overdue work, open follow-ups, and stale projects.
version: 0.1.0
allowed-tools:
  [
    Bash,
    Read,
    Glob,
    mcp__plugin_fbg-core_atlassian__getJiraIssue,
    mcp__plugin_fbg-core_atlassian__searchJiraIssuesUsingJql,
    mcp__plugin_fbg-core_atlassian__getJiraIssueRemoteIssueLinks,
  ]
---

# What-Next Skill

Advisory skill that answers the question **"What should I work on next?"** by reading three sources and synthesizing a recommendation in narrative form. Read-only and terminal-only — no file writes, no daily note mutation. Live JIRA status and GitHub PR status are verified for any candidate before it's recommended, to avoid suggesting work that has already been closed, merged, or moved since `/start-of-day` last refreshed the daily note. Cheap to run multiple times a day.

## Purpose

Between explicit start-of-day and end-of-day routines the user often needs a quick re-orientation: returning from a meeting, finishing a task with no obvious next step, starting an afternoon block, or just losing the thread. Today's daily note (populated by `/start-of-day`) holds open PRs and JIRA. The vault's `projects/` directory holds active project state. The wiki's `_log.md` holds recent compile activity. This skill pulls those three signals together and recommends one concrete next move, with a short triage view underneath.

The skill is one-shot and non-interactive: read, reason, print, done.

## When to Use

- User explicitly runs `/what-next`
- User asks "what next", "what's next", "what should I work on", "what should I do next"
- User says they're stuck, between tasks, or coming back from a break with no clear next step

## When NOT to Use

- Start of day — run `/start-of-day` instead (it populates today's daily note that *this* skill consumes)
- End of day — run `/end-of-day` instead
- Standup prep — run `/daily-standup`
- User already has a specific JIRA ticket in mind — use `/start-ticket`

## Pipeline

```
  Read (parallel)         →   LLM synthesizes recommendation   →   Print to terminal
  - today's daily note        - single pick + reasoning            (no file writes)
  - projects index +          - themed sections grouped
    each active project's       by urgency
    README.md and log.md
  - wiki/_log.md tail
```

## Prerequisites

- `/start-of-day` should ideally have been run today so the daily note is populated. If it hasn't, the skill notes that and continues with the other two sources.
- `obsidian` CLI available for resolving the daily note path. If absent, fall back to constructing the path from `date`.

## Process Flow

### Step 1: Resolve today's daily note path

```bash
DAILY_NOTE_PATH="$(obsidian daily:path 2>/dev/null)" || \
  DAILY_NOTE_PATH="$HOME/Documents/Work/raw/daily_notes/$(date +%Y/%m/%Y-%m-%d-%A).md"
```

If the file doesn't exist, find the most recent daily note as a fallback and remember its age:

```bash
test -f "$DAILY_NOTE_PATH" || \
  DAILY_NOTE_PATH="$(ls -t $HOME/Documents/Work/raw/daily_notes/*/*/*.md | head -1)"
```

Record whether today's note exists. If it doesn't, the final output must flag the staleness ("Daily note is N days old — recommendation may miss recent JIRA/PR activity.").

### Step 2: Read all sources in parallel

Issue these Read tool uses in a single assistant message so they execute concurrently:

1. **Today's daily note** (path from Step 1). Extract:
   - Overdue / today Todoist items
   - Any manual sections the user added outside the `<!-- sod:begin/end -->` markers (Plan, Blockers, Done — these are high-signal)
   - PR cross-link hints: lines like `· PRs: [#N](url)` on JIRA rows. Useful as a starting set of repos/numbers, but treat as advisory — the authoritative JIRA list comes from the live JQL fetch below, not the daily note. The note's JIRA snapshot may be hours stale; **always prefer the fresh JQL result** when the two disagree.
2. **Live assigned JIRA tickets** — call `mcp__plugin_fbg-core_atlassian__searchJiraIssuesUsingJql` with JQL like:

   ```
   assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC
   ```

   This catches tickets added or re-assigned throughout the day that won't be in today's daily note. Capture each result's key, status, summary, priority, and updated timestamp. This list — not the daily note — is the source of truth for "what JIRA work is on my plate right now."
3. **Projects index** — `~/Documents/Work/projects/_projects-index.md`. Enumerate the **Active** section.
4. **Wiki log tail** — `~/Documents/Work/wiki/_log.md`. Read the last ~80 lines. Look for recent compile entries with unresolved follow-ups, `Step failures`, or themes that keep recurring.

### Step 3: For each active project, read its state

Issue Read calls for both files of each active project in parallel:

- `~/Documents/Work/projects/<name>/README.md` — pull `due_date`, `next_action`, `status`, and any unchecked success-criteria checkboxes.
- `~/Documents/Work/projects/<name>/log.md` — focus on the top ~80 lines (newest entries are first). Look for unfinished sub-tasks, "follow-up", "TODO", "blocked on", "awaiting", or in-progress markers.

### Step 4: Synthesize

Apply these heuristics in narrative form. **No numeric scoring** — read between the lines.

- **Overdue / due soon** — projects whose `due_date` is past or within 7 days of today.
- **Open follow-ups from recent work** — items mentioned in the last few daily-note sections / project logs / wiki log entries that don't have a "Done", "Closed", or "Merged" marker.
- **Stale projects worth touching** — Active projects whose `log.md` hasn't been updated in 14+ days.
- **In-progress JIRA** — from the fresh JQL fetch in Step 2. A ticket with status `In Progress` / `In Review` and no matching recent activity in any log is a strong signal — it's been forgotten.
- **Ready to close — work shipped, ticket still open** — JIRA tickets from the fresh JQL fetch whose linked PRs are all MERGED (no still-open PR pointing at the same key). Detected via the live cross-check in Step 5 (see below); the LLM should treat any candidate carrying this signal as a top-priority quick-win, since closing it is a 30-second status transition that clears the board. Don't rely on the daily note's `🟢 Potential to close` marker — recompute from live JIRA + PR state because tickets get added/moved throughout the day.

When picking the top recommendation:

- Prefer items that show up in **two or more** of the buckets above.
- If "Ready to close" candidates exist and nothing is strictly overdue or actively blocked, lead with one of them. Phrasing: "Close FANDEVX-2920 — PR #2227 merged on 2026-05-17 and the ticket is still In Progress. 30-second admin win, then move on to …"

### Step 5: Verify live status before recommending

Before printing, verify the live status of any JIRA ticket or GitHub PR you plan to name in the recommendation. The daily note can be hours stale; recommending work on a ticket that has moved to Done or a PR that has merged or been closed wastes the user's time.

**Only verify the candidates named in the output** — the top pick plus any runner-up mentioned in the lead paragraph. Typically 1–3 verification calls. The triage sections below the recommendation can quote the daily note's snapshot without re-verifying. Issue all verification calls in parallel (single assistant message, multiple tool uses).

**JIRA ticket verification** — if the candidate references a key like `FANDEVX-1234`, `FESFEAT-5678`, or any `[A-Z]+-\d+` pattern:

- Call `mcp__plugin_fbg-core_atlassian__getJiraIssue` with the key.
- If status is **Done / Closed / Cancelled / Won't Do**, drop and pick the next candidate. Mention what changed ("FANDEVX-2920 is actually Done as of this morning — moving on to …").
- If **re-assigned to someone else**, drop and pick the next candidate.
- If genuinely still open (In Progress / In Review / To Do), include the **live** status in the reasoning.
- **Ready-to-close cross-check** — call `mcp__plugin_fbg-core_atlassian__getJiraIssueRemoteIssueLinks` to list the ticket's linked PRs. For each linked PR URL, run `gh pr view` (per the GitHub PR verification block below) to get its `state`. If the ticket is still open but **every** linked PR is `MERGED` (and there is at least one), flag this candidate as **ready to close** and lead the recommendation with "Close <KEY> — work shipped". If `getJiraIssueRemoteIssueLinks` returns no PR links, fall back to scanning the ticket description / comments returned by `getJiraIssue` for `github.com/.../pull/\d+` URLs.

**GitHub PR verification** — if the candidate is a PR (URL like `github.com/<owner>/<repo>/pull/<n>` or `#<n>` cited from the daily note's Open Pull Requests section):

- Ensure the correct identity is active: `gh auth switch --user ian-at-fes` (for any `fanatics-gaming` org repo). Then fetch with:

  ```bash
  gh pr view <number> --repo <owner>/<repo> \
    --json state,isDraft,reviewDecision,mergeable,mergeStateStatus,statusCheckRollup,updatedAt,headRefName
  ```

- If `state` is **MERGED** or **CLOSED**, drop and pick the next candidate. Mention what changed ("PR #2227 already merged — the recommendation is now the follow-up on …").
- If `isDraft` is true, keep it as a candidate only if the action is "finish the draft"; otherwise prefer a non-draft PR or another candidate.
- If `reviewDecision` is **APPROVED** and `mergeable` is **MERGEABLE** with all checks green (`statusCheckRollup` all SUCCESS) → the recommendation should be "merge this PR" not "keep working on it."
- If `reviewDecision` is **CHANGES_REQUESTED**, the recommendation should be "address review feedback on PR #N."
- If `statusCheckRollup` shows FAILURE / PENDING for blocking checks, surface that in the reasoning ("CI failing on PR #N — fix that before moving on").

If the MCP/gh call fails (network, auth, transient), continue with the daily-note snapshot but add a one-line caveat in the Quick context section ("JIRA/PR status check failed — recommendation based on daily-note snapshot only.").

If the recommendation is **not** a JIRA ticket or PR (e.g. "finish the team-proposal.md draft", "compile the open follow-ups from yesterday's wiki log"), skip this step entirely.

### Step 6: Print the recommendation

Output goes to the terminal only — no file writes. Use this format:

```markdown
## Recommended next: <single clear pick>

<2–3 sentences explaining why — cite the specific signal: due date, in-progress ticket, follow-up note, log entry. Mention a runner-up in the last sentence if there's a close second.>

---

### Ready to close — work shipped, ticket still open
- **[KEY](url)** — <title> · PR <#N> merged <date> · suggest transition to Done

### Overdue / Due Soon
- **<Project>** — due <date> · <one-line state>

### Open Follow-ups
- **<Project or wiki entry>** — <follow-up description> (from <source>, <date>)

### Stale — worth touching
- **<Project>** — last log entry <date> (<N days ago>)

### Quick context
- Daily note: <fresh / N days stale / missing>
- Active projects: <count>
- Open PRs in daily note: <count>
- In-progress JIRA tickets: <count>
```

**Omit any section that has no items** — don't print empty headers. If there's nothing urgent at all, say so plainly ("Nothing overdue, no open follow-ups surfaced. Suggested pick is the project with the most open success-criteria items: …").

## Error Handling

- **Daily note missing entirely (no fallback found):** proceed with only `projects/` + `wiki/_log.md`. Note the absence in the Quick context section.
- **`obsidian` CLI absent:** silently fall back to the manually-constructed path; do not error.
- **Projects index missing or empty Active section:** print a single line ("No active projects in `_projects-index.md`.") and emit only the wiki / daily-note view.
- **Empty wiki log tail:** skip wiki-derived follow-ups, continue.

Never write to disk. Never modify the daily note. Live calls allowed: `searchJiraIssuesUsingJql` (once, in Step 2, for assigned-open tickets), `getJiraIssue` and `getJiraIssueRemoteIssueLinks` (Step 5, only for candidates being recommended), and `gh pr view` (Step 5, only for PRs being recommended or linked from a recommended ticket). No live Todoist calls — those come from the daily note.

## Related Skills

- `/start-of-day` — populates today's daily note (open PRs, JIRA, Todoist). Run this first if it hasn't been today.
- `/daily-standup` — formal standup prep with blocker prompts.
- `/end-of-day` — closes out the day and flushes to the wiki log.
- `/start-ticket` — kick off work on a specific JIRA ticket (use after `/what-next` if the recommendation is a ticket).
