---
name: parallel-code-build
description: Execute a written implementation plan as a parallel general-coding agent team. Decomposes the plan into file-disjoint ownership groups and spawns code-implementer teammates to build with strict TDD in parallel, then runs integration, verify (build + test + run), and multi-lens review phases before close-out. Falls back to sequential subagent-driven-development when the work isn't actually parallelizable. Triggers on "/parallel-code-build", "build this plan as a team", "parallelize this build", "spin up the coding team". Execution-only - assumes brainstorming + writing-plans already produced a plan.
version: 0.1.0
allowed-tools: [Bash, Read, Glob, Grep, Skill, TeamCreate, TeamDelete, TaskCreate, TaskList, TaskUpdate, SendMessage]
---

# Parallel Code-Build

Team-aware executor for larger general-coding changes: plugins/skills, CLIs, apps, libraries, CI tooling. This is the **execution** stage of the superpowers pipeline (`brainstorming` -> `writing-plans` -> **here**), acting as a parallel alternative to `subagent-driven-development`. Design is never parallelized - it stays a single-session dialogue. This skill takes an existing plan and runs it as an agent team when, and only when, the work is genuinely parallelizable.

## When to Use

- A written plan exists (from `writing-plans` / a `docs/superpowers/specs/...` artifact) for a code change that touches multiple independent packages, modules, directories, or layers.
- The user asks to build a plan "as a team", "in parallel", or invokes `/parallel-code-build`.

## When NOT to Use

- No plan exists yet - stop and direct the user to `brainstorming` then `writing-plans` first. Do not improvise a plan here.
- The change is a single cohesive unit (one module, tightly coupled files) - the parallelizability gate below will catch this and hand off to `subagent-driven-development`.
- Pure review with no building - use `/code-review` directly.
- Debugging-only work - use `superpowers:systematic-debugging` directly.
- Terraform / IaC work - use `/parallel-infra-build`.

## Hard Guardrails (enforced by the lead even if a teammate drifts)

- **Teammates never push, publish, release, or deploy.** No `git push`, `npm publish`, `gh release`, marketplace version bumps. No destructive git ops (`reset --hard`, `clean`, `rebase`). The lead owns ALL git write operations; pushing happens only at close-out under the normal PR conventions.
- **Strict TDD is non-negotiable.** No production code without a failing test first; no task marked complete with red tests. Implementers follow `superpowers:test-driven-development`.
- **Lint + build + scope-relevant tests green** before any task is marked complete.
- **No emojis; no em dashes** in code, commits, or PR text.
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

**Install dependencies once, here.** Run the repo's dependency sync (`npm install` / `uv sync` / `go mod download` / warm `swift build`) before any teammate spawns. Teammates never mutate the dependency tree afterward - parallel installs in a shared worktree corrupt caches.

While here, detect the repo's verified build / lint / test commands (Makefile, justfile, package.json scripts, pyproject.toml, CI workflows, repo CLAUDE.md). The lead detects once and passes exact commands into task descriptions so teammates don't re-discover divergently.

### 2. Locate the plan

Find the plan being executed - the most recent `docs/superpowers/specs/*-design.md` + its implementation plan, or a plan path the user named. If none exists, **stop**:

> No implementation plan found. Run `brainstorming` then `writing-plans` first - design isn't parallelized. Re-invoke this skill once a plan exists.

### 3. Worktree isolation

Ensure work happens in an isolated worktree via the `using-git-worktrees` skill: `<repo-root>/.claude/worktrees/<branch>`, branch named `<ticket-id>-<ticket-name>` per convention. Pull the default branch first. The whole team works in this one worktree; file-ownership boundaries (not separate worktrees) prevent collisions.

### 4. Decompose into ownership groups

Read the plan's tasks and group them into **file-disjoint ownership groups** by package, module, directory, or layer (e.g. plugin skill dirs, `src/` subtrees, app targets). For each group, capture: the exact file/path set it owns, its tasks, and dependency edges to other groups (e.g. "group B imports types group A defines"). Target 3-5 groups, ~5-6 tasks each.

**Shared-file hotspots get exactly one named owner.** Manifests (`plugin.json`, `package.json`, `pyproject.toml`, `go.mod`, `Package.swift`) and shared test infrastructure (`conftest.py`, fixtures, test helpers) are touched by nearly every group under TDD. Assign each hotspot file to exactly one group (or keep it with the lead). Routing rule: any other group needing a change there (a dependency, a registration entry, a fixture) messages the owner with the exact addition needed - it never edits the file itself.

### 5. Parallelizability gate

If the decomposition yields **only one** independent group (the work is inherently sequential or tightly coupled), do **not** spawn a team - the token cost and coordination overhead aren't justified. Hand off to `subagent-driven-development` and stop. Otherwise continue.

### 6. Build phase

Create the team and the shared task list, then spawn one `code-implementer` per ownership group.

- Each task description states the owning group's **scope boundary explicitly**, its dependency edges, and the **verified build/lint/test commands** from preflight, so blocked tasks stay blocked and toolchains stay consistent.
- Teammates follow the leader's model (set "Default teammate model -> leader's model" in `/config`); display is split-panes via tmux.
- The lead does not poll - teammates self-claim unblocked tasks and notify on idle.
- **Git discipline:** only the lead runs git write operations, and every commit is **path-scoped**: `git add <group's owned paths>` only, never `git add -A` or `git add .` - other groups have in-flight red-test TDD state in the same worktree. Commit when each group reports done.
- **Test-run contention:** teammates run only their scope-relevant test subsets (safe in parallel). Full-suite and clean full-build runs are reserved for the verifier, or serialized by the lead if a teammate needs one.

### 7. Integration phase

At dependency boundaries (and once the building teammates report their seam-touching changes), bring in `code-integration-checker` to confirm imports resolve, shared types/interfaces agree, signatures match call sites, and config/registration wiring composes (e.g. a skill registered in `plugin.json` by one group, implemented by another). Read-only seam checks can run anytime; build/test execution only against scopes whose owners report done - mid-flight groups have intentionally-red TDD tests. Route any break back to the two owning teammates.

### 8. Verify phase

Once all groups report done, spawn `code-verifier` on the assembled branch: full build, lint, the entire test suite, then run the artifact where feasible (CLI smoke commands, invoking a built skill, launching the app). Route failures to the owning implementer, who debugs per `superpowers:systematic-debugging`. **Verify re-runs after each fix and loops until green.**

### 9. Review phase

Spawn **three `code-lens-reviewer` teammates in parallel**, one per lens: `correctness`, `tests-tdd`, `simplicity-architecture`. Each task description names its lens and points at this skill's `references/<lens>.md` rubric plus `references/output-schema.md`. Each reviews the assembled branch diff against `origin/<default-branch>`. Surface findings >= 80 confidence, attributed to owning scopes; route fixes to owners. For team builds, this phase fulfills the pre-PR code-review requirement.

### 10. Close out

The **lead** (never a teammate - teammate context may not resolve cleanly) tears the team down with `TeamDelete`. Then route to `finishing-a-development-branch`, opening the PR via the normal `commit-push-pr` / `finish-work` conventions under `ian-at-fes`. If the repo relates to a project under `~/Documents/Work/projects/<project>/`, append an entry to that project's `log.md`.

## Notes

- One team at a time; clean up before starting another. No nested teams. The lead is fixed for the team's lifetime.
- Known feature limitation: in-process teammates aren't restored by `/resume` or `/rewind`; if a session is resumed mid-build, re-check team state before messaging teammates.
- Optional future hardening (not built yet): `TeammateIdle` / `TaskCompleted` hooks to hard-block idle-with-failing-tests or any push/publish attempt.
