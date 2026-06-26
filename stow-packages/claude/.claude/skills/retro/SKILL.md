---
name: retro
description: This skill should be used when the user asks to "do a retro", "retro this", "retro the work", "what went wrong", "post-mortem this", "run a retrospective", or runs "/retro". Reflects on work just done, surfaces what went wrong with evidence, runs a five-whys root-cause analysis on each issue, and proposes confirm-before-apply self-improvements to CLAUDE.md, memory, skills, or Honcho/wiki so the same mistake does not recur.
version: 0.1.0
allowed-tools:
  [
    Skill,
    Read,
    Edit,
    Write,
    Bash,
    Glob,
    Grep,
    AskUserQuestion,
    mcp__honcho__search,
    mcp__honcho__chat,
    mcp__honcho__add_messages_to_session,
    mcp__honcho__create_conclusions,
    mcp__plugin_fbg-core_atlassian__getJiraIssue,
    mcp__plugin_fbg-core_atlassian__searchJiraIssuesUsingJql,
  ]
---

# Retro Skill

A retrospective on work just done. Surfaces what went wrong, drills each issue to a root cause with five-whys, and proposes concrete self-improvements that prevent the same class of mistake recurring. Every fix is shown and confirmed before anything is written. The applied fixes are the primary output; each run is also recorded to a dated retro log under `~/Documents/Work/docs/retros/`.

## Purpose

Mistakes in a session are cheap to notice and expensive to repeat. Without a deliberate pass, the same friction recurs across sessions: assumptions made silently, verification skipped, a workflow done the slow way, a correction the user had to issue more than once. This skill turns that friction into durable changes to the things that govern future behaviour: global instructions (CLAUDE.md), persistent memory, skills, and personalization (Honcho / wiki).

It is reflective and corrective, not celebratory. It does not produce a "what went well" section unless something is genuinely worth locking in as a new default.

## When to Use

- User runs `/retro`, or says "do a retro", "retro this", "what went wrong", "post-mortem this", "run a retrospective"
- At the end of a piece of work, a ticket, or a session where something felt slower or rougher than it should have
- After the user had to correct, re-explain, or redo something

## When NOT to Use

- Mid-task — finish the work first; a retro on incomplete work has no outcome to reflect on
- As a status report — use `/verify-status` or `/what-next`
- As a daily wrap-up of activity — use `/end-of-day` (which captures learnings differently). `/retro` is about *what went wrong and how to stop it*, not what happened.

## Scope Resolution

Resolve scope from the invocation before gathering anything.

| Invocation | Mode | What "the work" means |
|---|---|---|
| `/retro` (no arg) | **Session** | The current conversation transcript only. No external lookups. |
| `/retro <TICKET>` e.g. `FANDEVX-1234` | **Period** | Session signal plus git log, project `log.md`, and JIRA/PR context for that ticket. |
| `/retro today` / `/retro <date>` | **Period** | Session signal plus git log and project `log.md` entries for that span. |
| `/retro <project>` | **Period** | Session signal plus that project's `log.md` and recent git history. |

Default is session mode. Only widen to period mode when an argument is given. Do not pull external context in session mode.

## Pipeline

```
  Resolve scope  →  Gather signal  →  Identify issues  →  Five-whys per issue  →  Propose fixes  →  Confirm each  →  Apply  →  Write log
                    (session ±period)  (numbered, cited)   (automated, shown)     (1 target each)   (per-fix)      (proper path)  (dated .md)
```

### Step 1: Gather signal

**Session mode** — reflect on the current conversation. Look specifically for:

- Corrections the user issued (especially anything they had to say more than once)
- Assumptions made without asking, where asking was warranted
- Tool calls that were denied, errored, or retried
- Work that was redone, backed out, or thrown away
- Verification that was skipped before reporting something as done
- Dead ends, wrong turns, time spent on the wrong thing
- Violations of CLAUDE.md instructions or active skill rules

**Period mode** — additionally gather, for the resolved target:

- `git log` over the span (commits, reverts, force-pushes, churn on the same files)
- The project's `log.md` entries
- JIRA status/history and PR review state where a ticket or PR is in scope

### Step 2: Identify what went wrong

Produce a short numbered list of distinct issues. Each issue gets:

- A one-line statement of the problem
- A one-line evidence cite (what happened — quote the correction, name the denied tool call, point at the redone work)

Keep it tight. Merge duplicates. If nothing genuinely went wrong, say so plainly and stop — do not manufacture issues to justify the skill.

### Step 3: Five-whys per issue

For each issue, drill to a root cause by asking "why" until reaching something actionable (typically 3-5 levels). This is **automated** — reason through it, do not interrogate the user at each level.

Present each chain compactly:

```
Issue 2: Reported the fix as done without running the test.
  Why? Assumed the one-line change was obviously correct.
  Why? The change looked trivial.
  Why? No habit of treating "trivial" changes as still needing verification.
  Root cause: Verification was gated on perceived risk, not made unconditional.
```

Then ask the user to confirm or correct the root causes before any fix is proposed. A wrong root cause produces a wrong fix. Wait for the user's response.

### Step 4: Propose fixes

Map each confirmed root cause to exactly **one** target. Pick the right home:

| Target | Use when the root cause is... | Change takes the form of... |
|---|---|---|
| **CLAUDE.md** | A rule or default that should govern all future work | A precise edit to the relevant section, shown as the before/after text |
| **Memory** | A piece of guidance/context worth persisting but not a hard rule | A new memory file (`feedback`/`project`/`reference`) plus its `MEMORY.md` pointer line, following the memory format |
| **Skill** | Workflow-shaped — a repeated multi-step task done inconsistently | An edit to an existing skill (via `edit-skill`), or a one-line sketch of a proposed new skill |
| **Honcho / wiki** | Personalization (who the user is / preferences → Honcho) or a technical/domain learning (→ wiki) | A Honcho conclusion/note, or a wiki entry |

Present every proposed fix with three things: the **target**, the **exact change** (diff or full text), and **which root cause it kills**. One fix can only target one place; if a root cause needs two homes, propose two fixes.

### Step 5: Confirm each, then apply

The user approves **per fix** — not all-or-nothing. Nothing is written before approval.

On approval:

- **CLAUDE.md** edits → apply with `Edit` to `~/.claude/CLAUDE.md`
- **Memory** → `Write` the memory file under the memory directory and add its `MEMORY.md` pointer
- **Skill** edits → go through the `edit-skill` skill so the stow → symlink chain is honoured; a *new* skill goes through `writing-skills`
- **Honcho** → write the conclusion/note via the Honcho MCP tools
- **Wiki** → follow the user's wiki conventions for adding a learning

Report what was applied and what was declined.

### Step 6: Write the retro log

Record the run to `~/Documents/Work/docs/retros/`. Create the directory if it does not exist (`mkdir -p`). This happens on every run, including a clean "nothing went wrong" run (the log then states that explicitly).

Filename: `YYYY-MM-DD-<scope>-retro.md` where `<scope>` is the ticket key, project, or date for period mode, or a short kebab-case slug of the session's focus for session mode (e.g. `2026-06-26-retro-skill-build-retro.md`). If a file with that name already exists for today, append a `-2`, `-3` suffix rather than overwriting.

Write this structure:

```markdown
# Retro: <scope> — YYYY-MM-DD

**Mode:** session | period
**Scope:** <what "the work" covered>

## What went wrong
1. <issue> — evidence: <cite>

## Root causes (five-whys)
### Issue 1
- Why? ...
- Root cause: ...

## Fixes
| Root cause | Target | Change | Status |
|---|---|---|---|
| ... | CLAUDE.md | <summary of edit> | applied / declined |
```

Keep the log a faithful record of what was surfaced and decided — it is the trail, not a place to re-argue the analysis. Report the path written.

## Guardrails

- **Confirm before write, always.** This skill never edits CLAUDE.md, skills, or memory without explicit per-fix approval. The user's "Ask, don't assume" principle governs here.
- **One target per fix.** Forces the right home for each change instead of scattering the same lesson everywhere.
- **No invented issues.** If the work went cleanly, the correct output is "nothing went wrong worth a fix." A retro that always finds three problems is noise.
- **Root causes get confirmed.** Five-whys is automated but the root cause is shown for correction before any fix is built on it.
- **Don't touch unrelated config.** A retro fix addresses a root cause surfaced this session. It is not licence to refactor CLAUDE.md or reorganise skills.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Listing symptoms instead of root causes | Keep asking "why" until the answer is something you can change in a file |
| Proposing a vague rule ("be more careful") | A fix must be a concrete edit that would have prevented the specific issue |
| Auto-applying because the change "is obviously good" | Confirm anyway. Global config edits are exactly where silent assumptions cost most |
| Manufacturing problems to seem thorough | No issues found is a valid, honest result |
| Pulling git/JIRA in session mode | External lookups only happen when an argument widens scope to period mode |
