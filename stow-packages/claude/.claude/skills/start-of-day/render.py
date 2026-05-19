#!/usr/bin/env python3
"""Render the /start-of-day daily-note section from three fetch-result JSON files.

The model writes gh / JIRA / Todoist fetch results to JSON, invokes this script,
and splices the stdout block into today's Obsidian daily note. The model never
composes the markdown itself, eliminating whitespace drift.

Output is the full block from `## Start of Day` through `<!-- sod:end -->` on
stdout, terminated by a trailing newline. Byte-identical to v0.18.0's hand-
rendered shape so this version is a pure refactor.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

JIRA_BASE = "https://betfanatics.atlassian.net/browse/"
KEY_RE = re.compile(r"(?:FANDEVX|FESFEAT)-\d+")

# Status / priority emoji prefixes — lowercased lookup. Unknown values render
# without a prefix. 🟢 is reserved for "Potential to close" and must NOT appear
# in either table.
STATUS_EMOJI = {
    "to do": "📋", "backlog": "📋", "open": "📋", "new": "📋",
    "in progress": "🚧", "in development": "🚧",
    "in review": "👀", "code review": "👀", "qa": "👀",
    "blocked": "🛑", "on hold": "🛑", "waiting": "🛑",
    "done": "✅", "closed": "✅", "resolved": "✅",
    "cancelled": "❌", "canceled": "❌", "won't do": "❌", "wont do": "❌",
}
PRIORITY_EMOJI = {
    "highest": "🔥", "p0": "🔥", "critical": "🔥",
    "high": "🔴", "p1": "🔴",
    "medium": "🟡", "p2": "🟡",
    "low": "🔵", "p3": "🔵",
    "lowest": "⚪", "p4": "⚪",
}


def status_emoji(name: str) -> str:
    return STATUS_EMOJI.get((name or "").strip().lower(), "")


def priority_emoji(name: str) -> str:
    return PRIORITY_EMOJI.get((name or "").strip().lower(), "")


def extract_keys(text: str) -> list[str]:
    """Return unique JIRA keys found in `text`, preserving first-seen order."""
    return list(dict.fromkeys(KEY_RE.findall(text or "")))


def build_crosslinks(prs_data):
    """Return (keys_by_pr_number, prs_by_key) from the PR list.

    keys_by_pr_number: { PR_NUMBER: [KEY, ...] } — keys parsed from PR titles.
    prs_by_key:        { KEY: [(PR_NUMBER, PR_URL), ...] } — inverse map.

    Both maps are empty when PR data is missing or errored — JIRA rows then
    render without a PRs suffix and PR rows without a JIRA suffix.
    """
    keys_by_pr_number: dict = {}
    prs_by_key: dict = {}
    if not isinstance(prs_data, list):
        return keys_by_pr_number, prs_by_key
    for pr in prs_data:
        number = pr.get("number")
        url = pr.get("url", "")
        keys = extract_keys(pr.get("title", ""))
        if not keys:
            continue
        keys_by_pr_number[number] = keys
        for k in keys:
            prs_by_key.setdefault(k, []).append((number, url))
    return keys_by_pr_number, prs_by_key


def build_merged_count_by_key(merged_prs_data):
    """Return {KEY: int} — merged-PR count per JIRA key, parsed from PR titles.

    Empty when the merged-PR data is missing or errored. The "Potential to
    close" flag then never fires (silently degraded).
    """
    counts: dict = {}
    if not isinstance(merged_prs_data, list):
        return counts
    for pr in merged_prs_data:
        for k in extract_keys(pr.get("title", "")):
            counts[k] = counts.get(k, 0) + 1
    return counts


def parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def load_source(path: Path):
    """Return (data, error_message). Treats a `{"error": "..."}` payload as a
    fetch failure for that source; the section then renders the failure line."""
    try:
        raw = path.read_text()
        data = json.loads(raw)
    except FileNotFoundError:
        return None, f"input file not found: {path}"
    except json.JSONDecodeError as e:
        return None, f"invalid JSON in {path.name}: {e}"
    if isinstance(data, dict) and set(data.keys()) == {"error"}:
        return None, str(data["error"])
    return data, None


def relative_time(updated_at: datetime, now: datetime) -> str:
    delta = now - updated_at
    hours = int(delta.total_seconds() // 3600)
    if hours < 0:
        hours = 0
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def render_prs(data, error, now: datetime, keys_by_pr_number) -> str:
    if error is not None:
        return "\n".join([
            "### Open Pull Requests (0)",
            "",
            f"_Open Pull Requests — lookup failed: {error}._",
        ])
    prs = data or []
    out = [f"### Open Pull Requests ({len(prs)})", ""]
    if not prs:
        out.append("_None._")
        return "\n".join(out)
    for pr in prs:
        repo = (pr.get("repository") or {}).get("nameWithOwner", "?/?")
        num = pr.get("number", "?")
        url = pr.get("url", "")
        title = pr.get("title", "(no title)")
        updated_raw = pr.get("updatedAt")
        rel = relative_time(parse_iso(updated_raw), now) if updated_raw else "?"
        draft = " · _(draft)_" if pr.get("isDraft") else ""
        keys = keys_by_pr_number.get(num) or []
        jira_suffix = ""
        if keys:
            jira_suffix = " · JIRA: " + ", ".join(f"[{k}]({JIRA_BASE}{k})" for k in keys)
        out.append(
            f"- [{repo}#{num}]({url}) — {title} · _updated {rel} ago_{draft}{jira_suffix}"
        )
    return "\n".join(out)


def _heading_for_depth(depth: int) -> str:
    """Return the `####`-family heading marker for a node at `depth` (root=0)."""
    return "#" * min(4 + depth, 6)


def _jira_node_lines(node, depth: int, children_by_key, prs_by_key, merged_count_by_key) -> list[str]:
    """Render one node and recursively render its children. Returns list of lines."""
    indent = "    " * depth
    key = node["key"]
    summary = node["summary"]
    status = node["status"]
    prio = node["priority"]
    itype = node["issuetype"]
    assigned = node["assignedToMe"]
    heading = _heading_for_depth(depth)
    if depth == 0:
        title_md = f"{heading} {summary} [{key}]({JIRA_BASE}{key})"
    else:
        title_md = f"{heading} **{summary}** [{key}]({JIRA_BASE}{key})"
    not_assigned_suffix = "" if assigned else " · _(not assigned to you)_"
    matched_prs = prs_by_key.get(key) or []
    # "Potential to close" fires only for assigned tickets with at least one
    # merged PR and zero open PRs referencing the key. Ancestors never flag —
    # closing them isn't the user's call.
    potential_suffix = ""
    if assigned and merged_count_by_key.get(key, 0) >= 1 and not matched_prs:
        potential_suffix = " · **🟢 Potential to close**"
    s_emoji = status_emoji(status)
    p_emoji = priority_emoji(prio)
    status_md = f"{s_emoji} `{status}`" if s_emoji else f"`{status}`"
    prio_md = f"{p_emoji} `{prio}`" if p_emoji else f"`{prio}`"
    meta = f"{status_md} · {prio_md} · _{itype}_{potential_suffix}{not_assigned_suffix}"
    lines = [f"{indent}- {title_md}", f"{indent}    - {meta}"]
    if matched_prs:
        prs_md = ", ".join(f"[#{n}]({u})" for n, u in matched_prs)
        lines.append(f"{indent}    - PRs: {prs_md}")
    for child in children_by_key.get(key, []):
        lines.extend(_jira_node_lines(child, depth + 1, children_by_key, prs_by_key, merged_count_by_key))
    return lines


def _normalise_jira_node(issue):
    """Project a raw MCP issue into the flat shape the renderer expects."""
    f = issue.get("fields") or {}
    parent = issue.get("parent") or f.get("parent") or {}
    parent_key = parent.get("key") if isinstance(parent, dict) else None
    return {
        "key": issue.get("key", "?"),
        "summary": f.get("summary", "(no summary)"),
        "status": (f.get("status") or {}).get("name") or "(unknown)",
        "priority": (f.get("priority") or {}).get("name") or "(none)",
        "issuetype": (f.get("issuetype") or {}).get("name") or "(unknown)",
        "updated": f.get("updated") or "",
        "parent_key": parent_key,
        "assignedToMe": bool(issue.get("assignedToMe", True)),
    }


def render_jira(data, error, prs_by_key, merged_count_by_key) -> str:
    if error is not None:
        return "\n".join([
            "### Open JIRA Tickets (0 assigned · 0 top-level · 0 total)",
            "",
            f"_Open JIRA Tickets — lookup failed: {error}._",
        ])
    raw = (data or {}).get("issues", []) if isinstance(data, dict) else []
    nodes = [_normalise_jira_node(i) for i in raw]
    by_key = {n["key"]: n for n in nodes}
    children_by_key: dict = {}
    roots = []
    for n in nodes:
        pk = n["parent_key"]
        if pk and pk in by_key:
            children_by_key.setdefault(pk, []).append(n)
        else:
            roots.append(n)
    # Sort siblings by updated DESC at every level.
    for siblings in children_by_key.values():
        siblings.sort(key=lambda x: x["updated"], reverse=True)
    roots.sort(key=lambda x: x["updated"], reverse=True)

    assigned_count = sum(1 for n in nodes if n["assignedToMe"])
    out = [
        f"### Open JIRA Tickets ({assigned_count} assigned · {len(roots)} top-level · {len(nodes)} total)",
        "",
    ]
    if not nodes:
        out.append("_None._")
        return "\n".join(out)
    for root in roots:
        out.extend(_jira_node_lines(root, 0, children_by_key, prs_by_key, merged_count_by_key))
    return "\n".join(out)


def render_todoist(data, error, now: datetime) -> str:
    if error is not None:
        return "\n".join([
            "### Today + Overdue (0)",
            "",
            f"_Today + Overdue — `td` unavailable: {error}._",
        ])
    # `td today --json` returns {"results": [...], "nextCursor": ...}; older
    # versions / fixtures may return a bare list. Accept both.
    if isinstance(data, dict):
        tasks = data.get("results", []) or []
    else:
        tasks = data or []
    out = [f"### Today + Overdue ({len(tasks)})", ""]
    if not tasks:
        out.append("_Nothing due today, nothing overdue._")
        return "\n".join(out)
    today_local = now.astimezone().date()
    for task in tasks:
        prio = task.get("priority", 4)
        url = task.get("url", "")
        content = task.get("content", "(no content)")
        # `due.date` may be a bare date ("2026-05-13") or a datetime
        # ("2026-05-13T19:00:00"). Either way the first 10 chars are the date.
        due_raw = (task.get("due") or {}).get("date") or ""
        days_overdue = 0
        if due_raw:
            try:
                due_date = datetime.strptime(due_raw[:10], "%Y-%m-%d").date()
                days_overdue = (today_local - due_date).days
            except ValueError:
                days_overdue = 0
        if days_overdue > 0:
            prefix = f"**⏰ overdue {days_overdue}d**"
        else:
            prefix = "**due today**"
        out.append(f"- {prefix} · priority `p{prio}` — [{content}]({url})")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--prs", required=True, type=Path)
    p.add_argument("--jira", required=True, type=Path)
    p.add_argument("--todoist", required=True, type=Path)
    p.add_argument("--merged-prs", default=None, type=Path,
                   help="Optional. PRs the user merged (gh search prs --state=closed --merged). "
                        "Feeds the 🟢 Potential to close flag. Omit to disable the flag.")
    p.add_argument("--generated-at", required=True,
                   help="ISO-8601 UTC timestamp for the <!-- sod:begin --> marker")
    p.add_argument("--now", default=None,
                   help="ISO-8601 reference time for relative-time math (default: now)")
    args = p.parse_args()

    now = parse_iso(args.now).astimezone(timezone.utc) if args.now else datetime.now(timezone.utc)

    prs_data, prs_err = load_source(args.prs)
    jira_data, jira_err = load_source(args.jira)
    td_data, td_err = load_source(args.todoist)

    keys_by_pr_number, prs_by_key = build_crosslinks(prs_data)

    merged_count_by_key: dict = {}
    if args.merged_prs is not None:
        merged_data, _ = load_source(args.merged_prs)
        merged_count_by_key = build_merged_count_by_key(merged_data)

    blocks = [
        "## Start of Day",
        "",
        f"<!-- sod:begin generated={args.generated_at} -->",
        "",
        render_prs(prs_data, prs_err, now, keys_by_pr_number),
        "",
        render_jira(jira_data, jira_err, prs_by_key, merged_count_by_key),
        "",
        render_todoist(td_data, td_err, now),
        "",
        "<!-- sod:end -->",
    ]
    sys.stdout.write("\n".join(blocks) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
