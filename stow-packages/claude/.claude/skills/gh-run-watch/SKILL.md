---
name: gh-run-watch
description: Use when the user wants to poll a single GitHub Actions run to terminal state and, on failure, pull failed-job logs with error highlights. Triggers on "watch run <id>", "is run <id> done", "poll <workflow> on <branch>", "did <run> finish", or any step that needs a workflow run's terminal status before recommending next actions. Generic — works on any repo and any single run id. For fanapp-terraform deployments with environment-aware rerun semantics, use /fes-deployments instead.
version: 0.1.0
allowed-tools: [Bash]
---

# GH Run Watch

Polls a single GitHub Actions run to terminal state (`completed` + a conclusion), and on failure auto-fetches the failed job's log slice with error highlights. Replaces the hand-typed `gh run view <id> --json status,conclusion,jobs | python3 ...` loop that recurs across long sessions.

## When to Use

- User names a specific GitHub Actions run id and wants its terminal state
- User asks to watch a workflow on a branch ("watch the perf_infra run on FANDEVX-3026")
- A workflow was just triggered and the next decision depends on its outcome
- Need failed-job logs after a run finished red

## When NOT to Use

- Polling a fanapp-terraform deployment workflow → use `/fes-deployments` (knows env order, rerun semantics, drift checks)
- Listing many runs at once → `gh run list` directly is fine, no skill needed
- Triggering / re-running a workflow → `gh workflow run` or `gh run rerun` directly
- Watching CI on the local branch right after pushing → if `gh pr checks` answers the question in one shot, prefer that

## Inputs

Accept any of:

- A bare run id (`12345678901`) — requires `--repo` or current-directory git remote
- A full run URL (`https://github.com/<owner>/<repo>/actions/runs/<id>`)
- A `(workflow, branch)` pair → resolve to the newest run for that pair
- A `(workflow, branch, event)` triple if multiple workflows trigger on the same branch

## Steps

### 1. Resolve identity (mandatory for fanatics-gaming repos)

```bash
gh auth status 2>&1 | grep -q "ian-at-fes" \
  || { echo "FATAL: gh CLI not authed as ian-at-fes. Run 'gh auth switch -u ian-at-fes'. Halting."; exit 1; }
```

CLAUDE.md rule: any `fanatics-gaming/*` repo must be queried as `ian-at-fes`.

### 2. Resolve the run id

If given a URL, parse `<owner>/<repo>/<id>` from the path.

If given a `(workflow, branch)` pair:

```bash
gh run list -R <owner>/<repo> --workflow=<workflow> --branch <branch> --limit 1 \
  --json databaseId,status,conclusion,url,headSha
```

Pick the newest run's `databaseId`. Quote the URL back to the user.

### 3. Poll until terminal

```bash
gh run view <id> -R <owner>/<repo> --json status,conclusion,name,workflowName,headBranch,url,jobs
```

Stop when `status == "completed"`. Sleep 30 seconds between polls. **Cap total poll time at 30 minutes** — if not terminal by then, halt and report the still-running state with the URL; do not loop forever.

Quote `status` on every poll the user sees, so progress is visible. Avoid spamming — one update per minute is enough during a long run.

### 4. On success: report and exit

Print:

```
status=completed conclusion=success workflow=<workflowName> branch=<headBranch>
url: <url>
```

Done.

### 5. On failure: triage the failed job

Identify the failed job from the `jobs` array (`conclusion == "failure"`). Then:

```bash
gh run view --job <job-id> -R <owner>/<repo> --log-failed | tail -200
gh run view --job <job-id> -R <owner>/<repo> --log \
  | grep -nE 'Error:|FAILED|exit code|exit status|Process completed with' | tail -40
```

Report:

- Failing job name + failing step name
- The matched error lines (deduped if obvious repetition)
- Run URL
- One concrete next-action suggestion if the error pattern is recognizable (CI flake → suggest rerun-failed, dependency error → check lockfile, permission error → check secret/identity)

If `--log-failed` returns empty (rare — happens when the job was cancelled mid-step), fall back to `gh run view --job <job-id> --log | tail -100` and surface the trailing output verbatim.

### 6. If the user is in a fanapp-terraform worktree

The rerun decision for that repo has environment-aware semantics (don't rerun prod after a perf failure, etc.). At the end of triage, suggest:

> For the rerun decision, switch to /fes-deployments — it knows the env order and drift checks for this workflow.

Do not invoke `gh run rerun` from this skill without explicit user confirmation.

## Mandatory Verification

- Quote the final `status`, `conclusion`, and URL in the user-facing report — never just "done" or "failed".
- For `fanatics-gaming/*` repos, the `gh auth status` check in Step 1 MUST pass before any `gh run` call.
- If `gh run view` fails (404, rate limit, network), halt and surface the raw error — do not retry silently more than once.

## Red Flags

- Polling without a cap — silent infinite loop on a hung workflow
- Re-running `gh run view --json status` more than once per ~30 seconds — wasteful and rate-limit risk
- Reporting "done" without quoting the actual `conclusion` field — `completed` can be success, failure, cancelled, or skipped
- Calling `gh run rerun` automatically on failure — always confirm with the user
- Using `--author=@me` in any `gh search` call — resolves to personal account; use `--author=ian-at-fes` explicitly

## Related Skills

- `/fes-deployments` — fanapp-terraform-specific, env-aware rerun and drift handling
- `/pr-status-sweep` — for live PR state across a list of PRs (different question)
- `/fes-terraform-plan-risk` — accepts a workflow-run URL and scores the plan diff
