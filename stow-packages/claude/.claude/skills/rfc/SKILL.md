---
name: rfc
description: This skill should be used when the user asks to "write an RFC", "draft an RFC", "make an RFC", "create a TechOps RFC", or runs "/rfc". Drafts a decision-focused TechOps RFC in raw/rfcs/ following the TechOps RFC Template (Confluence page 2097676435, FAN space) — metadata table (Authors / Publish Date / 1-Pager(s) / Epic(s) / Review Until Date / Status) and the seven canonical sections (Background, Goals & Scope, Current State, Possible Solutions evaluated across 10 lenses, Recommended Solution, Paths Not Taken, Open Questions). The user provides a starting prompt and the skill asks at least 3 clarifying questions. Runs two parallel agent personas (Senior Technical Product Manager, Principal Platform Engineer) for feedback, iterates with the user until approved, then copies the RFC template page into the TechOps RFC's parent (Confluence page 2053603380, FAN space) populated with the draft. Page stays DRAFT — the user clicks Publish manually.
version: 0.1.0
argument-hint: "[topic or starting prompt]"
allowed-tools: [Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion, Agent, mcp__plugin_fbg-core_atlassian__getAccessibleAtlassianResources, mcp__plugin_fbg-core_atlassian__getConfluencePage, mcp__plugin_fbg-core_atlassian__createConfluencePage, mcp__plugin_fbg-core_atlassian__updateConfluencePage]
---

# RFC Skill

Draft, review, and stage a TechOps RFC end-to-end. The skill writes a draft to `raw/rfcs/`, runs **two** parallel agent reviewers (Senior Technical Product Manager, Principal Platform Engineer), iterates with the user, then creates a copy of the RFC template in Confluence under `TechOps RFC's` (parent page `2053603380`, space `FAN`) populated with the approved content. The Confluence page stays in `DRAFT` — the user clicks Publish themselves so the `#fes-rfcs` Slack notification fires under their identity.

## When to Use

Invoke this skill when:

- User runs `/rfc` (with or without a topic argument)
- User says "write an RFC", "draft an RFC", "make an RFC", "create a TechOps RFC", or any variation
- User has a 1-pager that's been agreed to and now needs to make a concrete technical decision among multiple approaches

## What an RFC is for (vs. a 1-pager)

Per the user's wiki at `~/Documents/Work/wiki/concepts/one-pager-and-rfc.md` and the canonical Confluence guidance:

- **1-pagers are the "what and why"** — persuasion artifacts for non-engineer decision-makers.
- **RFCs are the detailed "how"** — commitment artifacts for the engineers who'll build it. The point of the RFC is to **make a decision** among multiple technical approaches and execute on it.
- **One-pager first, always.** This skill strongly recommends a linked 1-pager and will nudge once if the user skips it. If skipped, leave a `TODO` in the 1-Pager(s) row of the metadata table.

## Format Reference

The canonical section structure is the **TechOps RFC Template** in Confluence (page id `2097676435`, FAN space). This skill mirrors that template exactly so drafts publish cleanly under the same parent.

Metadata table (top of every page):

| Field | Notes |
|-------|-------|
| **Author(s)** | One or more author names. Confluence template uses @ mentions; the skill writes display names. |
| **Publish Date** | Leave blank for drafts; user will fill on Publish. |
| **1-Pager(s)** | Link(s) to the related 1-pager. Strongly recommended. |
| **Epic(s)** | Jira epic keys (e.g. `FANDEVX-2448`). Optional. |
| **Review Until Date** | Date the review period ends and a decision is made. Optional but expected. |
| **Document status** | `DRAFT` on create. The user transitions through `IN REVIEW` → `APPROVED` / `REJECTED` themselves. |

Required body sections (in order):

1. **🧭 Background & Motivation** — why now; problem or opportunity; prior work/incidents; summary of linked 1-pager(s).
2. **🎯 Goals & Scope** — what we're aiming to achieve and explicit non-goals. Includes a **Requirements table** with columns: Requirement / User Story / Importance (HIGH/MEDIUM/LOW) / Notes.
3. **🧱 Current State** *(optional — include only when replacing or evolving something)* — existing system, shortcomings, diagrams.
4. **🧪 Possible Solutions** — 2–3 alternatives. **Every alternative is evaluated across the following 10 lenses.** Each lens must be addressed explicitly — even if the answer is "N/A — single-tenant, no compliance impact." Brevity is fine; absence is not.
   - *System Behavior* — logical view; key components, responsibilities, interactions, data models. Diagrams help.
   - *Deployment* — physical view; where it lives; environments, infrastructure, network topology.
   - *Developer Experience* — code structure, repos, libraries, interfaces, ownership. Include only useful detail.
   - *Observability* — logs, metrics, traces. How will we measure the system?
   - *Security and Compliance* — auth, data handling, trust boundaries, compliance concerns.
   - *Cost* — infra use, waste, efficiency. Big increases or savings?
   - *Reliability* — operating concerns, SPoFs, failure modes, blast radius.
   - *Maintainability* — tech debt, upgrade paths.
   - *Tradeoffs & Risks* — third-party dependencies, internal blockers, untested assumptions.
   - *Delivery Effort* — phased delivery; sequencing, not timelines; what ships independently; effort/resourcing.
5. **🎺 Recommended Solution** — which alternative and why. Must tie back explicitly to the trade-offs analysis, not preference.
6. **🛑 Paths Not Taken** — design paths explicitly not investigated, with rationale.
7. **🤔 Open Questions** — what's still unclear, undecided, or under discussion.

### Length & balance

RFCs are denser than 1-pagers but must not bloat. Target ~1,500–2,500 words. Warn if below 500 (likely too thin) or above 4,000 (likely too verbose) before reviewers dispatch.

Suggested per-section length:

- Background: 1–3 paragraphs
- Goals & Scope: bulleted goals + non-goals; Requirements table 3–7 rows
- Current State: only when applicable; 1–3 paragraphs + diagram if helpful
- Possible Solutions: 2–3 alternatives; each lens 1–2 paragraphs (or one sentence + "N/A" where genuinely not applicable)
- Recommended Solution: 2–3 paragraphs, explicitly citing the trade-offs that drove the choice
- Paths Not Taken / Open Questions: bullets

## Paths

All file paths are relative to the **vault root** (`~/Documents/Work/`):

- Drafts: `raw/rfcs/.draft-<slug>.md`
- Final: `raw/rfcs/<slug>.md`

The dot-prefix on drafts mirrors the `one-pager` and `research` skill conventions and hides them from any `/compile`-style scanners.

## Confluence constants

| Item | Value |
|---|---|
| Space key | `FAN` |
| Parent page (TechOps RFC's) | `2053603380` |
| Template page (TechOps RFC Template) | `2097676435` |
| Notification on Publish (handled by Confluence) | Slack `#fes-rfcs` |

Cloud id is resolved at runtime via `getAccessibleAtlassianResources` (currently `efc5fcb9-cd3f-4ee1-8d0d-255a135bf4e8`).

## Workflow

### Step 1 — Gather input

If the user passed a starting prompt on the command line (e.g. `/rfc karpenter ARM migration`), use it as the working title. Otherwise ask:

```
What's the working title for this RFC?
```

Then ask **at least three** clarifying questions, one at a time, conversational, accepting multi-line answers. Do **not** use `AskUserQuestion` for these — they're free text, not menu choices.

**Required clarifying questions (ask all of these):**

| # | Field | Prompt | Seeds section |
|---|-------|--------|---------------|
| 1 | Problem & motivation | What problem are we solving, or what opportunity are we acting on? Why is this coming up now? Any relevant incidents, deadlines, or prior decisions? | Background & Motivation |
| 2 | Linked 1-pager(s) | What 1-pager backs this RFC? (path under `raw/one-pagers/` or a Confluence URL) | Metadata table |
| 3 | Goals & non-goals | What does success look like — and what are we *explicitly* not solving here? Any hard requirements or constraints? | Goals & Scope |
| 4 | Alternatives considered | What technical approaches have you already weighed? (At least the two or three you want compared. If only one, that's fine — name it and we'll surface a credible alternative or two for contrast.) | Possible Solutions |

**Optional follow-ups (ask if not already covered):**

- Author(s) — defaults to `Ian Bartholomew` if blank.
- Jira epic(s) — comma-separated keys, e.g. `FANDEVX-2448, FESFEAT-428`. Leave blank to omit.
- Review-until date — `YYYY-MM-DD`. Leave blank to omit.
- Current state — is this replacing/evolving something? (If yes, the skill will include the optional **Current State** section; if no, omit it.)

**1-pager nudge.** If the user skips question #2 (no path/URL provided), nudge **once**:

```
RFCs are the "how" — they generally come after a 1-pager that established the
"what and why." Are you sure you want to proceed without one? I'll leave a TODO
in the metadata table.
```

If they confirm, proceed and place `TODO — link 1-pager before publishing` in the `1-Pager(s)` row.

### Step 2 — Pre-flight checks

```bash
VAULT_ROOT="$HOME/Documents/Work"
RFCS_DIR="$VAULT_ROOT/raw/rfcs"
mkdir -p "$RFCS_DIR"

# Derive kebab-case slug from title
SLUG=$(printf '%s' "$TITLE" \
  | tr '[:upper:]' '[:lower:]' \
  | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')

DRAFT_PATH="$RFCS_DIR/.draft-$SLUG.md"
FINAL_PATH="$RFCS_DIR/$SLUG.md"
```

If either `$DRAFT_PATH` or `$FINAL_PATH` already exists, ask via `AskUserQuestion`:

- **Resume** — re-open the existing draft and skip ahead to Step 4
- **Overwrite** — discard the existing file and write fresh
- **Rename** — prompt for a new title and recompute the slug
- **Cancel** — exit

### Step 3 — Write the draft

Write `$DRAFT_PATH` using the template below. Omit any optional section the user marked as not applicable (do not include a heading with placeholder filler). Always include all 10 lenses under each Possible Solution — using a single "N/A — <one-line reason>" when genuinely not applicable.

```markdown
---
title: <Title>
authors: <Authors>
date: <YYYY-MM-DD>
status: draft
review_until: <YYYY-MM-DD or null>
one_pager: <path or URL or null>
jira_epics: <comma-separated keys or null>
confluence_template_id: "2097676435"
confluence_parent_id: "2053603380"
confluence_space: FAN
confluence_cloud_id: "efc5fcb9-cd3f-4ee1-8d0d-255a135bf4e8"
confluence_page_id: null
confluence_url: null
---

# <Title>

| **Author(s)** | <Authors> |
| --- | --- |
| **Publish Date** | — |
| **1-Pager(s)** | <link or `TODO — link 1-pager before publishing`> |
| **Epic(s)** | <ticket keys or `—`> |
| **Review Until Date** | <date or `—`> |
| **Document status** | DRAFT |

## 🧭 Background & Motivation

<1–3 paragraphs. Why now; problem/opportunity; prior work or incidents; brief summary of the linked 1-pager(s) so this RFC stands on its own.>

## 🎯 Goals & Scope

**Goals**

- <goal>
- <goal>

**Non-goals**

- <non-goal>
- <non-goal>

**Requirements**

| Requirement | User Story | Importance | Notes |
|---|---|---|---|
| <requirement> | <user story or N/A> | HIGH / MEDIUM / LOW | <notes> |

## 🧱 Current State

<Optional. Include only when replacing/evolving something. Describe existing system, shortcomings, limitations. Diagrams welcome.>

## 🧪 Possible Solutions

### Option A — <name>

**System Behavior.** <…>

**Deployment.** <…>

**Developer Experience.** <…>

**Observability.** <…>

**Security and Compliance.** <…>

**Cost.** <…>

**Reliability.** <…>

**Maintainability.** <…>

**Tradeoffs & Risks.** <…>

**Delivery Effort.** <phased delivery; what ships independently; effort/resourcing>

### Option B — <name>

<same 10 lenses>

### Option C — <name>

<same 10 lenses, if a third alternative is warranted>

## 🎺 Recommended Solution

<2–3 paragraphs. Which option and *why*, explicitly tied to the trade-offs above. Cite the specific lenses that drove the choice.>

## 🛑 Paths Not Taken

- <path>: <one-line rationale for not investigating>
- <path>: <one-line rationale>

## 🤔 Open Questions

- <question>
- <question>
```

**Length guard.** After writing, count words in the body (everything below the closing `---` of frontmatter):

```bash
WORDS=$(awk '/^---$/{c++; next} c>=2' "$DRAFT_PATH" | wc -w | tr -d ' ')
```

- 1,500–2,500 words: ideal.
- 500–1,499 or 2,501–4,000: acceptable; note in reviewer dispatch which side it's on.
- < 500 words: too thin. Push back to the user before dispatching reviewers — likely a lens or alternative is missing.
- > 4,000 words: too verbose. Trim before Step 4.

### Step 4 — Parallel persona review

Dispatch **two `Agent` calls in a single message** (one tool-use block with two `Agent` calls). Each agent gets `Read`-only access — the main loop holds the pen.

Use `subagent_type: "general-purpose"` for both. Pass the absolute draft path so each agent can `Read` it directly.

**Shared response contract** (include in every persona prompt):

```
Return ONLY this format:

  Top issues:
    1. <issue>
    2. <issue>
    3. <issue>

  Missing or unclear:
    - <item>
    - <item>

  Suggested edits:
    - <section>: <concrete edit>
    - <section>: <concrete edit>

  Verdict: approve | revise

Keep your response under 300 words. Critique only — do not rewrite the RFC.
```

**Persona prompts** (substitute `<DRAFT_PATH>` with the absolute path):

**Senior Technical Product Manager:**

```
You are a Senior Technical Product Manager reviewing a TechOps RFC at <DRAFT_PATH>.
The doc follows the TechOps RFC Template: a metadata table (Author / Publish Date / 1-Pager(s) / Epic(s) / Review Until Date / Status), then Background & Motivation, Goals & Scope (with a Requirements table), an optional Current State, Possible Solutions (each evaluated across 10 lenses), Recommended Solution, Paths Not Taken, and Open Questions.

Your concerns:
- Is the business motivation tied to a concrete outcome — or does Background drift into solutioning?
- Are Goals and Non-goals crisp and mutually exclusive? Are Requirements testable (clear acceptance criteria)?
- Is the Recommended Solution defensible from a delivery and risk perspective — and does it cite the trade-offs that drove the choice, not preference?
- Is incremental delivery clearly described (sequencing, what ships independently, decision checkpoints)? Are cross-team dependencies named?
- Is the doc decision-ready, or do material unknowns hide in Open Questions that should be resolved first?

Read the file, then return the response format described below.

[shared response contract]
```

**Principal Platform Engineer:**

```
You are a Principal Platform Engineer reviewing a TechOps RFC at <DRAFT_PATH>.
The doc follows the TechOps RFC Template: a metadata table (Author / Publish Date / 1-Pager(s) / Epic(s) / Review Until Date / Status), then Background & Motivation, Goals & Scope (with a Requirements table), an optional Current State, Possible Solutions (each evaluated across 10 lenses: System Behavior, Deployment, Developer Experience, Observability, Security and Compliance, Cost, Reliability, Maintainability, Tradeoffs & Risks, Delivery Effort), Recommended Solution, Paths Not Taken, and Open Questions.

Your concerns:
- Are the alternatives genuinely explored — or are some strawmen set up to make the recommendation look obvious? Are trade-offs sharp and honest?
- Are all 10 lenses addressed for every alternative at appropriate depth? Where lenses say "N/A," is the rationale credible?
- Are operational concerns covered: SLOs, failure modes, blast radius, rollback strategy, on-call impact?
- Is the recommendation justified by the trade-off analysis itself — not by preference, vendor familiarity, or sunk cost?
- Are Open Questions truly open, or are they unstated assumptions in disguise (i.e. things the recommendation depends on but doesn't acknowledge)?

Read the file, then return the response format described below.

[shared response contract]
```

### Step 5 — Incorporate reviewer feedback

Merge the two reports. Group suggested edits by section. Deduplicate (the personas often flag the same thing from different angles).

Apply edits to `$DRAFT_PATH` using `Edit`. Prefer surgical changes — don't rewrite passages the reviewers didn't flag.

Show the user a compact digest before moving on:

```
Reviewer round 1:

  Senior TPM:           revise (3 issues)
  Principal Platform Engineer: revise (2 issues)

  Merged edits applied:
    - Possible Solutions / Option B / Observability: added trace propagation note (Principal PE)
    - Recommended Solution: tied choice back to Cost + Delivery Effort lenses (Senior TPM, Principal PE)
    - Goals & Scope: split conflated goal "reduce cost and migrate" into two requirements (Senior TPM)
    - Open Questions: removed Q3 — was actually a decision dependency, moved to Recommended Solution caveat (Principal PE)

  Word count: 1,872
```

### Step 6 — User review

Print the draft path and a short summary of what changed. Then ask via `AskUserQuestion`:

- **Approve** — proceed to Step 7 (Confluence draft creation)
- **Revise** — describe what you'd like changed (or edit the file directly, then say "re-review")
- **Cancel** — leave the draft on disk and exit

If the user picks **Revise**, accept either:

- inline change instructions in their answer → apply via `Edit`
- a signal that they edited the file themselves → read the file fresh

Then re-run Step 4 (parallel persona review) on the modified draft, incorporate per Step 5, and return to this step.

**Termination:** the loop exits only when the user picks Approve or Cancel.

**Iteration counter:** track rounds. After **3 rounds**, prepend the Step 6 prompt with:

```
You've done 3 review rounds. Continuing is fine, but this is also a good place
to ship the RFC into Confluence and let the wider reviewers push back on real
content rather than polish.
```

This is a nudge, not a hard cap.

### Step 7 — Create the Confluence DRAFT

Once the user approves at Step 6:

1. **Promote the local file.**

   ```bash
   mv "$DRAFT_PATH" "$FINAL_PATH"
   ```

2. **Resolve cloud id.** Call `mcp__plugin_fbg-core_atlassian__getAccessibleAtlassianResources` and pick the `betfanatics` resource. Use that `id` as `cloudId` for all subsequent Confluence calls.

3. **Verify connectivity.** Call `mcp__plugin_fbg-core_atlassian__getConfluencePage` with `cloudId` and `pageId: "2053603380"` to confirm the TechOps RFC's parent exists and is reachable. If 404 or auth fails, bail with a clear error and instructions to re-auth. The local file is preserved at `$FINAL_PATH`.

4. **Optionally fetch the template** by calling `getConfluencePage` with `pageId: "2097676435"`. The skill already knows the template structure (it's embedded in this SKILL.md), so this call is only needed if you want to confirm the template hasn't drifted. Skip unless something looks off.

5. **Inspect the `createConfluencePage` schema** (use `ToolSearch` with `query: "select:mcp__plugin_fbg-core_atlassian__createConfluencePage"` to load it if it isn't already in scope). Determine:

   - Whether it accepts `spaceKey` (`"FAN"`) or requires `spaceId` (look up via `getConfluenceSpaces` if needed).
   - Whether `status: "draft"` is exposed.
   - Whether `body` accepts markdown directly or requires storage format.
   - Whether `parentId` is the right param name (vs. `parentPageId`).

6. **Pre-call confirmation gate.** If the schema does **not** expose `status: "draft"`, the page will be created as a live (current) page the moment the call fires. The user explicitly said they want this RFC to stay in DRAFT so they can publish themselves. Before calling, ask via `AskUserQuestion`:

   ```
   The MCP tool does not support draft pages. Creating this RFC will publish it
   immediately as a live page under TechOps RFC's in the FAN space — which will
   also fire the #fes-rfcs Slack notification. You wanted to publish manually.

   Continue anyway, or cancel and create the page later via the Confluence UI?
   [Continue / Cancel]
   ```

   If Cancel: exit with the local file at `$FINAL_PATH`. Print the file path and the parent Confluence URL so the user can copy/paste from the local doc into a manually-created page.

   If `status: "draft"` is exposed, skip this prompt and proceed.

7. **Create the page.** Call `mcp__plugin_fbg-core_atlassian__createConfluencePage` with:

   - `cloudId`: from step 2.
   - `spaceKey: "FAN"` (or `spaceId` from the lookup above).
   - `parentId: "2053603380"`.
   - `title`: the RFC title from frontmatter.
   - `body`: the full markdown body (everything below the closing `---` of frontmatter). The template includes a markdown metadata table which Confluence renders natively. No images or macros on a fresh create.
   - `contentFormat: "markdown"`.
   - `status: "draft"` **only** if the schema accepted it.

8. **Persist Confluence identity** — update the file's frontmatter via `Edit`:

   - `status: confluence-draft`
   - `confluence_page_id`: the returned page id
   - `confluence_url`: the returned `_links.webui` (or equivalent absolute URL)

9. **Hand back to the user.** Print:

   ```
   RFC drafted in Confluence.

     File:       raw/rfcs/<slug>.md
     Confluence: <url>   (status: DRAFT)
     Title:      <title>
     Rounds:     <n> review round(s) before approval

   Review the Confluence page and click Publish yourself when ready.
   Publishing fires the #fes-rfcs Slack notification under your identity.
   ```

   Do **not** call `updateConfluencePage` to promote `status: "current"`. That's the user's job.

## Edge Cases

| Condition | Behavior |
|-----------|----------|
| `raw/rfcs/` missing | `mkdir -p` in Step 2. |
| Existing draft/final for the same slug | Step 2 asks Resume / Overwrite / Rename / Cancel. |
| Draft < 500 words | Step 3 pushes back to the user before dispatching reviewers — likely a missing lens or alternative. |
| Draft > 4,000 words | Step 3 trims (or asks user to trim) before Step 4 dispatches reviewers. |
| Only one alternative provided | At Step 1 / Step 3, prompt the user once for at least one credible alternative or strawman to contrast. If they insist on one alternative, write it that way and let the personas flag it. |
| 1-pager link skipped | Nudge once at Step 1; if confirmed, place `TODO — link 1-pager before publishing` in the metadata table. |
| Reviewer agent returns malformed output | Treat as `verdict: revise` with no parsed edits; show the raw output to the user in the merged digest. |
| Both reviewers approve on round 1 | Skip Step 5 incorporation. Surface the two reports in the Step 6 digest so the user sees the verdicts. |
| User picks Revise but makes no concrete request | Re-read the file fresh; if unchanged, ask explicitly: "What would you like changed?" before re-dispatching reviewers. |
| Confluence auth missing/expired | Bail with `Atlassian access failed. Re-authenticate and re-run; your draft at <path> is preserved.` |
| `createConfluencePage` schema lacks `status` field | Detected at Step 7.5. Ask the user via `AskUserQuestion` (Step 7.6) to confirm creating a live page (which fires the Slack notification) or cancel. Default expectation is cancel — user picked manual-publish for this skill. |
| `createConfluencePage` accepts `status` but the request fails at runtime | Surface the error verbatim, do not retry silently. Ask the user whether to retry without `status` (same prompt as above) or cancel with the local file preserved. |
| Mid-step crash after Confluence create | `confluence_page_id` and `confluence_url` are already persisted in frontmatter at Step 7.8. The user can find the draft in Confluence and either Publish or delete it from the UI. |

## Examples

### Example 1 — Happy path, single review round

```
User: /rfc karpenter ARM migration

What problem are we solving / why now? We're running cluster-autoscaler on x86 nodes, costs are growing 18% QoQ, and Graviton instances are 20% cheaper for our workload mix. FinOps wants a decision by 2026-06-15.
What 1-pager backs this RFC? raw/one-pagers/karpenter-org-wide.md
Goals & non-goals? Goals: cut EKS compute spend by ≥15% within 90 days; preserve current pod-startup p99. Non-goals: rewriting workloads for ARM-native libraries; multi-arch images for everything (only the fleet's top 10 services).
Alternatives considered? (1) Karpenter on Graviton with multi-arch images for top-10 services. (2) cluster-autoscaler with mixed instance pools including Graviton. (3) Stay on x86, focus on bin-packing improvements.
Authors? Ian Bartholomew
Jira epics? FANDEVX-2448
Review-until date? 2026-06-15

Wrote draft: ~/Documents/Work/raw/rfcs/.draft-karpenter-arm-migration.md (1,943 words)

Dispatching 2 reviewers in parallel...

Reviewer round 1:
  Senior TPM:                 revise (2 issues)
  Principal Platform Engineer: revise (3 issues)

Merged edits applied:
  - Option B / Reliability: added rollback-time estimate (Principal PE)
  - Option C / Tradeoffs & Risks: surfaced "deferred-decision" cost (Senior TPM, Principal PE)
  - Recommended Solution: explicitly cited Cost + Delivery Effort as the deciding lenses (Senior TPM)
  - Goals & Scope: split "cut spend by 15%" and "preserve p99" into two rows in Requirements (Senior TPM)
  - Open Questions: removed Q2 (it was a Recommended-Solution caveat, not an open question) (Principal PE)

Word count: 2,037

Draft ready. Review at raw/rfcs/.draft-karpenter-arm-migration.md.
[Approve / Revise / Cancel] > Approve

Creating Confluence draft under TechOps RFC's...
Created: https://betfanatics.atlassian.net/wiki/spaces/FAN/pages/2197812345 (DRAFT)

RFC drafted in Confluence.
  File:       raw/rfcs/karpenter-arm-migration.md
  Confluence: https://betfanatics.atlassian.net/wiki/spaces/FAN/pages/2197812345  (status: DRAFT)
  Title:      Karpenter ARM Migration
  Rounds:     1 review round before approval

Review the Confluence page and click Publish yourself when ready.
Publishing fires the #fes-rfcs Slack notification under your identity.
```

### Example 2 — Skip the 1-pager, get nudged

```
User: /rfc launchdarkly big segments cutover

What problem are we solving / why now? <…>
What 1-pager backs this RFC? (skipped)

RFCs are the "how" — they generally come after a 1-pager that established the
"what and why." Are you sure you want to proceed without one? I'll leave a TODO
in the metadata table.

> yes, this is small enough that the 1-pager would just be friction.

Got it. Continuing.

Goals & non-goals? <…>
…
```

### Example 3 — Resume an in-flight RFC

```
User: /rfc karpenter ARM migration

Found existing: raw/rfcs/.draft-karpenter-arm-migration.md
[Resume / Overwrite / Rename / Cancel] > Resume

Loaded draft (2,037 words). Skipping to review.
…
```

## Related

- `~/Documents/Work/wiki/concepts/one-pager-and-rfc.md` — canonical 1-pager vs. RFC doctrine. Read this if the user asks "what goes in an RFC?" or pushes back on the section template.
- `~/Dev/lyt-assistant/skills/one-pager/SKILL.md` — sibling skill (1-pager) that follows the same draft → parallel reviewer → finalize pattern with three personas. Useful reference for the dispatch idiom and the Confluence MCP call sequence.
- TechOps RFC Template — Confluence page `2097676435`, FAN space.
- TechOps RFC's (parent) — Confluence page `2053603380`, FAN space.
