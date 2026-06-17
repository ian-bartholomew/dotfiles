---
name: pr-status-sweep
description: This skill should be used when the user needs live status for one or more GitHub PRs before recommending next actions — verifying merge state, draft/ready, review decision, and CI rollup. Triggers on "is PR N still open", "status of PR/branch X", "check my open PRs", batch PR audits, or any step that names PRs in a recommendation. Honors the CLAUDE.md rule to verify live PR status before suggesting next actions, and the ian-at-fes identity rule for fanatics-gaming repos.
version: 0.1.0
allowed-tools: [Bash, Read]
---

# PR Status Sweep

Read-only skill that pulls a uniform status snapshot for one or many GitHub PRs and returns a tight per-PR summary. Replaces the hand-typed `gh pr view ... --json state,reviewDecision,mergeable,...` incantation that recurs whenever a workflow (what-next, end-of-day, finish-work, standup) needs to confirm PR state.

## When to Use

- User asks "is PR N still open / merged / approved"
- User asks for status of a branch, ticket, or list of PRs
- Any workflow about to *name* a PR in a recommendation — verify live state first
- Batch audits ("show me my open PRs", "what's the state of everything I have out")
- Before any `finish-work`, `start-ticket`, or `end-of-day` synthesis that references PRs

## When NOT to Use

- You only need the PR title or URL — `gh pr view <N> --json title,url` directly is fine
- You're about to *act* on a PR (comment, approve, merge) — this skill is read-only; use `gh pr <action>` directly with explicit user confirmation
- The PR is on a non-GitHub host (GitLab, Bitbucket) — use that host's CLI

## Inputs

Accept any of:

- One or more PR numbers (requires a `--repo` or current-directory git remote to resolve)
- Full PR URLs (`https://github.com/<owner>/<repo>/pull/<N>`)
- A branch name (resolve via `gh pr list --head <branch> --json number,...`)
- "my open PRs" / "all my PRs" → batch mode

## Pipeline

### 1. Resolve identity

```bash
gh auth status 2>&1 | grep -E '^\s*Active account: true' -A0 -B1
```

For any repo under `fanatics-gaming/`, abort if active account is not `ian-at-fes`. Switch with:

```bash
gh auth switch --user ian-at-fes
```

Never use `--author=@me` in `gh search` — it resolves to the personal account even when `ian-at-fes` is active. Always pass `--author=ian-at-fes` explicitly.

### 2. Resolve input → list of (repo, number) pairs

For URLs, parse `<owner>/<repo>` and `<number>` from the path.

For bare numbers without a repo, use the current directory's default repo:

```bash
gh repo view --json nameWithOwner -q .nameWithOwner
```

For "my open PRs":

```bash
gh search prs --author=ian-at-fes --state=open \
  --json number,title,url,repository,isDraft,updatedAt --limit 100
```

For a branch name:

```bash
gh pr list --repo <owner/repo> --head <branch> \
  --json number,title,url,state --limit 5
```

### 3. Fetch per PR in parallel

Use a fixed field set so output is uniform across PRs:

```bash
gh pr view <N> --repo <owner/repo> --json \
  state,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,latestReviews,updatedAt,headRefName,title,url
```

Issue calls in parallel (one Bash call per PR, dispatched in a single message) — `gh` is rate-limit friendly and per-PR latency dominates serial loops.

### 4. Reduce to one line per PR

Format:

```
#<N> <title>
    state=<OPEN|MERGED|CLOSED> draft=<y|n> review=<APPROVED|CHANGES_REQUESTED|REVIEW_REQUIRED|none>
    checks=<pass|fail|pending|none> merge=<CLEAN|DIRTY|BLOCKED|UNSTABLE|...>
    updated <relative-time> · <url>
```

- `checks`: derive from `statusCheckRollup`. Empty array → `none` (say so explicitly; don't imply "passing").
- `review`: from `reviewDecision`; null → `none`.
- `merge`: from `mergeStateStatus`. Combine with `mergeable` if `mergeStateStatus` is `UNKNOWN`.

### 5. Surface actionable PRs at the top

Promote to a "needs attention" block any PR matching:

- `reviewDecision == CHANGES_REQUESTED`
- `statusCheckRollup` contains a failed check
- `state == OPEN && reviewDecision == APPROVED && mergeStateStatus == CLEAN` (mergeable, just needs a click)
- `state == OPEN && isDraft == false && updatedAt > 7 days ago` (stale)

### 6. Output

Terminal-only. Plain URLs (not markdown links — per global CLAUDE.md). No file writes. No comments posted. No approvals. No merges.

## Read-only Guarantee

This skill MUST NOT call:

- `gh pr comment` / `gh pr review` / `gh pr merge` / `gh pr close` / `gh pr edit`
- Any `mcp__*` write tool against GitHub
- `git push` of any kind

If the user follows up with "merge it" or "comment X", that is a *separate* action requiring its own confirmation per the executing-actions-with-care guidance in the global system prompt.

## Common Mistakes

| Mistake | Reason |
|---|---|
| Using `--author=@me` for batch mode | Resolves to personal account `ian-bartholomew`, not `ian-at-fes`. Always explicit. |
| Reporting "checks passing" when `statusCheckRollup` is empty | No checks ≠ passing checks. Say "no CI configured" or "no checks run yet". |
| Truncating output to one PR when several were requested | If user asked about a list, report on the list — don't summarize "they all look fine". |
| Treating `mergeable=true` as "mergeable" | `mergeStateStatus` is the live signal. `mergeable` can lag. |
| Using GitHub MCP for fanatics-gaming repos | Per CLAUDE.md, prefer `gh` CLI for that org. MCP has shown PR visibility gaps. |
