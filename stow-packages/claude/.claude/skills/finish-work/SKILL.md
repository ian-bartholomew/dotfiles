---
name: finish-work
description: Close out a JIRA ticket inferred from the current branch — transition based on PR state, capture learnings into project documents, and optionally clean up the worktree.
arguments: []
---

# Finish Work Workflow

You are closing out the work tied to the current git branch. This is the bookend to `/start-ticket`: it transitions the JIRA ticket, captures learnings into the right project documents, and optionally cleans up the worktree.

Follow these steps in order. Each step has explicit branching logic — do not skip or reorder them.

## Step 1: Detect the ticket key from the branch

- Run `git rev-parse --abbrev-ref HEAD`.
- Extract the ticket key with regex `^[A-Z]+-\d+`. This handles both `FANDEVX-2926/foo` and `FANDEVX-2926-foo` formats.
- If no match, stop with the message: `Branch '<name>' does not start with a JIRA ticket key. Aborting.`

## Step 2: Confirm GitHub identity

- Run `gh auth status`.
- If the active account is not `ian-at-fes`, instruct the user to switch (`gh auth switch -u ian-at-fes`) before continuing. Do not auto-switch — the user's CLAUDE.md mandates explicit identity management.

## Step 3: Fetch ticket and PR state in parallel

- JIRA: use the Atlassian MCP server (`getJiraIssue` and `getTransitionsForJiraIssue`) to fetch the ticket summary, current status, and available transitions.
- PR: `gh pr view --json state,merged,url,headRefName` on the current branch. If `gh pr view` exits non-zero, treat it as "no PR".
- Display a short summary to the user: ticket key, summary, current JIRA status, PR state (merged / open / closed-unmerged / none) with URL when present.

## Step 4: Determine and execute the JIRA transition

Smart default based on PR state:

| PR state           | Target transition |
|--------------------|-------------------|
| merged             | Done              |
| open               | In Review         |
| closed (unmerged)  | ask user          |
| no PR              | ask user          |

- Show the user the chosen target transition (or the prompt for "closed-unmerged" / "no PR") alongside the available transitions list from JIRA. Confirm before executing.
- Execute the transition via the Atlassian MCP server (`transitionJiraIssue`).
- If the transition fails (e.g., required field missing), surface the error and prompt the user — do not silently swallow.

## Step 5: Find the project folder

- Run `grep -l "<TICKET-KEY>" ~/Documents/Work/projects/*/log.md` (case-sensitive, exact ticket key).
- Exactly 1 match: use that project.
- 0 matches or 2+ matches: present the user with the full list of subdirectories under `~/Documents/Work/projects/` plus a "none — skip project updates" option, and let them pick.

If the user picks "none", skip Steps 6 and 7 and continue to Step 8.

## Step 6: Gather learnings sources in parallel

- **Conversation context**: synthesize from the current Claude session — what was tried, what failed, what worked, decisions made, dead ends.
- **Git log**: determine the base branch (`main` for most repos; use `git symbolic-ref refs/remotes/origin/HEAD` if uncertain), then run `git log <base-branch>..HEAD --pretty=format:'%h %s%n%b'` to capture concrete shipped commits.
- If the session is fresh and has no conversation context (e.g., running `/finish-work` cold the next morning), fall back to git-log-only and tell the user the log entry will be sparse.

## Step 7: Draft and apply project document updates

For each of the following documents, draft an addition, show it as a diff, and apply only on user approval. Use a separate approval prompt for each — never bundle them.

### `log.md` — always

Append one dated entry per finish, using this structure:

```
## YYYY-MM-DD — <ticket-key>: <ticket summary>

**Outcome:** <merged / in-review / closed-unmerged / etc.>

**What shipped:**
- <bullets from git log>

**Learnings:**
- <bullets from conversation context>
```

### `decisions.md` — only if applicable

Only update if an architectural choice surfaced during the session. Append a dated entry with: decision, alternatives considered, rationale.

### `todos-and-followups.md` — only if applicable

Only update if new follow-up items emerged. Append as a bulleted list under a dated heading.

### Missing files

If a target doc doesn't exist in the project folder (e.g., no `decisions.md`), create it with a minimal markdown header (`# Decisions`, `# Todos and Follow-ups`) before appending.

## Step 8: Worktree cleanup

- Detect worktree state: parse `git worktree list --porcelain` and compare to the current working directory.
- If the current directory is the main checkout (not a worktree): skip silently.
- If on a worktree:
  1. **Check for uncommitted changes**: run `git status --porcelain`. If non-empty, warn the user and ask whether to abort cleanup or discard changes.
  2. **Decide based on PR state**:
     - **PR merged OR closed-unmerged**: prompt `Clean up worktree '<path>' and delete branch '<name>'? [y/n]`. On `y`, change directory out of the worktree to the main checkout path (from `git worktree list`), then run `git worktree remove <path>` and `git branch -D <name>`. Prefer the `ExitWorktree` tool if available.
     - **PR still open**: skip cleanup. Tell the user the worktree was left in place because the PR is still open.

## Step 9: Final summary

Print a recap:

- **Ticket**: transitioned `<from>` → `<to>` (with JIRA URL)
- **Project updates**: list of files modified, or "skipped"
- **Worktree**: removed / left in place / not applicable

## What this skill does NOT do

- Does not push code, open PRs, or merge anything — use `commit-commands:commit-push-pr`.
- Does not run tests or verify the work — use `superpowers:verification-before-completion`.
- Does not bulk-clean other worktrees — use `commit-commands:clean_gone`.
