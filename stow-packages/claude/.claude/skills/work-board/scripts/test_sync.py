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
    find_stale,
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

    def test_strip_preserves_interior_lookalike(self):
        desc = "note: sync: jira-status=Fake | synced=2026-01-01 is the format\nsync: jira-status=To Do | synced=2026-06-10"
        self.assertEqual(strip_state_line(desc),
                         "note: sync: jira-status=Fake | synced=2026-01-01 is the format")


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
        self.assertNotIn("priority", a)

    def test_create_carries_inferred_priority(self):
        t = {**ticket("A-1", "To Do", "new"), "priority": "p2"}
        a = decide(t, None, set(), SECTIONS, "2026-06-11")
        self.assertEqual(a["action"], "create")
        self.assertEqual(a["priority"], "p2")

    def test_create_drops_invalid_priority(self):
        t = {**ticket("A-1", "To Do", "new"), "priority": "urgent"}
        a = decide(t, None, set(), SECTIONS, "2026-06-11")
        self.assertEqual(a["action"], "create")
        self.assertNotIn("priority", a)

    def test_existing_card_priority_never_touched(self):
        # Priority only applies on create; a move action must not carry it.
        c = card("A-1 x", "S2", "u\nsync: jira-status=To Do | synced=2026-06-10")
        t = {**ticket("A-1", "In Progress"), "priority": "p1"}
        a = decide(t, c, set(), SECTIONS, "2026-06-11")
        self.assertEqual(a["action"], "move")
        self.assertNotIn("priority", a)

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

    def test_no_state_line_correct_column_stamps(self):
        c = card("A-1 x", "S3", "")
        a = decide(ticket("A-1", "In Progress"), c, set(), SECTIONS, "2026-06-11")
        self.assertEqual(a["action"], "update-state")

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


class TestFindStale(unittest.TestCase):
    def test_inactive_manual_card_in_active_column_flagged(self):
        cards = [card("Lead docs reorg", "S3")]
        out = find_stale(cards, set(), SECTIONS, 7)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["action"], "stale")
        self.assertEqual(out[0]["column"], "In Progress")

    def test_recently_active_card_not_flagged(self):
        cards = [card("Lead docs reorg", "S3")]
        out = find_stale(cards, {"T1"}, SECTIONS, 7)
        self.assertEqual(out, [])

    def test_jira_linked_card_excluded(self):
        cards = [card("A-1 ticket card", "S3")]
        self.assertEqual(find_stale(cards, set(), SECTIONS, 7), [])

    def test_backlog_and_waiting_not_flagged(self):
        cards = [card("parked idea", "S1"), card("waiting thing", "S5")]
        self.assertEqual(find_stale(cards, set(), SECTIONS, 7), [])


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
