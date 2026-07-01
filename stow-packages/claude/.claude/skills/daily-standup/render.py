#!/usr/bin/env python3
"""Render the /daily-standup section from a single normalized JSON blob.

The model gathers raw sources (wiki log, project logs, meeting summaries,
JIRA, Todoist, Slack, Obsidian daily note), normalizes everything into the
three-bucket contract below, writes it to a JSON file, and invokes this
script. The model never composes the standup markdown by hand.

Within each bucket, bullets are grouped under bold theme sub-headers
(Tickets, Meetings, Project work, ...) derived from each bullet's `source`
via SOURCE_TO_THEME. A bucket that resolves to a single theme renders flat
(no sub-header). The JSON contract is unchanged — theming is purely a render
concern.

JSON contract (read from --input or stdin):

  {
    "lookback_date": "2026-05-15",          # ISO date of "yesterday" (workday)
    "lookback_label": "Friday",             # Human label for the section header
    "today_date":    "2026-05-18",
    "today_label":   "Monday",
    "did":      [ { ...bullet... }, ... ],
    "will_do":  [ { ...bullet... }, ... ],
    "blockers": [ { ...bullet... }, ... ]
  }

Bullet shape:

  {
    "text":   "Shipped load testing env Karpenter migration",  # required
    "ref":    "FANDEVX-1234",          # optional JIRA / PR / task key
    "url":    "https://...",           # optional link target
    "source": "jira|project|log|meeting|slack|todoist|daily-note|inferred|input",
    "status": "In Progress"            # optional, only used for "Today" / "Blockers"
  }

Output: the full `## Daily Standup` ... `<!-- standup:end -->` block on stdout
with a trailing newline. Idempotent: callers replace anything between the
markers in today's Obsidian daily note.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Source labels used for the `· _source_` italic suffix on each bullet.
SOURCE_LABEL = {
    "jira": "jira",
    "project": "project",
    "log": "log",
    "meeting": "meeting",
    "slack": "slack",
    "todoist": "todoist",
    "daily-note": "daily-note",
    "inferred": "inferred",
    "input": "input",
}

# Theme grouping. Each bullet's `source` maps to a theme; bullets within a
# bucket render under bold theme sub-headers in THEME_ORDER. `inferred` and
# `daily-note` are provenance, not kind-of-thing, so they land in catch-all
# themes (Follow-ups / Notes).
SOURCE_TO_THEME = {
    "jira": "tickets",
    "meeting": "meetings",
    "project": "project",
    "log": "project",
    "slack": "comms",
    "todoist": "todos",
    "daily-note": "notes",
    "input": "notes",
    "inferred": "follow-ups",
}

THEME_LABEL = {
    "tickets": "Tickets",
    "meetings": "Meetings",
    "project": "Project work",
    "comms": "Comms",
    "todos": "Todos",
    "notes": "Notes",
    "follow-ups": "Follow-ups",
    "other": "Other",
}

# Render order of theme sub-groups within every bucket. A bucket renders only
# the themes actually present; unknown / missing sources fall through to "other".
THEME_ORDER = ["tickets", "meetings", "project", "comms", "todos", "notes", "follow-ups", "other"]


def render_bullet(b: dict, verbose: bool) -> str:
    text = b.get("text") or "(no text)"
    ref = b.get("ref") or ""
    url = b.get("url") or ""
    source = b.get("source") or ""

    head = ""

    # Verbose: [REF](url) markdown link with backtick fallback, and source suffix.
    # Brief:   bare REF (Slack-friendly; JIRA app autolinks if installed), no suffix.
    if verbose:
        if ref and url:
            anchor = f"[{ref}]({url}) "
        elif ref:
            anchor = f"`{ref}` "
        else:
            anchor = ""
        label = SOURCE_LABEL.get(source)
        suffix = f" · _{label}_" if label else ""
    else:
        anchor = f"{ref} " if ref else ""
        suffix = ""

    return f"- {head}{anchor}{text}{suffix}".rstrip()


def render_bucket(title: str, bullets: list[dict], empty_msg: str, verbose: bool) -> str:
    out = [f"### {title}", ""]
    if not bullets:
        out.append(f"_{empty_msg}_")
        return "\n".join(out)

    # Group by derived theme while preserving incoming order inside each group.
    by_theme: dict[str, list[dict]] = {}
    for b in bullets:
        theme = SOURCE_TO_THEME.get(b.get("source") or "", "other")
        by_theme.setdefault(theme, []).append(b)

    # Single-theme bucket renders flat — a lone sub-header is just noise.
    show_headers = len(by_theme) > 1

    for theme in THEME_ORDER:
        group = by_theme.get(theme)
        if not group:
            continue
        if show_headers:
            out.append(f"**{THEME_LABEL.get(theme, theme)}**")
        for b in group:
            out.append(render_bullet(b, verbose))
    return "\n".join(out)


def render(payload: dict, verbose: bool) -> str:
    lookback_date = payload.get("lookback_date") or ""
    lookback_label = payload.get("lookback_label") or ""
    today_date = payload.get("today_date") or ""
    today_label = payload.get("today_label") or ""

    header_bits = []
    if lookback_label and lookback_date:
        header_bits.append(f"{lookback_label} {lookback_date}")
    elif lookback_date:
        header_bits.append(lookback_date)
    if today_label and today_date:
        header_bits.append(f"{today_label} {today_date}")
    elif today_date:
        header_bits.append(today_date)
    # Date-range header is verbose-only — Slack readers already know what day it is.
    header_line = "_Since " + " → ".join(header_bits) + "_" if (verbose and header_bits) else ""

    blocks = [
        "## Daily Standup",
        "",
        "<!-- standup:start -->",
        "",
    ]
    if header_line:
        blocks.extend([header_line, ""])

    blocks.append(render_bucket(
        "Yesterday",
        payload.get("did") or [],
        "No activity captured for the lookback window.",
        verbose,
    ))
    blocks.append("")
    blocks.append(render_bucket(
        "Today",
        payload.get("will_do") or [],
        "No planned work captured.",
        verbose,
    ))
    blocks.append("")
    blocks.append(render_bucket(
        "Blockers",
        payload.get("blockers") or [],
        "None.",
        verbose,
    ))
    blocks.append("")
    blocks.append("<!-- standup:end -->")
    return "\n".join(blocks) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--input", type=Path, default=None,
        help="Path to the normalized JSON blob. If omitted, read from stdin.",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Verbose mode: markdown JIRA links, source-of-truth suffix, "
             "and the date-range header. Default is brief (Slack-friendly).",
    )
    args = p.parse_args()

    if args.input is not None:
        try:
            payload = json.loads(args.input.read_text())
        except FileNotFoundError:
            print(f"render.py: input not found: {args.input}", file=sys.stderr)
            return 2
        except json.JSONDecodeError as e:
            print(f"render.py: invalid JSON in {args.input.name}: {e}", file=sys.stderr)
            return 2
    else:
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError as e:
            print(f"render.py: invalid JSON on stdin: {e}", file=sys.stderr)
            return 2

    sys.stdout.write(render(payload, verbose=args.verbose))
    return 0


if __name__ == "__main__":
    sys.exit(main())
