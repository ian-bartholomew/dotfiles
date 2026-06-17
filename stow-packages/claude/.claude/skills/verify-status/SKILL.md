---
name: verify-status
description: This skill should be used when the user needs a verified snapshot of live work state — current repo + project repos, JIRA In Progress/Blocked tickets, open GitHub PRs (mine + needing review) — reconciled against local project README/log drift, before recommending a next action. Triggers on "verify status", "where am I", "status check", "what's the state of my work", or runs "/verify-status". Honors the CLAUDE.md rule to verify live JIRA/PR status before suggesting next actions, and the ian-at-fes identity rule for fanatics-gaming repos.
version: 0.1.0
allowed-tools: [Bash, Read, Glob, mcp__plugin_fbg-core_atlassian__searchJiraIssuesUsingJql]
---

# Verify Status

Read-only skill that produces a verified live snapshot across git, JIRA, and GitHub, reconciles it against active project notes, and emits a single next-action recommendation. Replaces the ad-hoc "let me check git, then JIRA, then PRs, then your notes" walk that recurs whenever the user is about to pick up work or hand off.

## When to Use

- User asks "where am I", "verify status", "what's the state of my work"
- Before recommending a next action when stale local state (daily note, project README, log.md) is plausible
- After a context switch or returning from time off, before resuming work
- Any step that would otherwise rely on a README or daily note as ground truth for ticket/PR state

## When NOT to Use

- User wants a full triage view across all work — invoke `what-next` instead, which already synthesizes broader context
- User wants only PR status — invoke `pr-status-sweep` directly (this skill is broader and slower)
- User has already verified status this session and is asking a follow-up — don't re-run, reuse the prior snapshot

## Read-only Guarantee

This skill MUST NOT write to any file, push to any remote, transition any JIRA ticket, comment on any PR, or merge anything. All operations are read-only. If reconciliation surfaces drift in a README or log.md, *report* it — don't auto-edit.

## Pipeline

### 1. Resolve identity

```bash
gh auth status 2>&1 | sed -n '/Active account: true/{x;p;x;p;}'
```

For any repo under `fanatics-gaming/`, abort and switch if active account is not `ian-at-fes`:

```bash
gh auth switch --user ian-at-fes
```

Never use `--author=@me` — always pass `--author=ian-at-fes` explicitly.

### 2. Git state — current repo + active-work repos

**Current repo (if cwd is inside a git repo):**

```bash
git -C <repo> fetch origin --quiet
git -C <repo> status --short --branch
```

Report the branch, ahead/behind counts vs `origin/<default>`, and any uncommitted files.

**Active-work repos:** Local clones live under `~/Dev/<repo-name>/`. Project READMEs do NOT carry a canonical `repo:` / `path:` front-matter key — don't rely on one.

Filter `~/Dev/*` to the repos meeting any of these conditions (this is a union, not an intersection):

- Owns one of the open PRs from step 4 (mine or needing review)
- Named in a branch / repo reference on an open JIRA ticket from step 3 (e.g. branch matches `<TICKET-KEY>-*`)
- Named in a **recent** log entry (within the last 14 days) under any `~/Documents/Work/projects/*/log.md`

The 14-day window on the log condition matters: project logs accumulate historical narrative referencing repos that haven't been touched in months. Only count log entries dated within the last 14 days as evidence of active work. Mentions in `README.md` alone do not count — README is a static project description, not a recency signal.

This means steps 3 and 4 must run before this part of step 2 — the JIRA + GitHub results define the repo set. The `git fetch + status` calls themselves are still parallel within that set.

Do NOT `git fetch` every clone under `~/Dev/` — there are dozens, most are dormant. The active-work filter is what makes this step cheap.

For each repo in the active set, run the `fetch + status` pair in parallel. Skip directories where `git -C <repo> rev-parse --git-dir` returns non-zero.

Emit one line per repo:

```
<repo>  <branch>  ahead=<n> behind=<n>  dirty=<n files | clean>
```

### 3. JIRA — In Progress / Blocked assigned to me

Use the Atlassian MCP `searchJiraIssuesUsingJql` tool. Default JQL:

```
assignee = currentUser()
AND statusCategory != Done
AND status in ("In Progress", "Blocked", "In Review")
ORDER BY updated DESC
```

Request fields: `summary, status, priority, updated, parent, issuetype`. Cloud ID resolves via `getAccessibleAtlassianResources` if not already known this session.

Emit one line per ticket:

```
<KEY>  <status>  <summary>  updated <relative-time>
```

If a ticket's status is `Blocked`, also fetch and surface the most recent comment (last 7 days) so the blocker is visible without a second call.

### 4. GitHub — open PRs (mine + needing review)

**Mine:**

```bash
gh search prs --author=ian-at-fes --state=open \
  --json number,title,url,repository,isDraft,updatedAt --limit 100
```

**Needing my review:**

```bash
gh search prs --review-requested=ian-at-fes --state=open \
  --json number,title,url,repository,isDraft,updatedAt --limit 100
```

Do NOT add `reviewDecision` to the `--json` field list — `gh search prs` rejects it (the field is only valid on `gh pr view`). Review state comes from the per-PR drill-down below.

Merge the two lists, dedupe by URL, and decide which PRs to drill into.

**Drill-down rules:**

- Always `gh pr view` every PR I authored (the `--author=ian-at-fes` list).
- For the `--review-requested=ian-at-fes` list, filter before drilling:
  - Skip PRs authored by `dependabot[bot]`, `renovate[bot]`, or any `*[bot]` account
  - Skip PRs where `updatedAt` is older than 14 days (stale review asks rarely need action this turn)
  - Drill the rest

Report the filtered-out review-requested PRs as a single line at the end of the GitHub section (e.g. `(+ 12 dependabot/stale review requests not drilled)`) so the user knows they exist without spending a `gh pr view` per bot PR.

For each PR that survives the filter, fetch the live status snapshot in parallel:

```bash
gh pr view <N> --repo <owner/repo> --json \
  state,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,updatedAt,headRefName,title,url
```

(This is the same per-PR shape `pr-status-sweep` uses — keep it consistent.)

Emit one line per PR:

```
#<N> <owner/repo>  <title>
    state=<...> draft=<y|n> review=<APPROVED|CHANGES_REQUESTED|REVIEW_REQUIRED|none>
    checks=<pass|fail|pending|none> merge=<CLEAN|DIRTY|BLOCKED|...>  updated <relative-time>
    <url>
```

`checks=none` means no checks ran (or none configured) — say so explicitly; never imply "passing".

### 5. Reconcile against project README.md / log.md

For each project under `~/Documents/Work/projects/*/`, read `README.md` and the **recent** portion of `log.md` (skip if missing). Extract every reference to:

- A JIRA key matching `[A-Z]+-\d+`
- A PR number matching `#\d+` or a full PR URL
- A branch name matching `[A-Z]+-\d+-.*`

**Scope rules for `log.md`:** Logs accumulate historical narrative — a closed PR from 6 months ago is not "drift", it's history. Restrict reference extraction to log entries dated within the last 30 days. Entries are typically headed by a date line (`## 2026-06-05`, `### 2026-05-28`, etc.); stop scanning once you hit an entry older than 30 days. Apply the full file to `README.md` since READMEs describe current state, not history.

For each reference, compare against the live state from steps 3 and 4. Flag drift:

| Local file says | Live state shows | Drift |
|---|---|---|
| Ticket open / In Progress | Done / Closed | stale-ticket |
| PR open | merged / closed | stale-pr |
| PR draft | ready for review | promoted |
| PR needs review | approved + clean | mergeable |
| Branch named | no matching PR found | no-pr |
| Ticket Blocked | now In Progress | unblocked |

Emit one line per drift finding:

```
<project>/<file>  <drift-type>  <ref>  local=<...> live=<...>
```

Do NOT edit the README or log.md — flag only. The user decides whether to update.

### 6. Next-action recommendation

Single line, derived from the verified snapshot. Priority order:

1. Mergeable PR (mine, approved, checks clean, merge=CLEAN) → "merge PR #N"
2. PR with changes requested or failing checks → "address feedback on PR #N"
3. PR awaiting my review → "review PR #N"
4. In-Progress JIRA ticket with no open PR and no recent commits → "resume work on KEY"
5. Blocked JIRA ticket with a recent blocker comment → "unblock KEY: <comment summary>"
6. Drift finding → "reconcile <project>/<file>: <drift-type>"
7. Nothing actionable → "no actionable items — pick up new work via `/what-next`"

Emit as a single final line:

```
NEXT: <recommendation>
```

## Output Shape

```
== identity ==
ian-at-fes (active)

== git ==
fanapp-terraform           main                          ahead=0 behind=2  clean
fanapp-fanflow             FANDEVX-2592-fbg-fanflow-...  ahead=3 behind=0  dirty=2 files

== jira ==
FANDEVX-2592  In Progress  Wire fanflow kafka dev  updated 2h ago
FANDEVX-2480  Blocked      ... last comment 2d ago: "waiting on instaclustr SRE"

== github ==
#1842 fanatics-gaming/fanapp-terraform  Bump karpenter to 1.1.0
    state=OPEN draft=n review=APPROVED checks=pass merge=CLEAN  updated 1h ago
    https://github.com/fanatics-gaming/fanapp-terraform/pull/1842

== reconcile ==
projects/karpenter-migration/log.md  stale-ticket  FANDEVX-2410  local=In Progress  live=Done

NEXT: merge PR #1842 (approved, clean, mine)
```

## Parallelization

Step 2's *active-work set* depends on steps 3 and 4 — run JIRA (step 3) and the two `gh search prs` calls (step 4 discovery) in parallel first, then compute the active-work repo set, then fan out per-repo `git fetch + status` (step 2) and per-PR `gh pr view` (step 4 drill-down) in a second parallel batch.

Order:

1. Step 1 (identity check) — serial, fast
2. Parallel batch: step 3 JIRA + step 4 `gh search prs` (mine + needing review)
3. Compute active-work repo set from the results
4. Parallel batch: step 2 per-repo `git fetch + status` + step 4 per-PR `gh pr view`
5. Step 5 (reconcile) — serial, reads local files
6. Step 6 (NEXT:) — serial

## Common Mistakes

| Mistake | Reason |
|---|---|
| Using `--author=@me` | Resolves to `ian-bartholomew`, not `ian-at-fes`. Always explicit. |
| Adding `reviewDecision` to `gh search prs --json` | `gh search` rejects the field. It's only valid on `gh pr view`. Get review state from the per-PR drill-down. |
| Running `git fetch` on all of `~/Dev/` | Dozens of dormant clones — wasteful. Intersect with active PRs/tickets/projects first. |
| Looking for `repo:` / `path:` keys in project README front-matter | Project READMEs don't carry one. Match project log/README mentions against `~/Dev/<repo-name>/` instead. |
| Treating empty `statusCheckRollup` as "passing" | No checks ≠ passing checks. Say "no CI" or "no checks yet". |
| Editing README/log.md to fix drift | Skill is read-only. Flag only — user decides. |
| Running `git fetch` without `--quiet` and pasting the output | Noisy. Use `--quiet` and rely on `git status -b` for ahead/behind. |
| Skipping JIRA when MCP errors out | Say so explicitly ("JIRA query failed: <reason>") rather than producing a snapshot that silently omits tickets. |
| Re-running the full pipeline for a follow-up question in the same session | Reuse the prior snapshot. Only re-run if the user explicitly asks or > 30 min have passed. |
| Producing the snapshot but skipping step 6 | The user invoked this to get a recommendation. Always emit `NEXT:`. |
