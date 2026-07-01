#!/usr/bin/env python3
"""Self-check for render.py theme grouping. Run: python3 test_render.py"""

from render import render

PAYLOAD = {
    "lookback_date": "2026-06-23",
    "lookback_label": "Monday",
    "today_date": "2026-06-24",
    "today_label": "Tuesday",
    "did": [
        {"text": "Shipped migration", "ref": "FANDEVX-123", "source": "jira"},
        {"text": "fanflow Kafka cutover", "source": "project"},
        {"text": "More fanflow", "source": "log"},
        {"text": "Platform sync: rollout plan", "source": "meeting"},
        {"text": "Posted infra update", "source": "slack"},
        {"text": "Continue migration", "source": "inferred"},
    ],
    "will_do": [
        {"text": "In progress", "ref": "FANDEVX-456", "source": "jira"},
        {"text": "Review terraform PR", "source": "todoist"},
    ],
    "blockers": [
        {"text": "Waiting on SSO fix", "ref": "FANDEVX-789", "source": "jira"},
    ],
}


def main() -> None:
    out = render(PAYLOAD, verbose=False)
    lines = out.splitlines()

    def section(title: str) -> list[str]:
        start = lines.index(f"### {title}")
        rest = lines[start + 1:]
        end = next((i for i, l in enumerate(rest) if l.startswith("### ")), len(rest))
        return rest[:end]

    yesterday = section("Yesterday")
    # (a) multi-theme bucket gets bold sub-headers
    assert "**Tickets**" in yesterday, yesterday
    assert "**Meetings**" in yesterday, yesterday
    assert "**Project work**" in yesterday, yesterday
    assert "**Follow-ups**" in yesterday, yesterday

    # log + project collapse into a single Project work header
    assert yesterday.count("**Project work**") == 1, yesterday

    # (b) single-theme bucket renders flat — no sub-header
    blockers = section("Blockers")
    assert not any(l.startswith("**") for l in blockers), blockers
    assert any("FANDEVX-789" in l for l in blockers), blockers

    # (c) themes emitted in THEME_ORDER (tickets before meetings before project)
    headers = [l for l in yesterday if l.startswith("**")]
    assert headers == ["**Tickets**", "**Meetings**", "**Project work**", "**Comms**", "**Follow-ups**"], headers

    print("ok")


if __name__ == "__main__":
    main()
