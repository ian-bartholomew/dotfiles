#!/usr/bin/env python3
"""crew: session-level orchestration over herdr.

Every herdr call in the foreman/crew system goes through this file. It is
deterministic: no model is in the loop.
"""
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import time
import warnings

# Protocols whose behaviour has been MEASURED, not assumed. herdr self-updates,
# so this will go stale: 0.7.5 shipped 17 and 0.8.0 shipped 19 mid-build. Do not
# add a number without re-running the drift checks in the spec, because the
# design rests on five measured behaviours, not on documented ones.
HERDR_VERIFIED_PROTOCOLS = (17, 19)
CREW_DIR = os.path.expanduser("~/.crew")
MAILBOX = os.path.join(CREW_DIR, "mailbox.jsonl")
CURSOR = os.path.join(CREW_DIR, "cursor")

DRY_RUN = False

BUCKETS = {
    "working": "working",
    "done": "awaiting",
    "idle": "awaiting",
    "blocked": "blocked",
    "unknown": "recover",
}

# Fields crew reads that herdr's schema declares OPTIONAL. A pane that has
# never been tagged genuinely has no tokens key, so requiring one in data is
# wrong; but a rename would make every lookup return None and the fleet read
# as empty. Assert the declaration, not the value.
# Only fields crew actually READS. Asserting a field nobody reads turns a
# harmless herdr rename into crew ls refusing a healthy fleet, which is a
# false positive in the one surface that must never lie.
SCHEMA_OPTIONAL_PANE_FIELDS = ("tokens",)
SCHEMA_OPTIONAL_AGENT_FIELDS = ("name", "terminal_title_stripped")

# crew-guard.py is the only enforcement of a boundary that is otherwise pure
# convention, and it can act only on the tools the PreToolUse matcher hands it.
# That matcher lives in a file this repo neither ships nor mirrors, so the hook's
# tables and the matcher drift apart silently, and an under-matched hook is inert
# while every doc still calls it the enforcement. doctor reads the required tools
# out of the hook itself; a list here would be the same drift one layer up.
SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")
GUARD_HOOK = "crew-guard.py"
GUARD_TABLES = ("FORBIDDEN_TOOLS", "COMMAND_FIELDS")
GUARD_RELOAD = ("then open /hooks or restart Claude Code, because a hook change "
                "is not live until then")


class HerdrError(Exception):
    pass


class CrewError(Exception):
    pass


def sanitize_name(key):
    s = re.sub(r"[^a-z0-9_-]+", "-", key.lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        raise ValueError("key produced an empty name: %r" % key)
    if not s[0].isalpha():
        s = "c-" + s
    return s[:32]


def pick_name(key, live_names):
    base = sanitize_name(key)
    if base not in live_names:
        return base
    for n in range(2, 100):
        suffix = "-%d" % n
        candidate = base[: 32 - len(suffix)] + suffix
        if candidate not in live_names:
            return candidate
    raise CrewError("no free agent name for %s" % key)


def bucket(agent_status):
    return BUCKETS.get(agent_status, "recover")


def herdr(*args, **kwargs):
    capture = kwargs.pop("capture", True)
    read_only = kwargs.pop("read_only", False)
    raw = kwargs.pop("raw", False)
    if kwargs:
        raise TypeError("unexpected kwargs: %s" % sorted(kwargs))
    if DRY_RUN and not read_only:
        print("herdr " + " ".join(shlex.quote(a) for a in args))
        return None
    proc = subprocess.run(
        ["herdr"] + list(args), capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise HerdrError(
            (proc.stderr or proc.stdout).strip()
            or "herdr %s exited %d" % (args[0], proc.returncode)
        )
    # `agent read` returns raw terminal text, not JSON; `--format json` is
    # rejected outright. raw callers want that text as-is, never parsed.
    if raw:
        return proc.stdout
    out = proc.stdout.strip()
    if not out or not capture:
        return None
    try:
        return json.loads(out)
    except ValueError:
        raise HerdrError("herdr %s returned non-JSON: %.200s" % (args[0], out))


def snapshot():
    payload = herdr("api", "snapshot", read_only=True)
    if payload is None:
        raise CrewError("no snapshot (dry-run?)")
    try:
        snap = payload["result"]["snapshot"]
    except (KeyError, TypeError):
        raise CrewError("SNAPSHOT UNPARSED: no result.snapshot in herdr output")
    return snap


def schema_defs():
    payload = herdr("api", "schema", "--json", read_only=True)
    if payload is None:
        raise CrewError("no schema returned")
    try:
        return payload["schemas"]["success_response"]["$defs"]
    except (KeyError, TypeError):
        raise CrewError("SCHEMA UNPARSED: no schemas.success_response.$defs")


def assert_schema_declares(defs):
    """Catch a renamed optional field at the declaration layer."""
    for type_name, fields in (("PaneInfo", SCHEMA_OPTIONAL_PANE_FIELDS),
                              ("AgentInfo", SCHEMA_OPTIONAL_AGENT_FIELDS)):
        props = (defs.get(type_name) or {}).get("properties")
        if not props:
            raise CrewError("SCHEMA UNPARSED: no %s.properties" % type_name)
        missing = [f for f in fields if f not in props]
        if missing:
            raise CrewError("SCHEMA DRIFT: %s no longer declares %s"
                            % (type_name, ", ".join(missing)))


def assert_snapshot_shape(snap, defs):
    """Fail closed against herdr's own required lists, not a hand-written
    one. A shape change must not read as an empty fleet."""
    for name in ("agents", "panes"):
        if not isinstance(snap.get(name), list):
            raise CrewError("SNAPSHOT UNPARSED: %s is not a list" % name)
    for type_name, name in (("AgentInfo", "agents"), ("PaneInfo", "panes")):
        required = (defs.get(type_name) or {}).get("required")
        if not required:
            raise CrewError("SCHEMA UNPARSED: no %s.required" % type_name)
        for item in snap[name]:
            missing = [f for f in required if f not in item]
            if missing:
                raise CrewError("SNAPSHOT UNPARSED: %s missing %s"
                                % (type_name, ", ".join(missing)))


def ensure_crew_dir():
    os.makedirs(CREW_DIR, mode=0o700, exist_ok=True)
    os.chmod(CREW_DIR, 0o700)


def doctor():
    problems = []

    ok, version = _probe(["herdr", "--version"])
    if ok:
        print("herdr: %s" % version)
    else:
        problems.append(version)

    ok, schema = _probe(["herdr", "api", "schema"])
    if not ok:
        problems.append(schema)
    else:
        found = re.search(r"protocol:\s*(\d+)", schema)
        if not found:
            problems.append("could not read protocol from herdr api schema")
        elif int(found.group(1)) not in HERDR_VERIFIED_PROTOCOLS:
            problems.append(
                "herdr protocol %s has not been verified against crew "
                "(verified: %s). herdr self-updates, so this is expected "
                "eventually. Re-run the drift checks in the spec's Verified "
                "substrate section, then add %s to HERDR_VERIFIED_PROTOCOLS. "
                "Do not just add the number."
                % (found.group(1), ", ".join(str(n) for n in
                                             HERDR_VERIFIED_PROTOCOLS),
                   found.group(1))
            )
        else:
            print("protocol: %s (verified)" % found.group(1))

    try:
        defs = schema_defs()
        assert_schema_declares(defs)
        snap = snapshot()
        assert_snapshot_shape(snap, defs)
        print("snapshot: %d agents, %d panes" % (len(snap["agents"]), len(snap["panes"])))
    except (CrewError, HerdrError) as exc:
        problems.append(str(exc))

    ok, help_text = _probe(["claude", "--help"])
    if not ok:
        problems.append(help_text)
    else:
        # No --permission-mode: the planner type deliberately does not run in
        # plan mode, because plan mode blocks bash and a planner could then
        # never send its own report. Requiring a flag nothing passes would fail
        # the preflight for a reason unrelated to anything crew does, and the
        # foreman skill says to stop on a red doctor.
        for flag in ("--append-system-prompt", "--continue", "--model"):
            if flag not in help_text:
                problems.append("claude CLI is missing %s" % flag)

    link = os.path.expanduser("~/.local/bin/crew")
    if not os.path.exists(link):
        problems.append("%s does not exist; crew is not on PATH for crew members" % link)

    ensure_crew_dir()
    if oct(os.stat(CREW_DIR).st_mode & 0o777) != oct(0o700):
        problems.append("%s is not mode 700" % CREW_DIR)
    if os.path.exists(MAILBOX) and oct(os.stat(MAILBOX).st_mode & 0o777) != oct(0o600):
        problems.append("%s is not mode 600" % MAILBOX)

    shadow = os.path.expanduser("~/.claude/skills/herdr.md")
    if os.path.exists(shadow):
        problems.append("%s shadows the stowed herdr skill" % shadow)

    summary, guard_issues = guard_status(SETTINGS_PATH)
    if summary:
        print("guard: %s" % summary)
    problems.extend(guard_issues)

    if not os.environ.get("HERDR_ENV"):
        print("note: not running inside a herdr pane; pane-scoped verbs will not work")

    if problems:
        print("\nFAIL")
        for p in problems:
            print("  - %s" % p)
        return 1
    print("\nOK")
    return 0


TOKEN_VERSION = "1"


def _probe(argv):
    """Run a read-only external command for doctor. A preflight that crashes
    cannot report, so every failure mode returns instead of raising. The
    message names the whole command: doctor runs two different herdr probes
    and argv[0] alone cannot tell them apart."""
    label = " ".join(argv)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        return False, "%s not runnable: %s" % (label, exc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:120]
        return False, "%s exited %d: %s" % (label, proc.returncode, detail)
    text = proc.stdout.strip()
    if not text:
        return False, "%s produced no output" % label
    return True, text


def _guard_token(command):
    """The crew-guard.py path a settings hook command runs, and whether it runs
    that file directly rather than through an interpreter. None when the command
    is not the guard at all."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for index, token in enumerate(tokens):
        if os.path.basename(token) == GUARD_HOOK:
            return os.path.expandvars(os.path.expanduser(token)), index == 0
    return None


def _guard_registrations(data):
    """(matcher, path, runs_directly) for every PreToolUse hook that runs the
    guard.

    Tolerant of any shape under `hooks`, because crew does not own this file and
    a preflight that raises on a surprise cannot report one."""
    hooks = data.get("hooks") if isinstance(data, dict) else None
    entries = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    found = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        listed = entry.get("hooks")
        for hook in listed if isinstance(listed, list) else []:
            command = hook.get("command") if isinstance(hook, dict) else None
            token = _guard_token(command) if isinstance(command, str) else None
            if token:
                found.append((entry.get("matcher"), token[0], token[1]))
    return found


def _guard_tools(path):
    """The tool names the matcher must deliver, read FROM the hook: the union of
    its two tables plus Bash, as (tools, error).

    Executed rather than parsed. A parse recreates this check's own bug one layer
    up: a table entry written in a form the parse does not recognise reads as a
    tool that needs no covering, and doctor then passes a matcher that is short.
    Executing cannot under-report, and every way it can fail says so out loud.
    The hook's own work stays behind __main__, so a name other than __main__ here
    runs none of it.

    Compiled and exec'd rather than imported because import caches bytecode
    beside the source, and doctor must not leave a __pycache__ in the dotfiles
    checkout that the registered hook path resolves into.

    Bash is required whatever the tables say, because the hook's FORBIDDEN
    command table exists for the shell and a matcher that stopped delivering Bash
    would silence all of it.
    """
    namespace = {"__name__": "crew_guard_tables", "__file__": path}
    try:
        with open(path) as handle:
            source = handle.read()
        # doctor reports on crew, not on the hook's lint. A compile warning from
        # a file doctor merely reads would print as if it were a doctor finding,
        # and SyntaxWarning is shown by default. A real SyntaxError still raises.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            exec(compile(source, path, "exec"), namespace)
    except (Exception, SystemExit) as exc:
        # The path comes from a settings file crew does not own, so this runs
        # code of unknown shape: any failure has to be reportable, not raised.
        return None, "%s: %s" % (type(exc).__name__, exc)
    tools = set(["Bash"])
    for name in GUARD_TABLES:
        table = namespace.get(name)
        if not isinstance(table, dict):
            return None, "%s does not declare %s as a dict" % (path, name)
        tools.update(str(key) for key in table)
    return sorted(tools), None


def _matcher_pattern(matcher):
    """A PreToolUse matcher as (pattern, error), where a pattern of None delivers
    every tool.

    A matcher is a pattern and not a list, so this compiles it rather than
    comparing text. It is read the strict way, whole name only: Claude Code's
    engine is not Python's `re`, so a pattern that matches part of a tool name is
    reported as not delivering it rather than assumed to be delivered. A missing,
    empty or `*` matcher is Claude Code's own spelling of every tool.
    """
    if matcher is None:
        return None, None
    if not isinstance(matcher, str):
        return None, "matcher %r is not a string" % (matcher,)
    if matcher.strip() in ("", "*"):
        return None, None
    try:
        return re.compile(matcher), None
    except re.error as exc:
        return None, "matcher %r is not a readable pattern: %s" % (matcher, exc)


def guard_status(settings_path):
    """Whether crew-guard.py is armed, as (summary, problems).

    Three failures with three different remedies: not registered at all, so
    nothing is enforced; registered at a path that cannot run, which enforces
    nothing either; and registered with a matcher that omits a tool the hook can
    act on, which is the dangerous one because it looks armed.

    What this cannot prove: it reads one settings file, so a registration in a
    project, local or enterprise file is invisible to it; and matching a pattern
    here is not proof that Claude Code delivers the tool, only that the pattern
    names it.
    """
    try:
        with open(settings_path) as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None, ["crew-guard is NOT registered: there is no %s, so nothing "
                      "enforces the crew boundary and a crew member can dispatch "
                      "paid sessions. Add a PreToolUse hook running %s with a "
                      "matcher covering every tool its FORBIDDEN_TOOLS and "
                      "COMMAND_FIELDS name, plus Bash, %s."
                      % (settings_path, GUARD_HOOK, GUARD_RELOAD)]
    except (OSError, ValueError) as exc:
        return None, ["crew-guard cannot be shown to be registered: %s could not "
                      "be read as JSON (%s). Claude Code loads no hooks from a "
                      "settings file it cannot parse, so read this as nothing "
                      "enforced. Repair the file, %s."
                      % (settings_path, exc, GUARD_RELOAD)]

    registrations = _guard_registrations(data)
    if not registrations:
        return None, ["crew-guard is NOT registered: %s has no PreToolUse hook "
                      "running %s, so nothing enforces the crew boundary and a "
                      "crew member can dispatch paid sessions. This check reads "
                      "that file alone, so a registration in a project or local "
                      "settings file is invisible to it. Add the entry with a "
                      "matcher covering every tool the hook's FORBIDDEN_TOOLS "
                      "and COMMAND_FIELDS name, plus Bash, %s."
                      % (settings_path, GUARD_HOOK, GUARD_RELOAD)]

    problems = []
    live = []
    for matcher, hook_path, runs_directly in registrations:
        if not os.path.exists(hook_path):
            problems.append("crew-guard's registration in %s is dangling: %s "
                            "does not exist, so the PreToolUse entry enforces "
                            "nothing. Point it at the stowed %s, or re-stow the "
                            "claude package, %s."
                            % (settings_path, hook_path, GUARD_HOOK,
                               GUARD_RELOAD))
        elif runs_directly and not os.access(hook_path, os.X_OK):
            # Only when settings runs the file itself. Registered behind an
            # interpreter, the executable bit is not what decides.
            problems.append("crew-guard at %s is not executable, so the "
                            "PreToolUse entry in %s enforces nothing. chmod +x "
                            "that file, %s."
                            % (hook_path, settings_path, GUARD_RELOAD))
        else:
            live.append((matcher, hook_path))
    if not live:
        return None, problems

    tools = set()
    for hook_path in dict.fromkeys(path for _, path in live):
        found, error = _guard_tools(hook_path)
        if error:
            problems.append("doctor cannot verify the PreToolUse matcher: "
                            "crew-guard's own tool tables could not be read "
                            "from %s (%s)." % (hook_path, error))
        else:
            tools.update(found)
    if not tools:
        return None, problems

    patterns = []
    unreadable = False
    for matcher, _ in live:
        pattern, error = _matcher_pattern(matcher)
        if error:
            unreadable = True
            problems.append("doctor cannot verify which tools reach crew-guard: "
                            "%s in %s. Use an alternation such as Bash|Monitor, "
                            "%s." % (error, settings_path, GUARD_RELOAD))
        else:
            patterns.append(pattern)
    # Naming missing tools off an unreadable matcher would assert what has just
    # been reported as unknown. It is already a FAIL, and the tools it does
    # deliver are reportable once the pattern can be read.
    missing = [] if unreadable else [
        tool for tool in sorted(tools)
        if not any(p is None or p.fullmatch(tool) for p in patterns)]
    if missing:
        problems.append("crew-guard is registered but INERT for %s: the "
                        "PreToolUse matcher (%s) in %s does not deliver those "
                        "tools, and the hook can act only on what the matcher "
                        "delivers, so the registration looks armed and is not. "
                        "Widen the matcher to cover them, %s."
                        % (", ".join(missing),
                           ", ".join(repr(m) for m, _ in live),
                           settings_path, GUARD_RELOAD))

    if problems:
        return None, problems
    return ("registered at %s, matcher delivers %s"
            % (live[0][1], ", ".join(sorted(tools)))), []


def crew_members(snap):
    agents_by_pane = dict((a["pane_id"], a) for a in snap["agents"])
    members = []
    for pane in snap["panes"]:
        tokens = pane.get("tokens") or {}
        if tokens.get("crew") != "true":
            continue
        agent = agents_by_pane.get(pane["pane_id"], {})
        ctype = tokens.get("type", "unknown")
        if tokens.get("v") != TOKEN_VERSION:
            ctype = "unknown-v%s" % tokens.get("v", "none")
        status = agent.get("agent_status", "unknown")
        members.append({
            # Absence from the agent list, never a status: an agent-less pane
            # reports agent_status `unknown`, and so does an agent herdr cannot
            # classify, so the status cannot tell the two apart. The bucket
            # keeps calling both `recover`, which is right for a foreman
            # reading load; this field is what retirement is allowed to act on.
            "agent": pane_has_agent(snap, pane["pane_id"]),
            "name": agent.get("name") or "(unnamed)",
            "key": tokens.get("key", "(no key)"),
            "repo": tokens.get("repo", "(no repo)"),
            "type": ctype,
            "branch": tokens.get("branch", ""),
            "root": tokens.get("root", ""),
            "worktree": worktree_for(tokens.get("root", ""),
                                     tokens.get("branch", "")),
            "pane": pane["pane_id"],
            "status": status,
            "bucket": bucket(status),
        })
    members.sort(key=lambda m: (m["repo"], m["key"]))
    return members


def untagged_agents(snap):
    tagged = set(
        p["pane_id"] for p in snap["panes"]
        if (p.get("tokens") or {}).get("crew") == "true"
    )
    out = []
    for agent in snap["agents"]:
        if agent["pane_id"] in tagged:
            continue
        out.append({
            "pane": agent["pane_id"],
            "title": agent.get("terminal_title_stripped", ""),
            "status": agent["agent_status"],
        })
    return out


# A tab crew created is labelled `<repo>/<sanitised key>`. The key half is
# whatever sanitize_name produces, which always starts with a letter.
CREW_TAB_LABEL_RE = re.compile(r"^[^/]+/[a-z][a-z0-9_-]*$")


def crew_tab_label(repo, key):
    """The label dispatch gives the tab it creates for a crew member."""
    return "%s/%s" % (repo, sanitize_name(key))


def is_crew_tab_label(label):
    return bool(CREW_TAB_LABEL_RE.match(label))


def tab_panes(snap, tab_id):
    return [p for p in snap["panes"] if p.get("tab_id") == tab_id]


def tab_holds_only(snap, tab_id, pane_ids):
    """Whether this tab holds exactly these panes and nothing crew cannot see.

    A tab whose pane_count exceeds the panes the snapshot lists is NOT held by
    them alone: crew cannot prove a pane it cannot see is agent-less, so the tab
    is left alone rather than closed on a guess. A tab missing from the tabs
    list entirely is treated the same way, so a herdr that stops reporting tabs
    makes crew close fewer things rather than more."""
    seen = sorted(p["pane_id"] for p in tab_panes(snap, tab_id))
    if seen != sorted(pane_ids):
        return False
    for tab in snap.get("tabs") or []:
        if tab.get("tab_id") == tab_id:
            return (tab.get("pane_count") or len(seen)) == len(seen)
    return False


def orphan_crew_tabs(snap):
    """Tabs crew created that hold no agent at all.

    `crew dispatch` creates one tab per crew member and nothing removed it, so
    they accumulate: four were found from earlier testing, each holding one dead
    pane, and each carrying NO tokens. Tokens DO survive an agent's exit,
    measured: an agent exited with its pane alive left all eight intact. So a
    tokenless orphan is not an exited session, it is a dispatch that failed
    before tag_pane ran, and the only record crew has of creating it is the tab
    LABEL. Matching on the label is what makes those retirable at all.

    Occupancy is read off the agent list, never off a status. `tabs` carries an
    agent_status too, and an agent-less tab reports `unknown` there exactly as
    an unclassifiable agent would."""
    out = []
    for tab in snap.get("tabs") or []:
        tab_id = tab.get("tab_id")
        if not tab_id or not is_crew_tab_label(tab.get("label") or ""):
            continue
        panes = [p["pane_id"] for p in tab_panes(snap, tab_id)]
        if any(pane_has_agent(snap, pane_id) for pane_id in panes):
            continue
        if not tab_holds_only(snap, tab_id, panes):
            continue
        out.append({"tab": tab_id, "label": tab.get("label") or "",
                    "panes": panes})
    out.sort(key=lambda t: t["tab"])
    return out


def render_ls(members, untagged, orphan_tabs=None):
    counts = {"working": 0, "awaiting": 0, "blocked": 0, "recover": 0}
    for m in members:
        counts[m["bucket"]] += 1
    lines = ["%d working / %d awaiting you / %d blocked" % (
        counts["working"], counts["awaiting"], counts["blocked"])]
    if counts["recover"]:
        lines[0] += " / %d need recovery" % counts["recover"]
    lines.append("")
    for m in members:
        lines.append("  %-10s %-22s %-18s %-12s %s" % (
            m["bucket"], m["repo"], m["key"], m["type"], m["pane"]))
    if untagged:
        lines.append("")
        lines.append("%d untagged (not crew):" % len(untagged))
        for u in untagged:
            lines.append("  %-8s %-10s %s" % (u["status"], u["pane"], u["title"]))
    # A cleanup verb nobody knows to run does not help, so the accumulation is
    # reported here, where the foreman already looks.
    spent = [m for m in members if not m["agent"]]
    if spent:
        lines.append("")
        lines.append("%d crew pane(s) no agent occupies, retirable:" % len(spent))
        for m in spent:
            lines.append("  %-22s %-18s %-10s propose: crew retire %s"
                         % (m["repo"], m["key"], m["pane"], m["key"]))
    if orphan_tabs:
        lines.append("")
        lines.append("%d tab(s) crew created holding no agent, retirable "
                     "(matched by label: a dispatch that failed before tagging "
                     "leaves a pane with no tokens):" % len(orphan_tabs))
        for t in orphan_tabs:
            lines.append("  %-10s %-24s %-14s propose: crew retire %s"
                         % (t["tab"], t["label"], ",".join(t["panes"]),
                            t["tab"]))
    return "\n".join(lines)


def cmd_ls(as_json):
    defs = schema_defs()
    assert_schema_declares(defs)
    snap = snapshot()
    assert_snapshot_shape(snap, defs)
    members = crew_members(snap)
    if as_json:
        # Still the members ARRAY. The spec's own drift check reads its length,
        # and orphan tabs are a prompt for the human, not a fleet record.
        print(json.dumps(members, indent=2, sort_keys=True))
    else:
        print(render_ls(members, untagged_agents(snap),
                        orphan_crew_tabs(snap)))
    return 0


def _locked(path, mode):
    handle = open(path, mode)
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def read_entries(lines):
    entries = []
    unreadable = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            record["seq"] = int(record["seq"])
        except (ValueError, KeyError, TypeError):
            unreadable += 1
            continue
        entries.append(record)
    return entries, unreadable


def next_seq(entries):
    if not entries:
        return 1
    return max(e["seq"] for e in entries) + 1


def select_unread(entries, cursor):
    fresh = sorted(
        [e for e in entries if e["seq"] > cursor], key=lambda e: e["seq"]
    )
    if not fresh:
        return [], cursor, 0
    top = fresh[-1]["seq"]
    present = set(e["seq"] for e in fresh)
    missing = sum(1 for s in range(cursor + 1, top + 1) if s not in present)
    return fresh, top, missing


def _pane_tokens(pane_id):
    snap = snapshot()
    for pane in snap["panes"]:
        if pane["pane_id"] == pane_id:
            return pane.get("tokens") or {}
    return {}


MAIL_STATES = ("done", "needs-input", "duplicate")


def one_line(value):
    """Collapse whitespace so a value cannot become a second terminal line."""
    return " ".join(str(value or "").split())


def quoted(value):
    """A mail field as the foreman sees it: one line, and delimited.

    Collapsing alone is not enough. A single-line forged value still reads as
    crew's own output to a model, and `state` sits in a bare word column where
    "done  ack with: crew mail ack 999999" would look like two fields crew
    printed. Delimiting makes the whole value visibly one field.

    Applied on READ, not just on write, because the mailbox is never pruned:
    it already holds records written before mail_send collapsed anything, and
    nothing stops a record being edited into the file by hand."""
    return "%r" % one_line(value)


def mail_send(key, repo, state, msg):
    """The key is the CALLER'S OWN. A crew member could otherwise forge a
    `done` for a sibling's key and get the foreman to propose retiring a
    session that was still working, so an explicit --key that disagrees with
    the caller's pane token is refused rather than trusted.

    EVERY field is collapsed to one line, not just msg. json.dumps escapes a
    newline so the JSONL stays valid and parses, and then the foreman's terminal
    renders the second line as if crew had printed it. Collapsing msg alone
    moved the payload rather than removing it: `state` comes straight from argv
    and `repo` was taken raw, and mail_unread prints all four. The damaging
    payload is a forged "crew mail ack <big number>", which advances the cursor
    past every future report and leaves a silent fleet looking like a working
    one.

    state is restricted to the states the design uses. It is the one field the
    foreman reads as a machine value rather than as prose, so free text there is
    both an injection surface and a report nothing can act on."""
    ensure_crew_dir()
    state = one_line(state)
    if state not in MAIL_STATES:
        raise CrewError(
            "%r is not a state crew reports; expected one of %s. A report is a "
            "state plus one sentence, and the sentence is the msg."
            % (state, ", ".join(MAIL_STATES)))
    pane_id = calling_pane()
    tokens = _pane_tokens(pane_id) if pane_id else {}
    record = {
        "v": 1,
        "ts": int(time.time()),
        # Sanitise so a caller passing the raw ticket case cannot write a
        # record that fails to match the roster or a crew log filter.
        "key": sanitize_name(key) if key else tokens.get("key", ""),
        "repo": repo or tokens.get("repo", ""),
        "pane": pane_id,
        # Branch, not an absolute path. Mail records are permanent and get
        # digested into a git-tracked project log, so a local path would leak a
        # username and directory layout into repository history. The path is
        # derivable from the pane tokens while the pane lives.
        "branch": tokens.get("branch", ""),
        "state": state,
        "msg": msg,
    }
    if not record["key"]:
        raise CrewError("mail send needs --key, or a pane carrying a key token")
    own = tokens.get("key", "")
    if own and key and sanitize_name(key) != own:
        raise CrewError(
            "refusing to send as %r: this pane is crew member %r. A report must "
            "name the sender's own key." % (sanitize_name(key), own))
    for field, value in list(record.items()):
        if isinstance(value, str):
            record[field] = one_line(value)
    if DRY_RUN:
        print("would append to %s: %s" % (MAILBOX, json.dumps(record, sort_keys=True)))
        return 0

    with _locked(MAILBOX, "a+") as handle:
        handle.seek(0)
        entries, _ = read_entries(handle.readlines())
        record["seq"] = next_seq(entries)
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(MAILBOX, 0o600)
    return 0


def _read_cursor():
    """Locked. An unlocked read racing an ack could see a truncated file, read
    0, and re-deliver the entire mailbox."""
    if not os.path.exists(CURSOR):
        return 0
    try:
        with _locked(CURSOR, "r") as handle:
            return int(handle.read().strip() or 0)
    except (IOError, OSError, ValueError):
        return 0


def mail_unread():
    ensure_crew_dir()
    if not os.path.exists(MAILBOX):
        print("no mail")
        print("ack with: crew mail ack 0")
        return 0
    with _locked(MAILBOX, "r") as handle:
        entries, unreadable = read_entries(handle.readlines())
    fresh, new_cursor, missing = select_unread(entries, _read_cursor())
    for record in fresh:
        print("%s  %-14s %-24s %-14s %s" % (
            record["seq"], quoted(record.get("state", "")),
            quoted(record.get("repo", "")),
            one_line(record.get("key", "")), quoted(record.get("msg", ""))))
    if not fresh:
        print("no new mail")
    if unreadable or missing:
        print("%d unreadable, %d missing" % (unreadable, missing))
    print("ack with: crew mail ack %d" % new_cursor)
    return 0


def calling_pane():
    """The calling pane, from the environment herdr injects into it.

    This IS spoofable: a crew member can set HERDR_PANE_ID and claim to be
    the foreman. It is kept anyway because the alternative is worse.
    Stripping the variable and asking herdr does NOT yield the caller: herdr
    falls back to the UI-FOCUSED pane. Measured: with the variable stripped
    herdr returned wX:p1, the focused pane in another workspace, while the
    caller was wV:p1.

    A focus-based check would authorise a crew member whenever the human
    happened to be looking at the foreman, and refuse the real foreman
    whenever they were not. Wrong beats spoofable, so the spoofable check
    stands and the real control is the PreToolUse hook outside this script."""
    return os.environ.get("HERDR_PANE_ID", "")


def is_foreman_pane():
    me = calling_pane()
    if not me:
        return False
    for agent in snapshot()["agents"]:
        if agent.get("name") == "foreman":
            return agent.get("pane_id") == me
    return False


def claim_foreman():
    """Make this pane the foreman, or explain why it cannot be.

    Needed because a herdr agent name binds to the AGENT occupying a pane, not
    to the pane, and is cleared when that agent exits. Verified: renaming a pane
    that holds no agent fails with agent_not_found. So the name cannot be set
    before the session starts, and it evaporates on /clear or a restart.

    Without this, the foreman skill's claim that "you are the agent named
    foreman" is simply false, and `crew mail ack` refuses with exit 4 while the
    same mail is reported over and over."""
    me = calling_pane()
    if not me:
        raise CrewError("not running inside a herdr pane, so there is no pane "
                        "to name. Start the foreman inside herdr.")
    # Same two assertions cmd_ls and cmd_dispatch both use. `name` is
    # optional precisely because a rename empties every lookup, so under
    # drift this would attempt a rename with herdr's uniqueness check as
    # the only guard.
    defs = schema_defs()
    assert_schema_declares(defs)
    snap = snapshot()
    assert_snapshot_shape(snap, defs)
    for agent in snap["agents"]:
        if agent.get("name") != "foreman":
            continue
        if agent.get("pane_id") == me:
            print("already the foreman (%s)" % me)
            return 0
        raise CrewError(
            "pane %s is already the foreman. herdr allows one live agent per "
            "name, and this design allows one foreman. Use that pane, or rename "
            "it first." % agent.get("pane_id"))
    herdr("agent", "rename", me, "foreman")
    print("claimed foreman on %s. The name is cleared if this agent exits, so "
          "re-run this after a /clear or restart." % me)
    return 0


def mail_ack(seq):
    if not is_foreman_pane():
        print(
            "refusing: crew mail ack is foreman-only, and this pane does not "
            "host the agent named foreman", file=sys.stderr)
        return 4
    ensure_crew_dir()
    # "a+" then truncate under the lock: "w" truncates at open, before flock
    # is taken, so a concurrent reader could see an empty file.
    with _locked(CURSOR, "a+") as handle:
        handle.seek(0)
        handle.truncate()
        handle.write("%d\n" % seq)
    return 0


def _run(args):
    if not args:
        print("usage: crew <doctor|claim-foreman|ls|dispatch|peek|nudge|retire|"
              "mail> [args]", file=sys.stderr)
        return 2
    verb = args[0]
    if verb == "doctor":
        return doctor()
    if verb == "claim-foreman":
        return claim_foreman()
    if verb == "ls":
        return cmd_ls("--json" in args)
    if verb == "dispatch":
        if len(args) < 2:
            print("usage: crew dispatch <key> --type T [--repo R] [--model M]",
                  file=sys.stderr)
            return 2
        key = require_positional(args[1], "dispatch key")
        opts = {"--type": "implementer", "--repo": None, "--model": None}
        rest = args[2:]
        while rest:
            flag, value, rest = take_flag(rest, tuple(opts))
            if flag is None:
                raise CrewError("unexpected argument: %s" % rest[0])
            opts[flag] = value
        # No local except here: main's central handler maps CrewError and
        # HerdrError to exit 3 for every verb. A per-verb catch previously
        # returned 1 for this one, so the same failure exited differently
        # depending on which verb produced it.
        return cmd_dispatch(key, opts["--type"], opts["--repo"],
                            opts["--model"])
    if verb == "peek":
        if len(args) < 2:
            print("usage: crew peek <name> [--lines N]", file=sys.stderr)
            return 2
        name = require_positional(args[1], "peek name")
        opts = {"--lines": None}
        rest = args[2:]
        while rest:
            flag, value, rest = take_flag(rest, tuple(opts))
            if flag is None:
                raise CrewError("unexpected argument: %s" % rest[0])
            opts[flag] = value
        return cmd_peek(name, opts["--lines"])
    if verb == "nudge":
        if len(args) < 3:
            print("usage: crew nudge <name> \"<text>\"", file=sys.stderr)
            return 2
        name = require_positional(args[1], "nudge name")
        return cmd_nudge(name, " ".join(args[2:]))
    if verb == "retire":
        if len(args) < 2:
            print("usage: crew retire <name|key|pane id|tab id>",
                  file=sys.stderr)
            return 2
        return cmd_retire(require_positional(args[1], "retire name"))
    if verb == "mail":
        sub = args[1] if len(args) > 1 else ""
        if sub == "send":
            opts = {"--key": None, "--repo": None}
            rest = args[2:]
            while rest and rest[0] in opts:
                flag, value, rest = take_flag(rest, tuple(opts))
                opts[flag] = value
            if len(rest) < 2:
                print("usage: crew mail send [--key K] [--repo R] <state> <msg>",
                      file=sys.stderr)
                return 2
            return mail_send(opts["--key"], opts["--repo"], rest[0],
                              " ".join(rest[1:]))
        if sub == "unread":
            return mail_unread()
        if sub == "ack":
            if len(args) < 3:
                print("usage: crew mail ack <seq>", file=sys.stderr)
                return 2
            return mail_ack(int(args[2]))
        print("usage: crew mail <send|unread|ack>", file=sys.stderr)
        return 2
    print("unknown verb: %s" % verb, file=sys.stderr)
    return 2


CONTRACT_PATH = os.path.expanduser("~/.claude/skills/crew-member/SKILL.md")
DRY_PANE = "wDRY:pDRY"
MODEL_BY_TYPE = {"implementer": "opus", "planner": "opus", "reviewer": "opus"}

SETUP_PROMPT = (
    "You are a short-lived setup agent. Do exactly this and nothing else.\n"
    "1. Run /start-ticket {key} and answer its prompts with the human.\n"
    "2. When the worktree exists, write this JSON to {artifact} and stop:\n"
    '   {{"worktree": "<absolute worktree path>", '
    '"branch": "<branch name>", "repo": "{repo}"}}\n'
    "   The worktree must be {root}/.claude/worktrees/<branch name>, which is "
    "where /start-ticket puts it. Dispatch derives that path back from the "
    "branch and refuses an artifact where the two disagree.\n"
    "Do not implement anything. Do not open a PR."
)


def require_positional(value, what):
    """A --flag where a positional belongs is a typo, not a value. Accepting
    one cost a live Opus session when `crew dispatch --help` dispatched a crew
    member named "help"."""
    if value is None or value.startswith("-"):
        raise CrewError("%s must be a value, not a flag: got %r" % (what, value))
    return value


def take_flag(rest, names):
    """Pull `--flag value` off the front of rest. Reports a missing value
    rather than raising IndexError at a crew member."""
    if not rest or rest[0] not in names:
        return None, None, rest
    flag = rest[0]
    if len(rest) < 2:
        raise CrewError("%s needs a value" % flag)
    return flag, rest[1], rest[2:]


def contract_pointer(name, ctype, key, repo, worktree):
    return (
        "You are crew member `%s`, type %s, on %s in repo %s, worktree %s. "
        "Read %s now and follow it for the rest of this session. "
        "Report state changes with `crew mail send --key %s`."
        % (name, ctype, key, repo, worktree, CONTRACT_PATH, sanitize_name(key))
    )


TOKEN_VALUE_MAX = 80


def token_too_long(name, value):
    """Why this value cannot be written as a token, or None.

    herdr silently truncates a token VALUE at 80 characters, and the tokens are
    the authoritative record, so a truncated value is a record that lies.
    tag_pane refuses to write one, but tag_pane runs after the pane exists: a
    real dispatch from a 109 character repo root returned exit 3 with exactly
    the right message, having already run `git worktree add` and `tab create`,
    and left a pane, a tab and a worktree behind. That pane carried NO tokens,
    because tag_pane is what raised, so by this design's own rule that an
    untagged pane is not crew, nothing could ever see or retire it.

    So every value that will become a token is checked through here BEFORE
    anything is created, and tag_pane's check is the same check, kept as the
    backstop for a caller that skipped the early one."""
    if len(value) > TOKEN_VALUE_MAX:
        return ("%s is %d chars, over herdr's %d limit, and would be silently "
                "truncated: %r" % (name, len(value), TOKEN_VALUE_MAX, value))
    return None


def worktree_for(root, branch):
    """Derive the worktree path from two short tokens rather than storing the
    path itself. herdr truncates a token VALUE at 80 characters and a real
    worktree path exceeds that, so storing the path produced an authoritative
    record pointing at a directory that does not exist.

    The repo ROOT is stored rather than resolved from the repo name, because a
    repo is not necessarily under ~/Dev: this project is itself built inside a
    worktree of the dotfiles repo."""
    if not root or not branch:
        return ""
    return os.path.join(root, ".claude", "worktrees", branch)


def branch_for(root, worktree):
    """The inverse of worktree_for: the branch a worktree path records.

    /start-ticket names branches <TICKET>/<slug>, so the branch is the path
    RELATIVE to the worktrees directory, not its basename. Basename kept only
    the last component, and the tokens are the authoritative record every later
    reader recomputes the path from, so `crew ls`, `crew peek` and the exit 5
    resume line would all print a directory that does not exist."""
    return os.path.relpath(worktree, os.path.join(root, ".claude", "worktrees"))


def branch_path_problem(branch):
    """Why this branch cannot name a worktree directory, or None.

    An interior separator is the ORDINARY case: /start-ticket mandates
    <TICKET>/<slug>, and refusing it meant every JIRA dispatch was refused
    after a paid setup session had already been spent. What stays refused is
    what makes the derived path unsafe or ambiguous: empty, absolute, a
    leading or trailing separator, an empty interior component, and any `.` or
    `..` component.

    COMPONENTS are inspected rather than the string scanned for a substring,
    so `a/../../b` is caught while `feat..ure`, a legal branch name, is not."""
    if not branch:
        return "is empty"
    if os.path.isabs(branch):
        return "is an absolute path"
    parts = branch.split(os.sep)
    if "" in parts:
        return "has an empty path component"
    if os.curdir in parts or os.pardir in parts:
        return "has a %r or %r component" % (os.curdir, os.pardir)
    return None


def tag_pane(pane_id, key, repo, ctype, branch, root):
    args = [
        "pane", "report-metadata", pane_id, "--source", "crew",
        "--token", "crew=true",
        "--token", "v=%s" % TOKEN_VERSION,
        "--token", "key=%s" % sanitize_name(key),
        "--token", "repo=%s" % repo,
        "--token", "type=%s" % ctype,
        "--token", "dispatched=%d" % int(time.time()),
    ]
    if branch:
        args += ["--token", "branch=%s" % branch]
    if root:
        args += ["--token", "root=%s" % root]

    # The backstop. cmd_dispatch checks these values before it creates
    # anything, because by the time this raises there is a pane to leak.
    for i in range(0, len(args)):
        if args[i] == "--token":
            name, _, value = args[i + 1].partition("=")
            problem = token_too_long(name, value)
            if problem:
                raise CrewError("token %s" % problem)
    herdr(*args)


def find_member(snap, root, key):
    """Match on the authoritative root, not the repo label: two repos can share
    a basename, and matching on the label alone rejected a legitimate dispatch
    and printed a resume command into the wrong repository.

    A `setup` pane is never a match. An orphaned setup pane would otherwise
    make every retry of that key report a duplicate forever, and it carries no
    branch token so the resume command it printed was empty."""
    wanted = sanitize_name(key)
    for member in crew_members(snap):
        if member["type"] == "setup":
            continue
        if member["root"] == root and member["key"] == wanted:
            return member
    return None


def find_setup_panes(snap, root, key):
    """EVERY setup pane carrying this key, not just the first.

    One key really can have several. Dispatch refuses to close the pane it is
    running in, and the remedy printed on every rejection tells the human to
    re-run the command, which they naturally do from the setup pane they are
    already sitting in: that leaves the old pane tagged and opens a second one.

    Acting on the first match alone then reached for the wrong pane. It could
    close a stale one and open ANOTHER setup pane, paying for a second Opus
    session, while a live setup agent for the same key was still working. So the
    whole set is inspected together: one occupied pane means close nothing, and
    when none is occupied every one of them goes, or the next call trips over
    whatever was left behind."""
    wanted = sanitize_name(key)
    return [member for member in crew_members(snap)
            if member["type"] == "setup" and member["root"] == root
            and member["key"] == wanted]


def find_setup_pane(snap, root, key):
    """Setup panes are excluded from find_member so an orphan cannot brick a
    key. They must still be findable, or a retry spawns a second paid one."""
    panes = find_setup_panes(snap, root, key)
    return panes[0] if panes else None


def pane_has_agent(snap, pane_id):
    """Whether an agent occupies this pane, read off the snapshot's agent list.

    Absence is the only sound signal that closing a setup pane destroys
    nothing. It is the same fact the `recover` bucket rests on, where an
    agent-less crew pane shows up because crew_members finds no agent for it,
    but it is read straight off the agent list here rather than through a
    status, because EVERY status means an agent is present. Per herdr's own
    definitions: `idle` is ready for input, `done` is that same idle state after
    unseen background work finished, and `unknown` is an agent present that
    herdr cannot classify confidently.

    Gating a close on ("done", "idle", "unknown") therefore closed live
    sessions, and the modal JIRA path lands exactly there: /start-ticket asks
    the human questions in prose, which settles as idle or done and never as
    blocked, so a human part way through answering lost the pane and the re-run
    spent another paid Opus session.

    Narrowing that list cannot fix it either. SETUP_PROMPT tells the setup agent
    to write its JSON and stop, and a stopped Claude Code session is still
    resident, so a FINISHED setup agent reads as idle or done as well. herdr
    cannot tell "finished" from "waiting for your answer" by status at all."""
    return any(agent["pane_id"] == pane_id for agent in snap["agents"])


DEV_ROOT = os.path.expanduser("~/Dev")


def resolve_repo(repo_arg):
    """--repo names a LOCATION, not just a label. The repo token is
    authoritative, so it must never be able to disagree with the worktree it
    describes. The name always comes from the resolved directory."""
    if not repo_arg:
        root = canonical_repo_root(os.getcwd())
        return root, os.path.basename(root)
    candidate = repo_arg
    if not os.path.isabs(candidate):
        candidate = os.path.join(DEV_ROOT, repo_arg)
    if not os.path.isdir(candidate):
        raise CrewError("--repo %s does not resolve to a directory (tried %s)"
                        % (repo_arg, candidate))
    root = canonical_repo_root(candidate)
    return root, os.path.basename(root)


def canonical_repo_root(path):
    """The repo root that identifies the REPOSITORY, not the checkout.

    `--show-toplevel` returns the worktree path when the caller is inside a
    linked worktree, so a foreman running inside one labelled the authoritative
    repo token after the worktree rather than the repo. `--git-common-dir`
    points at the shared .git directory for every checkout of the same repo,
    so its parent is stable."""
    proc = subprocess.run(
        ["git", "-C", path, "rev-parse", "--path-format=absolute",
         "--git-common-dir"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise CrewError("%s is not inside a git repository" % path)
    common = proc.stdout.strip()
    return os.path.dirname(common) if os.path.basename(common) == ".git" else common


def repo_root_for(path):
    proc = subprocess.run(
        ["git", "-C", path, "rev-parse", "--show-toplevel"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise CrewError("%s is not inside a git repository" % path)
    return proc.stdout.strip()


JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")


def is_ticket(key):
    """A JIRA key takes the interactive /start-ticket path. Anything else is a
    slug: there is no ticket to fetch, so no setup pane and no agent."""
    return bool(JIRA_KEY_RE.match(key))


def wrong_case_ticket(key):
    """The uppercase spelling of a JIRA-shaped key given in another case, or
    None.

    JIRA_KEY_RE is uppercase only and is_ticket is exact case, so
    `fandevx-3511` is not a ticket and takes the SLUG path: plain_worktree
    branches off the main checkout's HEAD and a paid session starts with no
    /start-ticket, no ticket payload and no plan, at exit 0, and the crew member
    is then told to read a plan that does not exist.

    It is reachable because the foreman never sees the raw key again.
    crew_members stores sanitize_name'd keys, render_ls prints that lowercase
    form and the exit 5 line prints the stored one, so a foreman re-dispatching
    a key it read out of `crew ls` uses the spelling that breaks. While the
    first pane lives find_member catches it at exit 5; once that pane is
    retired, which is the normal end state, nothing does."""
    if is_ticket(key):
        return None
    upper = key.upper()
    return upper if JIRA_KEY_RE.match(upper) else None


def plain_worktree(key, repo_root):
    """Worktree for a ticketless slug, at the same convention path."""
    name = sanitize_name(key)
    path = os.path.join(repo_root, ".claude", "worktrees", name)
    if DRY_RUN:
        print("git -C %s worktree add %s -b %s" % (repo_root, path, name))
        return path
    if os.path.isdir(path):
        return path
    exists = subprocess.run(
        ["git", "-C", repo_root, "rev-parse", "--verify", "--quiet",
         "refs/heads/%s" % name], capture_output=True, text=True).returncode == 0
    add = ["git", "-C", repo_root, "worktree", "add", path]
    add += [name] if exists else ["-b", name]
    proc = subprocess.run(add, capture_output=True, text=True)
    if proc.returncode != 0:
        raise CrewError(
            "git worktree add failed: %s" % proc.stderr.strip()[:200])
    return path


def _start_agent(args, tries=8, delay=3):
    """A pane created moments earlier can reject agent start with
    agent_pane_busy: its shell has not yet settled into an interactive
    prompt. Observed live: tab create immediately followed by agent start
    fails every time; retrying a few seconds later succeeds."""
    for attempt in range(tries):
        try:
            return herdr(*args)
        except HerdrError as exc:
            if "agent_pane_busy" not in str(exc) or attempt == tries - 1:
                raise
            time.sleep(delay)


# Measured 4.04s on a real dispatch, so this is 10x headroom rather than a
# guess. It is bounded because the whole dispatch has to finish inside Claude
# Code's own Bash timeout (120s default): agent start retries up to 24s and the
# delivery confirmation adds 15s, so 45s here leaves the call comfortably short.
READY_TIMEOUT_MS = "45000"


def _wait_ready(name, tries=5, delay=2):
    """Wait for a freshly started agent to be ready for input. True if it is.

    `agent_pane_busy` is a check on the SHELL, not on the agent. Measured on a
    real dispatch: `agent start` returned at 0.54s, the agent first appeared at
    3.51s with status `unknown`, still booting, and reached `idle` at 4.04s. The
    `agent prompt` fired in between went into a REPL that was not accepting
    input yet and was SILENTLY DROPPED. The session sat at an empty prompt box,
    the working-or-blocked confirmation could not succeed because an agent that
    received no prompt settles at idle, and dispatch reported exit 6 having paid
    for a session that got no work. The same text delivered fine by `crew nudge`
    afterwards, so the remedy worked and the delivery did not.

    Readiness comes from herdr's own signal: `idle` is documented as ready for
    input, and `unknown` is deliberately not accepted because that is the state
    the booting agent reported. agent_not_found is retried, because the agent is
    not registered the moment `agent start` returns."""
    for attempt in range(tries):
        try:
            herdr("agent", "wait", name, "--until", "idle",
                  "--timeout", READY_TIMEOUT_MS)
            return True
        except HerdrError as exc:
            if "agent_not_found" not in str(exc) or attempt == tries - 1:
                return False
            time.sleep(delay)
    return False


def dispatch_artifact_path(key):
    return os.path.join(CREW_DIR, "dispatch-%s.json" % sanitize_name(key))


def clear_dispatch_artifact(key):
    """Delete the handoff artifact.

    Nothing deleted it once dispatch consumed it, so after the crew member's
    pane was gone the next dispatch of that key found the stale artifact,
    skipped /start-ticket entirely, and started a PAID session on the old
    worktree at exit 0. Every refusal deletes it too, or the key bricks at
    exit 3 forever, because the only other caller is start_setup and that is
    unreachable while an artifact exists.

    A dry run leaves it alone, and says so, because the refusals below report
    the deletion in the past tense. It is resumable state a real dispatch
    already paid a setup session for, and re-creating it costs another one."""
    artifact = dispatch_artifact_path(key)
    if DRY_RUN:
        print("would delete %s" % artifact)
        return
    if os.path.exists(artifact):
        os.unlink(artifact)


def is_inside(child, parent):
    """Boundary-safe containment. A bare startswith matches /a/repo-old
    against /a/repo, which is exactly how a neighbouring checkout's artifact
    would pass as this repo's. Both sides are resolved so a symlinked path is
    compared as the directory it actually is. Equal is not inside: a crew
    worktree is never the repo root itself."""
    child = os.path.realpath(child)
    parent = os.path.realpath(parent)
    return child.startswith(parent.rstrip(os.sep) + os.sep)


def same_path(a, b):
    """One notion of path equality for this file, resolved the way is_inside
    resolves: two spellings that reach one directory are one path."""
    return os.path.realpath(a) == os.path.realpath(b)


def start_setup(key, repo, repo_root):
    """Open the ephemeral setup pane and start /start-ticket in it, then
    return immediately. Dispatch must never block on the human answering it:
    this pane and the artifact it eventually writes are the resumable state,
    and a later `crew dispatch` call on the same key picks up from either."""
    ensure_crew_dir()
    artifact = dispatch_artifact_path(key)
    clear_dispatch_artifact(key)

    split = herdr("pane", "split", "--current", "--direction", "down",
                  "--cwd", repo_root, "--no-focus")
    setup_pane = DRY_PANE if split is None else split["result"]["pane"]["pane_id"]
    tag_pane(setup_pane, key, repo, "setup", "", repo_root)

    setup_name = ("setup-" + sanitize_name(key))[:32]
    _start_agent(["agent", "start", setup_name, "--kind", "claude",
                 "--pane", setup_pane, "--", "--model", "opus"])
    herdr("agent", "prompt", setup_name,
          SETUP_PROMPT.format(key=key, artifact=artifact, repo=repo,
                              root=repo_root))
    return setup_pane


def close_setup_pane(pane_id):
    """Close crew's own spent setup pane. True if it is gone, False with the
    reason printed if it is not.

    Both refusals leave dispatch making progress, because this close exists to
    unwedge a key and must never become the thing that wedges it.

    The CALLING pane is never closed. Every rejection tells the human to re-run
    this command, nothing hooks a human shell inside the setup pane, and with
    HERDR_PANE_ID set to the setup pane dispatch emitted a close for the pane it
    was running in. calling_pane() is spoofable, which is harmless here: this
    check only ever prevents an action, so a spoofed value cannot cause a close
    that would not otherwise happen.

    A close that raises warns and returns. Aborting produced exit 3 twice with
    `pane split` never running, which is the exact wedge this close was added to
    remove. A stale pane left behind is untidy; refusing to make progress is a
    bug."""
    me = calling_pane()
    if me and pane_id == me:
        print("setup pane %s is the pane this command is running in, so it has "
              "NOT been closed: that would kill this session. Close it yourself "
              "once you no longer need it." % pane_id)
        return False
    try:
        herdr("pane", "close", pane_id)
    except HerdrError as exc:
        print("could not close spent setup pane %s (%s), so it is still there. "
              "Carrying on regardless; close that pane yourself."
              % (pane_id, exc), file=sys.stderr)
        return False
    # Says only what BOTH callers can vouch for. One has an artifact proving
    # setup finished, the other has an empty pane; neither has both, and a
    # message this wave exists to make honest must not claim the other one.
    print("closed spent setup pane %s." % pane_id)
    return True


ARTIFACT_DISCARDED = (
    "It has been discarded. Re-running this command starts setup again: a setup "
    "pane no agent occupies any more is closed first, while one an agent is "
    "still in is named and left alone, because closing it would destroy that "
    "session."
)


def read_dispatch_artifact(key, repo, repo_root):
    """None if setup has not written it yet, which is the common case on a
    fresh call.

    Otherwise every field is checked against the dispatch about to consume it.
    The file is keyed on the ticket key alone, so the same key in two repos,
    which is ordinary, aims both dispatches at one artifact; and a model wrote
    it. Unchecked, an artifact whose repo disagreed and whose worktree lay
    outside the repo completed anyway, tagging the foreman's own root with the
    other checkout's branch, so the worktree derived from those tokens did not
    exist and both `crew ls` and the resume line printed that path.

    Returns the path DERIVED from root and branch, not the artifact's spelling
    of it, once the two are proven to be one directory. The tokens are the
    authoritative record and every later reader recomputes the path from them,
    so dispatch must work in the path that recomputation yields.

    Every refusal deletes the artifact. Unusable is unusable, whether that is
    unparseable JSON, another repo, or a worktree that is not there, and
    keeping the file would brick the key at exit 3 on every later call."""
    artifact = dispatch_artifact_path(key)
    if not os.path.exists(artifact):
        return None
    try:
        with open(artifact) as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except ValueError as exc:
        clear_dispatch_artifact(key)
        raise CrewError(
            "setup wrote unreadable JSON to %s (%s). %s"
            % (artifact, exc, ARTIFACT_DISCARDED))

    claimed = str(data.get("repo") or "").strip()
    if claimed != repo:
        clear_dispatch_artifact(key)
        raise CrewError(
            "%s is for repo %r, but this dispatch is for %r. %s"
            % (artifact, claimed, repo, ARTIFACT_DISCARDED))

    worktree = str(data.get("worktree") or "").strip()
    if not worktree or not is_inside(worktree, repo_root):
        clear_dispatch_artifact(key)
        raise CrewError(
            "%s names worktree %r, which is not inside repo root %r. %s"
            % (artifact, worktree, repo_root, ARTIFACT_DISCARDED))
    if not os.path.isdir(worktree):
        clear_dispatch_artifact(key)
        raise CrewError(
            "setup wrote %s but worktree %r does not exist. %s"
            % (artifact, worktree, ARTIFACT_DISCARDED))

    # The branch is validated HERE, not left to reach tag_pane. Over the token
    # limit it would raise only after a pane already existed, leaking an
    # untagged pane on every retry; and a `..` component derives a directory
    # that is inside the repo but is not a worktree.
    branch = str(data.get("branch") or "").strip()
    problem = branch_path_problem(branch)
    if problem:
        clear_dispatch_artifact(key)
        raise CrewError(
            "%s names branch %r, which %s, so it cannot name a worktree "
            "directory. %s" % (artifact, branch, problem, ARTIFACT_DISCARDED))
    problem = token_too_long("branch", branch)
    if problem:
        clear_dispatch_artifact(key)
        raise CrewError("%s names a branch that cannot be recorded: %s. %s"
                        % (artifact, problem, ARTIFACT_DISCARDED))

    # Containment is not enough: <root>/tmp/wt is inside the repo, and the
    # tokens written from it derive <root>/.claude/worktrees/wt, a path that
    # does not exist. `crew ls`, `crew peek` and the exit 5 resume line would
    # then all confidently print where the work is not.
    derived = worktree_for(repo_root, branch)
    if not same_path(worktree, derived):
        clear_dispatch_artifact(key)
        raise CrewError(
            "%s names worktree %r, but branch %r derives %r, and the tokens "
            "store the branch rather than the path. %s"
            % (artifact, worktree, branch, derived, ARTIFACT_DISCARDED))
    return derived


def _complete_dispatch(key, ctype, repo, repo_root, workspace, model, worktree, snap):
    """Tag, start and confirm the crew member's own pane. Shared by the
    artifact-ready path for a ticket and the inline path for a slug: once a
    worktree exists there is no interactive step left either way."""
    tab = herdr("tab", "create", "--workspace", workspace,
                "--label", crew_tab_label(repo, key),
                "--cwd", worktree, "--no-focus")
    pane = DRY_PANE if tab is None else tab["result"]["root_pane"]["pane_id"]

    # Tag before agent.start. Tokens are authoritative, so an untagged
    # pane is invisible to ls; a tag that failed afterwards would leave a
    # live session unowned and burning shared quota.
    branch = branch_for(repo_root, worktree)
    tag_pane(pane, key, repo, ctype, branch, repo_root)

    live = set(a.get("name") for a in snap["agents"] if a.get("name"))
    name = pick_name(key, live)

    start = ["agent", "start", name, "--kind", "claude", "--pane", pane,
             "--", "--model", model,
             "--append-system-prompt",
             contract_pointer(name, ctype, key, repo, worktree)]
    # A planner is an ordinary session told to plan. It must NOT run in
    # plan mode: that blocks bash, so it could never send its own report.
    _start_agent(start)

    # Never prompt a session that is still booting: the text is dropped and the
    # dispatch pays for a session that receives no work. A timeout here is not
    # fatal, because prompting anyway is no worse than what this replaced and
    # the confirmation below still catches a non-delivery as exit 6.
    if not _wait_ready(name):
        print("%s did not report ready for input within %ss, so its assignment "
              "is being sent to a session that may still be starting. If the "
              "confirmation below fails, resend with: crew nudge %s \"<text>\""
              % (name, int(READY_TIMEOUT_MS) // 1000, name), file=sys.stderr)

    duty = {
        "implementer": "Read the plan in your worktree, then implement it.",
        "planner": ("Do not implement anything. Produce a plan and stop, "
                    "then report needs-input with what you decided."),
        "reviewer": ("Do not change code. Review, write your findings to a "
                     "file in your worktree, and report done naming that "
                     "file."),
    }[ctype]
    assignment = (
        "You are dispatched on %s in %s as a %s. Your worktree is %s. %s "
        "Report with crew mail send --key %s when you settle." % (
            key, repo, ctype, worktree, duty, sanitize_name(key)))
    herdr("agent", "prompt", name, assignment)

    # `agent prompt` without --wait can silently not land, so the old
    # agent_prompt_stalled handler was dead code. Confirm a lifecycle
    # change instead: either state proves the prompt arrived. Any error
    # here means delivery is unconfirmed, and herdr's timeout code is not
    # enumerated in the schema, so treat every failure the same way.
    #
    # --until must be repeated, not comma-joined: a comma-joined value
    # was rejected live with "invalid agent status: working,blocked".
    try:
        herdr("agent", "wait", name, "--until", "working",
              "--until", "blocked", "--timeout", "15000")
    except HerdrError:
        print("DISPATCH INCOMPLETE: %s did not react to its assignment "
              "within 15s, so delivery is unconfirmed. The pane stays "
              "tagged, so crew ls shows it. Resend with: crew nudge %s "
              "\"<text>\"" % (name, name), file=sys.stderr)
        return 6

    # Names the branch and the worktree, not just the key: a dispatch into the
    # wrong tree looked identical to a correct one, and the tree is the whole
    # question when --repo is wrong or an artifact came from another checkout.
    print("dispatched %s as %s in pane %s on branch %s in %s"
          % (key, name, pane, branch, worktree))
    return 0


def cmd_dispatch(key, ctype, repo, model):
    if sanitize_name(key) in MODEL_BY_TYPE or sanitize_name(key) == "setup":
        raise CrewError(
            "%r is a crew type, not a key. Did you mean --type %s?"
            % (key, sanitize_name(key)))
    upper = wrong_case_ticket(key)
    if upper:
        raise CrewError(
            "%r is JIRA shaped but not uppercase, so it is refused rather than "
            "dispatched as a slug: the slug path branches off HEAD with no "
            "/start-ticket, no ticket and no plan, and a paid session at exit "
            "0. Dispatch %s if that is the ticket. If it is genuinely a "
            "free-form slug, name it so it is not JIRA shaped, for instance "
            "%s, because <letters>-<digits> alone cannot be told apart from a "
            "key." % (key, upper, key + "-slug"))
    if ctype not in MODEL_BY_TYPE:
        raise CrewError("unknown type %r; expected one of %s"
                        % (ctype, ", ".join(sorted(MODEL_BY_TYPE))))
    workspace = os.environ.get("HERDR_WORKSPACE_ID")
    if not workspace:
        raise CrewError("dispatch must run inside a herdr pane")

    repo_root, repo = resolve_repo(repo)
    model = model or MODEL_BY_TYPE[ctype]

    # Before a worktree, a tab or a pane exists. Measured: a dispatch from a 109
    # character repo root refused correctly but only inside tag_pane, by which
    # time `git worktree add` and `tab create` had run, and it left an UNTAGGED
    # pane behind, which by this design's own rule nothing can see or retire.
    for name, value in (("root", repo_root), ("repo", repo)):
        problem = token_too_long(name, value)
        if problem:
            raise CrewError(
                "%s. Nothing has been created. Dispatch from a shorter path, or "
                "pass --repo pointing at one: crew records the repo root as a "
                "pane token and derives every worktree path from it."
                % problem)

    ensure_crew_dir()
    lock_path = os.path.join(CREW_DIR, "dispatch-%s.lock" % sanitize_name(key))
    with _locked(lock_path, "w"):
        # Both checks, as cmd_ls does. assert_snapshot_shape only validates
        # herdr's REQUIRED fields, and tokens is not one, so without the
        # declaration check a renamed tokens field makes crew_members return
        # nothing, find_member find nothing, and this dispatch spend a second
        # paid session on top of a live one.
        defs = schema_defs()
        assert_schema_declares(defs)
        snap = snapshot()
        assert_snapshot_shape(snap, defs)

        existing = find_member(snap, repo_root, key)
        if existing:
            print("already dispatched: %s/%s in pane %s (%s). "
                  "Resume with: cd %s && claude --continue"
                  % (repo, existing["key"], existing["pane"],
                     existing["bucket"], existing["worktree"]))
            mail_send(key, repo, "duplicate",
                      "dispatch declined, a live session already holds this key")
            return 5

        # Resumable, so a retry never has to wait for the human. If setup
        # already wrote its artifact, this call has nothing interactive
        # left to do regardless of whether it is a ticket or a slug.
        worktree = read_dispatch_artifact(key, repo, repo_root)
        if worktree is not None:
            setup = find_setup_pane(snap, repo_root, key)
            if setup is not None:
                close_setup_pane(setup["pane"])
            code = _complete_dispatch(key, ctype, repo, repo_root, workspace,
                                      model, worktree, snap)
            # Only after it returns. By then the paid session is started and
            # the pane is tagged, including on the exit 6 unconfirmed-delivery
            # path, so the artifact is spent. Deleting it any earlier means a
            # call that raises partway loses the setup work and the next call
            # opens a second paid setup pane.
            clear_dispatch_artifact(key)
            return code

        if is_ticket(key):
            setups = find_setup_panes(snap, repo_root, key)
            occupied = [s for s in setups if pane_has_agent(snap, s["pane"])]
            if occupied:
                label = "pane" if len(occupied) == 1 else "panes"
                subject = "that pane" if len(occupied) == 1 else "those panes"
                # Only OCCUPIED panes are named as somewhere to act. Naming an
                # empty one would send the human to a pane with no prompt in it
                # while the live session sat elsewhere.
                where = ", ".join("%s (herdr reports %s)"
                                  % (s["pane"], s["status"]) for s in occupied)
                leftover = ("" if len(setups) == len(occupied) else
                            " Any other setup pane for this key that no agent "
                            "occupies is closed on that same re-run.")
                print("setup for %s still has an agent in it: %s %s. Nothing "
                      "has been closed, because that is where /start-ticket "
                      "may still be waiting on your answer. Either answer or "
                      "redo setup there, or close %s yourself and re-run this "
                      "command to start setup fresh.%s"
                      % (key, label, where, subject, leftover))
                return 7
            # crew opened these panes and already closes one on the success
            # path, and no agent is in any of them, so there is no session here
            # to destroy. All of them go: an orphan left behind is what the
            # next call reaches for instead of the live pane.
            for setup in setups:
                close_setup_pane(setup["pane"])
            setup_pane = start_setup(key, repo, repo_root)
            print("opened setup pane %s to run /start-ticket for %s. Answer "
                  "the prompt in that pane, then re-run this command."
                  % (setup_pane, key))
            return 7

        # A slug has no ticket to fetch, so there is no interactive step:
        # the worktree exists as soon as plain_worktree returns, and this
        # one call finishes the whole dispatch.
        worktree = plain_worktree(key, repo_root)
        return _complete_dispatch(key, ctype, repo, repo_root, workspace,
                                  model, worktree, snap)


PEEK_DEFAULT = 40
PEEK_CAP = 200


def clamp_lines(requested):
    if requested is None:
        return PEEK_DEFAULT
    return max(1, min(int(requested), PEEK_CAP))


def cmd_peek(name, lines):
    # raw=True: see herdr()'s comment, this is terminal text, not JSON.
    text = herdr("agent", "read", name, "--source", "detection",
                 "--lines", str(clamp_lines(lines)), raw=True)
    if text is None or not text.strip():
        print("(no output; the pane may be on an alternate screen)")
        return 0
    print(text.rstrip())
    return 0


def cmd_nudge(name, text):
    herdr("agent", "prompt", name, text)
    print("nudged %s" % name)
    return 0


def pane_tab(snap, pane_id):
    for pane in snap["panes"]:
        if pane["pane_id"] == pane_id:
            return pane.get("tab_id") or ""
    return ""


def retire_target(snap, name):
    """The one thing `crew retire <name>` names.

    A dead crew member cannot be named by its agent name: herdr clears the name
    when the agent exits, which is exactly the case retirement exists for, so
    crew_members reports it as `(unnamed)`. The key token, the pane id and the
    tab id all outlive the agent, so any of them names a target.

    A tab is only ever a target if its label is one crew gives a tab it created.
    Without that, `crew retire <any tab id>` would close the human's own tab."""
    labelled = []
    for member in crew_members(snap):
        if name in (member["name"], member["pane"]) \
                or member["key"] == sanitize_name(name):
            labelled.append(("member", member,
                             "crew member %s/%s in pane %s"
                             % (member["repo"], member["key"], member["pane"])))
    for tab in snap.get("tabs") or []:
        if tab.get("tab_id") != name:
            continue
        if not is_crew_tab_label(tab.get("label") or ""):
            raise CrewError(
                "tab %s is labelled %r, which is not the label crew gives a tab "
                "it creates, so crew will not close it."
                % (name, tab.get("label") or ""))
        labelled.append(("tab", tab, "tab %s (%s)" % (name, tab.get("label"))))
    if not labelled:
        raise CrewError(
            "nothing named %r: no crew member with that name, key or pane, and "
            "no tab crew created with that id. `crew ls` names what there is."
            % name)
    if len(labelled) > 1:
        raise CrewError(
            "%r names %d things: %s. Name a pane id or a tab id, which are "
            "unique." % (name, len(labelled),
                         "; ".join(text for _, _, text in labelled)))
    kind, payload, _ = labelled[0]
    return kind, payload


def _close_or_warn(kind, target_id):
    """A close that fails warns and the caller carries on.

    Aborting is how the setup-pane close became the thing that wedged a key, and
    a retire that gives up after the pane leaves behind the tab it exists to
    remove."""
    try:
        herdr(kind, "close", target_id)
    except HerdrError as exc:
        print("could not close %s %s (%s), so it is still there. Carrying on "
              "with the rest of the cleanup; close it yourself."
              % (kind, target_id, exc), file=sys.stderr)
        return False
    print("closed %s %s." % (kind, target_id))
    return True


def cmd_retire(name):
    """Close a spent crew member's pane and the tab dispatch created for it.

    Dispatched panes and their tabs outlived their sessions and nothing ever
    removed them, so they accumulated, in two forms: a member whose session
    exited keeps its pane and all its tokens, so it is named by its key, and a
    dispatch that failed before tag_pane leaves an untagged pane that only its
    tab label identifies, so it is named by that tab id.

    Retirement is still PROPOSED by the foreman and run by the human; this verb
    only means the human has something to run other than raw herdr.

    It can never take out live work: the calling pane is refused, a pane an
    agent still occupies is refused whatever its status, and a tab that holds
    anything else is left alone. A close that fails warns and the rest of the
    cleanup continues."""
    defs = schema_defs()
    assert_schema_declares(defs)
    snap = snapshot()
    assert_snapshot_shape(snap, defs)
    kind, payload = retire_target(snap, name)
    me = calling_pane()

    if kind == "member":
        panes = [payload["pane"]]
        tab_id = pane_tab(snap, payload["pane"])
        what = "crew member %s/%s" % (payload["repo"], payload["key"])
    else:
        tab_id = payload["tab_id"]
        panes = [p["pane_id"] for p in tab_panes(snap, tab_id)]
        what = "tab %s (%s)" % (tab_id, payload.get("label"))
        if not tab_holds_only(snap, tab_id, panes):
            raise CrewError(
                "tab %s holds panes this snapshot does not list, so crew cannot "
                "show that nothing in it is live. Close it yourself if you know "
                "it is spent." % tab_id)

    if me and me in panes:
        raise CrewError(
            "%s is the pane this command is running in, so nothing has been "
            "closed: that would kill this session. Close it yourself once you "
            "no longer need it." % me)
    occupied = [p for p in panes if pane_has_agent(snap, p)]
    if occupied:
        raise CrewError(
            "%s still has an agent in %s, so nothing has been closed. herdr "
            "cannot tell a finished session from one waiting on you, so an "
            "occupied pane is never retired: `crew peek` it, let it close "
            "itself once its report is in the mailbox, or close the pane "
            "yourself." % (what, ", ".join(occupied)))

    print("retiring %s." % what)
    done = True
    for pane_id in panes:
        done = _close_or_warn("pane", pane_id) and done

    if not tab_id:
        return 0 if done else 3
    if not tab_holds_only(snap, tab_id, panes):
        others = [p["pane_id"] for p in tab_panes(snap, tab_id)
                  if p["pane_id"] not in panes]
        print("tab %s is not held by %s alone, so it is left alone.%s"
              % (tab_id, "that pane" if len(panes) == 1 else "those panes",
                 (" It also holds %s." % ", ".join(others)) if others else
                 " Crew cannot account for every pane in it."))
        return 0 if done else 3
    # Re-read rather than assume: whether closing a tab's last pane takes the
    # tab with it is herdr's own behaviour, and crew asserting either way would
    # warn on a clean retire or leave the tab behind.
    if any(tab.get("tab_id") == tab_id
           for tab in (snapshot().get("tabs") or [])):
        done = _close_or_warn("tab", tab_id) and done
    else:
        print("tab %s went with its last pane." % tab_id)
    return 0 if done else 3


def main(argv):
    global DRY_RUN
    args = list(argv)
    if "--dry-run" in args:
        DRY_RUN = True
        args.remove("--dry-run")
    try:
        return _run(args)
    except HerdrError as exc:
        print("crew: herdr: %s" % exc, file=sys.stderr)
        return 3
    except CrewError as exc:
        print("crew: %s" % exc, file=sys.stderr)
        return 3
    except (ValueError, IndexError) as exc:
        print("crew: bad arguments: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("crew: filesystem: %s" % exc, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
