# Todoist Kanban Work Board

Design doc. Status: draft (not committed). Date: 2026-06-11.

## Goal

Make the Todoist Work project's board view the trustworthy operational picture of Ian's work:
columns reflect real JIRA/PR state, cards exist for everything on his plate, and the daily
skills keep it that way without manual bookkeeping.

## Current state (verified 2026-06-11)

- Work project has 4 sections: Backlog, Next Up, In Progress, Blocked.
- No In Review column; an `@In Review` label fakes it. `@blocked` / `@backlog` labels are
  redundant with sections.
- Cards don't reflect reality: active JIRA work (FANDEVX tickets) has no cards; In Progress
  holds vague items; stale cards sit untouched.
- Meeting action items and ad-hoc todos flow into the project (some sectionless).

## Decisions (made with Ian, 2026-06-11)

1. Six-column flow: **Backlog | Next Up | In Progress | In Review | Waiting on Others |
   Blocked**. Done is implicit (completed cards leave the board).
2. Scope: all four improvement modes - column restructure, one-time cleanup, auto-sync from
   JIRA/PRs, daily-ritual reconciliation.
3. PRs are a *state* of a ticket, not their own cards. An open PR on a ticket puts the card
   in In Review.
4. Auto-creation source: JIRA tickets assigned to Ian with `statusCategory != Done`. Nothing
   else auto-creates; manual cards keep arriving as today.
5. Waiting on Others is manual-only: no JIRA state maps to it; sync never moves a card out of
   it (or out of any user-chosen column) unless the underlying JIRA state changed.

## Card identity convention

- JIRA-linked card title: `<KEY> <ticket summary>` (e.g. `FANDEVX-2471 Instaclustr Cassandra -
  Identity Clusters`). The key prefix is the deterministic match handle (`^[A-Z]+-\d+`).
- Card description carries the JIRA URL plus a machine-maintained state line (see Sync state).
- Manual cards: free-form, untouched by sync except section filing nudges in the daily ritual.

## State mapping

| Live state | Column |
|---|---|
| To Do | Next Up |
| In Progress | In Progress |
| In Code Review, or any open PR linked to the ticket | In Review |
| Blocked | Blocked |
| Done / Abandoned / any done-category status | complete the card |

Open-PR detection: `gh search prs --author=ian-at-fes --state=open` filtered by ticket key in
`headRefName` (the branch convention guarantees the key prefix).

## Sync algorithm (deterministic script)

Inputs: a JSON file of assigned open tickets (fetched by the skill via the Atlassian MCP -
scripts cannot call MCP), plus `gh` and `td` CLI access.

For each assigned open ticket:

1. Find the card by key prefix in the Work project (any section).
2. No card: create it in the mapped column with URL + state line in the description.
3. Card exists: compute mapped column.
   - Mapped column == card's column: update state line if stale, done.
   - Differs AND recorded `jira-status` (from the state line) != live status: move card to the
     mapped column, update state line. This is a real state change.
   - Differs AND recorded status == live status: Ian moved it manually (e.g. to Waiting on
     Others or back to Backlog). Respect it; update nothing; report as `manual-override` so
     drift is visible but not "fixed".

For each JIRA-linked card in the project whose ticket is now done-category (or whose key no
longer appears in the assigned-open set because it closed): complete the card. Re-assigned
tickets (open but no longer Ian's): report, don't auto-complete - Ian decides.

The script is idempotent, supports `--dry-run` (prints the move/create/complete plan without
acting), and never touches cards lacking a key prefix.

### Sync state line

Last line of the card description, machine-owned:
`sync: jira-status=<status> | synced=<YYYY-MM-DD>`
This is what distinguishes "JIRA changed" from "Ian moved the card" without external storage.

## Components

| Piece | Form | Home |
|---|---|---|
| `work-board` skill | SKILL.md orchestrating: MCP fetch -> JSON -> script | `~/.dotfiles/stow-packages/claude/.claude/skills/work-board/` |
| Sync script | Python (stdlib + subprocess to `td`/`gh`), all diff/move/create/complete logic | `work-board/scripts/sync.py` |
| Section setup | One-time: create In Review + Waiting on Others via `td section create`; reorder if td supports it (else manual drag in app, noted in cleanup) | part of cleanup |

LLM-vs-script split per Ian's rule: MCP fetch and human-facing reporting are LLM; everything
mechanical (matching, mapping, moving) is the script.

## Skill integrations

- **/start-of-day**: run work-board sync (live mode) after the JIRA fetch it already does;
  report creates/moves/completes/manual-overrides in the daily-note summary.
- **/end-of-day**: run sync in `--dry-run`; surface drift (sectionless manual cards, manual
  overrides, stale Next Up items) as a reconcile list rather than auto-acting.
- **/finish-work**: after transitioning the ticket, complete (or move to In Review) the
  matching card directly - one `td` call, no full sync.
- **/what-next** (optional, later): treat board columns as a signal source. Out of scope for
  the first build.

## One-time cleanup (interactive, with Ian)

1. Create the two new sections.
2. Seed JIRA-linked cards from the current assigned-open set (today: FANDEVX-2471 -> Waiting
   on Others probably, FANDEVX-2601 -> Next Up, FANDEVX-2975 -> Backlog/Next Up - Ian calls each).
3. Triage existing cards: complete/delete stale ones, file the rest into honest columns,
   merge duplicates with seeded JIRA cards.
4. Retire `@In Review`, `@blocked`, `@backlog` labels from Work cards (labels themselves can
   stay for non-board uses; decision at cleanup time).

## Out of scope

- Personal projects / other boards.
- Changing how meeting-action-items creates tasks (sectionless arrivals are caught by the
  end-of-day drift report).
- JIRA REST tokens / direct API access from the script (MCP-fetch hand-off avoids new creds).

## Verification

- `--dry-run` against the live board before the first real run; Ian reviews the plan output.
- After first live sync: board matches the live JIRA snapshot (spot-check the three current
  tickets); re-run is a no-op (idempotency check).
- Manual-override respect: move a card to Waiting on Others, re-run sync, confirm it stays.

## Open items

1. Whether `td` can set section *order* (board column order) - if not, one manual drag in the
   Todoist app during cleanup.
2. Whether to also complete cards for re-assigned tickets automatically (current call: report
   only).
3. Label retirement scope (remove-from-cards vs delete-entirely) - decide during cleanup.
