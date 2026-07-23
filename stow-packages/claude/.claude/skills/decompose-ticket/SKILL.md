---
name: decompose-ticket
description: Use when the user wants to break a Jira Feature, Epic, or Story down one level into child work items for the FES Platform team: Feature into epics, Epic into stories, Story into sub-tasks (FANDEVX/FESFEAT). Triggers on "decompose", "break down this feature/epic/story", "split this ticket into children", "generate the epics/stories/subtasks for". Not for creating a single standalone ticket.
---

# Decompose Ticket

## Overview

Break one Jira parent (Feature, Epic, or Story) into its immediate children (one level only), then create those children under the parent.

A capable agent can already list plausible children and pick the right issue type. The value of this skill is the *process* around that list: real research, an adversarial review pass, and a mandatory confirmation gate before anything is written to Jira. Follow the pipeline; do not shortcut to "here are the children, created."

Tiers: Feature → Epics · Epic → Stories · Story → Sub-tasks. One level, never recurse.

## When to use

- "Decompose / break down / split" a Feature, Epic, or Story into children.
- Generate the child epics / stories / sub-tasks *and create them* under a parent.

Not for: a single standalone ticket (use `fes-platform-jira-tickets`); a Bug or an existing Sub-task, which the classifier refuses (nothing to decompose).

## Pipeline

Run in order. REQUIRED steps stay in even when the breakdown "looks obvious"; those are exactly the steps a one-shot agent drops.

1. **Fetch & classify.** `getJiraIssue` (fields: summary, description, status, issuetype, subtasks, parent, comment, issuelinks). Get the child tier and routing from the script, do not hand-map:

   ```bash
   scripts/classify-ticket.sh "<parent issue type>"   # relative to this skill's base dir
   ```

   Prints `child_type`, `child_project`, `creator`; or exits 2 with a reason (Bug / Sub-task / unsupported, so stop and tell the user why). If the parent already has children of `child_type`, show them and ask augment-vs-abort. Warn if the parent is Done/closed.

2. **Research the parent (REQUIRED).** Wiki-first (`~/Documents/Work/wiki/_index.md`), then Confluence/Jira, then web. Choose 3-5 angles tailored to *splitting the work*: definition-of-done, system/codebase touchpoints, dependencies & sequencing, risks/unknowns/spikes, prior similar tickets. Dispatch one general-purpose agent per angle in a single message (tools: WebSearch, WebFetch, Grep, Read, Atlassian search); each returns sourced atomic findings. Synthesize a short brief: what the work entails, the natural seams to split on, cross-child dependencies, risks. The brief, not the ticket text alone, grounds the plan.

3. **Draft the plan.** Write a plan file to `~/.claude/plans/decompose-<KEY>-plan.md` (mkdir -p if needed; this is where `adversarial-review` looks by default) with a `## Context` section (so the reviewer reads it) and, per child: title, context, **AC**, **implementation hints** where applicable, one-line rationale. Add sibling sequencing and an explicit out-of-scope list.

4. **Adversarial review, max 2 rounds (REQUIRED).** Run the `adversarial-review` skill on the plan file. Auto-incorporate the consolidated findings each round; re-run once if round 1 produced any; stop at 2. (This automates that skill's cherry-pick prompt; the value kept is the parallel red-team, not the interactivity.)

5. **Confirm before creating (REQUIRED GATE).** Present the final children (title / AC / hints / child_type, plus the fields you will set: sprint, priority, work category, and the parent link), then AskUserQuestion: **Create all / Edit first / Cancel.** Create nothing before an explicit yes. Creating Jira tickets is outward-facing and hard to reverse; this is the one hard gate.

6. **Create the children**, routed by `creator`:
   - `jira-skill` (Epics, Stories): follow the `fes-platform-jira-tickets` contract. Read its `references/jira_config.md` at runtime for current field IDs; never hardcode them here. Fetch the active sprint once and reuse. Set the parent via the native `parent` param. Work Category is REQUIRED on Stories. After each create, run the `editJiraIssue` markdown-fix (REQUIRED every time) or the description renders as raw source.
   - `direct-mcp-subtask` (Sub-tasks): `createJiraIssue` with `issueTypeName: "Sub-task"`, `parent: "<story-key>"`. **Omit** `customfield_10001` (Team); the API rejects it on sub-tasks. Fetch the Sub-task createmeta (`getJiraIssueTypeMetaWithFields`) to set exactly the required fields; don't guess. Then the same markdown-fix `editJiraIssue`.

7. **Summarize & log.** Print the parent plus each child key + URL and confirm linkage. If the parent maps to a `~/Documents/Work/projects/<name>/` folder, append a `log.md` entry. Don't comment on the parent ticket unless asked.

## Quick reference

| Parent | Child | Created via |
|--------|-------|-------------|
| Feature (FESFEAT) | Epic | fes-platform-jira-tickets |
| Epic | Story | fes-platform-jira-tickets |
| Story | Sub-task | direct Atlassian MCP (omit Team field) |
| Bug / Sub-task | n/a | refuse, nothing to decompose |

## Common mistakes

- **Skipping research or review because the split "looks obvious."** Those are the two steps a one-shot agent drops, and the reason this skill exists. REQUIRED.
- **Creating tickets before the gate.** Never. Present, then wait for an explicit Create all.
- **Story → Task.** A Task is level 0, a sibling of Story, not a child; the child of a Story is a Sub-task. The classifier enforces this, so use it, don't hand-map.
- **Forgetting the `editJiraIssue` markdown-fix** after create; descriptions render as raw source without it.
- **Setting the Team field on a sub-task**; the create call fails. Omit it.
- **Recursing past one level.** Decompose only the immediate tier.

## Validating changes to this skill

The mapping, routing, and guards have a runnable test. Red/green it before touching `classify-ticket.sh`:

```bash
bash scripts/classify-ticket.test.sh
```
