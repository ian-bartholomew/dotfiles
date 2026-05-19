---
name: one-pager
description: This skill should be used when the user asks to "write a one-pager", "draft a one-pager", "make a one-pager", or "create a TechOps one-pager". Drafts a persuasion-focused one-page proposal in raw/one-pagers/ following the TechOps 1 Pager Template (Confluence page 1898676290, FAN space) — header table (Published / Authors / Jira Tickets / Status) and sections What / Why / Other Considerations / Additional Reading. Runs three parallel agent personas (TPM, Lead Platform Engineer, Engineering Manager) for feedback, iterates with the user until approved, then publishes as a draft child of the TechOps 1-Pagers page in Confluence (parent 1164869728, FAN space) and gates the final publish on user confirmation.
version: 0.2.0
argument-hint: "[topic or short pitch]"
allowed-tools: [Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion, Agent, mcp__plugin_fbg-core_atlassian__getAccessibleAtlassianResources, mcp__plugin_fbg-core_atlassian__getConfluencePage, mcp__plugin_fbg-core_atlassian__createConfluencePage, mcp__plugin_fbg-core_atlassian__updateConfluencePage]
---

# One-Pager Skill

Draft, review, and publish a TechOps one-pager end-to-end. The skill writes a draft to `raw/one-pagers/`, runs three parallel agent reviewers (TPM, Lead Platform Engineer, Engineering Manager), iterates with the user, then publishes to Confluence as a draft under `TechOps 1-Pagers` (parent page `1164869728`, space `FAN`) and gates the final publish on the user's confirmation in Confluence.

## When to Use

Invoke this skill when:

- User runs `/lyt-assistant:one-pager` (with or without a topic argument)
- User says "write a one-pager", "draft a one-pager", "make a one-pager", "create a TechOps one-pager", or any variation
- User wants to socialize a proposal with stakeholders before committing to an RFC

## Format Reference

The canonical section structure is the **TechOps 1 Pager Template** in Confluence (page id `1898676290`, FAN space). This skill mirrors that template exactly so drafts publish cleanly under the same parent and read consistently with sibling one-pagers.

Persuasion principles come from the user's wiki at `~/Documents/Work/wiki/concepts/one-pager-and-rfc.md`:

- **Audience:** anyone who decides whether the thing happens (managers, VPs, peer leads). Usually *not* engineers who'd build it.
- **Goal:** agreement on the *outcome* and the *why*, not on the design.
- **Length:** the wiki says only *"One page. Anything longer is no longer a one-pager"* and flags *"one-pager that's actually three pages"* as the top anti-pattern. This skill operationalizes that with a soft target of 400–600 words and a hard trim above 700 — those numbers are this skill's calibration, not wiki-quoted.
- **Style:** persuasive, not exhaustive. Skip implementation details — they invite premature objections about *how* before *whether*.

Header metadata (template table at the top of every page):

| Field | Notes |
|-------|-------|
| **Published** | Date the page goes live (or current date for drafts). |
| **Authors** | One or more author names. |
| **Jira Tickets** | Optional related tickets. |
| **Status** | One of `Draft`, `Defining`, `Building`, `Completed`. |

Required sections (every one-pager):

1. **What** *(required)* — the outcome you hope to accomplish. Describe the *outcome*, not the technical approach. Avoid prescriptive tool selection.
2. **Why** *(required)* — why this needs to be considered and prioritized. Not the same as "what".

Optional sections (include when they add signal):

1. **Other Considerations** *(optional)* — security, compliance, developer experience, operational load, anything else that should be weighed.
2. **Additional Reading** *(optional)* — context links (RFCs, dashboards, prior one-pagers, vendor docs).

## Paths

All file paths are relative to the **vault root** (`~/Documents/Work/`):

- Drafts: `raw/one-pagers/.draft-<slug>.md`
- Final: `raw/one-pagers/<slug>.md`

The dot-prefix on drafts mirrors the `research` skill's convention and hides them from any future `/compile`-style scanners.

## Workflow

### Step 1 — Gather input

If the user passed a topic on the command line (e.g. `/lyt-assistant:one-pager adopt Karpenter org-wide`), use it as the working title. Otherwise ask:

```
What's the working title for this one-pager?
```

Then collect the remaining inputs via plain conversational prompts (one at a time, accept multi-line answers). Do **not** use `AskUserQuestion` here — the answers are free text, not menu choices.

| Field | Prompt |
|-------|--------|
| Audience | Who decides whether this happens? (e.g. "VP Eng + peer team leads") |
| Authors | Author name(s) for the header table (defaults to `Ian Bartholomew` if blank). |
| Jira tickets | Optional. Related tickets (comma-separated keys, e.g. `FANDEVX-2444, FESFEAT-428`). Leave blank to omit. |
| What | One paragraph: the *outcome* you hope to accomplish. Describe the outcome, not the technical approach. Avoid prescribing tools. |
| Why | One paragraph: why this needs to be considered and prioritized. Not the same as "what". |
| Other considerations | Optional. Security, compliance, developer experience, operational load, edge cases. Leave blank to omit. |
| Additional reading | Optional. Context links (RFCs, dashboards, prior one-pagers, vendor docs). Leave blank to omit. |

If the user gives a very short answer for any field, accept it — the persona reviewers in Step 4 will flag thinness.

### Step 2 — Pre-flight checks

```bash
VAULT_ROOT="$HOME/Documents/Work"
ONE_PAGERS_DIR="$VAULT_ROOT/raw/one-pagers"
mkdir -p "$ONE_PAGERS_DIR"

# Derive kebab-case slug from title
SLUG=$(printf '%s' "$TITLE" \
  | tr '[:upper:]' '[:lower:]' \
  | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')

DRAFT_PATH="$ONE_PAGERS_DIR/.draft-$SLUG.md"
FINAL_PATH="$ONE_PAGERS_DIR/$SLUG.md"
```

If either `$DRAFT_PATH` or `$FINAL_PATH` already exists, ask via `AskUserQuestion`:

- **Resume** — re-open the existing draft and skip ahead to Step 4
- **Overwrite** — discard the existing file and write fresh
- **Rename** — prompt for a new title and recompute the slug
- **Cancel** — exit

### Step 3 — Write the draft

Write `$DRAFT_PATH` with this template. The header table mirrors the canonical TechOps 1 Pager Template (Confluence page `1898676290`) so the published page reads identically to its siblings. Omit any optional section the user left blank — do not include a heading with placeholder filler.

```markdown
---
title: <Title>
audience: <Audience>
authors: <Authors>
date: <YYYY-MM-DD>
status: draft
jira_tickets: <comma-separated keys or empty>
confluence_parent_id: "1164869728"
confluence_template_id: "1898676290"
confluence_space: FAN
confluence_page_id: null
confluence_url: null
---

# <Title>

| **Published** | <YYYY-MM-DD> |
| --- | --- |
| **Authors** | <Authors> |
| **Jira Tickets** | <ticket keys or `—`> |
| **Status** | Draft |

## What

<One paragraph describing the *outcome* you hope to accomplish. Do not go deep on technical approach; do not prescribe tools.>

## Why

<One paragraph explaining why this needs to be considered and prioritized. Different from "what".>

## Other Considerations

<Optional. Security, compliance, developer experience, operational load, edge cases.>

## Additional Reading

<Optional. Context links (RFCs, dashboards, prior one-pagers, vendor docs).>
```

**Status field semantics.** The header `Status` row tracks the *lifecycle* of the proposal in Confluence (`Draft` → `Defining` → `Building` → `Completed`), per the TechOps template's enum. Drafts always start at `Draft`. The frontmatter `status` field tracks the *local file* lifecycle (`draft` → `published`) and is set automatically by this skill — do not conflate the two.

**Length guard.** After writing, count words in the body (everything below the closing `---` of frontmatter):

```bash
WORDS=$(awk '/^---$/{c++; next} c>=2' "$DRAFT_PATH" | wc -w | tr -d ' ')
```

- ≤ 600 words: ideal.
- 601–700: acceptable, note in the reviewer dispatch.
- > 700: trim before Step 4. The wiki explicitly calls out *"one-pager that's actually three pages"* as the top anti-pattern.

### Step 4 — Parallel persona review

Dispatch **three `Agent` calls in a single message** (one tool-use block with three `Agent` calls). Each agent gets `Read`-only access — the main loop holds the pen.

Use `subagent_type: "general-purpose"` for all three. Pass the absolute draft path so each agent can `Read` it directly.

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

Keep your response under 250 words. Do not write the one-pager yourself — only critique.
```

**Persona prompts** (substitute `<DRAFT_PATH>` with the absolute path):

**TPM:**

```
You are a Technical Project Manager reviewing a one-pager at <DRAFT_PATH>.
The page follows the TechOps 1 Pager Template: a header table (Published / Authors / Jira Tickets / Status), then **What**, **Why**, optionally **Other Considerations** and **Additional Reading**.

Your concerns:
- Is **What** a clear, bounded outcome — or does it drift into prescribing technical approach/tools (template explicitly bars this)?
- Does **Why** make the prioritization case, or does it just restate **What**?
- Are cross-team dependencies, sequencing, and timeline implications surfaced (in Why or Other Considerations)?
- Is the Jira Tickets cell populated where applicable? Is Status appropriate (Draft for new proposals)?

Read the file, then return the response format described below.

[shared response contract]
```

**Lead Platform Engineer:**

```
You are a Lead Platform Engineer reviewing a one-pager at <DRAFT_PATH>.
The page follows the TechOps 1 Pager Template: a header table (Published / Authors / Jira Tickets / Status), then **What**, **Why**, optionally **Other Considerations** and **Additional Reading**.

Your concerns:
- Is the outcome in **What** technically feasible without leaking into design? (Template explicitly discourages tool/approach prescription here.)
- Is **Why** honest about the cost — or does it oversell the upside?
- Are second-order effects (migration paths, operational load, security/compliance/DX, edge cases) captured in **Other Considerations**? If that section is missing entirely, is its absence justified?
- Does the framing risk misleading non-engineers about real implementation cost?

Read the file, then return the response format described below.

[shared response contract]
```

**Engineering Manager:**

```
You are an Engineering Manager reviewing a one-pager at <DRAFT_PATH>.
The page follows the TechOps 1 Pager Template: a header table (Published / Authors / Jira Tickets / Status), then **What**, **Why**, optionally **Other Considerations** and **Additional Reading**.

Your concerns:
- Does **Why** answer "why prioritize this now over what we're already doing"? Capacity/displacement implicit?
- Is the outcome in **What** worth the prioritization cost it implies?
- Will the named audience actually buy in, or is the framing off for them?
- Are staffing or hiring assumptions hidden inside **Why** or **Other Considerations**?

Read the file, then return the response format described below.

[shared response contract]
```

### Step 5 — Incorporate reviewer feedback

Merge the three reports. Group suggested edits by section. Deduplicate (multiple reviewers often flag the same thing).

Apply edits to `$DRAFT_PATH` using `Edit`. Prefer surgical changes — don't rewrite passages the reviewers didn't flag.

Show the user a compact digest before moving on:

```
Reviewer round 1:

  TPM: revise (3 issues)
  Lead Platform Engineer: revise (2 issues)
  Engineering Manager: approve

  Merged edits applied:
    - What: stripped "by deploying Karpenter v0.34" — too prescriptive (TPM, Lead PE)
    - Why: added Q3 cost-review forcing function (TPM, EM)
    - Other Considerations: added "node-replacement churn during pilot" (Lead PE)
    - Header: added FANDEVX-2448 to Jira Tickets row (TPM)

  Word count: 487
```

### Step 6 — User review

Print the draft path and a short diff-style summary of what changed. Then ask via `AskUserQuestion`:

- **Approve** — proceed to Confluence publish
- **Revise** — describe what you'd like changed (or edit the file directly, then say "re-review")
- **Cancel** — leave the draft on disk and exit

If the user picks **Revise**, accept either:

- inline change instructions in their answer → apply via `Edit`
- a signal that they edited the file themselves → read the file fresh

Then loop to Step 7.

### Step 7 — Re-review loop

1. Re-run Step 4 (parallel persona review) on the modified draft.
2. Incorporate feedback per Step 5.
3. **Go to Step 6.** Always. There is no other exit from Step 7.

**Termination:** the loop exits only when the user picks Approve or Cancel at Step 6.

**Iteration counter:** track rounds. After **3 rounds**, prepend the Step 6 prompt with:

```
You've done 3 review rounds. Continuing is fine, but this is also a good place
to ship and let the audience push back on real content rather than polish.
```

This is a nudge, not a hard cap.

### Step 8 — Publish to Confluence as draft

Once the user approves at Step 6:

1. **Verify connectivity.** Call `mcp__plugin_fbg-core_atlassian__getConfluencePage` with `pageId: "1164869728"` to confirm the TechOps 1-Pagers parent exists and is reachable on the current auth. If 404 or auth fails, bail with a clear error and instructions to re-auth. Optionally call `getAccessibleAtlassianResources` first if you need to discover the FAN cloud id — but the subsequent create/update calls in this skill do not consume `cloudId` directly; the MCP plugin resolves the tenant from the authenticated session.

2. **Inspect the `createConfluencePage` schema** (use `ToolSearch` with `query: "select:mcp__plugin_fbg-core_atlassian__createConfluencePage"` to load it if it isn't already in scope). Determine:
   - Whether it accepts `spaceKey` or requires `spaceId` (the latter would need a lookup via `getConfluenceSpaces`).
   - Whether `status: "draft"` is exposed.
   - Whether `body` accepts markdown directly or requires storage format.

3. **Pre-call confirmation gate.** If the schema does **not** expose `status: "draft"`, the page will be created as a live (current) page in a public space the moment the call fires. Before calling, ask via `AskUserQuestion`:

   ```
   The MCP tool does not support draft pages. Creating this one-pager will
   publish it immediately as a live page under TechOps 1-Pagers in the FAN
   space. The Step 9 "publish" gate will then be a no-op.

   Continue and create as a live page?
   [Continue / Cancel]
   ```

   If the user picks Cancel, exit the skill with the local draft preserved at `$DRAFT_PATH`. Do not create the page.

   If `status: "draft"` is exposed, skip this prompt and proceed.

4. **Create the page.** Call `mcp__plugin_fbg-core_atlassian__createConfluencePage` with:
   - `spaceKey: "FAN"` (or `spaceId` from the lookup above)
   - `parentId: "1164869728"`
   - `title`: the one-pager title from frontmatter
   - `body`: the markdown body (everything below the frontmatter's closing `---`). The template includes a header metadata table (Published / Authors / Jira Tickets / Status) — markdown tables render natively in Confluence, no conversion needed. There are no images or macros to preserve on a fresh create.
   - `contentFormat: "markdown"`
   - `status: "draft"` only if the schema accepted it in step 2.

5. **Persist Confluence identity** — update the draft's frontmatter via `Edit`:
   - `confluence_page_id`: the returned page id
   - `confluence_url`: the returned `_links.webui` (or equivalent absolute URL)

### Step 9 — User confirms the Confluence page

Print the Confluence URL and ask via `AskUserQuestion`:

- **Publish** — promote the page from draft → current
- **Revise** — describe changes; apply to the local draft via `Edit`, then push to Confluence via `mcp__plugin_fbg-core_atlassian__updateConfluencePage` (keeping `status: "draft"` if supported), then loop back to this step
- **Cancel** — leave the page as draft in Confluence and exit (state is preserved in the local file's frontmatter; a re-run can resume)

**Reviewer personas do not re-run here.** Step 9 is a last-mile polish gate, not a content gate — the persona reviewers already approved at Step 6. If the user makes a substantive (non-cosmetic) change at this stage, mention this in the response: "I've updated the local draft and pushed to Confluence. The reviewer personas don't re-run at this stage — if you'd like another full review round, cancel and re-run the skill on the published draft."

On **Publish**: call `mcp__plugin_fbg-core_atlassian__updateConfluencePage` with `status: "current"` (transitioning from draft). If `status` is not exposed on the update tool, the page is already live from Step 8 and this step is a no-op rename — proceed to Step 10.

### Step 10 — Finalize

```bash
mv "$DRAFT_PATH" "$FINAL_PATH"
```

Update the frontmatter on the renamed file:

- `status: published`
- `published_at: <ISO 8601 with timezone offset>`

Print the final summary:

```
One-pager published.

  File:      raw/one-pagers/<slug>.md
  Confluence: <url>
  Title:     <title>
  Rounds:    <n> review round(s) before approval
```

## Edge Cases

| Condition | Behavior |
|-----------|----------|
| `raw/one-pagers/` missing | `mkdir -p` in Step 2. |
| Existing draft/final for the same slug | Step 2 asks Resume / Overwrite / Rename / Cancel. |
| Draft over 700 words | Step 3 trims (or asks user to trim) before Step 4 dispatches reviewers. |
| Reviewer agent returns malformed output | Treat as `verdict: revise` with no parsed edits; show the raw output to the user in the merged digest. |
| All three reviewers approve on round 1 | Skip Step 5 incorporation. Still surface the three reports in the Step 6 digest so the user sees the verdicts. |
| User picks Revise but makes no concrete request | Re-read the file fresh; if unchanged, ask explicitly: "What would you like changed?" before re-dispatching reviewers. |
| Confluence auth missing/expired | Bail with `Atlassian access failed. Re-authenticate and re-run; your draft at <path> is preserved.` |
| `createConfluencePage` schema lacks `status` field | Detected at Step 8.2. Ask the user via `AskUserQuestion` (Step 8.3) to confirm creating a live page before the call fires. On Continue, create without `status`; Step 9 publish becomes a no-op. On Cancel, exit with the local draft preserved. |
| `createConfluencePage` accepts `status` but the request fails at runtime | Surface the error verbatim, do not retry silently. Ask the user whether to retry without `status` (same prompt as above) or cancel. |
| User picks Cancel at Step 9 | Leave `confluence_page_id` and `confluence_url` populated in the draft's frontmatter so a re-run can pick up where it left off. |
| Mid-step crash | Anything already persisted (draft on disk, Confluence page id in frontmatter) is preserved. Re-running the skill with the same title resumes via the Step 2 prompt. |

## Examples

### Example 1 — Happy path, single review round

```
User: /lyt-assistant:one-pager adopt Karpenter org-wide

What's the audience? VP Eng + EKS team leads
Authors? Ian Bartholomew
Jira tickets? FANDEVX-2448
What (outcome)? Reduce EKS capacity waste and pod-pending events across the org by migrating from cluster-autoscaler to a workload-aware node provisioner. (No tool prescription in this section.)
Why? Q3 cost review flagged a $180k overrun in unused EKS capacity; FinOps wants a credible plan by end of month, and the pod-pending events are causing visible latency in identity flows.
Other considerations? Node-replacement churn during cutover; security review needed for any new IAM roles.
Additional reading? RFC-104 (cluster autoscaling alternatives), Q3 FinOps memo.

Wrote draft: ~/Documents/Work/raw/one-pagers/.draft-adopt-karpenter-org-wide.md (512 words)

Dispatching 3 reviewers in parallel...

Reviewer round 1:
  TPM: approve
  Lead Platform Engineer: revise (2 issues)
  Engineering Manager: approve

Merged edits applied:
  - What: removed "by deploying Karpenter" — too prescriptive, drifted into approach (Lead PE)
  - Other Considerations: added "audit-log volume increase during pilot" (Lead PE)

Word count: 528

Draft ready. Review at raw/one-pagers/.draft-adopt-karpenter-org-wide.md.
[Approve / Revise / Cancel] > Approve

Creating Confluence draft under TechOps 1-Pagers...
Created: https://betfanatics.atlassian.net/wiki/spaces/FAN/pages/1198273024 (draft)

Review the Confluence draft. Ready to publish?
[Publish / Revise / Cancel] > Publish

Promoted draft to current.

One-pager published.
  File:      raw/one-pagers/adopt-karpenter-org-wide.md
  Confluence: https://betfanatics.atlassian.net/wiki/spaces/FAN/pages/1198273024
  Title:     Adopt Karpenter Org-Wide
  Rounds:    1 review round before approval
```

### Example 2 — Two review rounds, user-initiated revision

```
[round 1 reviewers + edits as above]

[Approve / Revise / Cancel] > Revise
What would you like changed? Tighten What to one paragraph; the second paragraph is starting to read like a design.

Applied edit. Re-dispatching 3 reviewers...

Reviewer round 2:
  TPM: approve
  Lead Platform Engineer: approve
  Engineering Manager: approve

No new edits suggested. Word count: 498.

[Approve / Revise / Cancel] > Approve
...
```

### Example 3 — Resume an in-flight one-pager

```
User: /lyt-assistant:one-pager adopt Karpenter org-wide

Found existing: raw/one-pagers/.draft-adopt-karpenter-org-wide.md
[Resume / Overwrite / Rename / Cancel] > Resume

Loaded draft (512 words). Skipping to review.
...
```

## Related

- `~/Documents/Work/wiki/concepts/one-pager-and-rfc.md` — canonical one-pager vs RFC reference. Read this if the user asks "what goes in a one-pager?" or pushes back on the section template.
- `skills/research/SKILL.md` — sibling skill that follows the same draft → parallel reviewer → finalize pattern. Useful reference for the dispatch idiom.
- `skills/meeting-action-items/SKILL.md` — sibling skill that uses interactive `AskUserQuestion` mid-loop with state preserved across interruptions.
