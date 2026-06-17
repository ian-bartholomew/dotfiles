# Todoist Kanban Work Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `work-board` skill (deterministic `sync.py` + thin SKILL.md orchestrator) that keeps the Todoist Work project's six-column board in sync with Ian's assigned JIRA tickets and open PRs, and wire it into start-of-day, end-of-day, and finish-work.

**Architecture:** SKILL.md does only what a script cannot: fetch assigned tickets via the Atlassian MCP into a JSON file, and resolve "orphan" cards (ticket closed vs re-assigned) per the script's report. `sync.py` (Python stdlib + subprocess to `td`/`gh`) does all matching, column mapping, manual-override detection (via a machine-owned `sync:` state line in each card description), and the create/move/complete actions, with `--dry-run`.

**Tech Stack:** Python 3 stdlib (`unittest` for tests), `td` CLI (verified: `task update --description` exists; task JSON has id/content/description/sectionId), `gh` CLI, Atlassian MCP (skill side only).

**Commit policy:** Do NOT commit. `~/.dotfiles` is on the `cleanup` branch with unrelated uncommitted changes. Leave everything in the working tree.

**Spec:** `~/.dotfiles/docs/specs/2026-06-11-todoist-kanban-work-board-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `stow-packages/claude/.claude/skills/work-board/SKILL.md` | Create | Orchestration: MCP fetch, run script, orphan resolution, reporting |
| `stow-packages/claude/.claude/skills/work-board/scripts/sync.py` | Create | All deterministic logic + td/gh subprocess calls |
| `stow-packages/claude/.claude/skills/work-board/scripts/test_sync.py` | Create | Unit tests for the pure decision logic |
| `stow-packages/claude/.claude/skills/start-of-day/SKILL.md` | Modify | New Step 2.5: live sync + report |
| `stow-packages/claude/.claude/skills/end-of-day/SKILL.md` | Modify | New Step 8.5: dry-run drift report |
| `stow-packages/claude/.claude/skills/finish-work/SKILL.md` | Modify | New Step 4.5: complete/move the ticket's card |

All paths relative to `~/.dotfiles`. Note: `~/.claude/skills/work-board` will resolve via the stow symlink farm only after `stow` re-runs or a manual symlink; Task 5 handles that.

---

## Task 1: Pure logic in sync.py with tests (TDD)

**Files:**

- Create: `stow-packages/claude/.claude/skills/work-board/scripts/sync.py`
- Create: `stow-packages/claude/.claude/skills/work-board/scripts/test_sync.py`

- [ ] **Step 1: Create the test file with failing tests**

```python
"""Tests for the pure decision logic in sync.py."""
import unittest

from sync import (
    extract_key,
    parse_state_line,
    render_state_line,
    strip_state_line,
    map_column,
    decide,
    plan_actions,
)


class TestExtractKey(unittest.TestCase):
    def test_jira_prefixed(self):
        self.assertEqual(extract_key("FANDEVX-2471 Instaclustr Cassandra"), "FANDEVX-2471")

    def test_other_project(self):
        self.assertEqual(extract_key("FESFEAT-603 Self-service SSM"), "FESFEAT-603")

    def test_manual_card(self):
        self.assertIsNone(extract_key("Send Matt the maturity model draft"))

    def test_key_not_at_start(self):
        self.assertIsNone(extract_key("Follow up on FANDEVX-2471 stuff"))


class TestStateLine(unittest.TestCase):
    def test_roundtrip(self):
        line = render_state_line("In Progress", "2026-06-11")
        self.assertEqual(line, "sync: jira-status=In Progress | synced=2026-06-11")
        parsed = parse_state_line("https://jira/browse/X\n" + line)
        self.assertEqual(parsed, {"jira-status": "In Progress", "synced": "2026-06-11"})

    def test_missing(self):
        self.assertIsNone(parse_state_line("just a url"))
        self.assertIsNone(parse_state_line(""))

    def test_strip(self):
        desc = "https://jira/browse/X\nsync: jira-status=To Do | synced=2026-06-10"
        self.assertEqual(strip_state_line(desc), "https://jira/browse/X")


class TestMapColumn(unittest.TestCase):
    def test_todo(self):
        self.assertEqual(map_column("To Do", False), "Next Up")

    def test_in_progress(self):
        self.assertEqual(map_column("In Progress", False), "In Progress")

    def test_code_review_status(self):
        self.assertEqual(map_column("In code review", False), "In Review")

    def test_open_pr_overrides(self):
        self.assertEqual(map_column("In Progress", True), "In Review")

    def test_blocked(self):
        self.assertEqual(map_column("Blocked", False), "Blocked")

    def test_unknown_status_defaults_next_up(self):
        self.assertEqual(map_column("Some New Status", False), "Next Up")


def card(content, section, description=""):
    return {"id": "T1", "content": content, "sectionId": section, "description": description}


def ticket(key, status="In Progress", category="indeterminate",
           summary="Summary", url="https://betfanatics.atlassian.net/browse/X"):
    return {"key": key, "status": status, "statusCategory": category,
            "summary": summary, "url": url}


SECTIONS = {"Backlog": "S1", "Next Up": "S2", "In Progress": "S3",
            "In Review": "S4", "Waiting on Others": "S5", "Blocked": "S6"}


class TestDecide(unittest.TestCase):
    def test_no_card_creates(self):
        a = decide(ticket("A-1", "To Do", "new"), None, set(), SECTIONS, "2026-06-11")
        self.assertEqual(a["action"], "create")
        self.assertEqual(a["column"], "Next Up")

    def test_done_completes(self):
        c = card("A-1 x", "S3", "u\nsync: jira-status=In Progress | synced=2026-06-10")
        a = decide(ticket("A-1", "Done", "done"), c, set(), SECTIONS, "2026-06-11")
        self.assertEqual(a["action"], "complete")

    def test_state_changed_moves(self):
        c = card("A-1 x", "S2", "u\nsync: jira-status=To Do | synced=2026-06-10")
        a = decide(ticket("A-1", "In Progress"), c, set(), SECTIONS, "2026-06-11")
        self.assertEqual(a["action"], "move")
        self.assertEqual(a["column"], "In Progress")

    def test_manual_override_respected(self):
        # Ian moved the card to Waiting on Others; JIRA still In Progress.
        c = card("A-1 x", "S5", "u\nsync: jira-status=In Progress | synced=2026-06-10")
        a = decide(ticket("A-1", "In Progress"), c, set(), SECTIONS, "2026-06-11")
        self.assertEqual(a["action"], "manual-override")

    def test_in_place_noop(self):
        c = card("A-1 x", "S3", "u\nsync: jira-status=In Progress | synced=2026-06-11")
        a = decide(ticket("A-1", "In Progress"), c, set(), SECTIONS, "2026-06-11")
        self.assertEqual(a["action"], "noop")

    def test_no_state_line_adopts_card(self):
        # Pre-existing card without a sync line: treat as state change (sync owns it now).
        c = card("A-1 x", "S1", "")
        a = decide(ticket("A-1", "In Progress"), c, set(), SECTIONS, "2026-06-11")
        self.assertEqual(a["action"], "move")

    def test_open_pr_moves_to_review(self):
        c = card("A-1 x", "S3", "u\nsync: jira-status=In Progress | synced=2026-06-10")
        a = decide(ticket("A-1", "In Progress"), c, {"A-1"}, SECTIONS, "2026-06-11")
        self.assertEqual(a["action"], "move")
        self.assertEqual(a["column"], "In Review")


class TestPlanActions(unittest.TestCase):
    def test_orphan_reported(self):
        cards = [card("A-9 gone", "S3", "u\nsync: jira-status=In Progress | synced=2026-06-10")]
        actions = plan_actions([], cards, set(), SECTIONS, "2026-06-11")
        self.assertEqual(actions[0]["action"], "orphan")
        self.assertEqual(actions[0]["key"], "A-9")

    def test_manual_cards_ignored(self):
        cards = [card("buy milk", "S1")]
        actions = plan_actions([], cards, set(), SECTIONS, "2026-06-11")
        self.assertEqual(actions, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/.dotfiles/stow-packages/claude/.claude/skills/work-board/scripts && python3 -m unittest test_sync -v 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'sync'` (or ImportError).

- [ ] **Step 3: Write the pure logic in sync.py**

```python
#!/usr/bin/env python3
"""Sync the Todoist Work board with assigned JIRA tickets.

Usage:
  python3 sync.py --tickets-file /tmp/work-board-tickets.json [--dry-run] [--project Work]

The tickets file is written by the work-board skill from an Atlassian MCP query:
  [{"key": "FANDEVX-2471", "summary": "...", "status": "In Progress",
    "statusCategory": "indeterminate", "url": "https://..."}]

Pure logic lives in the functions below (unit-tested); td/gh subprocess calls
live in main() and the thin helpers at the bottom.
"""
import argparse
import datetime
import json
import re
import subprocess
import sys

KEY_RE = re.compile(r"^([A-Z][A-Z0-9]*-\d+)\b")
STATE_RE = re.compile(r"^sync: jira-status=(.*?) \| synced=(\d{4}-\d{2}-\d{2})\s*$", re.M)

COLUMNS = ["Backlog", "Next Up", "In Progress", "In Review", "Waiting on Others", "Blocked"]

STATUS_TO_COLUMN = {
    "to do": "Next Up",
    "in progress": "In Progress",
    "in code review": "In Review",
    "blocked": "Blocked",
}


def extract_key(content):
    m = KEY_RE.match(content or "")
    return m.group(1) if m else None


def render_state_line(status, date):
    return f"sync: jira-status={status} | synced={date}"


def parse_state_line(description):
    m = STATE_RE.search(description or "")
    if not m:
        return None
    return {"jira-status": m.group(1), "synced": m.group(2)}


def strip_state_line(description):
    return STATE_RE.sub("", description or "").rstrip("\n")


def map_column(status, has_open_pr):
    if has_open_pr:
        return "In Review"
    return STATUS_TO_COLUMN.get((status or "").lower(), "Next Up")


def decide(tkt, card, open_pr_keys, sections, today):
    """Return one action dict for an assigned-open ticket vs its card (or None card)."""
    key = tkt["key"]
    done = tkt.get("statusCategory") == "done"
    has_pr = key in open_pr_keys
    column = map_column(tkt["status"], has_pr)
    if card is None:
        if done:
            return {"action": "noop", "key": key}
        return {"action": "create", "key": key, "column": column,
                "content": f"{key} {tkt['summary']}",
                "description": f"{tkt['url']}\n{render_state_line(tkt['status'], today)}"}
    base = {"key": key, "card_id": card["id"]}
    if done:
        return {**base, "action": "complete"}
    state = parse_state_line(card.get("description", ""))
    recorded = state["jira-status"] if state else None
    current_col = next((n for n, sid in sections.items() if sid == card.get("sectionId")), None)
    new_desc = (strip_state_line(card.get("description", "")) + "\n" +
                render_state_line(tkt["status"], today)).lstrip("\n")
    if current_col == column:
        if recorded != tkt["status"]:
            return {**base, "action": "update-state", "description": new_desc}
        return {**base, "action": "noop"}
    if recorded is not None and recorded == tkt["status"]:
        return {**base, "action": "manual-override", "column": current_col,
                "mapped": column}
    return {**base, "action": "move", "column": column, "description": new_desc}


def plan_actions(tickets, cards, open_pr_keys, sections, today):
    """Full plan: per-ticket actions plus orphans for linked cards with no open ticket."""
    by_key = {}
    for c in cards:
        k = extract_key(c.get("content", ""))
        if k:
            by_key.setdefault(k, c)
    actions = []
    seen = set()
    for t in tickets:
        seen.add(t["key"])
        a = decide(t, by_key.get(t["key"]), open_pr_keys, sections, today)
        if a["action"] != "noop":
            actions.append(a)
    for k, c in sorted(by_key.items()):
        if k not in seen:
            actions.append({"action": "orphan", "key": k, "card_id": c["id"],
                            "content": c.get("content", "")})
    return actions


# ---------- subprocess layer (not unit-tested; exercised by --dry-run) ----------

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"FATAL: {' '.join(cmd)} failed: {r.stderr.strip()}")
    return r.stdout


def fetch_cards(project):
    data = json.loads(run(["td", "task", "list", "--project", project, "--json", "--all"]))
    return data.get("results", [])


def fetch_sections(project):
    out = run(["td", "section", "list", project])
    sections = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            sections[parts[1].strip()] = parts[0].strip()
    missing = [c for c in COLUMNS if c not in sections]
    if missing:
        sys.exit(f"FATAL: missing board sections {missing} - run setup first")
    return sections


def fetch_open_pr_keys():
    out = run(["gh", "search", "prs", "--author=ian-at-fes", "--state=open",
               "--json", "headRefName", "--limit", "100"])
    keys = set()
    for pr in json.loads(out):
        m = KEY_RE.match(pr.get("headRefName", ""))
        if m:
            keys.add(m.group(1))
    return keys


def execute(action, project):
    a = action["action"]
    if a == "create":
        run(["td", "task", "add", action["content"], "--project", project,
             "--section", action["column"], "--description", action["description"]])
    elif a == "move":
        run(["td", "task", "move", f"id:{action['card_id']}", "--section", action["column"]])
        run(["td", "task", "update", f"id:{action['card_id']}",
             "--description", action["description"]])
    elif a == "update-state":
        run(["td", "task", "update", f"id:{action['card_id']}",
             "--description", action["description"]])
    elif a == "complete":
        run(["td", "task", "complete", f"id:{action['card_id']}"])
    # manual-override and orphan are report-only.


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tickets-file", required=True)
    p.add_argument("--project", default="Work")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    with open(args.tickets_file) as f:
        tickets = json.load(f)
    today = datetime.date.today().isoformat()
    sections = fetch_sections(args.project)
    cards = fetch_cards(args.project)
    pr_keys = fetch_open_pr_keys()
    actions = plan_actions(tickets, cards, pr_keys, sections, today)

    for a in actions:
        print(json.dumps(a))
    if args.dry_run:
        print(f"# dry-run: {len(actions)} action(s), nothing executed", file=sys.stderr)
        return
    for a in actions:
        execute(a, args.project)
    print(f"# executed {len(actions)} action(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/.dotfiles/stow-packages/claude/.claude/skills/work-board/scripts && python3 -m unittest test_sync -v 2>&1 | tail -3`
Expected: `OK` with 17 tests.

NOTE (no commit): leave changes uncommitted per the commit policy.

---

## Task 2: work-board SKILL.md

**Files:**

- Create: `stow-packages/claude/.claude/skills/work-board/SKILL.md`

- [ ] **Step 1: Write the skill**

````markdown
---
name: work-board
description: Sync the Todoist Work project's kanban board with assigned JIRA tickets and open PRs. Triggers on "sync my board", "work board", "update my kanban", "/work-board", or as a step inside /start-of-day (live), /end-of-day (dry-run), and /finish-work (single-card). Deterministic script does all moves; the LLM only fetches JIRA via MCP and resolves orphans.
---

# Work Board Sync

Keeps the Todoist **Work** project board (Backlog | Next Up | In Progress | In Review |
Waiting on Others | Blocked) in sync with live JIRA/PR state.

Card contract: JIRA-linked cards are titled `<KEY> <summary>`; the description ends with a
machine-owned line `sync: jira-status=<status> | synced=<date>`. Cards without a key prefix
are never touched. A card whose column disagrees with JIRA while its recorded status is
unchanged was moved by Ian on purpose - the script reports it as `manual-override` and leaves it.

## Steps

### 1. Fetch assigned tickets (MCP - the only LLM data step)

Call `mcp__plugin_fbg-core_atlassian__searchJiraIssuesUsingJql` with
`assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC`
(fields: summary, status). Write `/tmp/work-board-tickets.json`:

```json
[{"key": "FANDEVX-2471", "summary": "...", "status": "In Progress",
  "statusCategory": "indeterminate",
  "url": "https://betfanatics.atlassian.net/browse/FANDEVX-2471"}]
```

`status` = live status NAME; `statusCategory` = its statusCategory key (new/indeterminate/done).

### 2. Run the script

```bash
python3 ~/.claude/skills/work-board/scripts/sync.py \
  --tickets-file /tmp/work-board-tickets.json [--dry-run]
```

Live mode for /start-of-day and direct invocations; `--dry-run` for /end-of-day drift reports.
The script prints one JSON action per line: create / move / update-state / complete /
manual-override / orphan. It exits FATAL if the six board sections don't exist.

### 3. Resolve orphans (LLM)

For each `orphan` action (JIRA-linked card whose key wasn't in the assigned-open set), call
`getJiraIssue` for the key:
- done-category status -> `td task complete id:<card_id>`
- still open but assigned to someone else -> report ("re-assigned to X - complete or keep?");
  do NOT auto-complete.
- MCP failure -> report the orphan as unverified; do not guess.

### 4. Report

Summarize in one short block: created N, moved N (from -> to with keys), completed N,
manual overrides (key: column vs mapped - informational), orphans resolved/flagged.
In dry-run, prefix with "drift report - no changes made".

## Setup (one-time, idempotent)

```bash
td section create --project "Work" --name "In Review"
td section create --project "Work" --name "Waiting on Others"
```

Column ORDER cannot be set via td - drag columns in the Todoist app once:
Backlog | Next Up | In Progress | In Review | Waiting on Others | Blocked.
````

- [ ] **Step 2: Verify frontmatter and structure**

Run: `head -5 ~/.dotfiles/stow-packages/claude/.claude/skills/work-board/SKILL.md`
Expected: frontmatter with `name: work-board` and the trigger-rich description.

---

## Task 3: Integrate into the three skills

**Files:**

- Modify: `stow-packages/claude/.claude/skills/start-of-day/SKILL.md` (after `### Step 2: Fetch all sources in parallel`, before `### Step 3: Invoke the render script`)
- Modify: `stow-packages/claude/.claude/skills/end-of-day/SKILL.md` (after `### Step 8: Verify-Status (read-only snapshot)`, before `### Step 9: Daily-Note Synthesis`)
- Modify: `stow-packages/claude/.claude/skills/finish-work/SKILL.md` (after `## Step 4: Determine and execute the JIRA transition`, before `## Step 5: Find the project folder`)

- [ ] **Step 1: start-of-day - add Step 2.5**

Insert this section between Step 2 and Step 3:

```markdown
### Step 2.5: Work-board sync (live)

Run the `work-board` skill in live mode, reusing the assigned-open JIRA result already
fetched in Step 2 (write it to `/tmp/work-board-tickets.json` in the shape the skill
documents; do not re-query JIRA). Include the sync summary (created / moved / completed /
manual overrides / orphans) in the Step 5 terminal confirmation. A sync failure is
non-fatal to start-of-day: report it and continue - the board is a view, not a gate.
```

- [ ] **Step 2: end-of-day - add Step 8.5**

Insert this section between Step 8 and Step 9:

```markdown
### Step 8.5: Work-board drift report (dry-run)

Run the `work-board` skill with `--dry-run`. Do not execute moves. Surface in the EOD
summary: pending moves the morning sync will make, manual overrides (cards Ian parked
deliberately), orphans needing a decision, and any sectionless cards in the Work project
(`td task list --project "Work" --json` entries with null sectionId) as filing candidates.
```

- [ ] **Step 3: finish-work - add Step 4.5**

Insert this section between Step 4 and Step 5:

```markdown
## Step 4.5: Update the work-board card

After the JIRA transition, update the ticket's Todoist card directly (no full sync):

- Find it: `td task list --project "Work" --json --all` filtered to content starting with
  `<TICKET-KEY> `. No card -> skip silently (not all tickets have cards).
- Transitioned to Done (or any done-category status): `td task complete id:<card_id>`.
- Transitioned to In Code Review: `td task move id:<card_id> --section "In Review"`, then
  `td task update id:<card_id> --description "<existing description with the sync: line's
  jira-status replaced by the new status and synced set to today>"`.
- Report the card action in the Step 9 final summary.
```

- [ ] **Step 4: Verify all three insertions**

Run: `grep -c "work-board\|Work-board" ~/.dotfiles/stow-packages/claude/.claude/skills/{start-of-day,end-of-day,finish-work}/SKILL.md`
Expected: each file reports >= 1.

---

## Task 4: Stow link + section setup + live dry-run

**Files:** none new (symlink + Todoist sections + verification)

- [ ] **Step 1: Make the new skill visible at ~/.claude/skills**

```bash
ls ~/.claude/skills/work-board 2>/dev/null || ln -s ../../.dotfiles/stow-packages/claude/.claude/skills/work-board ~/.claude/skills/work-board
ls -la ~/.claude/skills/work-board/
```

Expected: symlink resolves; SKILL.md and scripts/ visible. (Matches the relative-target style of the existing symlinks; `stow` re-run would produce the same.)

- [ ] **Step 2: Create the two new sections (idempotent)**

```bash
td section list "Work"
td section create --project "Work" --name "In Review"        # skip if listed
td section create --project "Work" --name "Waiting on Others" # skip if listed
td section list "Work"
```

Expected: six sections listed (order fixed later by hand in the app).

- [ ] **Step 3: Build a real tickets file and dry-run against the live board**

Fetch assigned-open tickets via the Atlassian MCP (per the SKILL.md shape) and write
`/tmp/work-board-tickets.json`. Then:

```bash
python3 ~/.claude/skills/work-board/scripts/sync.py \
  --tickets-file /tmp/work-board-tickets.json --dry-run
```

Expected: one JSON action per line - `create` for each assigned ticket with no card,
`orphan` only if a stale key-prefixed card exists, no crashes; stderr says
`# dry-run: N action(s), nothing executed`.

- [ ] **Step 4: Run the unit tests one final time**

Run: `cd ~/.dotfiles/stow-packages/claude/.claude/skills/work-board/scripts && python3 -m unittest test_sync 2>&1 | tail -2`
Expected: `OK`.

- [ ] **Step 5: Stop - hand back for the interactive cleanup**

Do NOT execute a live sync yet. The first live run happens during the interactive cleanup
session with Ian (triage stale cards, seed/merge JIRA cards, retire @In Review/@blocked/
@backlog labels, drag column order in the app), driven from the dry-run output above.

---

## Self-Review notes

- **Spec coverage:** card identity + state line (Task 1 logic), state mapping incl. PR
  override (map_column), manual-override respect (decide), orphan flow split script/LLM
  (plan_actions + SKILL.md step 3), auto-create scope (assigned-open only), dry-run +
  idempotency (noop actions; Task 4 step 3), skill integrations (Task 3 - all three),
  section setup + order limitation (Task 4 / SKILL.md setup), cleanup interactive and
  out of build scope (Task 4 step 5). Open item 1 (section order) resolved: not supported,
  manual drag documented. Open items 2-3 deferred to cleanup as the spec says.
- **Placeholder scan:** none; all code complete.
- **Consistency:** action dict fields (`action`, `key`, `card_id`, `column`, `content`,
  `description`, `mapped`) match between decide/plan_actions/execute and the tests;
  section names match COLUMNS everywhere; tickets-file shape identical in sync.py
  docstring, SKILL.md, and Task 4.
