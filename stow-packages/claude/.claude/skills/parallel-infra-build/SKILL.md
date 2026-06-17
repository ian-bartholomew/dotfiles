---
name: parallel-infra-build
description: Execute a written implementation plan as a parallel Terraform agent team. Decomposes the plan into file-disjoint ownership groups and spawns tf-implementer teammates to build in parallel, then runs integration, plan-risk, and review phases before close-out. Falls back to sequential subagent-driven-development when the work isn't actually parallelizable. Triggers on "/parallel-infra-build", "build this plan as a team", "parallelize this infra build", "spin up the infra team". Execution-only - assumes brainstorming + writing-plans already produced a plan.
version: 0.1.0
allowed-tools: [Bash, Read, Glob, Grep, Skill, TeamCreate, TeamDelete, TaskCreate, TaskList, TaskUpdate, SendMessage]
---

# Parallel Infra-Build

Team-aware executor for larger Terraform / platform changes. This is the **execution** stage of the superpowers pipeline (`brainstorming` -> `writing-plans` -> **here**), acting as a parallel alternative to `subagent-driven-development`. Design is never parallelized — it stays a single-session dialogue. This skill takes an existing plan and runs it as an agent team when, and only when, the work is genuinely parallelizable.

## When to Use

- A written plan exists (from `writing-plans` / a `docs/superpowers/specs/...` artifact) for a Terraform or platform change that touches multiple independent modules, env dirs, or repo subtrees.
- The user asks to build a plan "as a team", "in parallel", or invokes `/parallel-infra-build`.

## When NOT to Use

- No plan exists yet — stop and direct the user to `brainstorming` then `writing-plans` first. Do not improvise a plan here.
- The change is a single cohesive unit (one module, tightly coupled files) — the parallelizability gate below will catch this and hand off to `subagent-driven-development`.
- Pure review or pure risk-assessment with no building — use `/terraform-review` or `/fes-terraform-plan-risk` directly.

## Hard Guardrails (enforced by the lead even if a teammate drifts)

- **Never** `terraform apply` / `destroy` / `import` / `state mv|rm`, and never touch remote state. Building produces HCL; verification produces a `plan`. Nothing in this skill mutates infrastructure.
- **fmt + validate** on every changed scope before a task is marked complete.
- **`aws-profile-check`** before any `aws --profile <name>` call against a non-default account; re-auth SSO if expired.
- **`ian-at-fes`** GitHub identity for every `fanatics-gaming` repo operation (`gh auth status` first).
- **Worktrees only** in `<repo-root>/.claude/worktrees/<branch>`. The whole team shares the lead's cwd; teammates do not create their own worktrees.

## Pipeline

### 1. Preflight

```bash
claude --version                                  # need >= 2.1.32
grep -q AGENT_TEAMS ~/.claude/settings.json && echo "teams enabled"
[ -n "$TMUX" ] && echo "tmux session active"
```

If teams aren't enabled, or there's no tmux session (split-panes needs it), stop and give the exact fix. Confirm `gh auth status` shows `ian-at-fes` for `fanatics-gaming` work.

### 2. Locate the plan

Find the plan being executed — the most recent `docs/superpowers/specs/*-design.md` + its implementation plan, or a plan path the user named. If none exists, **stop**:

> No implementation plan found. Run `brainstorming` then `writing-plans` first — design isn't parallelized. Re-invoke this skill once a plan exists.

### 3. Worktree isolation

Ensure work happens in an isolated worktree via the `using-git-worktrees` skill: `<repo-root>/.claude/worktrees/<branch>`, branch named `<ticket-id>-<ticket-name>` per convention. Pull the default branch first. The whole team works in this one worktree; file-ownership boundaries (not separate worktrees) prevent collisions.

### 4. Decompose into ownership groups

Read the plan's tasks and group them into **file-disjoint ownership groups**, picking the split that fits the change:

- **Intra-repo:** by module directory, or by env dir (dev / perf / prod) in `fanapp-terraform`.
- **Cross-repo:** each group owns a different repo subtree.

For each group, capture: the exact file/module/path set it owns, its tasks, and dependency edges to other groups (e.g. "group B consumes module output from group A"). Target 3-5 groups, ~5-6 tasks each (the docs' efficiency sweet spot).

### 5. Parallelizability gate

If the decomposition yields **only one** independent group (the work is inherently sequential or tightly coupled), do **not** spawn a team — the token cost and coordination overhead aren't justified. Hand off to `subagent-driven-development` and stop. Otherwise continue.

### 6. Build phase

Create the team and the shared task list, then spawn one `tf-implementer` per ownership group.

- Each task description states the owning group's **scope boundary explicitly** and lists dependency edges so blocked tasks stay blocked until their dependency completes.
- Teammates follow the leader's model (set "Default teammate model -> leader's model" in `/config`); display is split-panes via tmux.
- The lead does not poll — teammates self-claim unblocked tasks and notify on idle.

### 7. Integration phase

At dependency boundaries (and once the building teammates report their seam-touching changes), bring in `tf-integration-checker` to confirm module output/input wiring, cross-repo references, and remote-state/data-source resolution compose across the groups. Route any break back to the two owning teammates.

### 8. Verify phase

Spawn `tf-plan-verifier` to run `terraform plan` on every changed root and score blast radius via `/fes-terraform-plan-risk`. Watch for rename-without-`moved`-block, cross-env divergence, and credential rotation. Route failures to the owning implementer.

### 9. Review phase

Spawn `tf-team-reviewer` to run `/terraform-review` on the assembled branch diff and surface findings >= 80 confidence, attributed to owning scopes.

### 10. Close out

The **lead** (never a teammate — teammate context may not resolve cleanly) tears the team down with `TeamDelete`. Then route to `requesting-code-review` and `finishing-a-development-branch`, opening the PR via the normal `commit-push-pr` / `finish-work` conventions under `ian-at-fes`.

## Notes

- One team at a time; clean up before starting another. No nested teams. The lead is fixed for the team's lifetime.
- Known feature limitation: in-process teammates aren't restored by `/resume` or `/rewind`; if a session is resumed mid-build, re-check team state before messaging teammates.
- Optional future hardening (not built yet): `TeammateIdle` / `TaskCompleted` hooks to hard-block idle-with-failing-validate or any `apply` attempt.
