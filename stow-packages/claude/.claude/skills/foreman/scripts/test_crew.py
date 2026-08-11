"""Tests for the pure decision logic in crew.py."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import crew
from crew import (sanitize_name, pick_name, bucket, _probe, crew_members,
                   untagged_agents, render_ls, assert_snapshot_shape,
                   assert_schema_declares, CrewError, HerdrError,
                   read_entries, next_seq, select_unread,
                   contract_pointer, find_member, is_ticket, take_flag,
                   _start_agent, resolve_repo, repo_root_for, clamp_lines,
                   require_positional, worktree_for)


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
               "branch": "FANDEVX-3511-x", "root": "/repo",
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
        self.assertEqual(m["branch"], CREW_TOKENS["branch"])
        self.assertEqual(
            m["worktree"],
            worktree_for(CREW_TOKENS["root"], CREW_TOKENS["branch"]))

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
        self.assertEqual(
            crew_members(snap)[0]["worktree"],
            worktree_for(CREW_TOKENS["root"], CREW_TOKENS["branch"]))

    def test_unknown_token_version_is_reported_not_guessed(self):
        tokens = dict(CREW_TOKENS)
        tokens["v"] = "99"
        snap = _snap([_agent("wQ:p1", "idle", "fandevx-3511")],
                     [_pane("wQ:p1", tokens)])
        self.assertEqual(crew_members(snap)[0]["type"], "unknown-v99")


# The real required lists from herdr 0.7.5 protocol 17, verified against
# `herdr api schema --json`. An earlier fixture claimed to mirror the schema
# while listing 3 and 1 required fields against the real 7 and 7, so a test
# asserted a pane shape the live schema rejects.
DEFS = {
    "AgentInfo": {
        "required": ["terminal_id", "agent_status", "workspace_id", "tab_id",
                     "pane_id", "focused", "revision"],
        "properties": {"name": {}, "terminal_title_stripped": {},
                       "state_change_seq": {}, "tokens": {}, "cwd": {},
                       "foreground_cwd": {}},
    },
    "PaneInfo": {
        "required": ["pane_id", "terminal_id", "workspace_id", "tab_id",
                     "focused", "agent_status", "revision"],
        "properties": {"tokens": {}, "cwd": {}, "foreground_cwd": {}},
    },
}


def _full_agent(pane, status="idle", name=None, title=""):
    a = {"terminal_id": "t1", "agent_status": status, "workspace_id": "wQ",
         "tab_id": "wQ:t1", "pane_id": pane, "focused": False, "revision": 1,
         "terminal_title_stripped": title}
    if name:
        a["name"] = name
    return a


def _full_pane(pane, tokens=None):
    return {"pane_id": pane, "terminal_id": "t1", "workspace_id": "wQ",
            "tab_id": "wQ:t1", "focused": False, "agent_status": "idle",
            "revision": 1, "tokens": tokens}


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
        snap = _snap([], [_full_pane("wQ:p1")])
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
        with mock.patch.object(crew, "calling_pane", return_value=""):
            self.assertEqual(crew.main(["mail", "send", "done", "x"]), 3)

    def test_herdr_failure_during_send_is_a_clean_error(self):
        with mock.patch.object(crew, "calling_pane", return_value="wQ:p1"), \
             mock.patch.object(crew, "_pane_tokens",
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


class TestKeyCaseIsConsistent(unittest.TestCase):
    """One dispatch must not tell a crew member two different keys."""

    def test_contract_pointer_uses_the_sanitised_key(self):
        out = contract_pointer("fandevx-3511", "implementer", "FANDEVX-3511",
                               "r", "/w")
        self.assertIn("--key fandevx-3511", out)
        self.assertNotIn("--key FANDEVX-3511", out)


class TestResolveRepo(unittest.TestCase):
    def test_no_argument_uses_the_cwd_repo(self):
        root, name = resolve_repo(None)
        self.assertTrue(os.path.isdir(root))
        self.assertEqual(name, os.path.basename(root))

    def test_unresolvable_name_is_an_error_not_a_relabel(self):
        with mock.patch.object(crew, "DEV_ROOT", "/no-such-dev-root"):
            with self.assertRaises(CrewError):
                resolve_repo("not-a-real-repo")

    def test_name_comes_from_the_directory_not_the_argument(self):
        # The argument and the resolved name must be able to disagree, or an
        # implementation that just echoed --repo back would pass this test
        # too: mock canonical_repo_root to a name that cannot possibly match
        # the argument by construction.
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "argument-name"))
            with mock.patch.object(crew, "DEV_ROOT", tmp), \
                 mock.patch.object(crew, "canonical_repo_root",
                                   return_value="/elsewhere/resolved-name"):
                _, name = resolve_repo("argument-name")
        self.assertEqual(name, "resolved-name")


class TestRepoRootFor(unittest.TestCase):
    """repo_root_for (--show-toplevel) is no longer called by resolve_repo,
    which now needs the repository root rather than the worktree path, but it
    stays as a small, separately useful primitive."""

    def test_returns_the_top_level_directory(self):
        root = repo_root_for(os.getcwd())
        self.assertTrue(os.path.isdir(root))
        self.assertTrue(os.path.realpath(os.getcwd()).startswith(
            os.path.realpath(root)))


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
    def test_matches_on_root_and_key(self):
        snap = _snap([_agent("wQ:p1", "idle", "fandevx-3511")],
                     [_pane("wQ:p1", CREW_TOKENS)])
        found = find_member(snap, CREW_TOKENS["root"], "fandevx-3511")
        self.assertIsNotNone(found)
        self.assertEqual(found["pane"], "wQ:p1")

    def test_same_key_different_root_is_not_a_match(self):
        snap = _snap([_agent("wQ:p1", "idle", "fandevx-3511")],
                     [_pane("wQ:p1", CREW_TOKENS)])
        self.assertIsNone(find_member(snap, "/somewhere-else", "fandevx-3511"))

    def test_key_is_compared_sanitised(self):
        snap = _snap([_agent("wQ:p1", "idle", "fandevx-3511")],
                     [_pane("wQ:p1", CREW_TOKENS)])
        self.assertIsNotNone(
            find_member(snap, CREW_TOKENS["root"], "FANDEVX-3511"))


class TestFindMemberIgnoresSetupPanes(unittest.TestCase):
    """An orphaned setup pane used to make every retry of that key report a
    duplicate forever, and it carries no branch token so the resume command it
    printed was empty."""

    def test_a_setup_pane_is_never_a_match(self):
        toks = {"crew": "true", "v": "1", "key": "k", "repo": "r",
                "root": "/root", "type": "setup"}
        snap = {"agents": [_full_agent("wQ:p1", "idle", "k")],
                "panes": [_full_pane("wQ:p1", toks)]}
        self.assertIsNone(find_member(snap, "/root", "k"))

    def test_matching_is_on_root_not_the_repo_label(self):
        toks = {"crew": "true", "v": "1", "key": "k", "repo": "service",
                "root": "/a/service", "type": "implementer"}
        snap = {"agents": [_full_agent("wQ:p1", "idle", "k")],
                "panes": [_full_pane("wQ:p1", toks)]}
        self.assertIsNotNone(find_member(snap, "/a/service", "k"))
        # Same label, different repository: not a duplicate.
        self.assertIsNone(find_member(snap, "/b/service", "k"))


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


class TestDispatchConfirmsDelivery(unittest.TestCase):
    """agent prompt has no --wait, so it cannot tell dispatch whether the
    assignment landed. cmd_dispatch now confirms with a lifecycle check
    afterwards instead of relying on the (unreachable) agent_prompt_stalled
    error the old code checked for."""

    def _herdr(self, wait_error=None):
        def fake(*args, **kwargs):
            if args[:2] == ("tab", "create"):
                return {"result": {"root_pane": {"pane_id": "wTest:pDispatch"}}}
            if args[:2] == ("agent", "wait") and wait_error is not None:
                raise wait_error
            return {"ok": True}
        return fake

    def _dispatch(self, wait_error=None):
        with mock.patch.object(crew, "resolve_repo",
                               return_value=("/fake/repo", "dispatch-test")), \
             mock.patch.object(crew, "plain_worktree",
                               return_value="/fake/repo/.claude/worktrees/x"), \
             mock.patch.object(crew, "snapshot", return_value=_snap([], [])), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.object(crew, "herdr",
                               side_effect=self._herdr(wait_error)) as herdr, \
             mock.patch.dict(crew.os.environ, {"HERDR_WORKSPACE_ID": "wTest"}):
            code = crew.main(["dispatch", "test-dispatch-confirm",
                              "--type", "implementer", "--repo", "dispatch-test"])
            return code, herdr

    def test_confirmed_delivery_returns_zero(self):
        # Asserting the return code alone passes 0 == 0 even with the agent
        # wait confirmation call deleted outright, since cmd_dispatch would
        # still fall through to "return 0". Assert the call happened too.
        code, herdr = self._dispatch()
        self.assertEqual(code, 0)
        self.assertTrue(
            any(call.args[:2] == ("agent", "wait")
                for call in herdr.call_args_list),
            "cmd_dispatch must confirm delivery with agent wait before "
            "reporting success")

    def test_unconfirmed_delivery_returns_six_not_a_traceback(self):
        code, _ = self._dispatch(wait_error=HerdrError("timeout"))
        self.assertEqual(code, 6)


class TestDispatchExitCodeMatchesOtherVerbs(unittest.TestCase):
    """dispatch used to catch CrewError/HerdrError itself and return 1,
    while every other verb relies on main's central handler and returns 3
    for the same exception classes. Same failure, different exit code
    depending on which verb produced it."""

    def test_crew_error_from_dispatch_exits_three_like_every_other_verb(self):
        with mock.patch.object(crew, "cmd_dispatch",
                               side_effect=CrewError("boom")):
            self.assertEqual(crew.main(["dispatch", "k"]), 3)


class TestHerdrRawMode(unittest.TestCase):
    """`agent read` returns terminal text, not JSON. Without raw mode the
    JSON parse raises and every peek fails."""

    def test_raw_returns_stdout_untouched(self):
        fake = mock.Mock(returncode=0, stdout="not json at all\n", stderr="")
        with mock.patch.object(crew.subprocess, "run", return_value=fake):
            self.assertEqual(crew.herdr("agent", "read", "x", raw=True),
                             "not json at all\n")

    def test_without_raw_the_same_output_is_an_error(self):
        fake = mock.Mock(returncode=0, stdout="not json at all\n", stderr="")
        with mock.patch.object(crew.subprocess, "run", return_value=fake):
            with self.assertRaises(HerdrError):
                crew.herdr("agent", "read", "x")


class TestClampLines(unittest.TestCase):
    def test_default(self):
        self.assertEqual(clamp_lines(None), 40)

    def test_within_range_passes_through(self):
        self.assertEqual(clamp_lines(120), 120)

    def test_capped_at_200(self):
        self.assertEqual(clamp_lines(5000), 200)

    def test_floor_of_one(self):
        self.assertEqual(clamp_lines(0), 1)
        self.assertEqual(clamp_lines(-9), 1)


class TestMainErrorMapping(unittest.TestCase):
    """main wraps _run centrally; peek and nudge rely on this rather than
    each carrying their own try/except. Completes the mapping for the two
    exception types that were not yet covered."""

    def test_index_error_from_a_verb_exits_two_not_a_traceback(self):
        with mock.patch.object(crew, "_run", side_effect=IndexError("boom")):
            self.assertEqual(crew.main(["peek", "x"]), 2)

    def test_os_error_from_a_verb_exits_three_not_a_traceback(self):
        with mock.patch.object(crew, "_run", side_effect=OSError("boom")):
            self.assertEqual(crew.main(["peek", "x"]), 3)


class TestMainErrorPrefixesAreDistinct(unittest.TestCase):
    """Four exception classes used to collapse onto the same `crew: `
    prefix, so a herdr failure, a crew error, a bad argument and a
    filesystem error were indistinguishable in output. Exit codes already
    cover the classification; this asserts on the message itself."""

    def _stderr_for(self, side_effect):
        buf = io.StringIO()
        with mock.patch.object(crew, "_run", side_effect=side_effect), \
             mock.patch.object(crew.sys, "stderr", buf):
            crew.main(["peek", "x"])
        return buf.getvalue()

    def test_herdr_and_bad_argument_prefixes_differ(self):
        herdr_msg = self._stderr_for(HerdrError("boom"))
        arg_msg = self._stderr_for(ValueError("boom"))
        self.assertIn("herdr:", herdr_msg)
        self.assertIn("bad arguments:", arg_msg)
        self.assertNotEqual(herdr_msg, arg_msg)


class TestTokenTruncationIsRefused(unittest.TestCase):
    """herdr silently truncates a token value at 80 characters. Tokens are the
    authoritative record, so a truncated value is a record that lies. This was
    found live: a real worktree path stored as a token pointed at a directory
    that did not exist."""

    def test_an_over_length_value_raises_rather_than_being_written(self):
        with mock.patch.object(crew, "herdr") as fake:
            with self.assertRaises(CrewError):
                crew.tag_pane("w:p1", "K", "repo", "implementer",
                              "b" * 81, "/root")
            fake.assert_not_called()

    def test_a_value_at_the_limit_is_allowed(self):
        with mock.patch.object(crew, "herdr") as fake:
            crew.tag_pane("w:p1", "K", "repo", "implementer", "b" * 80, "/root")
            fake.assert_called_once()

    def test_the_error_names_the_offending_token(self):
        with mock.patch.object(crew, "herdr"):
            try:
                crew.tag_pane("w:p1", "K", "repo", "implementer",
                              "b" * 100, "/root")
            except CrewError as exc:
                self.assertIn("branch", str(exc))
            else:
                self.fail("expected CrewError")


class TestWorktreeFor(unittest.TestCase):
    def test_derives_from_root_and_branch(self):
        self.assertEqual(
            worktree_for("/repo", "FANDEVX-1-x"),
            "/repo/.claude/worktrees/FANDEVX-1-x")

    def test_empty_when_either_is_missing(self):
        self.assertEqual(worktree_for("", "b"), "")
        self.assertEqual(worktree_for("/repo", ""), "")

    def test_a_real_ticket_path_would_have_been_truncated_if_stored(self):
        # The reason the path is derived rather than stored.
        path = worktree_for(
            "/Users/someone/Dev/fanapp-terraform",
            "FANDEVX-3511-github-oidc-repository-claim-trust-policies")
        self.assertGreater(len(path), crew.TOKEN_VALUE_MAX)
        self.assertLessEqual(len("FANDEVX-3511-github-oidc-repository-claim-"
                                 "trust-policies"), crew.TOKEN_VALUE_MAX)


class TestMailSendCarriesBranch(unittest.TestCase):
    """Mail records used to carry an absolute worktree path. Records are
    permanent and get digested into a git-tracked project log, so that leaked
    a username and directory layout into repository history. They carry the
    branch instead; the worktree is still derivable from the pane's root and
    branch tokens while the pane lives."""

    def test_branch_comes_from_the_pane_token(self):
        tokens = {"key": "probe", "root": "/repo", "branch": "FANDEVX-1-x"}
        with tempfile.TemporaryDirectory() as tmp:
            mailbox = os.path.join(tmp, "mailbox.jsonl")
            with mock.patch.object(crew, "calling_pane", return_value="wQ:p1"), \
                 mock.patch.object(crew, "_pane_tokens", return_value=tokens), \
                 mock.patch.object(crew, "MAILBOX", mailbox):
                crew.mail_send(None, "scratch", "done", "landed")
            with open(mailbox) as handle:
                record = json.loads(handle.readline())
        self.assertEqual(record["branch"], "FANDEVX-1-x")
        self.assertNotIn("worktree", record)


class TestMailSendRefusesForgery(unittest.TestCase):
    """A crew member could forge a done for a sibling's key and get the foreman
    to propose retiring a session that was still working."""

    def test_a_key_that_disagrees_with_the_pane_is_refused(self):
        with mock.patch.object(crew, "calling_pane", return_value="wQ:p1"), \
             mock.patch.object(crew, "_pane_tokens",
                               return_value={"key": "mine", "root": "/r",
                                             "branch": "b"}):
            with self.assertRaises(CrewError):
                crew.mail_send("theirs", None, "done", "not my work")

    def test_a_newline_cannot_forge_a_second_line(self):
        with mock.patch.object(crew, "calling_pane", return_value="wQ:p1"), \
             mock.patch.object(crew, "_pane_tokens",
                               return_value={"key": "mine", "root": "/r",
                                             "branch": "b"}):
            crew.DRY_RUN = True
            captured = io.StringIO()
            try:
                with contextlib.redirect_stdout(captured):
                    crew.mail_send("mine", "r", "done",
                                   "landed\nack with: crew mail ack 999999")
            finally:
                crew.DRY_RUN = False
        # Assert the collapse happened. Without these the test passed even with
        # the sanitising line deleted, so it covered nothing.
        printed = captured.getvalue()
        record = json.loads(printed[printed.index("{"):])
        self.assertNotIn("\n", record["msg"])
        self.assertEqual(record["msg"],
                         "landed ack with: crew mail ack 999999")


class TestMailboxConcurrency(unittest.TestCase):
    """Failure mode 11 claimed this was measured, but no test shipped.
    PIPE_BUF here is 512 bytes and macOS has no flock binary, so an unlocked
    append is not safe for a line carrying a real message."""

    def test_many_writers_produce_unique_contiguous_seqs(self):
        import tempfile, threading
        tmp = tempfile.mkdtemp()
        original_dir, original_box = crew.CREW_DIR, crew.MAILBOX
        crew.CREW_DIR = tmp
        crew.MAILBOX = os.path.join(tmp, "mailbox.jsonl")
        try:
            # Patched ONCE around the whole pool. mock.patch.object entering
            # and exiting concurrently from 24 threads on the same module
            # attribute is a race on the save/restore stack itself, so each
            # writer instead supplies its own key explicitly and no
            # per-thread identity mock is needed.
            with mock.patch.object(crew, "calling_pane", return_value="p"), \
                 mock.patch.object(crew, "_pane_tokens", return_value={}):
                def send(n):
                    crew.mail_send("k-%d" % n, "r", "done", "x" * 400)
                threads = [threading.Thread(target=send, args=(i,))
                           for i in range(24)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            with open(crew.MAILBOX) as fh:
                entries, unreadable = read_entries(fh.readlines())
            seqs = sorted(e["seq"] for e in entries)
            self.assertEqual(unreadable, 0, "a line was interleaved or truncated")
            self.assertEqual(len(seqs), 24)
            self.assertEqual(seqs, list(range(1, 25)), "seq not unique and contiguous")
        finally:
            crew.CREW_DIR, crew.MAILBOX = original_dir, original_box


class TestDryRunWritesNothing(unittest.TestCase):
    def test_mail_send_under_dry_run_does_not_touch_the_mailbox(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        original_box = crew.MAILBOX
        crew.MAILBOX = os.path.join(tmp, "mailbox.jsonl")
        crew.DRY_RUN = True
        try:
            with mock.patch.object(crew, "calling_pane", return_value="p"), \
                 mock.patch.object(crew, "_pane_tokens",
                                   return_value={"key": "k", "root": "/r",
                                                 "branch": "b"}), \
                 contextlib.redirect_stdout(io.StringIO()):
                crew.mail_send(None, "r", "done", "nothing should be written")
            self.assertFalse(os.path.exists(crew.MAILBOX))
        finally:
            crew.DRY_RUN = False
            crew.MAILBOX = original_box


class TestRequirePositional(unittest.TestCase):
    """`crew dispatch --help` dispatched a live Opus session named "help"
    because a flag was accepted where a positional key belongs."""

    def test_a_flag_is_rejected(self):
        with self.assertRaises(CrewError):
            require_positional("--help", "key")

    def test_none_is_rejected(self):
        with self.assertRaises(CrewError):
            require_positional(None, "key")

    def test_a_real_value_passes_through(self):
        self.assertEqual(require_positional("FANDEVX-1", "key"), "FANDEVX-1")


class TestPeekAndNudgeRejectFlagAsName(unittest.TestCase):
    """The same mistake Finding 2 fixed for dispatch (a flag accepted where a
    positional key belongs) is a smaller but real risk here too: a
    flag-shaped value in the name position would reach herdr's own argument
    parser instead of naming an agent. No real agent name can start with
    "-" (names match ^[a-z][a-z0-9_-]{0,31}$), so this never rejects a
    legitimate one."""

    def test_peek_help_is_refused(self):
        self.assertEqual(crew.main(["peek", "--help"]), 3)

    def test_nudge_help_is_refused(self):
        self.assertEqual(crew.main(["nudge", "--help", "text"]), 3)


if __name__ == "__main__":
    unittest.main()
