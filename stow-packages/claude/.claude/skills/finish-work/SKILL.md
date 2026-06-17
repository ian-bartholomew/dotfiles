---
name: finish-work
description: Close out a JIRA ticket — inferred from the current branch, or targeted explicitly via a ticket key or PR number. Transitions JIRA based on PR state, captures learnings into project documents, and optionally cleans up the worktree and branch.
arguments:
  - name: target
    description: Optional JIRA ticket key (e.g. FANDEVX-1234) or GitHub PR number (e.g. 1234). If omitted, the ticket is inferred from the current branch.
    required: false
---

# Finish Work Workflow

You are closing out the work tied to a JIRA ticket. This is the bookend to `/start-ticket`: it transitions the JIRA ticket, captures learnings into the right project documents, and optionally cleans up the worktree and branch.

The skill operates in two modes:

- **No-arg mode** (`/finish-work`): infers the ticket from the current branch. Use this from inside the worktree where the work happened.
- **Targeted mode** (`/finish-work <target>`): targets a specific ticket via JIRA key or PR number. Use this when you've already moved off the branch — e.g. PR merged overnight, branch auto-deleted, and you're on `main` the next morning.

Follow these steps in order. Each step has explicit branching logic — do not skip or reorder them.

## Step 0: Pre-Flight Verification (MANDATORY)

Run these four checks before resolving the ticket or touching any state. `/finish-work` transitions a JIRA ticket and may delete a branch / worktree — both are state-changing operations that the user's CLAUDE.md explicitly demands be gated on live verification of JIRA + PR + git state. **Fail loudly and halt if any check cannot execute.** Do not proceed on stale local state.

### 1. Sync local git state

This skill always runs inside a git repo (Step 1 already enforces that for Case B; Case A reads the current branch). Sync the default branch and check staleness:

```bash
git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { echo "FATAL: /finish-work must be run inside a git repository. Halting."; exit 1; }
git fetch origin --quiet \
  || { echo "FATAL: git fetch origin failed — cannot verify branch / PR state. Halting."; exit 1; }
DEFAULT_BRANCH="$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')"
DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"
BEHIND="$(git rev-list --count "$DEFAULT_BRANCH..origin/$DEFAULT_BRANCH" 2>/dev/null || echo 0)"
[ "$BEHIND" -gt 0 ] && echo "NOTE: local $DEFAULT_BRANCH is $BEHIND commits behind origin/$DEFAULT_BRANCH — pull before starting new work."
```

The `git fetch` is critical here: PR state (merged / open / closed) drives the JIRA transition decision in Step 4 and the cleanup decision in Step 8. A stale local view of the PR will produce the wrong transition.

### 2. GitHub identity must be `ian-at-fes`

```bash
gh auth status 2>&1 | grep -q "Active account: true" \
  && gh auth status 2>&1 | grep -q "ian-at-fes" \
  || { echo "FATAL: gh CLI is not authed as ian-at-fes. Run 'gh auth switch -u ian-at-fes' and re-run /finish-work. Halting."; exit 1; }
```

Do **not** auto-switch — the user's CLAUDE.md mandates explicit identity management. Halt and ask the user to switch.

### 3. Live JIRA status for the target ticket

Step 1 resolves the ticket key. Before transitioning anything in Step 4, the ticket's **live** status and available transitions must be fetched via `mcp__plugin_fbg-core_atlassian__getJiraIssue` + `mcp__plugin_fbg-core_atlassian__getTransitionsForJiraIssue`. If either call fails, halt:

```
FATAL: JIRA MCP unavailable — cannot verify <KEY> status or fetch transitions. Halting.
```

Do not transition a ticket based on the assumed status — re-fetch it. Tickets get moved by other people between when the branch was created and when `/finish-work` runs.

### 4. Live PR merge state via `gh pr view`

The PR state drives the JIRA transition in Step 4 (merged → Done, open → In Review) and the cleanup branching in Step 8. Step 3 already calls `gh pr view`; that call must succeed when a PR exists. If `gh pr view` returns a non-zero exit code **and** other evidence (branch name, ticket-key search) suggests a PR should exist, halt:

```
FATAL: gh pr view failed for <branch or PR #N> — cannot determine merge state. Halting.
```

The exception: a genuinely PR-less branch (Case A on a local-only branch, or Case B with `gh pr list` returning zero matches) is fine — that's "no PR", not "verification failed". Step 3 distinguishes these.

## Step 1: Resolve the ticket key and branch

**Case A — no argument provided:**

- Run `git rev-parse --abbrev-ref HEAD` to get `<branch>`.
- Extract the ticket key with regex `^[A-Z]+-\d+`. This handles both `FANDEVX-2926/foo` and `FANDEVX-2926-foo` formats.
- If no match, stop with: `Branch '<name>' does not start with a JIRA ticket key. Aborting.`
- `<branch>` = current branch.

**Case B — argument provided:**

- Verify cwd is inside a git repo: `git rev-parse --is-inside-work-tree`. If not, stop with: `/finish-work <target> must be run from inside a git repository. Aborting.`
- If `<target>` matches `^[A-Z]+-\d+$`: ticket key = `<target>`. Branch resolution is deferred to Step 3 (resolved from the PR's `headRefName`).
- If `<target>` matches `^\d+$`: run `gh pr view <target> --json number,state,merged,url,headRefName,title`. Extract the ticket key from `headRefName` via `^[A-Z]+-\d+`. If no match, stop with: `PR #<num> branch '<headRef>' does not start with a JIRA ticket key. Aborting.` Cache the PR JSON for reuse in Step 3.
- Anything else: stop with `Argument '<value>' is neither a JIRA key (e.g. FANDEVX-1234) nor a PR number (e.g. 1234). Aborting.`

## Step 2: Confirm GitHub identity

GitHub identity is already verified by Step 0's pre-flight block — if you reach this step the active `gh` account is `ian-at-fes`. This step is retained as an explicit checkpoint; re-confirm if any earlier step has been skipped or run manually out of order.

- Run `gh auth status`.
- If the active account is not `ian-at-fes`, halt and instruct the user to switch (`gh auth switch -u ian-at-fes`) before re-running. Do not auto-switch — the user's CLAUDE.md mandates explicit identity management.

## Step 3: Fetch ticket and PR state in parallel

JIRA: use the Atlassian MCP server (`getJiraIssue` and `getTransitionsForJiraIssue`) to fetch the ticket summary, current status, and available transitions.

PR resolution depends on mode:

- **Case A (no arg):** `gh pr view --json state,merged,url,headRefName` on the current branch. If `gh pr view` exits non-zero, treat it as "no PR".
- **Case B, PR-number arg:** reuse the JSON cached in Step 1.
- **Case B, ticket-key arg:** `gh pr list --search "<ticket-key>" --state all --json number,state,merged,url,headRefName,title --limit 5`. Filter to PRs whose `headRefName` starts with `<ticket-key>`.
  - 0 matches → treat as "no PR".
  - 1 match → use it.
  - 2+ matches → display the list (number, title, state, branch) and ask the user to pick one.

Store the resolved `<branch>` = `headRefName` from the PR for use in Steps 6 and 8. In Case B with no PR, prompt the user for the branch name (or accept that branch-dependent steps will be skipped).

Display a short summary to the user: ticket key, summary, current JIRA status, PR state (merged / open / closed-unmerged / none) with URL when present.

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

## Step 4.5: Update the work-board card

After the JIRA transition, update the ticket's Todoist card directly (no full sync):

- Find it: `td task list --project "Work" --json --all` filtered to content starting with
  `<TICKET-KEY>`. No card -> skip silently (not all tickets have cards).
- Transitioned to Done (or any done-category status): `td task complete id:<card_id>`.
- Transitioned to In Code Review: `td task move id:<card_id> --section "In Review"`, then
  `td task update id:<card_id> --description "<existing description with the sync: line's
  jira-status replaced by the new status and synced set to today>"`.
- Report the card action in the Step 9 final summary.

## Step 5: Find the project folder

- Run `grep -l "<TICKET-KEY>" ~/Documents/Work/projects/*/log.md` (case-sensitive, exact ticket key).
- Exactly 1 match: use that project.
- 0 matches or 2+ matches: present the user with the full list of subdirectories under `~/Documents/Work/projects/` plus a "none — skip project updates" option, and let them pick.

If the user picks "none", skip Steps 6 and 7 and continue to Step 8.

## Step 6: Gather learnings sources in parallel

- **Conversation context**: synthesize from the current Claude session — what was tried, what failed, what worked, decisions made, dead ends.
- **Shipped commits** — pick the first source that works, in this order:
  1. **Local branch exists** (`git rev-parse --verify <branch>` succeeds): determine the base branch (`main` for most repos; use `git symbolic-ref refs/remotes/origin/HEAD` if uncertain), then run `git log <base-branch>..<branch> --pretty=format:'%h %s%n%b'`. In no-arg mode `<branch>` is `HEAD`.
  2. **PR exists** (any state, includes merged with deleted branch): `gh pr view <pr-num> --json commits --jq '.commits[] | "\(.oid[0:7]) \(.messageHeadline)\n\(.messageBody // "")"'`. This is the critical fallback for the "PR merged overnight, branch auto-deleted" scenario.
  3. **Neither**: fall back to `gh pr view <pr-num> --json title,body` (PR title + body). If there's no PR either, note "no shipped-commit source available" in the log entry.
- If the session is fresh and has no conversation context (e.g., running `/finish-work` cold the next morning), fall back to the shipped-commits source only and tell the user the log entry will be sparse.

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

## Step 8: Worktree and branch cleanup

Detect cleanup state:

- Parse `git worktree list --porcelain` and find a worktree whose branch matches `<branch>` (resolved in Steps 1/3). Note whether that worktree is the current working directory.
- Check `git rev-parse --verify <branch>` to see if the local branch still exists at all.

Then act based on the combination:

| Worktree | Local branch | PR state | Action |
|---|---|---|---|
| current dir | n/a | merged / closed-unmerged | Existing flow: uncommitted-changes check, then prompt `Clean up worktree '<path>' and delete branch '<name>'? [y/n]`. On `y`, `cd` out to the main checkout (from `git worktree list`), `git worktree remove <path>`, `git branch -D <branch>`. Prefer `ExitWorktree` if available. |
| current dir | n/a | open | Skip. Tell the user the worktree was left in place because the PR is still open. |
| other dir | n/a | merged / closed-unmerged | Uncommitted-changes check inside that worktree path, then prompt `Clean up worktree '<path>' and delete branch '<name>'? [y/n]`. On `y`, `git worktree remove <path>` and `git branch -D <branch>` from the current working directory. No `cd` needed. |
| other dir | n/a | open | Skip with a note that the worktree was left in place because the PR is still open. |
| none | yes | merged / closed-unmerged | Prompt `Local branch '<name>' still exists. Delete it? [y/n]`. On `y`, `git branch -D <branch>`. |
| none | yes | open | Skip. Branch is still active. |
| none | no | any | Skip silently — nothing to clean up. |

Uncommitted-changes check (`git status --porcelain` against the worktree path) only applies when a worktree exists. If non-empty, warn the user and ask whether to abort cleanup or discard changes before proceeding.

## Step 9: Final summary

Print a recap:

- **Ticket**: transitioned `<from>` → `<to>` (with JIRA URL)
- **Project updates**: list of files modified, or "skipped"
- **Cleanup**: worktree removed / worktree left in place / branch deleted / nothing to clean up

## What this skill does NOT do

- Does not push code, open PRs, or merge anything — use `commit-commands:commit-push-pr`.
- Does not run tests or verify the work — use `superpowers:verification-before-completion`.
- Does not bulk-clean other worktrees — use `commit-commands:clean_gone`.
