"""Tests for the pure decision logic in crew.py."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import crew
from crew import (sanitize_name, pick_name, bucket, _probe, crew_members,
                   untagged_agents, render_ls, assert_snapshot_shape,
                   assert_schema_declares, CrewError, HerdrError,
                   read_entries, next_seq, select_unread,
                   contract_pointer, find_member, find_setup_pane, is_ticket,
                   take_flag, _start_agent, resolve_repo, repo_root_for,
                   clamp_lines, require_positional, worktree_for, is_inside,
                   read_dispatch_artifact, same_path)


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


class TestFindSetupPane(unittest.TestCase):
    """The mirror image of find_member: a setup pane must still be findable
    on a retry, or a stuck human answering a prompt makes dispatch open a
    second paid setup session on top of the first."""

    def test_finds_a_matching_setup_pane(self):
        toks = {"crew": "true", "v": "1", "key": "k", "repo": "r",
                "root": "/root", "type": "setup"}
        snap = {"agents": [_full_agent("wQ:p1", "idle", "setup-k")],
                "panes": [_full_pane("wQ:p1", toks)]}
        found = find_setup_pane(snap, "/root", "k")
        self.assertIsNotNone(found)
        self.assertEqual(found["pane"], "wQ:p1")

    def test_ignores_a_non_setup_member(self):
        snap = _snap([_agent("wQ:p1", "idle", "fandevx-3511")],
                     [_pane("wQ:p1", CREW_TOKENS)])
        self.assertIsNone(
            find_setup_pane(snap, CREW_TOKENS["root"], "fandevx-3511"))

    def test_root_must_match(self):
        toks = {"crew": "true", "v": "1", "key": "k", "repo": "r",
                "root": "/root", "type": "setup"}
        snap = {"agents": [_full_agent("wQ:p1", "idle", "setup-k")],
                "panes": [_full_pane("wQ:p1", toks)]}
        self.assertIsNone(find_setup_pane(snap, "/elsewhere", "k"))


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
             mock.patch.dict(crew.os.environ, {"HERDR_WORKSPACE_ID": "wTest"}), \
             contextlib.redirect_stdout(io.StringIO()):
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


class TestDispatchRejectsATypeNameAsKey(unittest.TestCase):
    """`crew dispatch planner --type implementer` used to return 0 and start
    a paid session on a branch named planner, because planner is a valid
    slug that also happens to be a crew type name."""

    def test_a_type_name_is_refused_not_dispatched(self):
        with self.assertRaises(CrewError) as ctx:
            crew.cmd_dispatch("planner", "implementer", None, None)
        self.assertIn("crew type", str(ctx.exception))

    def test_case_is_normalised_before_the_check(self):
        with self.assertRaises(CrewError):
            crew.cmd_dispatch("PLANNER", "implementer", None, None)

    def test_setup_is_reserved_too(self):
        with self.assertRaises(CrewError):
            crew.cmd_dispatch("setup", "implementer", None, None)

    def test_refused_through_the_verb_too(self):
        self.assertEqual(
            crew.main(["dispatch", "planner", "--type", "implementer"]), 3)

    def test_a_real_key_passes_the_guard(self):
        # Not a type name, so this must fail later, for an unrelated reason,
        # rather than being caught by this guard.
        env = dict(crew.os.environ)
        env.pop("HERDR_WORKSPACE_ID", None)
        with mock.patch.dict(crew.os.environ, env, clear=True):
            with self.assertRaises(CrewError) as ctx:
                crew.cmd_dispatch("fandevx-9001", "implementer", None, None)
        self.assertIn("herdr pane", str(ctx.exception))


class TestSetupNoLongerPolls(unittest.TestCase):
    """setup_worktree polled for the artifact on a 900 second deadline AFTER
    the paid setup session already existed. Claude Code's own Bash timeout
    (120s default, 600s max) killed the call before the poll ever finished,
    and the setup pane survived the kill. Dispatch must resume on a later
    call instead of ever waiting."""

    def test_setup_timeout_constant_is_gone(self):
        self.assertFalse(hasattr(crew, "SETUP_TIMEOUT"))


class TestDispatchResumesFromArtifact(unittest.TestCase):
    """Once /start-ticket has written its handoff artifact, a later dispatch
    call must pick it up directly rather than re-running setup."""

    def _herdr(self):
        calls = []

        def fake(*args, **kwargs):
            calls.append(args)
            if args[:2] == ("tab", "create"):
                return {"result": {"root_pane": {"pane_id": "wTest:pArtifact"}}}
            return {"ok": True}
        return fake, calls

    def test_artifact_present_completes_without_opening_setup(self):
        fake, calls = self._herdr()
        with mock.patch.object(crew, "resolve_repo",
                               return_value=("/fake/repo", "artifact-test")), \
             mock.patch.object(crew, "read_dispatch_artifact",
                               return_value="/fake/repo/.claude/worktrees/x"), \
             mock.patch.object(crew, "find_setup_pane", return_value=None), \
             mock.patch.object(crew, "start_setup") as start, \
             mock.patch.object(crew, "snapshot", return_value=_snap([], [])), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.object(crew, "herdr", side_effect=fake), \
             mock.patch.dict(crew.os.environ, {"HERDR_WORKSPACE_ID": "wTest"}), \
             contextlib.redirect_stdout(io.StringIO()):
            code = crew.main(["dispatch", "FANDEVX-9001", "--type",
                              "implementer", "--repo", "artifact-test"])
        self.assertEqual(code, 0)
        start.assert_not_called()
        self.assertFalse(any(c[:2] == ("pane", "split") for c in calls))

    def test_a_lingering_setup_pane_is_closed_on_completion(self):
        fake, calls = self._herdr()
        with mock.patch.object(crew, "resolve_repo",
                               return_value=("/fake/repo", "artifact-test")), \
             mock.patch.object(crew, "read_dispatch_artifact",
                               return_value="/fake/repo/.claude/worktrees/x"), \
             mock.patch.object(crew, "find_setup_pane",
                               return_value={"pane": "wTest:pSetup"}), \
             mock.patch.object(crew, "snapshot", return_value=_snap([], [])), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.object(crew, "herdr", side_effect=fake), \
             mock.patch.dict(crew.os.environ, {"HERDR_WORKSPACE_ID": "wTest"}), \
             contextlib.redirect_stdout(io.StringIO()):
            code = crew.main(["dispatch", "FANDEVX-9002", "--type",
                              "implementer", "--repo", "artifact-test"])
        self.assertEqual(code, 0)
        self.assertIn(("pane", "close", "wTest:pSetup"), calls)


class TestDispatchOpensSetupWithoutBlocking(unittest.TestCase):
    """crew dispatch on a JIRA key must return immediately once the setup
    pane exists, rather than blocking on an artifact a paid setup session
    has not written yet. A retry that finds the setup pane still there must
    not spawn a second one."""

    def test_a_fresh_jira_key_opens_setup_and_returns_seven(self):
        with mock.patch.object(crew, "resolve_repo",
                               return_value=("/fake/repo", "setup-test")), \
             mock.patch.object(crew, "read_dispatch_artifact",
                               return_value=None), \
             mock.patch.object(crew, "find_setup_pane", return_value=None), \
             mock.patch.object(crew, "start_setup",
                               return_value="wTest:pSetup") as start, \
             mock.patch.object(crew, "snapshot", return_value=_snap([], [])), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.dict(crew.os.environ, {"HERDR_WORKSPACE_ID": "wTest"}), \
             contextlib.redirect_stdout(io.StringIO()):
            code = crew.main(["dispatch", "FANDEVX-9003", "--type",
                              "implementer", "--repo", "setup-test"])
        self.assertEqual(code, 7)
        start.assert_called_once()

    def test_an_existing_setup_pane_is_reported_not_duplicated(self):
        with mock.patch.object(crew, "resolve_repo",
                               return_value=("/fake/repo", "setup-test")), \
             mock.patch.object(crew, "read_dispatch_artifact",
                               return_value=None), \
             mock.patch.object(crew, "find_setup_pane",
                               return_value={"pane": "wTest:pSetup"}), \
             mock.patch.object(crew, "start_setup") as start, \
             mock.patch.object(crew, "snapshot", return_value=_snap([], [])), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.dict(crew.os.environ, {"HERDR_WORKSPACE_ID": "wTest"}), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            code = crew.main(["dispatch", "FANDEVX-9004", "--type",
                              "implementer", "--repo", "setup-test"])
        self.assertEqual(code, 7)
        start.assert_not_called()
        self.assertIn("wTest:pSetup", out.getvalue())


class TestSlugDispatchStillCompletesInOneCall(unittest.TestCase):
    """A slug has no ticket to fetch and so no interactive step. It must not
    be routed through setup at all, unlike a JIRA key."""

    def test_a_slug_never_touches_setup(self):
        def fake_herdr(*args, **kwargs):
            if args[:2] == ("tab", "create"):
                return {"result": {"root_pane": {"pane_id": "wTest:pSlug"}}}
            return {"ok": True}

        with mock.patch.object(crew, "resolve_repo",
                               return_value=("/fake/repo", "slug-test")), \
             mock.patch.object(crew, "plain_worktree",
                               return_value="/fake/repo/.claude/worktrees/y"), \
             mock.patch.object(crew, "find_setup_pane") as setup_lookup, \
             mock.patch.object(crew, "start_setup") as start, \
             mock.patch.object(crew, "snapshot", return_value=_snap([], [])), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.object(crew, "herdr", side_effect=fake_herdr), \
             mock.patch.dict(crew.os.environ, {"HERDR_WORKSPACE_ID": "wTest"}), \
             contextlib.redirect_stdout(io.StringIO()):
            code = crew.main(["dispatch", "spike-something", "--type",
                              "implementer", "--repo", "slug-test"])
        self.assertEqual(code, 0)
        setup_lookup.assert_not_called()
        start.assert_not_called()


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


class TestClaimForeman(unittest.TestCase):
    """A herdr agent name binds to the agent, not the pane, and clears when the
    agent exits. Verified: renaming a pane with no agent fails agent_not_found.
    So the foreman skill's claim to already BE named foreman needs something to
    make it true, every session."""

    def test_renames_when_no_foreman_exists(self):
        snap = {"agents": [_full_agent("wQ:p9", "idle", "someone-else")],
                "panes": []}
        with mock.patch.object(crew, "calling_pane", return_value="wV:p1"), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.object(crew, "snapshot", return_value=snap), \
             mock.patch.object(crew, "herdr") as fake, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(crew.claim_foreman(), 0)
            fake.assert_called_once_with("agent", "rename", "wV:p1", "foreman")

    def test_no_op_when_this_pane_already_holds_it(self):
        snap = {"agents": [_full_agent("wV:p1", "idle", "foreman")], "panes": []}
        with mock.patch.object(crew, "calling_pane", return_value="wV:p1"), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.object(crew, "snapshot", return_value=snap), \
             mock.patch.object(crew, "herdr") as fake, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(crew.claim_foreman(), 0)
            fake.assert_not_called()

    def test_refuses_to_steal_the_name_from_another_pane(self):
        snap = {"agents": [_full_agent("wQ:pT", "idle", "foreman")], "panes": []}
        with mock.patch.object(crew, "calling_pane", return_value="wV:p1"), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.object(crew, "snapshot", return_value=snap), \
             mock.patch.object(crew, "herdr") as fake:
            with self.assertRaises(CrewError):
                crew.claim_foreman()
            fake.assert_not_called()

    def test_outside_herdr_is_a_clean_error(self):
        with mock.patch.object(crew, "calling_pane", return_value=""):
            with self.assertRaises(CrewError):
                crew.claim_foreman()

    def test_schema_drift_is_caught_before_any_rename(self):
        """One commit after cmd_ls and cmd_dispatch both gained this check,
        claim_foreman still called snapshot() raw. `name` is optional
        precisely because a rename empties every lookup, so under drift this
        must fail closed rather than attempt a rename with herdr's own
        uniqueness check as the only guard."""
        with mock.patch.object(crew, "calling_pane", return_value="wV:p1"), \
             mock.patch.object(crew, "schema_defs",
                               side_effect=CrewError("boom")), \
             mock.patch.object(crew, "herdr") as fake:
            with self.assertRaises(CrewError):
                crew.claim_foreman()
            fake.assert_not_called()


class TestClaimForemanVerb(unittest.TestCase):
    """Deleting `if verb == "claim-foreman": return claim_foreman()` from
    `_run` would leave every test above green, because they call
    claim_foreman() directly, while `crew claim-foreman` silently became
    unknown-verb. Both skills instruct the session to run the VERB, so
    exercise crew.main instead."""

    def test_success_through_the_verb(self):
        snap = {"agents": [_full_agent("wQ:p9", "idle", "someone-else")],
                "panes": []}
        with mock.patch.object(crew, "calling_pane", return_value="wV:p1"), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.object(crew, "snapshot", return_value=snap), \
             mock.patch.object(crew, "herdr") as fake, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(crew.main(["claim-foreman"]), 0)
            fake.assert_called_once_with("agent", "rename", "wV:p1", "foreman")

    def test_no_op_through_the_verb(self):
        snap = {"agents": [_full_agent("wV:p1", "idle", "foreman")], "panes": []}
        with mock.patch.object(crew, "calling_pane", return_value="wV:p1"), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.object(crew, "snapshot", return_value=snap), \
             mock.patch.object(crew, "herdr") as fake, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(crew.main(["claim-foreman"]), 0)
            fake.assert_not_called()

    def test_refusal_to_steal_returns_three_and_names_the_pane(self):
        snap = {"agents": [_full_agent("wQ:pT", "idle", "foreman")], "panes": []}
        buf = io.StringIO()
        with mock.patch.object(crew, "calling_pane", return_value="wV:p1"), \
             mock.patch.object(crew, "schema_defs", return_value=DEFS), \
             mock.patch.object(crew, "snapshot", return_value=snap), \
             mock.patch.object(crew, "herdr") as fake, \
             mock.patch.object(crew.sys, "stderr", buf):
            code = crew.main(["claim-foreman"])
        self.assertEqual(code, 3)
        fake.assert_not_called()
        self.assertIn("wQ:pT", buf.getvalue())

    def test_outside_herdr_returns_three_through_the_verb(self):
        buf = io.StringIO()
        with mock.patch.object(crew, "calling_pane", return_value=""), \
             mock.patch.object(crew.sys, "stderr", buf):
            code = crew.main(["claim-foreman"])
        self.assertEqual(code, 3)


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


@contextlib.contextmanager
def _repo_world(branch="FANDEVX-9101-x", repo_name="repo"):
    """A real repo root with a real worktree under it, plus a private CREW_DIR.

    The artifact is a real file here rather than a mock, because its lifetime
    on disk IS the behaviour under test. CREW_DIR is redirected so the suite
    never writes into the developer's own ~/.crew."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_root = os.path.join(tmp, repo_name)
        worktree = os.path.join(repo_root, ".claude", "worktrees", branch)
        os.makedirs(worktree)
        crew_dir = os.path.join(tmp, "dot-crew")
        os.makedirs(crew_dir)
        with mock.patch.object(crew, "CREW_DIR", crew_dir):
            yield tmp, repo_root, worktree


def _write_artifact(key, worktree, repo, branch=None, text=None):
    path = crew.dispatch_artifact_path(key)
    if branch is None and worktree is not None:
        branch = os.path.basename(worktree)
    with open(path, "w") as handle:
        if text is not None:
            handle.write(text)
        else:
            handle.write(json.dumps({"worktree": worktree, "branch": branch,
                                     "repo": repo}))
    return path


def _run_dispatch(key, repo_root, repo_name, tab_error=None, dry=False):
    """crew.main(["dispatch", ...]) with herdr faked. Returns
    (code, herdr calls, stdout, stderr)."""
    calls = []

    def fake_herdr(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("tab", "create"):
            if tab_error is not None:
                raise tab_error
            return {"result": {"root_pane": {"pane_id": "wTest:pW3"}}}
        if args[:2] == ("pane", "split"):
            return {"result": {"pane": {"pane_id": "wTest:pSetup"}}}
        return {"ok": True}

    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(crew, "resolve_repo",
                           return_value=(repo_root, repo_name)), \
         mock.patch.object(crew, "snapshot", return_value=_snap([], [])), \
         mock.patch.object(crew, "schema_defs", return_value=DEFS), \
         mock.patch.object(crew, "herdr", side_effect=fake_herdr), \
         mock.patch.object(crew, "DRY_RUN", dry), \
         mock.patch.dict(crew.os.environ, {"HERDR_WORKSPACE_ID": "wTest"}), \
         contextlib.redirect_stdout(out), \
         mock.patch.object(crew.sys, "stderr", err):
        code = crew.main(["dispatch", key, "--type", "implementer"])
    return code, calls, out.getvalue(), err.getvalue()


class TestDispatchClearsTheConsumedArtifact(unittest.TestCase):
    """Making dispatch resumable gave the artifact a lifetime longer than one
    call, and nothing ended it. Once the crew member's pane was gone the next
    dispatch of that key found the stale artifact, skipped /start-ticket, and
    started a PAID session on the old worktree at exit 0."""

    def test_a_successful_dispatch_leaves_no_artifact(self):
        with _repo_world() as (_, repo_root, worktree):
            artifact = _write_artifact("FANDEVX-9101", worktree, "repo")
            code, calls, _, err = _run_dispatch("FANDEVX-9101", repo_root,
                                                "repo")
            self.assertEqual(code, 0, err)
            self.assertTrue(any(c[:2] == ("tab", "create") for c in calls))
            self.assertFalse(os.path.exists(artifact),
                             "a consumed artifact must not survive, or the "
                             "next dispatch of this key skips /start-ticket "
                             "and pays for a session on a stale worktree")

    def test_a_dispatch_that_fails_partway_stays_resumable(self):
        with _repo_world() as (_, repo_root, worktree):
            artifact = _write_artifact("FANDEVX-9102", worktree, "repo")
            code, _, _, _ = _run_dispatch("FANDEVX-9102", repo_root, "repo",
                                          tab_error=HerdrError("no tab"))
            self.assertEqual(code, 3)
            self.assertTrue(os.path.exists(artifact),
                            "deleting the artifact before the dispatch "
                            "succeeds loses the setup work, and the next call "
                            "opens a second paid setup pane")

            code, calls, _, err = _run_dispatch("FANDEVX-9102", repo_root,
                                                "repo")
            self.assertEqual(code, 0, err)
            self.assertFalse(any(c[:2] == ("pane", "split") for c in calls),
                             "the retry must resume from the artifact, not "
                             "open a new setup pane")
            self.assertFalse(os.path.exists(artifact))

    def test_a_dry_run_leaves_the_artifact_alone(self):
        # --dry-run is the documented safe way to exercise dispatch. Deleting
        # resumable state during one would cost a real setup session later.
        with _repo_world() as (_, repo_root, worktree):
            artifact = _write_artifact("FANDEVX-9103", worktree, "repo")
            code, _, out, err = _run_dispatch("FANDEVX-9103", repo_root,
                                              "repo", dry=True)
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(artifact))
            # Narrated, like every other dry-run line, because the refusals
            # report the deletion in the past tense.
            self.assertIn("would delete %s" % artifact, out)


class TestIsInside(unittest.TestCase):
    """The containment check the artifact's worktree is validated with. A bare
    startswith accepts a neighbouring checkout whose name shares a prefix."""

    def test_a_child_is_inside(self):
        self.assertTrue(is_inside("/a/repo/.claude/worktrees/x", "/a/repo"))

    def test_a_prefix_sibling_is_not_inside(self):
        self.assertFalse(is_inside("/a/repo-old/.claude/worktrees/x", "/a/repo"))

    def test_the_parent_itself_is_not_inside(self):
        self.assertFalse(is_inside("/a/repo", "/a/repo"))

    def test_a_trailing_separator_on_the_parent_is_handled(self):
        self.assertTrue(is_inside("/a/repo/x", "/a/repo/"))
        self.assertFalse(is_inside("/a/repo-old/x", "/a/repo/"))

    def test_the_filesystem_root_contains_everything(self):
        # The one separator realpath does not normalise away, so the only
        # input where stripping it in the comparison is load-bearing.
        self.assertTrue(is_inside("/a", "/"))


class TestReadDispatchArtifact(unittest.TestCase):
    """The artifact is keyed on the ticket key alone, so the same key in two
    repos aims both dispatches at one file, and a model wrote its contents.
    Both make it something to validate, not to trust."""

    def test_no_artifact_is_none_not_an_error(self):
        with _repo_world() as (_, repo_root, _worktree):
            self.assertIsNone(
                read_dispatch_artifact("FANDEVX-9110", "repo", repo_root))

    def test_a_matching_artifact_returns_its_worktree(self):
        with _repo_world() as (_, repo_root, worktree):
            _write_artifact("FANDEVX-9111", worktree, "repo")
            self.assertEqual(
                read_dispatch_artifact("FANDEVX-9111", "repo", repo_root),
                worktree)

    def test_the_repo_mismatch_message_names_both_values(self):
        with _repo_world() as (_, repo_root, worktree):
            _write_artifact("FANDEVX-9112", worktree, "other-repo")
            with self.assertRaises(CrewError) as ctx:
                read_dispatch_artifact("FANDEVX-9112", "repo", repo_root)
        self.assertIn("other-repo", str(ctx.exception))
        self.assertIn("'repo'", str(ctx.exception))

    def test_an_artifact_without_a_repo_field_is_refused(self):
        with _repo_world() as (_, repo_root, worktree):
            artifact = _write_artifact(
                "FANDEVX-9113", worktree, None,
                text=json.dumps({"worktree": worktree, "branch": "b"}))
            with self.assertRaises(CrewError):
                read_dispatch_artifact("FANDEVX-9113", "repo", repo_root)
            self.assertFalse(os.path.exists(artifact))

    def test_a_worktree_that_is_gone_is_refused_and_discarded(self):
        # Previously this raised but kept the file, so the key stayed bricked
        # at exit 3: only start_setup unlinks, and it cannot be reached while
        # an artifact exists.
        with _repo_world() as (_, repo_root, worktree):
            os.rmdir(worktree)
            artifact = _write_artifact("FANDEVX-9114", worktree, "repo")
            with self.assertRaises(CrewError):
                read_dispatch_artifact("FANDEVX-9114", "repo", repo_root)
            self.assertFalse(os.path.exists(artifact))


class TestArtifactMustBeForThisRepo(unittest.TestCase):
    """An artifact whose repo disagreed and whose worktree lay outside the
    repo completed anyway, tagging the foreman's own root with the other
    checkout's branch, so the worktree derived from those tokens did not
    exist and both `crew ls` and the resume line printed that path."""

    def test_another_repo_is_refused_discarded_and_then_set_up_fresh(self):
        with _repo_world() as (_, repo_root, worktree):
            artifact = _write_artifact("FANDEVX-9120", worktree, "other-repo")
            code, calls, _, err = _run_dispatch("FANDEVX-9120", repo_root,
                                                "repo")
            self.assertEqual(code, 3)
            self.assertIn("other-repo", err)
            self.assertFalse(any(c[:2] == ("tab", "create") for c in calls),
                             "a wrong-repo artifact must not reach a paid "
                             "session")
            self.assertFalse(os.path.exists(artifact))

            # And the key is not bricked: the next call starts setup again.
            code, calls, _, err = _run_dispatch("FANDEVX-9120", repo_root,
                                                "repo")
            self.assertEqual(code, 7, err)
            self.assertTrue(any(c[:2] == ("pane", "split") for c in calls))

    def test_a_worktree_outside_the_repo_root_is_refused(self):
        with _repo_world() as (tmp, repo_root, _worktree):
            outside = os.path.join(tmp, "elsewhere", "wt-good")
            os.makedirs(outside)
            artifact = _write_artifact("FANDEVX-9121", outside, "repo")
            code, calls, _, err = _run_dispatch("FANDEVX-9121", repo_root,
                                                "repo")
            self.assertEqual(code, 3)
            self.assertIn(outside, err)
            self.assertFalse(any(c[:2] == ("tab", "create") for c in calls))
            self.assertFalse(os.path.exists(artifact))

    def test_a_prefix_sibling_checkout_is_refused(self):
        # /a/repo-old against /a/repo: the case a bare startswith accepts.
        with _repo_world() as (tmp, repo_root, _worktree):
            sibling = os.path.join(tmp, "repo-old", ".claude", "worktrees", "x")
            os.makedirs(sibling)
            artifact = _write_artifact("FANDEVX-9122", sibling, "repo")
            code, calls, _, _ = _run_dispatch("FANDEVX-9122", repo_root, "repo")
            self.assertEqual(code, 3)
            self.assertFalse(any(c[:2] == ("tab", "create") for c in calls))
            self.assertFalse(os.path.exists(artifact))

    def test_a_convention_path_that_symlinks_out_of_the_repo_is_refused(self):
        # The one escape the derived-path check cannot see: the artifact sits
        # at exactly the path the tokens reproduce, and that path is a symlink
        # to a directory outside the repo. Only the containment check refuses
        # it, so this is what keeps that check honest.
        with _repo_world() as (tmp, repo_root, _worktree):
            outside = os.path.join(tmp, "outside-wt")
            os.makedirs(outside)
            escape = os.path.join(repo_root, ".claude", "worktrees", "escape")
            os.symlink(outside, escape)
            artifact = _write_artifact("FANDEVX-9124", escape, "repo",
                                       branch="escape")
            code, calls, _, _ = _run_dispatch("FANDEVX-9124", repo_root, "repo")
            self.assertEqual(code, 3)
            self.assertFalse(any(c[:2] == ("tab", "create") for c in calls),
                             "a worktree that resolves outside the repo must "
                             "not reach a paid session, however it is spelled")
            self.assertFalse(os.path.exists(artifact))

    def test_unparseable_json_is_refused_discarded_and_set_up_fresh(self):
        with _repo_world() as (_, repo_root, _worktree):
            artifact = _write_artifact("FANDEVX-9123", None, None,
                                       text="{not json at all")
            code, calls, _, _ = _run_dispatch("FANDEVX-9123", repo_root, "repo")
            self.assertEqual(code, 3)
            self.assertFalse(any(c[:2] == ("tab", "create") for c in calls))
            self.assertFalse(os.path.exists(artifact))

            code, calls, _, err = _run_dispatch("FANDEVX-9123", repo_root,
                                                "repo")
            self.assertEqual(code, 7, err)
            self.assertTrue(any(c[:2] == ("pane", "split") for c in calls))


class TestSetupPromptStatesTheWorktreeInvariant(unittest.TestCase):
    """Dispatch refuses an artifact whose branch does not derive its worktree.
    That has to be stated where the artifact is WRITTEN, or the setup agent
    can only discover it by having a paid dispatch refused."""

    def test_the_prompt_names_the_path_dispatch_will_derive(self):
        sent = []

        def fake_herdr(*args, **kwargs):
            sent.append(args)
            return {"result": {"pane": {"pane_id": "wTest:pSetup"}}}

        with _repo_world() as (_, repo_root, _worktree):
            with mock.patch.object(crew, "herdr", side_effect=fake_herdr), \
                 mock.patch.object(crew, "_start_agent"):
                crew.start_setup("FANDEVX-9140", "repo", repo_root)
            prompts = [a for a in sent if a[:2] == ("agent", "prompt")]
            self.assertEqual(len(prompts), 1)
            self.assertIn(os.path.join(repo_root, ".claude", "worktrees"),
                          prompts[0][3])


class TestSamePath(unittest.TestCase):
    def test_two_spellings_of_one_directory_are_equal(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.join(tmp, "real")
            os.mkdir(real)
            link = os.path.join(tmp, "link")
            os.symlink(real, link)
            self.assertTrue(same_path(link, real))
            self.assertTrue(same_path(os.path.join(tmp, ".", "real"), real))

    def test_different_directories_are_not_equal(self):
        self.assertFalse(same_path("/a/repo/x", "/a/repo/y"))


class TestArtifactBranchMustDeriveTheWorktree(unittest.TestCase):
    """The tokens are the authoritative record and the worktree path is
    DERIVED from root plus branch, so an artifact whose branch does not derive
    its own worktree makes `crew ls`, `crew peek` and the exit 5 resume line
    print a path the work is not at. Containment inside the repo does not
    establish that: <root>/tmp/wt is inside the repo and derives
    <root>/.claude/worktrees/wt, which does not exist."""

    def test_a_worktree_off_the_convention_path_is_refused(self):
        with _repo_world() as (_, repo_root, _worktree):
            off = os.path.join(repo_root, "tmp", "wt")
            os.makedirs(off)
            artifact = _write_artifact("FANDEVX-9130", off, "repo")
            code, calls, _, err = _run_dispatch("FANDEVX-9130", repo_root,
                                                "repo")
            self.assertEqual(code, 3)
            self.assertFalse(any(c[:2] == ("tab", "create") for c in calls),
                             "a worktree the tokens cannot reproduce must not "
                             "reach a paid session")
            self.assertFalse(os.path.exists(artifact))
            self.assertIn(off, err)
            self.assertIn(os.path.join(repo_root, ".claude", "worktrees", "wt"),
                          err)

    def test_a_branch_that_disagrees_with_the_worktree_is_refused(self):
        with _repo_world(branch="real-branch") as (_, repo_root, worktree):
            artifact = _write_artifact("FANDEVX-9131", worktree, "repo",
                                       branch="other-branch")
            with self.assertRaises(CrewError) as ctx:
                read_dispatch_artifact("FANDEVX-9131", "repo", repo_root)
            self.assertFalse(os.path.exists(artifact))
        message = str(ctx.exception)
        self.assertIn(worktree, message)
        self.assertIn("other-branch", message)

    def test_a_symlinked_worktree_resolves_to_the_derived_path(self):
        # Same directory, different spelling. Dispatch must use the path the
        # tokens reproduce, which is also the one the guard hook recognises as
        # a crew worktree.
        with _repo_world(branch="br") as (_, repo_root, worktree):
            link = os.path.join(repo_root, "link")
            os.symlink(worktree, link)
            _write_artifact("FANDEVX-9132", link, "repo", branch="br")
            self.assertEqual(
                read_dispatch_artifact("FANDEVX-9132", "repo", repo_root),
                worktree)

    def test_an_absent_branch_is_refused(self):
        with _repo_world() as (_, repo_root, worktree):
            artifact = _write_artifact(
                "FANDEVX-9133", worktree, "repo",
                text=json.dumps({"worktree": worktree, "repo": "repo"}))
            with self.assertRaises(CrewError):
                read_dispatch_artifact("FANDEVX-9133", "repo", repo_root)
            self.assertFalse(os.path.exists(artifact))

    def test_a_branch_with_a_separator_is_refused(self):
        # Built so the derivation would round-trip: only the single-component
        # rule can refuse it.
        with _repo_world() as (_, repo_root, _worktree):
            nested = os.path.join(repo_root, ".claude", "worktrees", "a", "b")
            os.makedirs(nested)
            _write_artifact("FANDEVX-9134", nested, "repo", branch="a/b")
            with self.assertRaises(CrewError):
                read_dispatch_artifact("FANDEVX-9134", "repo", repo_root)

    def test_a_pardir_branch_is_refused(self):
        # Also round-trips: <root>/.claude/worktrees/.. resolves to
        # <root>/.claude, which is inside the repo and exists.
        with _repo_world() as (_, repo_root, _worktree):
            dotclaude = os.path.join(repo_root, ".claude")
            _write_artifact("FANDEVX-9135", dotclaude, "repo", branch="..")
            with self.assertRaises(CrewError):
                read_dispatch_artifact("FANDEVX-9135", "repo", repo_root)

    def test_an_over_length_branch_is_refused_before_any_pane_exists(self):
        # tag_pane already refuses an over-length token, but only after the
        # tab and pane exist, so every retry leaked an untagged pane and the
        # artifact survived to do it again.
        long_branch = "b" * (crew.TOKEN_VALUE_MAX + 1)
        with _repo_world(branch=long_branch) as (_, repo_root, worktree):
            artifact = _write_artifact("FANDEVX-9136", worktree, "repo")
            code, calls, _, _ = _run_dispatch("FANDEVX-9136", repo_root, "repo")
            self.assertEqual(code, 3)
            self.assertFalse(any(c[:2] == ("tab", "create") for c in calls),
                             "the branch must be refused before a pane exists")
            self.assertFalse(os.path.exists(artifact))


CREW_GUARD = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "hooks", "crew-guard.py"))

GUARD_CREW_CWD = "/Users/someone/Dev/repo/.claude/worktrees/FANDEVX-1-x"
GUARD_HUMAN_CWD = "/Users/someone/Dev/repo"

# The guard's own list is deliberately NOT imported. A test that loops over
# FORBIDDEN shrinks silently when an entry is deleted, which is the whole
# defect this covers: removing both entries added in wave 2 left the suite
# green.
GUARD_FORBIDDEN = (
    "crew dispatch FANDEVX-1 --type implementer",
    'crew nudge sibling "get on with it"',
    "crew mail ack 12",
    "crew claim-foreman",
    "herdr agent start rogue --kind claude --pane wV:p2",
    "herdr agent prompt sibling 'do this instead'",
    "herdr agent rename wV:p1 foreman",
    "herdr agent send-keys sibling 'rm -rf .'",
    "herdr pane close wV:p2",
    "herdr tab close wV:t2",
    "herdr workspace close wV",
    "herdr server stop",
)


def _guard(command, cwd=GUARD_CREW_CWD, tool_name="Bash", stdin=None):
    payload = {"tool_name": tool_name, "cwd": cwd,
               "tool_input": {"command": command}}
    text = json.dumps(payload) if stdin is None else stdin
    return subprocess.run([sys.executable, CREW_GUARD], input=text,
                          capture_output=True, text=True)


def _guard_decision(proc):
    """The contract Claude Code actually uses: exit 0 always, an empty stdout
    allows, and a deny is a permissionDecision in a JSON object."""
    if not proc.stdout.strip():
        return "allow", ""
    hook = json.loads(proc.stdout)["hookSpecificOutput"]
    return hook["permissionDecision"], hook["permissionDecisionReason"]


class TestCrewGuardHook(unittest.TestCase):
    """crew-guard.py is the only enforcement of a boundary that is otherwise
    pure convention, and it shipped twice with no test at all. Driven as a
    subprocess over real JSON on stdin, because that contract, not any
    importable function, is what Claude Code invokes."""

    def _assert(self, proc, expected, command):
        self.assertEqual(proc.returncode, 0,
                         "the guard must never crash: %s" % proc.stderr)
        decision, reason = _guard_decision(proc)
        self.assertEqual(decision, expected, "%s: %s" % (command, reason))
        return reason

    def test_every_forbidden_command_is_denied_in_a_crew_worktree(self):
        for command in GUARD_FORBIDDEN:
            with self.subTest(command=command):
                reason = self._assert(_guard(command), "deny", command)
                self.assertIn("crew-guard", reason)
                self.assertIn(GUARD_CREW_CWD, reason)

    def test_the_same_commands_are_allowed_outside_a_worktree(self):
        # The human's own shell, and the foreman's. Denying here would get the
        # hook switched off.
        for command in GUARD_FORBIDDEN:
            with self.subTest(command=command):
                self._assert(_guard(command, cwd=GUARD_HUMAN_CWD), "allow",
                             command)

    def test_a_crew_member_may_still_report_and_read(self):
        for command in ("crew mail send --key fandevx-1 done landed",
                        "herdr agent read foreman --lines 40",
                        "crew mail unread",
                        "crew ls",
                        "crew peek sibling"):
            with self.subTest(command=command):
                self._assert(_guard(command), "allow", command)

    def test_every_bypass_form_that_has_broken_it_once_is_denied(self):
        for command in (
                # Bare name.
                "crew dispatch FANDEVX-1",
                # Absolute path to the same program.
                "/Users/someone/.local/bin/crew dispatch FANDEVX-1",
                # The .py form: five characters that defeated the first
                # version of this guard outright.
                "python3 /Users/someone/.claude/skills/foreman/scripts/"
                "crew.py dispatch FANDEVX-1",
                # A global flag between the program and its subcommand.
                "herdr --json agent rename wV:p1 foreman"):
            with self.subTest(command=command):
                self._assert(_guard(command), "deny", command)

    def test_a_forbidden_verb_inside_a_commit_message_is_allowed(self):
        # The raw substring pass was removed on purpose: it denied real work
        # to catch a wrapper evasion the module docstring already concedes.
        self._assert(_guard('git commit -m "crew dispatch is foreman-only"'),
                     "allow", "git commit")

    def test_a_non_bash_tool_is_not_inspected(self):
        self._assert(_guard("crew dispatch FANDEVX-1", tool_name="Read"),
                     "allow", "Read")

    def test_an_unknown_cwd_allows_rather_than_blocking_the_human(self):
        self._assert(_guard("crew dispatch FANDEVX-1", cwd=None), "allow",
                     "no cwd")

    def test_malformed_stdin_exits_zero_and_allows(self):
        self._assert(_guard("", stdin="not json at all"), "allow", "bad stdin")


class TestDoctorFailsClosedOnProtocolDrift(unittest.TestCase):
    """herdr self-updates, and the whole design rests on five MEASURED
    behaviours. An unverified protocol must fail the preflight, because the
    foreman skill is told to stop on a red doctor."""

    def _doctor(self, protocol):
        def fake_probe(argv):
            if argv[:2] == ["herdr", "--version"]:
                return True, "herdr 9.9.9"
            if argv[:3] == ["herdr", "api", "schema"]:
                return True, "protocol: %d" % protocol
            if argv[:2] == ["claude", "--help"]:
                return True, "--append-system-prompt --continue --model"
            raise AssertionError("unexpected probe: %s" % argv)

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(crew, "_probe", side_effect=fake_probe), \
                 mock.patch.object(crew, "schema_defs", return_value=DEFS), \
                 mock.patch.object(crew, "snapshot",
                                   return_value=_snap([], [])), \
                 mock.patch.object(crew, "CREW_DIR", tmp), \
                 mock.patch.object(crew, "MAILBOX",
                                   os.path.join(tmp, "mailbox.jsonl")), \
                 contextlib.redirect_stdout(out):
                code = crew.doctor()
        return code, out.getvalue()

    def test_an_unverified_protocol_makes_doctor_fail(self):
        code, out = self._doctor(99)
        self.assertEqual(code, 1)
        self.assertIn("protocol 99 has not been verified", out)
        self.assertNotIn("(verified)", out)
        self.assertIn("FAIL", out)

    def test_a_verified_protocol_is_reported_as_verified(self):
        # The exit code is deliberately not asserted here: doctor also probes
        # ~/.local/bin/crew and the shadowed skill path, which belong to the
        # machine rather than to this test. The message is the discriminator.
        _, out = self._doctor(crew.HERDR_VERIFIED_PROTOCOLS[-1])
        self.assertIn("(verified)", out)
        self.assertNotIn("has not been verified", out)


if __name__ == "__main__":
    unittest.main()
