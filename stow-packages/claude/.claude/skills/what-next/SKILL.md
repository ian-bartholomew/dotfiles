---
name: what-next
description: This skill should be used when the user asks "what next", "what-next", "what should I work on", "what's next", or otherwise wants a recommendation for the next piece of work to pick up. Synthesizes across active projects, project logs, the wiki log, and today's daily note to surface a single recommended task plus a triage view of overdue work, open follow-ups, and stale projects.
version: 0.2.0
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
  /verify-status   →   Read (parallel)         →   LLM synthesizes        →   Print to terminal
  (always first)       - today's daily note        recommendation             (no file writes)
                       - projects index +          - single pick + reasoning
                         each active project's     - themed sections grouped
                         README.md and log.md      by urgency
                       - wiki/_log.md tail
```

## Prerequisites

- `/start-of-day` should ideally have been run today so the daily note is populated. If it hasn't, the skill notes that and continues with the other two sources.
- `obsidian` CLI available for resolving the daily note path. If absent, fall back to constructing the path from `date`.

## Step 0: Run `/verify-status` FIRST (MANDATORY, ALWAYS)

**The first action of this skill — every single run, no exceptions — is to invoke the `verify-status` skill before doing anything else.** This produces the verified live snapshot of repo state, in-progress / blocked JIRA, and open PRs (mine + needing review) that every downstream step in `/what-next` depends on. Skipping it means recommending work off stale data, which the user's CLAUDE.md explicitly forbids.

Invoke it via the Skill tool with `skill: "verify-status"` as the very first tool call of this skill's execution. Wait for it to complete. Feed its output into the synthesis steps below — treat it as the authoritative live snapshot, overriding anything in the daily note or local git state that disagrees.

If `/verify-status` halts or errors, **do not proceed** with a recommendation. Surface the failure to the user and stop. Do not fall back to unverified sources.

Only after `/verify-status` returns cleanly do you continue to the pre-flight checks and the rest of the pipeline below.

## Pre-Flight Verification (MANDATORY)

Run these four checks **before printing any recommendation or naming any specific ticket / PR**. The user's CLAUDE.md explicitly forbids relying on stale README files, daily notes, or local git state — this block enforces that. **Fail loudly and halt if any check cannot execute.** Do not emit a recommendation built on unverified data.

### 1. Sync local git state (when in a repo)

If `git rev-parse --is-inside-work-tree` succeeds, sync the default branch and surface staleness:

```bash
git fetch origin --quiet \
  || { echo "FATAL: git fetch origin failed — cannot verify branch state. Halting."; exit 1; }
DEFAULT_BRANCH="$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')"
DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"
BEHIND="$(git rev-list --count "$DEFAULT_BRANCH..origin/$DEFAULT_BRANCH" 2>/dev/null || echo 0)"
[ "$BEHIND" -gt 0 ] && echo "NOTE: local $DEFAULT_BRANCH is $BEHIND commits behind origin/$DEFAULT_BRANCH — surface in Quick context."
```

If cwd is **not** inside a git repo, that is expected (`/what-next` often runs from a notes-only directory). Skip with a `git checks skipped — not in a repo` line in the Quick context section. This is the only "not applicable" branch — every other check below is unconditional.

### 2. GitHub identity must be `ian-at-fes`

```bash
gh auth status 2>&1 | grep -q "Active account: true" \
  && gh auth status 2>&1 | grep -q "ian-at-fes" \
  || { echo "FATAL: gh CLI is not authed as ian-at-fes. Run 'gh auth switch -u ian-at-fes'. Halting."; exit 1; }
```

### 3. Live JIRA status for every referenced ticket

For every JIRA key cited anywhere in the printed output — top pick, runner-up, or any triage bullet — call `mcp__plugin_fbg-core_atlassian__getJiraIssue` and use the **live** status. The daily-note JIRA section is a snapshot and may be hours stale; do not name a ticket without re-querying its current status.

If the MCP call fails for a ticket you plan to name, halt with:

```
FATAL: JIRA MCP unavailable — cannot verify <KEY>. Halting.
```

Do not fall back to the daily-note snapshot. Failure here is loud.

### 4. Live PR merge state for every referenced PR

For every PR cited in the output:

```bash
gh pr view <N> --repo <owner>/<repo> \
  --json state,isDraft,author,reviewDecision,latestReviews,mergeable,mergeStateStatus,statusCheckRollup,updatedAt,headRefName
```

If the call fails for a PR you intend to name, halt with:

```
FATAL: gh pr view failed for PR #N (<owner>/<repo>) — cannot verify merge state. Halting.
```

The detailed interpretation of these responses — drop-and-replace logic, `latestReviews` semantics, ready-to-close detection, approval handling — lives in **Step 5**. This pre-flight block establishes the contract that the calls **must happen and must succeed**; Step 5 covers how to act on the results.

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
5. **Work kanban board** — `td task list --project "Work" --json --all`, bucketed by section (`td section list "Work"` for the id->name map). Columns: Backlog | Next Up | In Progress | In Review | Waiting on Others | Blocked. This is the user's own prioritization signal; the work-board sync keeps JIRA-linked cards honest, manual cards reflect deliberate intent.

### Step 3: For each active project, read its state

Issue Read calls for both files of each active project in parallel:

- `~/Documents/Work/projects/<name>/README.md` — pull `due_date`, `next_action`, `status`, and any unchecked success-criteria checkboxes.
- `~/Documents/Work/projects/<name>/log.md` — focus on the top ~80 lines (newest entries are first). Look for unfinished sub-tasks, "follow-up", "TODO", "blocked on", "awaiting", or in-progress markers.

### Step 4: Synthesize

**Board-derived signals (from source 5):**

- **In Progress cards are claimed active work.** A manual In Progress card with no matching evidence in any log or the daily note is drift — surface it ("board says X is in progress; no trace of it this week — still real?") rather than silently recommending around it.
- **Next Up is the user's own queue.** When picking among otherwise-equal candidates, prefer ones with a Next Up card — the user already chose them.
- **Waiting on Others / Blocked cards are NOT candidates.** Never recommend them as the next pick; at most mention what they're waiting on.
- Bare `stale` findings from the work-board dry-run (if recent end-of-day output is available) are triage items, not picks.

Apply these heuristics in narrative form. **No numeric scoring** — read between the lines.

- **Overdue / due soon** — projects whose `due_date` is past or within 7 days of today.
- **Open follow-ups from recent work** — items mentioned in the last few daily-note sections / project logs / wiki log entries that don't have a "Done", "Closed", or "Merged" marker.
- **Stale projects worth touching** — Active projects whose `log.md` hasn't been updated in 14+ days.
- **In-progress JIRA** — from the fresh JQL fetch in Step 2. A ticket with status `In Progress` / `In Review` and no matching recent activity in any log is a strong signal — it's been forgotten.
- **Ready to close — work shipped, ticket still open** — JIRA tickets from the fresh JQL fetch whose linked PRs are all MERGED (no still-open PR pointing at the same key). Detected via the live cross-check in Step 5 (see below); the LLM should treat any candidate carrying this signal as a top-priority quick-win, since closing it is a 30-second status transition that clears the board. Don't rely on the daily note's `🟢 Potential to close` marker — recompute from live JIRA + PR state because tickets get added/moved throughout the day.

When picking the top recommendation:

- Prefer items that show up in **two or more** of the buckets above.
- If "Ready to close" candidates exist and nothing is strictly overdue or actively blocked, lead with one of them. Phrasing: `Close FANDEVX-2920 https://fanatics.atlassian.net/browse/FANDEVX-2920 — PR #2227 https://github.com/fanatics-gaming/<repo>/pull/2227 merged on 2026-05-17 and the ticket is still In Progress. 30-second admin win, then move on to …`

### Step 5: Verify live status before recommending

**MANDATORY — never suggest a JIRA ticket or PR without first verifying its current status.** The daily note and JQL results can be minutes-to-hours stale; recommending work on a ticket that has moved to Done, been re-assigned, or a PR that has merged/closed wastes the user's time and erodes trust in the skill's output. No exceptions: if a JIRA key or PR appears anywhere in the printed recommendation (top pick, runner-up, or any triage bullet that names a specific ticket/PR as actionable), it must be verified live in this step first.

Verify the live status of every JIRA ticket and GitHub PR you plan to name in the output — the top pick, any runner-up in the lead paragraph, and any specific ticket/PR cited in the triage sections as a suggested next action. Typically 1–5 verification calls. Pure count rows in Quick context (e.g. "Open PRs: 4") don't require per-item verification. Issue all verification calls in parallel (single assistant message, multiple tool uses).

If verification reveals the item is closed/merged/re-assigned, drop it from the recommendation and pick the next candidate — then verify that one too. Do not print an unverified candidate as a fallback.

**JIRA ticket verification** — if the candidate references a key like `FANDEVX-1234`, `FESFEAT-5678`, or any `[A-Z]+-\d+` pattern:

- Call `mcp__plugin_fbg-core_atlassian__getJiraIssue` with the key.
- If status is **Done / Closed / Cancelled / Won't Do**, drop and pick the next candidate. Mention what changed (`FANDEVX-2920 https://fanatics.atlassian.net/browse/FANDEVX-2920 is actually Done as of this morning — moving on to …`).
- If **re-assigned to someone else**, drop and pick the next candidate.
- If genuinely still open (In Progress / In Review / To Do), include the **live** status in the reasoning.
- **Ready-to-close cross-check** — call `mcp__plugin_fbg-core_atlassian__getJiraIssueRemoteIssueLinks` to list the ticket's linked PRs. For each linked PR URL, run `gh pr view` (per the GitHub PR verification block below) to get its `state`. If the ticket is still open but **every** linked PR is `MERGED` (and there is at least one), flag this candidate as **ready to close** and lead the recommendation with "Close <KEY> — work shipped". If `getJiraIssueRemoteIssueLinks` returns no PR links, fall back to scanning the ticket description / comments returned by `getJiraIssue` for `github.com/.../pull/\d+` URLs.

**GitHub PR verification** — if the candidate is a PR (URL like `github.com/<owner>/<repo>/pull/<n>` or `#<n>` cited from the daily note's Open Pull Requests section):

- Ensure the correct identity is active: `gh auth switch --user ian-at-fes` (for any `fanatics-gaming` org repo). Then fetch with:

  ```bash
  gh pr view <number> --repo <owner>/<repo> \
    --json state,isDraft,author,reviewDecision,reviews,latestReviews,mergeable,mergeStateStatus,statusCheckRollup,updatedAt,headRefName
  ```

- If `state` is **MERGED** or **CLOSED**, drop and pick the next candidate. Mention what changed (`PR #2227 https://github.com/<owner>/<repo>/pull/2227 already merged — the recommendation is now the follow-up on …`).
- If `isDraft` is true, keep it as a candidate only if the action is "finish the draft"; otherwise prefer a non-draft PR or another candidate.
- **Approval check — always inspect reviews before suggesting action on a PR.** Look at `latestReviews` (one entry per reviewer, most recent state) to determine:
  - **The user (`ian-at-fes`) has already approved it.** If the PR is authored by someone else and `ian-at-fes` appears in `latestReviews` with state `APPROVED`, do not suggest "review this PR" — the user's review obligation is done. Either drop the PR from the recommendation or, if it's still the strongest candidate, reframe as a passive nudge (`PR #N <url> — you already approved; waiting on other reviewers / merge by author`).
  - **Someone else has already approved it.** If `latestReviews` contains any `APPROVED` state from another user, surface that in the reasoning (`PR #N <url> — already approved by <login>, …`) so the user knows the review work isn't theirs to repeat. For a PR authored by the user, this often promotes the recommendation to "merge it" (see below). For a PR authored by someone else, this often means the user can drop it from their queue.
  - **Nobody has approved yet and `ian-at-fes` is a requested reviewer.** This is a genuine "review this PR" candidate.
- If `reviewDecision` is **APPROVED** and `mergeable` is **MERGEABLE** with all checks green (`statusCheckRollup` all SUCCESS) → the recommendation should be `merge PR #N <url>` not "keep working on it." (Only applies when the PR is authored by the user — otherwise the author merges.)
- If `reviewDecision` is **CHANGES_REQUESTED**, the recommendation should be `address review feedback on PR #N <url>` (only if the user is the author).
- If `statusCheckRollup` shows FAILURE / PENDING for blocking checks, surface that in the reasoning (`CI failing on PR #N <url> — fix that before moving on`).

If the MCP/gh call fails for a candidate you intend to name, **halt loudly** per the Pre-Flight Verification block at the top of the skill — do not fall back to the daily-note snapshot for the recommendation. This is a deliberate change from earlier versions: silent fallback was found to surface tickets that had already moved to Done or PRs that had already merged, eroding trust in the output. Fail loud, fix the upstream issue (re-auth, retry the MCP), then re-run the skill.

If the recommendation is **not** a JIRA ticket or PR (e.g. "finish the team-proposal.md draft", "compile the open follow-ups from yesterday's wiki log"), skip this step entirely.

### Step 6: Print the recommendation

Output goes to the terminal only — no file writes. Use this format:

```markdown
## Recommended next: <single clear pick>

<2–3 sentences explaining why — cite the specific signal: due date, in-progress ticket, follow-up note, log entry. Mention a runner-up in the last sentence if there's a close second.>

---

### Ready to close — work shipped, ticket still open
- **KEY** <jira-url> — <title> · PR #N <pr-url> merged <date> · suggest transition to Done

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
- Board: <n> Next Up · <n> In Progress · <n> Waiting/Blocked
```

**Linking rule — every JIRA key and PR reference in the output must be followed by its full URL as plain text.** Output renders in a terminal, so do NOT use markdown link syntax (`[text](url)`) — most terminals don't render it, and the brackets/parens become visual noise. Print the bare URL after the identifier; modern terminals will auto-detect it as clickable.

This applies everywhere JIRA keys and PRs appear: the recommendation paragraph, the runner-up mention, and every triage section bullet.

- **JIRA tickets:** `FANDEVX-1234 https://fanatics.atlassian.net/browse/FANDEVX-1234`. Use the same URL pattern for any project key (FANDEVX, FESFEAT, etc.).
- **GitHub PRs:** `PR #2227 https://github.com/<owner>/<repo>/pull/2227`. Prefer the URL captured from the daily note's `· PRs: [#N](url)` hints or from `gh pr view` in Step 5; never invent a repo. If the repo cannot be determined for a PR cited in the daily note, fall back to the raw URL from the daily note as-is.
- Bare `FANDEVX-1234` or `#2227` mentions without an accompanying URL are not allowed in the printed output.

**Omit any section that has no items** — don't print empty headers. If there's nothing urgent at all, say so plainly ("Nothing overdue, no open follow-ups surfaced. Suggested pick is the project with the most open success-criteria items: …").

## Error Handling

- **Daily note missing entirely (no fallback found):** proceed with only `projects/` + `wiki/_log.md`. Note the absence in the Quick context section.
- **`obsidian` CLI absent:** silently fall back to the manually-constructed path; do not error.
- **Projects index missing or empty Active section:** print a single line ("No active projects in `_projects-index.md`.") and emit only the wiki / daily-note view.
- **Empty wiki log tail:** skip wiki-derived follow-ups, continue.

Never write to disk. Never modify the daily note. Live calls allowed: `searchJiraIssuesUsingJql` (once, in Step 2, for assigned-open tickets), `getJiraIssue` and `getJiraIssueRemoteIssueLinks` (Step 5, only for candidates being recommended), and `gh pr view` (Step 5, only for PRs being recommended or linked from a recommended ticket). No live Todoist calls — those come from the daily note.

## Related Skills

- `/verify-status` — **always invoked first** (see Step 0). Produces the verified live snapshot of repos, JIRA, and PRs that everything else here depends on.
- `/start-of-day` — populates today's daily note (open PRs, JIRA, Todoist). Run this first if it hasn't been today.
- `/daily-standup` — formal standup prep with blocker prompts.
- `/end-of-day` — closes out the day and flushes to the wiki log.
- `/start-ticket` — kick off work on a specific JIRA ticket (use after `/what-next` if the recommendation is a ticket).
