"""Tests for the pure decision logic in crew.py."""
import unittest
from unittest import mock

import crew
from crew import (sanitize_name, pick_name, bucket, _probe, crew_members,
                   untagged_agents, render_ls, assert_snapshot_shape,
                   assert_schema_declares, CrewError, HerdrError,
                   read_entries, next_seq, select_unread,
                   contract_pointer, find_member, is_ticket, take_flag,
                   _start_agent)


class TestSanitizeName(unittest.TestCase):
    def test_jira_key_lowercased(self):
        self.assertEqual(sanitize_name("FANDEVX-3511"), "fandevx-3511")

    def test_uppercase_repo_style_name(self):
        self.assertEqual(
            sanitize_name("Hands-On-Large-Language-Models"),
            "hands-on-large-language-models",
        )

    def test_leading_digit_gets_alpha_prefix(self):
        self.assertEqual(sanitize_name("123-thing"), "c-123-thing")

    def test_illegal_chars_collapse_to_single_hyphen(self):
        self.assertEqual(sanitize_name("feat/add  thing!"), "feat-add-thing")

    def test_truncated_to_32(self):
        out = sanitize_name("a" * 50)
        self.assertEqual(len(out), 32)

    def test_empty_key_rejected(self):
        with self.assertRaises(ValueError):
            sanitize_name("")


class TestPickName(unittest.TestCase):
    def test_no_collision_returns_base(self):
        self.assertEqual(pick_name("FANDEVX-3511", set()), "fandevx-3511")

    def test_collision_suffixes_two(self):
        self.assertEqual(
            pick_name("FANDEVX-3511", {"fandevx-3511"}), "fandevx-3511-2"
        )

    def test_second_collision_suffixes_three(self):
        live = {"fandevx-3511", "fandevx-3511-2"}
        self.assertEqual(pick_name("FANDEVX-3511", live), "fandevx-3511-3")

    def test_suffix_never_exceeds_32(self):
        live = {sanitize_name("b" * 40)}
        out = pick_name("b" * 40, live)
        self.assertLessEqual(len(out), 32)
        self.assertTrue(out.endswith("-2"))


class TestBucket(unittest.TestCase):
    def test_done_and_idle_both_await_the_human(self):
        self.assertEqual(bucket("done"), "awaiting")
        self.assertEqual(bucket("idle"), "awaiting")

    def test_working(self):
        self.assertEqual(bucket("working"), "working")

    def test_blocked(self):
        self.assertEqual(bucket("blocked"), "blocked")

    def test_unknown_needs_recovery(self):
        self.assertEqual(bucket("unknown"), "recover")

    def test_unrecognised_status_fails_safe_to_recover(self):
        self.assertEqual(bucket("teleported"), "recover")


def _snap(agents, panes):
    return {"agents": agents, "panes": panes}


def _agent(pane, status="idle", name=None, title=""):
    a = {"agent_status": status, "pane_id": pane, "workspace_id": "wQ",
         "terminal_title_stripped": title}
    if name:
        a["name"] = name
    return a


def _pane(pane, tokens=None, cwd="/repo", fg=None):
    return {"pane_id": pane, "cwd": cwd, "foreground_cwd": fg or cwd,
            "tokens": tokens}


CREW_TOKENS = {"crew": "true", "v": "1", "key": "fandevx-3511",
               "repo": "fanapp-terraform", "type": "implementer",
               "worktree": "/repo/.claude/worktrees/FANDEVX-3511-x",
               "dispatched": "1786000000"}


class TestCrewMembers(unittest.TestCase):
    def test_tagged_pane_becomes_a_member(self):
        snap = _snap([_agent("wQ:p1", "working", "fandevx-3511")],
                     [_pane("wQ:p1", CREW_TOKENS)])
        members = crew_members(snap)
        self.assertEqual(len(members), 1)
        m = members[0]
        self.assertEqual(m["key"], "fandevx-3511")
        self.assertEqual(m["repo"], "fanapp-terraform")
        self.assertEqual(m["bucket"], "working")
        self.assertEqual(m["worktree"], CREW_TOKENS["worktree"])

    def test_untagged_pane_is_not_crew_even_in_a_worktree(self):
        snap = _snap([_agent("wQ:p1", "idle")],
                     [_pane("wQ:p1", None,
                            fg="/repo/.claude/worktrees/FANDEVX-3511-x")])
        self.assertEqual(crew_members(snap), [])
        self.assertEqual(len(untagged_agents(snap)), 1)

    def test_worktree_comes_from_token_not_cwd(self):
        # Crew changed directory out of its worktree. The token still rules.
        snap = _snap([_agent("wQ:p1", "working", "fandevx-3511")],
                     [_pane("wQ:p1", CREW_TOKENS, cwd="/somewhere/else",
                            fg="/tmp")])
        self.assertEqual(crew_members(snap)[0]["worktree"],
                         CREW_TOKENS["worktree"])

    def test_unknown_token_version_is_reported_not_guessed(self):
        tokens = dict(CREW_TOKENS)
        tokens["v"] = "99"
        snap = _snap([_agent("wQ:p1", "idle", "fandevx-3511")],
                     [_pane("wQ:p1", tokens)])
        self.assertEqual(crew_members(snap)[0]["type"], "unknown-v99")


# Mirrors herdr 0.7.5 protocol 17. tokens, cwd and foreground_cwd are
# declared but NOT required, which is why they are asserted at the schema
# layer instead of demanded on every pane.
DEFS = {
    "AgentInfo": {
        "required": ["agent_status", "pane_id", "workspace_id"],
        "properties": {"name": {}, "terminal_title_stripped": {},
                       "state_change_seq": {}, "tokens": {}},
    },
    "PaneInfo": {
        "required": ["pane_id"],
        "properties": {"tokens": {}, "cwd": {}, "foreground_cwd": {}},
    },
}


class TestAssertSnapshotShape(unittest.TestCase):
    def test_missing_required_agent_field_raises(self):
        snap = _snap([{"pane_id": "wQ:p1", "workspace_id": "wQ"}], [])
        with self.assertRaises(CrewError):
            assert_snapshot_shape(snap, DEFS)

    def test_agents_not_a_list_raises(self):
        with self.assertRaises(CrewError):
            assert_snapshot_shape({"agents": {}, "panes": []}, DEFS)

    def test_untagged_pane_without_tokens_is_valid(self):
        # An untagged pane genuinely has no tokens key. This must not raise.
        snap = _snap([], [{"pane_id": "wQ:p1"}])
        assert_snapshot_shape(snap, DEFS)

    def test_empty_schema_required_raises_rather_than_passing_vacuously(self):
        with self.assertRaises(CrewError):
            assert_snapshot_shape(_snap([], []), {"AgentInfo": {}, "PaneInfo": {}})


class TestAssertSchemaDeclares(unittest.TestCase):
    def test_declared_optional_fields_pass(self):
        assert_schema_declares(DEFS)

    def test_renamed_tokens_field_is_caught(self):
        defs = {"AgentInfo": DEFS["AgentInfo"],
                "PaneInfo": {"required": ["pane_id"],
                             "properties": {"metadata": {}, "cwd": {},
                                            "foreground_cwd": {}}}}
        with self.assertRaises(CrewError):
            assert_schema_declares(defs)

    def test_missing_properties_raises(self):
        with self.assertRaises(CrewError):
            assert_schema_declares({"PaneInfo": {}, "AgentInfo": {}})


class TestProbe(unittest.TestCase):
    def test_success_returns_output(self):
        ok, text = _probe(["echo", "hello"])
        self.assertTrue(ok)
        self.assertEqual(text, "hello")

    def test_nonzero_exit_fails(self):
        ok, text = _probe(["false"])
        self.assertFalse(ok)
        self.assertIn("exited", text)

    def test_empty_output_fails_rather_than_passing_blank(self):
        ok, text = _probe(["true"])
        self.assertFalse(ok)
        self.assertIn("no output", text)

    def test_missing_binary_fails_without_raising(self):
        ok, text = _probe(["crew-no-such-binary-xyz"])
        self.assertFalse(ok)
        self.assertIn("not runnable", text)

    def test_message_names_the_whole_command_not_just_the_binary(self):
        # doctor probes both `herdr --version` and `herdr api schema`. Labelling
        # by argv[0] makes two failures byte-identical.
        ok, text = _probe(["sh", "-c", "exit 3"])
        self.assertFalse(ok)
        self.assertIn("sh -c exit 3", text)


class TestLsFailsClosed(unittest.TestCase):
    def test_ls_verb_exits_3_rather_than_reporting_zeros(self):
        with mock.patch.object(crew, "schema_defs",
                               side_effect=CrewError("boom")):
            self.assertEqual(crew.main(["ls"]), 3)

    def test_ls_verb_exits_3_on_a_herdr_error(self):
        with mock.patch.object(crew, "snapshot",
                               side_effect=HerdrError("socket gone")), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.object(crew, "assert_schema_declares"):
            self.assertEqual(crew.main(["ls"]), 3)


class TestRenderLs(unittest.TestCase):
    def test_leads_with_counts(self):
        snap = _snap([_agent("wQ:p1", "working", "fandevx-3511")],
                     [_pane("wQ:p1", CREW_TOKENS)])
        out = render_ls(crew_members(snap), untagged_agents(snap))
        self.assertTrue(out.startswith("1 working / 0 awaiting you / 0 blocked"))

    def test_shows_repo(self):
        snap = _snap([_agent("wQ:p1", "done", "fandevx-3511")],
                     [_pane("wQ:p1", CREW_TOKENS)])
        out = render_ls(crew_members(snap), untagged_agents(snap))
        self.assertIn("fanapp-terraform", out)

    def test_untagged_counted_separately(self):
        snap = _snap([_agent("wQ:p1", "idle", None, "Align inf-dev")],
                     [_pane("wQ:p1", None)])
        out = render_ls(crew_members(snap), untagged_agents(snap))
        self.assertIn("1 untagged", out)
        self.assertIn("Align inf-dev", out)


class TestReadEntries(unittest.TestCase):
    def test_parses_valid_lines(self):
        lines = ['{"seq": 1, "state": "done"}', '{"seq": 2, "state": "done"}']
        entries, unreadable = read_entries(lines)
        self.assertEqual([e["seq"] for e in entries], [1, 2])
        self.assertEqual(unreadable, 0)

    def test_blank_lines_ignored(self):
        entries, unreadable = read_entries(["", "  ", '{"seq": 1}'])
        self.assertEqual(len(entries), 1)
        self.assertEqual(unreadable, 0)

    def test_truncated_line_counted_not_fatal(self):
        lines = ['{"seq": 1}', '{"seq": 2, "msg": "half', '{"seq": 3}']
        entries, unreadable = read_entries(lines)
        self.assertEqual([e["seq"] for e in entries], [1, 3])
        self.assertEqual(unreadable, 1)

    def test_line_without_seq_counted_unreadable(self):
        entries, unreadable = read_entries(['{"state": "done"}'])
        self.assertEqual(entries, [])
        self.assertEqual(unreadable, 1)


class TestNextSeq(unittest.TestCase):
    def test_empty_starts_at_one(self):
        self.assertEqual(next_seq([]), 1)

    def test_one_past_highest(self):
        self.assertEqual(next_seq([{"seq": 4}, {"seq": 9}, {"seq": 2}]), 10)


class TestSelectUnread(unittest.TestCase):
    def test_returns_only_records_past_the_cursor(self):
        entries = [{"seq": 1}, {"seq": 2}, {"seq": 3}]
        fresh, cursor, missing = select_unread(entries, 1)
        self.assertEqual([e["seq"] for e in fresh], [2, 3])
        self.assertEqual(cursor, 3)
        self.assertEqual(missing, 0)

    def test_gap_advances_the_cursor_and_is_counted(self):
        # A writer killed mid-append leaves 10 then 12. A contiguous cursor
        # would stick at 10 forever; this must move past it.
        entries = [{"seq": 10}, {"seq": 12}]
        fresh, cursor, missing = select_unread(entries, 9)
        self.assertEqual([e["seq"] for e in fresh], [10, 12])
        self.assertEqual(cursor, 12)
        self.assertEqual(missing, 1)

    def test_gap_does_not_redeliver_on_the_next_call(self):
        entries = [{"seq": 10}, {"seq": 12}]
        _, cursor, _ = select_unread(entries, 9)
        fresh, cursor2, missing = select_unread(entries, cursor)
        self.assertEqual(fresh, [])
        self.assertEqual(cursor2, 12)
        self.assertEqual(missing, 0)

    def test_out_of_order_records_sorted(self):
        entries = [{"seq": 3}, {"seq": 1}, {"seq": 2}]
        fresh, cursor, _ = select_unread(entries, 0)
        self.assertEqual([e["seq"] for e in fresh], [1, 2, 3])
        self.assertEqual(cursor, 3)

    def test_nothing_fresh_leaves_cursor_alone(self):
        fresh, cursor, missing = select_unread([{"seq": 1}], 5)
        self.assertEqual(fresh, [])
        self.assertEqual(cursor, 5)
        self.assertEqual(missing, 0)


class TestMainNeverTracebacks(unittest.TestCase):
    """Crew members invoke `crew mail send` from their own sessions. A
    traceback there is unreadable to them and loses the message."""

    def test_mail_send_without_a_key_is_a_clean_error(self):
        with mock.patch.object(crew, "_pane_tokens", return_value={}):
            self.assertEqual(crew.main(["mail", "send", "done", "x"]), 3)

    def test_herdr_failure_during_send_is_a_clean_error(self):
        with mock.patch.object(crew, "_pane_tokens",
                               side_effect=HerdrError("socket gone")):
            self.assertEqual(crew.main(["mail", "send", "done", "x"]), 3)

    def test_non_integer_ack_seq_is_a_clean_error(self):
        self.assertEqual(crew.main(["mail", "ack", "twelve"]), 2)

    def test_unknown_verb_returns_two(self):
        self.assertEqual(crew.main(["teleport"]), 2)


class TestContractPointer(unittest.TestCase):
    def test_names_the_contract_path_and_identity(self):
        out = contract_pointer("fandevx-3511", "implementer", "FANDEVX-3511",
                               "fanapp-terraform", "/w/FANDEVX-3511-x")
        self.assertIn("fandevx-3511", out)
        self.assertIn("implementer", out)
        self.assertIn("FANDEVX-3511", out)
        self.assertIn("fanapp-terraform", out)
        self.assertIn("/w/FANDEVX-3511-x", out)
        self.assertIn("crew-member/SKILL.md", out)
        self.assertIn("crew mail send", out)

    def test_no_em_dashes(self):
        out = contract_pointer("a", "implementer", "K", "r", "/w")
        self.assertNotIn("—", out)


class TestIsTicket(unittest.TestCase):
    def test_jira_key(self):
        self.assertTrue(is_ticket("FANDEVX-3511"))

    def test_another_project_prefix(self):
        self.assertTrue(is_ticket("FESFEAT-603"))

    def test_slug_is_not_a_ticket(self):
        self.assertFalse(is_ticket("spike-crew-smoke"))

    def test_lowercased_key_is_not_a_ticket(self):
        self.assertFalse(is_ticket("fandevx-3511"))

    def test_prefix_without_a_number_is_not_a_ticket(self):
        self.assertFalse(is_ticket("FANDEVX-"))


class TestTakeFlag(unittest.TestCase):
    def test_pulls_a_flag_and_its_value(self):
        flag, value, rest = take_flag(["--key", "k", "done", "msg"],
                                      ("--key", "--repo"))
        self.assertEqual((flag, value), ("--key", "k"))
        self.assertEqual(rest, ["done", "msg"])

    def test_unrecognised_leading_token_is_left_alone(self):
        flag, value, rest = take_flag(["done", "msg"], ("--key",))
        self.assertIsNone(flag)
        self.assertEqual(rest, ["done", "msg"])

    def test_flag_without_a_value_is_a_clean_error(self):
        with self.assertRaises(CrewError):
            take_flag(["--key"], ("--key",))

    def test_empty_rest(self):
        flag, value, rest = take_flag([], ("--key",))
        self.assertIsNone(flag)
        self.assertEqual(rest, [])


class TestMainArgumentErrors(unittest.TestCase):
    def test_dangling_flag_value_does_not_traceback(self):
        self.assertEqual(crew.main(["mail", "send", "--key"]), 3)

    def test_unexpected_dispatch_argument_does_not_traceback(self):
        self.assertEqual(crew.main(["dispatch", "k", "--nonsense", "x"]), 3)


class TestFindMember(unittest.TestCase):
    def test_matches_on_repo_and_key(self):
        snap = _snap([_agent("wQ:p1", "idle", "fandevx-3511")],
                     [_pane("wQ:p1", CREW_TOKENS)])
        found = find_member(snap, "fanapp-terraform", "fandevx-3511")
        self.assertIsNotNone(found)
        self.assertEqual(found["pane"], "wQ:p1")

    def test_same_key_different_repo_is_not_a_match(self):
        snap = _snap([_agent("wQ:p1", "idle", "fandevx-3511")],
                     [_pane("wQ:p1", CREW_TOKENS)])
        self.assertIsNone(find_member(snap, "fes-config-ops", "fandevx-3511"))

    def test_key_is_compared_sanitised(self):
        snap = _snap([_agent("wQ:p1", "idle", "fandevx-3511")],
                     [_pane("wQ:p1", CREW_TOKENS)])
        self.assertIsNotNone(
            find_member(snap, "fanapp-terraform", "FANDEVX-3511"))


class TestStartAgent(unittest.TestCase):
    # Observed live: agent start immediately after tab create can reject a
    # pane that has not yet settled into an interactive shell.
    def test_succeeds_without_retry_when_not_busy(self):
        with mock.patch.object(crew, "herdr", return_value={"ok": True}) as h:
            self.assertEqual(_start_agent(["agent", "start"]), {"ok": True})
        self.assertEqual(h.call_count, 1)

    def test_retries_past_a_busy_pane_then_succeeds(self):
        busy = HerdrError('{"error":{"code":"agent_pane_busy"}}')
        with mock.patch.object(crew, "herdr",
                               side_effect=[busy, busy, {"ok": True}]), \
             mock.patch.object(crew, "time") as mock_time:
            self.assertEqual(
                _start_agent(["agent", "start"], tries=5, delay=0),
                {"ok": True})
        self.assertEqual(mock_time.sleep.call_count, 2)

    def test_gives_up_after_exhausting_retries(self):
        busy = HerdrError('{"error":{"code":"agent_pane_busy"}}')
        with mock.patch.object(crew, "herdr", side_effect=busy), \
             mock.patch.object(crew, "time"):
            with self.assertRaises(HerdrError):
                _start_agent(["agent", "start"], tries=3, delay=0)

    def test_a_different_herdr_error_is_not_retried(self):
        other = HerdrError("socket gone")
        with mock.patch.object(crew, "herdr", side_effect=other) as h, \
             mock.patch.object(crew, "time"):
            with self.assertRaises(HerdrError):
                _start_agent(["agent", "start"], tries=5, delay=0)
        self.assertEqual(h.call_count, 1)


if __name__ == "__main__":
    unittest.main()
