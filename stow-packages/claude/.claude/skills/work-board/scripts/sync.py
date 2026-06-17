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

PRIORITIES = {"p1", "p2", "p3", "p4"}

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
    m = None
    for m in STATE_RE.finditer(description or ""):
        pass  # keep the LAST matching line
    if not m:
        return None
    return {"jira-status": m.group(1), "synced": m.group(2)}


def strip_state_line(description):
    """Only the final line, when it is exactly a state line, is machine-owned."""
    lines = (description or "").rstrip("\n").splitlines()
    if lines and STATE_RE.fullmatch(lines[-1].strip()):
        lines = lines[:-1]
    return "\n".join(lines)


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
        a = {"action": "create", "key": key, "column": column,
             "content": f"{key} {tkt['summary']}",
             "description": f"{tkt['url']}\n{render_state_line(tkt['status'], today)}"}
        # Priority is LLM-inferred (skill step 2) and applied on create only;
        # existing cards keep whatever priority Ian set by hand.
        if tkt.get("priority") in PRIORITIES:
            a["priority"] = tkt["priority"]
        return a
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
    jira_only_col = map_column(tkt["status"], False)
    if recorded is not None and recorded == tkt["status"] and current_col != jira_only_col:
        return {**base, "action": "manual-override", "column": current_col,
                "mapped": column}
    return {**base, "action": "move", "column": column, "description": new_desc}


ACTIVE_COLUMNS = ["Next Up", "In Progress"]


def find_stale(cards, active_ids, sections, stale_days):
    """Report-only: manual (non-key-prefixed) cards sitting in an active column
    with no Todoist activity in the lookback window. JIRA-linked cards are
    excluded - their freshness is governed by JIRA state, not card activity."""
    active_section_ids = {sections[c] for c in ACTIVE_COLUMNS if c in sections}
    out = []
    for c in cards:
        if extract_key(c.get("content", "")):
            continue
        if c.get("sectionId") not in active_section_ids:
            continue
        if c["id"] in active_ids:
            continue
        col = next((n for n, sid in sections.items() if sid == c.get("sectionId")), None)
        out.append({"action": "stale", "card_id": c["id"], "column": col,
                    "days": stale_days, "content": c.get("content", "")})
    return out


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
        # td section IDs are alphanumeric tokens (e.g. 64PjRpmjr92hXmgv), not numeric.
        if len(parts) == 2 and len(parts[0]) >= 10 and parts[0].isalnum():
            sections[parts[1].strip()] = parts[0].strip()
    missing = [c for c in COLUMNS if c not in sections]
    if missing:
        sys.exit(f"FATAL: missing board sections {missing} - run setup first")
    return sections


def fetch_open_pr_keys():
    # gh search prs does not support the headRefName field; fetch it per-PR.
    out = run(["gh", "search", "prs", "--author=ian-at-fes", "--state=open",
               "--json", "number,repository", "--limit", "100"])
    keys = set()
    for pr in json.loads(out):
        repo = pr.get("repository", {}).get("nameWithOwner")
        num = pr.get("number")
        if not repo or not num:
            continue
        r = subprocess.run(["gh", "pr", "view", str(num), "--repo", repo,
                            "--json", "headRefName"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            continue  # PR may have closed between search and view; skip
        m = KEY_RE.match(json.loads(r.stdout).get("headRefName", ""))
        if m:
            keys.add(m.group(1))
    return keys


def fetch_active_ids(project, since_date):
    """Task ids with ANY Todoist activity event since since_date (YYYY-MM-DD)."""
    out = run(["td", "activity", "--project", project, "--since", since_date,
               "--json", "--all"])
    data = json.loads(out)
    events = data.get("results", data if isinstance(data, list) else [])
    return {e.get("objectId") for e in events
            if e.get("objectType") == "task" and e.get("objectId")}


def execute(action, project):
    a = action["action"]
    if a == "create":
        cmd = ["td", "task", "add", action["content"], "--project", project,
               "--section", action["column"], "--description", action["description"]]
        if action.get("priority"):
            cmd += ["--priority", action["priority"]]
        run(cmd)
    elif a == "move":
        # Order matters: move first, then update. If we crash between the two,
        # the description still holds the OLD jira-status, which differs from
        # live status, so the next run emits update-state and self-heals.
        # (Update-first would leave recorded==live with a stale column, which
        # decide() reads as a deliberate manual move and respects forever.)
        run(["td", "task", "move", f"id:{action['card_id']}", "--section", action["column"]])
        run(["td", "task", "update", f"id:{action['card_id']}",
             "--description", action["description"]])
    elif a == "update-state":
        run(["td", "task", "update", f"id:{action['card_id']}",
             "--description", action["description"]])
    elif a == "complete":
        run(["td", "task", "complete", f"id:{action['card_id']}"])
    # manual-override, orphan, and stale are report-only.


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tickets-file", required=True)
    p.add_argument("--project", default="Work")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--stale-days", type=int, default=0,
                   help="If >0, also report manual cards in active columns with "
                        "no Todoist activity for this many days (report-only).")
    args = p.parse_args()

    with open(args.tickets_file) as f:
        tickets = json.load(f)
    today = datetime.date.today().isoformat()
    sections = fetch_sections(args.project)
    cards = fetch_cards(args.project)
    pr_keys = fetch_open_pr_keys()
    actions = plan_actions(tickets, cards, pr_keys, sections, today)
    if args.stale_days > 0:
        since = (datetime.date.today() -
                 datetime.timedelta(days=args.stale_days)).isoformat()
        active_ids = fetch_active_ids(args.project, since)
        actions += find_stale(cards, active_ids, sections, args.stale_days)

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
