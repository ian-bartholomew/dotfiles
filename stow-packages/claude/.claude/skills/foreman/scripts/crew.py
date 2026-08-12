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
import shutil
import socket
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
# A skill's scripts/ directory is not on PATH and ~/.local/bin is, so this
# symlink is what makes `crew mail send` runnable from a crew member's pane.
# doctor checks it and uninstall removes it, so it is named once.
CREW_BIN = os.path.expanduser("~/.local/bin/crew")

WATCHDOG_STATE_FILE = "watchdog.state"
WATCHDOG_HEARTBEAT_FILE = "watchdog.heartbeat"
WATCHDOG_LOCK_FILE = "watchdog.lock"

# The watchdog's OWN vocabulary, deliberately separate from MAIL_STATES. The
# reason an `alert` record exists at all is that a crew member cannot report
# `blocked` about itself: it is mid-turn at a permission prompt and can run
# nothing. Widening MAIL_STATES with these would hand a crew member the ability
# to claim exactly the states only an outside observer can establish.
WATCHDOG_STATES = ("blocked", "stalled", "dead")

WATCHDOG_TICK_SECONDS = 30
WATCHDOG_STALL_SECONDS = 900
# No single tick may credit more than this much elapsed time towards a stall.
# The tick is 30s and can be woken earlier, so a gap over two minutes means the
# loop was NOT running: the laptop slept, the clock stepped, or the process was
# stopped. Crediting that gap as time a crew member sat unchanged is how a lid
# opening declares the whole fleet stalled at once, which is the same storm the
# persisted state exists to prevent.
WATCHDOG_MAX_STEP_SECONDS = 120
# And a stall is never declared on a single observation.
WATCHDOG_STALL_MIN_TICKS = 2
WATCHDOG_HEARTBEAT_STALE_TICKS = 3
# The process name a live Claude Code session presents. Measured on herdr 0.8.0:
# `pane process-info` reports it with argv0 `claude` while `name` carries the
# VERSION string ("2.1.228"), so matching on `name` would match a number that
# changes with every Claude Code release.
AGENT_PROCESS = "claude"

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


def response_pane_id(payload, path, what):
    """The pane id a herdr response carries at `path`, or a HerdrError naming
    what was missing.

    Both readers go through here rather than chaining the keys raw. Chained,
    a response shape change is a KeyError or a TypeError, and main maps neither,
    so a herdr rename exited 1 with a traceback and a pane already created.
    herdr is pre-1.0 and self-updating, and this is the third defect caused by
    trusting its response shape, so this reads it the way the snapshot reader
    already reads its own fields.

    The value is checked, not just the path: a null or non-string pane id would
    otherwise flow into tag_pane and be written as a token, and the tokens are
    the authoritative record of who owns what."""
    node = payload
    for step in path:
        if not isinstance(node, dict) or step not in node:
            raise HerdrError("%s returned no %s" % (what, ".".join(path)))
        node = node[step]
    if not isinstance(node, str) or not node.strip():
        raise HerdrError("%s returned %s = %r, which is not a pane id"
                         % (what, ".".join(path), node))
    return node


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

    if not os.path.exists(CREW_BIN):
        problems.append("%s does not exist; crew is not on PATH for crew members"
                        % CREW_BIN)

    ensure_crew_dir()
    if oct(os.stat(CREW_DIR).st_mode & 0o777) != oct(0o700):
        problems.append("%s is not mode 700" % CREW_DIR)
    if os.path.exists(MAILBOX) and oct(os.stat(MAILBOX).st_mode & 0o777) != oct(0o600):
        problems.append("%s is not mode 600" % MAILBOX)

    # Fresh or absent-by-design passes; stale FAILS. Absent means the human
    # chose to run no watchdog. Stale means one was started and has stopped
    # reconciling, so the fleet has looked watched while nothing was.
    age = heartbeat_age(time.time())
    if age is None:
        print("watchdog: not running, no heartbeat. Nothing detects a blocked, "
              "stalled or dead crew member; that is a choice, not a fault.")
    elif age > WATCHDOG_TICK_SECONDS * WATCHDOG_HEARTBEAT_STALE_TICKS:
        problems.append(
            "the watchdog heartbeat %s is %ds old, over %d ticks, so a watchdog "
            "was started and has stopped reconciling. Nothing is detecting "
            "blocked, stalled or dead crew members. Restart it with `crew "
            "watchdog`, or delete that file if you meant to stop it."
            % (watchdog_path(WATCHDOG_HEARTBEAT_FILE), int(age),
               WATCHDOG_HEARTBEAT_STALE_TICKS))
    else:
        print("watchdog: alive, last reconcile %ds ago" % int(age))

    shadow = os.path.expanduser("~/.claude/skills/herdr.md")
    if os.path.exists(shadow):
        problems.append("%s shadows the stowed herdr skill" % shadow)

    summary, guard_issues = guard_status(SETTINGS_PATH)
    if summary:
        print("guard: %s" % summary)
    problems.extend(guard_issues)

    if not os.environ.get("HERDR_ENV"):
        print("note: not running inside a herdr pane; pane-scoped verbs will not work")

    # A note, never a problem. `gh` is used by one verb, and the doctor is the
    # gate the foreman skill says to stop on, so failing the whole preflight for
    # a binary most sessions never reach would stop work for a reason unrelated
    # to anything crew is about to do. `crew watch` refuses on its own, before it
    # creates a pane, which is where it matters.
    if not _probe(["gh", "--version"])[0]:
        print("note: gh is not usable here, so crew watch cannot follow a run")

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
        # A watcher is crew's pane but not a crew member: it holds no agent by
        # design, so counted here it would sit in the `recover` bucket forever
        # and a healthy fleet would read as needing recovery. Read off the RAW
        # token, which is what `watchers` matches on too, so no watcher can fall
        # between the two readers and become invisible.
        if tokens.get("type") == WATCH_TYPE:
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
    # Type is part of the sort because it is part of the identity: one key can
    # hold an implementer and the reviewer reading its worktree, and two rows
    # ordered by whatever the snapshot happened to list first read as a fleet
    # that reshuffles itself between calls.
    members.sort(key=lambda m: (m["repo"], m["key"], m["type"]))
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
    an unclassifiable agent would.

    A watcher's tab is excluded. A watcher holds no agent for its whole life, so
    the rule above would name a watcher still following a run as an orphan and
    propose closing it. The two are told apart by tokens, not by occupancy: an
    orphan carries none at all, because tag_pane never ran, while a watcher is
    tagged. Watchers are reported in their own section instead."""
    out = []
    watcher_panes = set(w["pane"] for w in watchers(snap))
    for tab in snap.get("tabs") or []:
        tab_id = tab.get("tab_id")
        if not tab_id or not is_crew_tab_label(tab.get("label") or ""):
            continue
        panes = [p["pane_id"] for p in tab_panes(snap, tab_id)]
        if any(pane_has_agent(snap, pane_id) for pane_id in panes):
            continue
        if any(pane_id in watcher_panes for pane_id in panes):
            continue
        if not tab_holds_only(snap, tab_id, panes):
            continue
        out.append({"tab": tab_id, "label": tab.get("label") or "",
                    "panes": panes})
    out.sort(key=lambda t: t["tab"])
    return out


def retire_handle(members, member):
    """The handle `crew retire` resolves to exactly this member.

    retire_target matches a key across every crew member, and one key can now
    hold two: an implementer and the reviewer reading its worktree. Proposing
    the key there names two things, so retire refuses and closes nothing, and
    the foreman is told to print what ls proposed. The pane id is always unique,
    so it is what gets proposed once the key is not."""
    if sum(1 for m in members if m["key"] == member["key"]) > 1:
        return member["pane"]
    return member["key"]


def render_ls(members, untagged, orphan_tabs=None, watchdog=None,
              watcher_list=None):
    counts = {"working": 0, "awaiting": 0, "blocked": 0, "recover": 0}
    for m in members:
        counts[m["bucket"]] += 1
    lines = ["%d working / %d awaiting you / %d blocked" % (
        counts["working"], counts["awaiting"], counts["blocked"])]
    if counts["recover"]:
        lines[0] += " / %d need recovery" % counts["recover"]
    # Directly under the counts, not in a section at the bottom. A dead watchdog
    # makes those counts mean less than they appear to, so it belongs where the
    # foreman cannot read the load without reading it.
    lines.extend(watchdog or [])
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
                         % (m["repo"], m["key"], m["pane"],
                            retire_handle(members, m)))
    if orphan_tabs:
        lines.append("")
        lines.append("%d tab(s) crew created holding no agent, retirable "
                     "(matched by label: a dispatch that failed before tagging "
                     "leaves a pane with no tokens):" % len(orphan_tabs))
        for t in orphan_tabs:
            lines.append("  %-10s %-24s %-14s propose: crew retire %s"
                         % (t["tab"], t["label"], ",".join(t["panes"]),
                            t["tab"]))
    # Their own section, and never a row in the table above: a watcher has no
    # agent, so it has no status to bucket and it is not load the human has to
    # review. It is named by its TAB, because a watcher is not a crew member and
    # so `crew retire <pane id>` cannot resolve it.
    if watcher_list:
        lines.append("")
        lines.append("%d watcher(s), no agent, so not counted as load:"
                     % len(watcher_list))
        for w in watcher_list:
            if w.get("outcome"):
                verdict = ("%s, in the mailbox at seq %d, so it is spent: "
                           "propose crew retire %s"
                           % (w["outcome"], w["seq"], w["tab"] or w["pane"]))
            else:
                verdict = ("nothing in the mailbox yet, so it is still "
                           "following the run, or it died without saying so; "
                           "look at the pane")
            lines.append("  %-12s %-14s %-20s %s"
                         % (w["pane"], "run " + (w["run"] or "?"), w["repo"],
                            verdict))
            if w["agent"]:
                lines.append("    an agent occupies this watcher pane, which "
                             "crew never puts there")
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
                        orphan_crew_tabs(snap),
                        watchdog=watchdog_report(time.time()),
                        watcher_list=watcher_rows(watchers(snap),
                                                 mailbox_entries())))
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


# The kind of a mailbox record: `report` from a crew member, `ack` from the
# foreman, `alert` from the watchdog. Every reader filters on it, because an ack
# in a crew digest, or an alert counted as a crew member's own report, is the
# confusion this field exists to prevent. MAIL_STATES stays the vocabulary of a
# `report` ALONE and must not be widened for an alert: a crew member cannot
# report `blocked` about itself, so `blocked` must not become a state it can
# claim.
KIND_REPORT = "report"
KIND_ACK = "ack"
KIND_ALERT = "alert"

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


def record_kind(record):
    """The kind of a mailbox record, where ABSENCE means `report`.

    The mailbox is never pruned and holds records written before the field
    existed, every one of them a crew member's report, so a missing `kind` is not
    unknown, it is the original kind. Every reader goes through here rather than
    reading the field: `record["kind"]` raises on those records, and
    `record.get("kind")` returns None, which matches no kind at all and would
    drop them from a digest that must still show them.

    Collapsed for the same reason every printed field is: kind reaches the
    foreman's terminal as a column of its own."""
    return one_line(record.get("kind")) or KIND_REPORT


def ack_upto(record):
    """The position an ack record claims, or 0 when it claims nothing readable.

    `upto` is untrusted input rather than a field crew can assume it wrote, since
    any process running as this user can append to the mailbox. A missing value,
    a list, or the string "all" has to leave the position where it was rather
    than raise inside the reader every status request goes through."""
    try:
        return max(0, int(record.get("upto")))
    except (TypeError, ValueError):
        return 0


def append_record(build):
    """The one writer for the mailbox, whatever kind of record is written.

    `build` receives the records already there and returns the record to append,
    or None to append nothing. It runs INSIDE the lock because both decisions
    depend on those records: seq is assigned from them, and an ack derives its
    position from them, so a second writer landing between the read and the write
    would hand out one seq twice.

    fsynced, and the file left mode 600, because the mailbox is the only record
    of what a crew member reported."""
    with _locked(MAILBOX, "a+") as handle:
        handle.seek(0)
        entries, _ = read_entries(handle.readlines())
        record = build(entries)
        if record is not None:
            record["seq"] = next_seq(entries)
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    os.chmod(MAILBOX, 0o600)
    return record


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
    both an injection surface and a report nothing can act on.

    The kind is written explicitly. Absence already means `report`, for the
    records that predate the field, but a writer that leaves it out makes every
    later reader depend on that fallback for records it could have labelled."""
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
        "kind": KIND_REPORT,
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

    append_record(lambda _entries: record)
    return 0


def _read_cursor():
    """The position the legacy cursor FILE holds, 0 if there is none.

    Read as a floor, and only while the mailbox holds no ack record at all: that
    is the one migration step, and the first ack retires the file for good. The
    file is a mutable integer any process running as this user can overwrite, and
    from a real crew pane `echo 999999 > ~/.crew/cursor` was allowed while `crew
    mail ack 999999` was denied, so the effect the guard refuses had an unguarded
    route. It is still read once so a real position is not lost.

    Locked. An unlocked read racing a writer could see a truncated file, read 0,
    and re-deliver the entire mailbox."""
    if not os.path.exists(CURSOR):
        return 0
    try:
        with _locked(CURSOR, "r") as handle:
            return int(handle.read().strip() or 0)
    except (IOError, OSError, ValueError):
        return 0


def ack_position(record):
    """The most an ack record can honestly claim to have read.

    Its OWN seq bounds it. seq is assigned in append order under the lock, so
    every record numbered above an ack was written after it, and an ack cannot
    have acknowledged a report that did not exist yet.

    This bound is what makes a forged ack a bounded loss instead of an unbounded
    one. The measured payload, `crew mail ack 999999`, marked every FUTURE report
    read as well, and that is the damage: the foreman saw an empty mailbox
    forever. A record claiming 999999 can now hide only what preceded it. It
    costs a real ack nothing, because the only seq crew ever proposes acking is
    one it has just printed, which is below the ack that records it."""
    return max(0, min(ack_upto(record), record["seq"] - 1))


def derived_cursor(entries):
    """The position the ack RECORDS establish, or None when there are none.

    The HIGHEST position, not the last one written: records are appended and
    never edited, so a later ack claiming less than an earlier one must not pull
    the position backwards and re-deliver mail the foreman has already read.

    A suspect ack still counts towards it. Deriving the position from the
    foreman's own acks alone would put a herdr round trip in every read, and would
    re-deliver the whole mailbox the moment the foreman moved pane. So the
    position deliberately does not decide who wrote it; mail_unread reports
    that."""
    positions = [ack_position(e) for e in entries
                 if record_kind(e) == KIND_ACK]
    return max(positions) if positions else None


def cursor_ceiling(entries):
    """The highest seq the mailbox holds, which is the most a record appended
    NEXT could honestly acknowledge.

    Used where there is no ack record to take the bound from: the position an ack
    is about to record, and the legacy cursor file, which carries no seq of its
    own to be bounded by."""
    return max([e["seq"] for e in entries] or [0])


def effective_cursor(entries):
    """The read position, DERIVED from the mailbox rather than stored beside it.

    One locked read of one file now yields both the records and the position, so
    the race between them is gone: there is no second file left to read 0 from
    while every record is still present.

    The legacy cursor file is consulted only while no ack record exists, and
    never again afterwards."""
    derived = derived_cursor(entries)
    if derived is not None:
        return derived
    return min(_read_cursor(), cursor_ceiling(entries))


ACK_SUSPECTS_NAMED = 10


def suspect_acks(entries, foreman_pane_id):
    """The ack records the foreman cannot account for, in seq order.

    Two shapes, because they mean different things and only one of them is
    certain. A record carrying NO pane is what a shell redirect or a hand edit
    leaves behind, and nothing crew writes is missing one. A record carrying some
    other pane was written by another session, which is a crew member acking its
    own reports away, but it is also what a foreman that moved to a new pane
    leaves behind, so it is reported as what it is rather than as proof.

    An empty foreman_pane_id means the foreman's pane is not known, and then only
    the pane-less records are named: calling a pane "not the foreman's" while
    nothing knows which pane that is would be the confident guess this whole
    check exists to remove."""
    suspects = []
    for record in sorted((e for e in entries if record_kind(e) == KIND_ACK),
                         key=lambda e: e["seq"]):
        pane = one_line(record.get("pane"))
        if not pane:
            why = ("carries no pane, which is what a shell redirect or a hand "
                   "edit into the file leaves")
        elif foreman_pane_id and pane != foreman_pane_id:
            why = "was written by pane %s, which is not the foreman's" % quoted(pane)
        else:
            continue
        # Both numbers. `claimed` is what the record asked for, which is the
        # evidence of intent, and `position` is what it actually reached once its
        # own seq bounded it, which is the damage.
        suspects.append({"seq": record["seq"], "pane": pane,
                         "claimed": ack_upto(record),
                         "position": ack_position(record), "why": why})
    return suspects


def render_ack_tampering(suspects, acks, foreman_pane_id):
    """What forged ack records mean, as lines, or nothing when there are none.

    Reported rather than only resisted, because the position is derived from
    every ack record including a forged one: the forgery is BOUNDED here, not
    undone, and a foreman that is not told cannot tell an acked fleet from a
    silent one. Returned as lines so the wording is testable."""
    if not suspects:
        return []
    lines = [
        "",
        "ACK TAMPERING: %d of %d ack record(s) in the mailbox %s not written by "
        "the foreman%s." % (
            len(suspects), acks, "was" if len(suspects) == 1 else "were",
            " pane %s" % foreman_pane_id if foreman_pane_id else ""),
        "An ack record is the only thing that marks mail read, so a forged one "
        "hides reports you never saw. These have marked everything up to seq %d "
        "read:" % max(s["position"] for s in suspects),
    ]
    for suspect in suspects[:ACK_SUSPECTS_NAMED]:
        lines.append("  seq %-6d claimed %-8d %s"
                     % (suspect["seq"], suspect["claimed"], suspect["why"]))
    if len(suspects) > ACK_SUSPECTS_NAMED:
        lines.append("  ... and %d more" % (len(suspects) - ACK_SUSPECTS_NAMED))
    lines.append(
        "Those records are permanent and nothing crew does removes them, and the "
        "loss is bounded: no position can sit above the highest record in the "
        "mailbox, so reports written after these still reach you.")
    lines.append(
        "What crew cannot do is prove who wrote a record, because the pane in it "
        "is self-reported: an ack forged with the foreman's own pane in it would "
        "not appear here. Treat the silence as unverified, `crew peek` the crew "
        "this touches, and tell the human.")
    return lines


def mail_unread():
    ensure_crew_dir()
    if not os.path.exists(MAILBOX):
        print("no mail")
        print("nothing to ack")
        return 0
    with _locked(MAILBOX, "r") as handle:
        entries, unreadable = read_entries(handle.readlines())
    cursor = effective_cursor(entries)
    fresh, _top, missing = select_unread(entries, cursor)
    # Filtered by kind on the way to the terminal. An ack is crew's own
    # bookkeeping rather than mail, and printing one here is how the foreman
    # would come to read its own position as a crew member's report. Every other
    # kind IS mail, including one this build does not know: an unrecognised kind
    # is shown with its name rather than hidden.
    shown = [record for record in fresh if record_kind(record) != KIND_ACK]
    for record in shown:
        print("%s  %-10s %-14s %-24s %-14s %s" % (
            record["seq"], quoted(record_kind(record)),
            quoted(record.get("state", "")),
            quoted(record.get("repo", "")),
            one_line(record.get("key", "")), quoted(record.get("msg", ""))))
    if not shown:
        print("no new mail")
    if unreadable or missing:
        print("%d unreadable, %d missing" % (unreadable, missing))

    # An ack record is never something to ack: its seq would be fresh on the next
    # call, and acking that appends another one, forever. The target is the
    # highest seq that is mail, and never below the position already reached.
    target = max([record["seq"] for record in entries
                  if record_kind(record) != KIND_ACK] or [0])
    if target > cursor:
        print("ack with: crew mail ack %d" % target)
    else:
        print("nothing to ack (position %d)" % cursor)

    if derived_cursor(entries) is None and os.path.exists(CURSOR):
        print("the position still comes from %s, a plain integer file any "
              "process running as this user can overwrite. Acking once records "
              "it in the mailbox as an ack record and retires that file."
              % CURSOR)

    acks = [record for record in entries if record_kind(record) == KIND_ACK]
    if acks:
        # Only when there is an ack to check, so a mailbox without one needs no
        # herdr at all and this digest keeps working when the socket does not.
        foreman, why_not = known_foreman_pane()
        if why_not:
            print("ack records are only partly checked: %s. An ack carrying no "
                  "pane at all is still reported." % why_not)
        for line in render_ack_tampering(suspect_acks(entries, foreman),
                                         len(acks), foreman):
            print(line)
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


def foreman_pane():
    """The pane hosting the agent named foreman, or "" when no agent holds that
    name. herdr enforces one live agent per name, so there is at most one."""
    for agent in snapshot()["agents"]:
        if agent.get("name") == "foreman":
            return agent.get("pane_id") or ""
    return ""


def known_foreman_pane():
    """(pane, why it is not known), for a reader that has to work either way.

    mail_unread checks ack records against the foreman's pane, and that check is
    a bonus on top of the digest rather than a precondition for it. A reader that
    exited 3 because the herdr socket was down, or because no agent holds the
    name yet, would be the same silent fleet this check exists to expose."""
    try:
        pane = foreman_pane()
    except (CrewError, HerdrError, OSError) as exc:
        return "", ("herdr could not be asked which pane hosts the foreman (%s)"
                    % exc)
    if not pane:
        return "", ("no agent is named foreman, so there is no pane to compare "
                    "an ack against; run crew claim-foreman")
    return pane, None


def is_foreman_pane():
    me = calling_pane()
    return bool(me) and foreman_pane() == me


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
    """Acknowledge mail by APPENDING an ack record, not by writing a position.

    The position used to live in ~/.crew/cursor as a mutable integer, and the
    guard denying `crew mail ack` did not deny its effect. Measured from a real
    crew pane: `crew mail ack 999999` was denied, while `echo 999999 >
    ~/.crew/cursor` was allowed, as were `tee`, the `Write` tool, and `rm` of the
    guard itself. A forged integer there marked every pending and every future
    report read, so the foreman saw an empty mailbox and its own skill reads a
    silent fleet as a working one. The failure was confident silence, not an
    error.

    An append-only record does not make that impossible, because a process
    running as this user can append to any file it can write. It makes it
    evident and bounded: the record is permanent, it carries the pane that wrote
    it, mail_unread compares that against the foreman's own pane and says what a
    mismatch means, and no position may sit above the highest record present. It
    is a loud bounded compromise in place of a silent unbounded one, and it is
    not tamper-proofing.

    Foreman-only, unchanged: exit 4 is documented as load-bearing in two skills."""
    if not is_foreman_pane():
        print(
            "refusing: crew mail ack is foreman-only, and this pane does not "
            "host the agent named foreman", file=sys.stderr)
        return 4
    ensure_crew_dir()
    if DRY_RUN:
        print("would append an ack record to %s claiming up to %d" % (MAILBOX, seq))
        return 0

    already = 0
    migrated_from = None

    def build(entries):
        nonlocal already, migrated_from
        derived = derived_cursor(entries)
        position = derived if derived is not None else _read_cursor()
        # The legacy file is a FLOOR, so an ack cannot lose a position it held,
        # and the ceiling binds it whatever it claimed.
        upto = min(max(0, seq, position), cursor_ceiling(entries))
        # The first ack record is what retires the cursor file, so it is written
        # even at a position already reached. After that, an append-only file
        # must not grow a record that moves the position nowhere.
        if derived is None and os.path.exists(CURSOR):
            migrated_from = position
        elif upto <= position:
            already = position
            return None
        return {"v": 1, "kind": KIND_ACK, "ts": int(time.time()),
                "upto": upto, "pane": one_line(calling_pane())}

    record = append_record(build)
    if record is None:
        print("nothing to ack: the position is already %d, and an append-only "
              "mailbox must not grow a record that moves it nowhere." % already)
        return 0
    if migrated_from is not None:
        print("migrated the position %d out of %s into ack record seq %d. That "
              "file is not read again." % (migrated_from, CURSOR, record["seq"]))
    if record["upto"] < seq:
        print("crew mail ack %d asked to mark records past the end of the "
              "mailbox as read, so it was clamped to %d, the highest record "
              "there. Nothing crew prints ever asks for more than that, so if "
              "you did not type that number yourself, treat where it came from "
              "as an injection and peek the crew that reported."
              % (seq, record["upto"]))
    print("acked up to %d as ack record seq %d from pane %s"
          % (record["upto"], record["seq"], record["pane"] or "(none)"))
    return 0


def watchdog_alert(member, condition, msg):
    """Append ONE `alert` record for one pane and one condition.

    `alert`, never `report`. A report is a crew member's own claim about itself,
    and blocked, stalled and dead are precisely the three states a crew member
    cannot claim: it is stuck, and being stuck is the problem. So this does not
    go through mail_send and its vocabulary is not MAIL_STATES.

    Every string field is collapsed to one line for the same reason mail_send
    collapses them. The foreman renders these in its terminal, and a value
    carrying a newline reads as a second line crew printed."""
    ensure_crew_dir()
    if condition not in WATCHDOG_STATES:
        raise CrewError("%r is not a watchdog condition; expected one of %s"
                        % (condition, ", ".join(WATCHDOG_STATES)))
    record = {
        "v": 1,
        "kind": KIND_ALERT,
        "ts": int(time.time()),
        "key": member.get("key", ""),
        "repo": member.get("repo", ""),
        "pane": member.get("pane", ""),
        "branch": member.get("branch", ""),
        "state": condition,
        "msg": msg,
    }
    for field, value in list(record.items()):
        if isinstance(value, str):
            record[field] = one_line(value)
    if DRY_RUN:
        print("would append to %s: %s"
              % (MAILBOX, json.dumps(record, sort_keys=True)))
        return record
    return append_record(lambda _entries: record)


def watchdog_notify(alerts):
    """ONE notification per tick, naming the count.

    During a fleet-wide quota exhaustion every crew member stalls at once, and
    one notification per pane is how a signal worth having becomes noise the
    human mutes. The mailbox still carries one record per pane.

    Syntax measured against herdr 0.8.0: `herdr notification show <TITLE>
    --body <TEXT>`. There is no pane argument, so the notification cannot carry
    attribution and does not pretend to; the mailbox is where the detail is."""
    counts = {}
    for _, condition, _ in alerts:
        counts[condition] = counts.get(condition, 0) + 1
    body = ", ".join("%d %s" % (counts[c], c)
                     for c in WATCHDOG_STATES if c in counts)
    try:
        herdr("notification", "show", "crew: %d alert(s)" % len(alerts),
              "--body", "%s. Read them with crew mail unread." % body)
    except HerdrError as exc:
        print("watchdog: notification not shown (%s). The alerts are in the "
              "mailbox regardless." % exc, file=sys.stderr)


def watchdog_path(name):
    """A watchdog file, resolved from CREW_DIR at CALL time.

    MAILBOX and CURSOR are bound at import, and every test that touches them has
    to remember to redirect each one by name. These are derived instead, so
    redirecting CREW_DIR is enough and nothing in the watchdog can quietly read
    or write the real ~/.crew from a test."""
    return os.path.join(CREW_DIR, name)


def read_watchdog_state(path=None):
    """The persisted per-pane state, as {pane: {seq, revision, last_seen,
    stalled_for, ticks, flags}}.

    Persisted rather than held in memory for one specific reason: without it a
    watchdog crash wipes every stall timer, and the restart declares every
    running pane stalled at once.

    Anything unreadable, whether the whole file or one entry, reads as ABSENT.
    That is the safe direction and it is the whole handling of a corrupt or
    truncated file: a pane missing from this map is NEWLY SEEN, and a newly seen
    pane can never be stalled, so a damaged state file costs detection latency
    and never produces a false alert. The same applies to a file written by an
    older shape of this record.

    Two fields differ from the design, and each replaces something that does not
    survive contact with the machine:

    - revision sits beside state_change_seq. Measured on herdr 0.8.0, the seq
      moves only when an agent changes STATUS, so an agent working productively
      for an hour holds one seq the whole time and a seq-only test would call it
      stalled. revision tracks terminal output, so requiring both to be frozen
      is the difference between "nothing is happening at all" and "no status
      transition happened".
    - stalled_for accumulates, rather than the design's `now - first_seen_ts`.
      A subtraction credits wall clock that passed while the watchdog was not
      running, so a laptop resuming after eight hours asleep would report every
      working pane stalled in one tick."""
    path = path or watchdog_path(WATCHDOG_STATE_FILE)
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (IOError, OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for pane, entry in data.items():
        if not isinstance(entry, dict):
            continue
        try:
            out[str(pane)] = {
                "seq": int(entry["seq"]),
                "revision": int(entry["revision"]),
                "last_seen": float(entry["last_seen"]),
                "stalled_for": float(entry["stalled_for"]),
                "ticks": int(entry.get("ticks", 1)),
                "flags": [str(f) for f in entry.get("flags") or []],
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def write_watchdog_state(state, path=None):
    """Atomic. A partial write is how this file becomes the corrupt one
    read_watchdog_state has to tolerate, and the loop writes it every tick, so
    the odds of being killed mid-write are not small."""
    path = path or watchdog_path(WATCHDOG_STATE_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(state, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def touch_heartbeat(now, path=None):
    """Record that a reconcile COMPLETED. The epoch is written into the file
    rather than left to mtime so a test can drive it from an injected clock and
    so a filesystem with coarse timestamps cannot blur two ticks together."""
    path = path or watchdog_path(WATCHDOG_HEARTBEAT_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        handle.write("%d\n" % int(now))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def heartbeat_age(now, path=None):
    """Seconds since the watchdog last completed a reconcile, or None when there
    is no heartbeat at all.

    Absent is NOT stale. Running no watchdog is a legitimate configuration and
    the two need different messages: absent means nothing was ever watching,
    stale means something was and has stopped, which is the worse of the two
    because the fleet looked watched.

    A heartbeat whose contents cannot be read falls back to its mtime rather
    than reading as absent, so a truncated write still reports an age."""
    path = path or watchdog_path(WATCHDOG_HEARTBEAT_FILE)
    try:
        with open(path) as handle:
            stamp = int(handle.read().strip() or 0)
    except (IOError, OSError):
        return None
    except ValueError:
        try:
            stamp = int(os.stat(path).st_mtime)
        except OSError:
            return None
    age = now - stamp
    # A clock that stepped backwards reads as fresh rather than as a negative
    # age. Declaring the watchdog stale because the clock moved would be a
    # false alarm about the thing that reports false alarms.
    return 0.0 if age < 0 else float(age)


def watchdog_report(now, tick=None, path=None):
    """The line `crew ls` prints about the watchdog itself.

    Nothing watches the watchdog, so this does. A watchdog that has died looks
    exactly like a healthy fleet from the load table alone, which is the failure
    the whole component exists to prevent, so this line is printed whether or
    not there is anything else to report."""
    tick = tick or WATCHDOG_TICK_SECONDS
    age = heartbeat_age(now, path)
    if age is None:
        return ["watchdog: NOT RUNNING (no heartbeat). Nothing detects a "
                "blocked, stalled or dead crew member. Start one in a pane "
                "with: crew watchdog"]
    if age > tick * WATCHDOG_HEARTBEAT_STALE_TICKS:
        return ["watchdog: STALE, last reconcile %ds ago, over %d ticks. It is "
                "running blind or gone, so treat blocked, stalled and dead as "
                "unmonitored." % (int(age), WATCHDOG_HEARTBEAT_STALE_TICKS)]
    return ["watchdog: alive, last reconcile %ds ago" % int(age)]


def watchdog_members(snap):
    """Crew members carrying the two agent fields the stall test needs.

    crew_members deliberately does not carry state_change_seq or revision: it
    feeds `crew ls --json`, which the spec's own drift check reads, and every
    other caller compares members for identity rather than for freshness. So
    the watchdog joins them on here instead of widening that record.

    An untagged pane is not crew by definition, so only what crew_members
    returns is the watchdog's business."""
    agents = dict((a["pane_id"], a) for a in snap["agents"])
    out = []
    for member in crew_members(snap):
        agent = agents.get(member["pane"], {})
        row = dict(member)
        row["seq"] = int(agent.get("state_change_seq") or 0)
        row["revision"] = int(agent.get("revision") or 0)
        out.append(row)
    return out


def pane_agent_alive(pane_id, agent=AGENT_PROCESS):
    """True, False, or None when herdr cannot say.

    None is not False, and the difference is the whole point. `dead` is the one
    condition with no second opinion behind it, so an unreadable answer must
    never be reported as a dead crew member: a false dead alert is how the human
    learns to ignore the watchdog.

    Measured on herdr 0.8.0: `herdr pane process-info --pane <id>` works. The
    spec recorded that `--pane`, `--target`, `--id` and a positional were all
    rejected on 2026-08-10 and that a direct socket call might be needed; that
    gap is closed, and the CLI is used rather than the socket. On an older herdr
    the call fails, which returns None here and emits nothing.

    The match is on argv0 and argv[0], never on `name`: a live session reports
    argv0 `claude` while `name` carries the Claude Code version string, so
    matching name would match a number that changes every release. cmdline is
    the third fallback because the schema declares all three nullable."""
    try:
        payload = herdr("pane", "process-info", "--pane", pane_id,
                        read_only=True)
    except HerdrError:
        return None
    info = payload.get("result") if isinstance(payload, dict) else None
    info = info.get("process_info") if isinstance(info, dict) else None
    processes = info.get("foreground_processes") if isinstance(info, dict) else None
    if not isinstance(processes, list) or not processes:
        # An empty list is not proof the agent is gone: herdr reports no
        # foreground process for a pane it cannot inspect either.
        return None
    for process in processes:
        if not isinstance(process, dict):
            continue
        argv = process.get("argv") if isinstance(process.get("argv"), list) else []
        cmdline = str(process.get("cmdline") or "").split()
        for candidate in (process.get("argv0"),
                          argv[0] if argv else None,
                          cmdline[0] if cmdline else None):
            if candidate and os.path.basename(str(candidate)) == agent:
                return True
    return False


def watchdog_message(member, condition, stalled_for):
    """One line, no command output, no paths beyond the pane id. These records
    are permanent and get digested into a git-tracked project log."""
    if condition == "blocked":
        return ("herdr reports this pane blocked, which a crew member cannot "
                "report about itself. Look at pane %s." % member["pane"])
    if condition == "stalled":
        return ("no agent state change and no terminal output for %d minutes "
                "while herdr still reports it working. Peek it before assuming "
                "a quota stall." % int(stalled_for // 60))
    return ("herdr still lists an agent for this pane but no %s process runs in "
            "it, so the session is gone while the pane reads as idle."
            % AGENT_PROCESS)


def watchdog_tick(members, state, now, own_pane, stall_seconds, liveness):
    """Decide, from a reconciled snapshot, what is newly wrong. It mutates
    `state` and returns [(member, condition, msg)]; it performs no I/O and reads
    no clock, which is what makes every rule below testable directly.

    The rules each exist because of a way this goes wrong:

    - The watchdog never alerts on its OWN pane. It is the one pane guaranteed
      to be sitting in a loop rather than working.
    - A pane absent from `state` is NEWLY SEEN and cannot be stalled. Both its
      accumulated time and its tick count say so, so a wiped or corrupt state
      file costs latency rather than producing a storm of false stalls.
    - Occupancy is read off `member["agent"]`, which pane_has_agent derives from
      absence in the snapshot's agent LIST. An agent_status of `unknown` means
      an agent herdr cannot classify, not an absent one, so a status check here
      would call every unclassifiable session dead.
    - `dead` suppresses the other two. A dead session's status is frozen at
      whatever it last was, so reporting it as stalled as well is noise about
      one fact.
    - A flag is cleared as soon as its condition stops holding, so one stall
      produces one alert and a recurrence later produces another."""
    alerts = []
    seen = set()
    for member in members:
        pane = member["pane"]
        if own_pane and pane == own_pane:
            continue
        if not member["agent"]:
            # No agent occupies it. `crew ls` already reports that pane as
            # retirable and it is the ordinary end state of a finished crew
            # member, so alerting here would fire on every normal completion.
            continue
        seen.add(pane)
        fresh = {"seq": member["seq"], "revision": member["revision"],
                 "last_seen": now, "stalled_for": 0.0, "ticks": 1, "flags": []}
        entry = state.get(pane)
        if entry is None:
            entry = fresh
            state[pane] = entry
        elif (entry["seq"] != member["seq"]
                or entry["revision"] != member["revision"]):
            entry.update(fresh)
        else:
            step = now - entry["last_seen"]
            # Negative means the clock stepped back and nothing is owed;
            # over the cap means the loop was not running for that gap.
            step = 0.0 if step < 0 else min(step, WATCHDOG_MAX_STEP_SECONDS)
            entry["stalled_for"] += step
            entry["last_seen"] = now
            entry["ticks"] += 1

        if liveness(pane) is False:
            conditions = ["dead"]
        else:
            conditions = []
            if member["status"] == "blocked":
                conditions.append("blocked")
            elif (member["status"] == "working"
                    and entry["stalled_for"] >= stall_seconds
                    and entry["ticks"] >= WATCHDOG_STALL_MIN_TICKS):
                conditions.append("stalled")
        for condition in conditions:
            if condition in entry["flags"]:
                continue
            entry["flags"].append(condition)
            alerts.append((member, condition,
                           watchdog_message(member, condition,
                                            entry["stalled_for"])))
        entry["flags"] = [f for f in entry["flags"] if f in conditions]

    # A snapshot that parsed is the authority on which panes exist, so anything
    # else is gone and its timer would otherwise accumulate forever. A snapshot
    # that did NOT parse never reaches here: watchdog_pass skips the tick and
    # keeps every timer.
    for gone in [p for p in state if p not in seen]:
        del state[gone]
    return alerts


def watchdog_pass(own_pane, stall_seconds, now=None, liveness=None,
                  state_path=None):
    """One reconcile: read herdr, decide, record, notify. Returns
    (panes, alerts, ok).

    ok is False when herdr could not be read, and the caller must NOT advance
    the heartbeat then. A heartbeat that keeps ticking while nothing can be
    decided makes `crew ls` report a healthy watchdog that is in fact blind,
    which is the exact failure this component exists to prevent.

    The same fail-closed assertions `crew ls` uses, for the same reason: under
    schema drift crew_members returns nothing, and a watchdog watching an empty
    fleet is indistinguishable from a quiet one.

    The mailbox record is written BEFORE the state file. A duplicate alert after
    a crash is a nuisance; a flag persisted for an alert that was never recorded
    is a condition nobody is ever told about. An append that fails drops its
    flag so the next tick tries again."""
    now = time.time() if now is None else now
    liveness = pane_agent_alive if liveness is None else liveness
    try:
        defs = schema_defs()
        assert_schema_declares(defs)
        snap = snapshot()
        assert_snapshot_shape(snap, defs)
    except (CrewError, HerdrError) as exc:
        print("watchdog: herdr is not readable (%s), so nothing was decided "
              "this tick, the heartbeat is NOT advanced and every stall timer "
              "is kept." % exc, file=sys.stderr)
        return [], [], False

    members = watchdog_members(snap)
    state = read_watchdog_state(state_path)
    alerts = watchdog_tick(members, state, now, own_pane, stall_seconds,
                           liveness)
    recorded = []
    for member, condition, msg in alerts:
        try:
            watchdog_alert(member, condition, msg)
        except (CrewError, HerdrError, OSError, ValueError) as exc:
            print("watchdog: %s on %s was NOT recorded in the mailbox (%s), so "
                  "it will be retried next tick"
                  % (condition, member["pane"], exc), file=sys.stderr)
            flags = state.get(member["pane"], {}).get("flags")
            if flags and condition in flags:
                flags.remove(condition)
            continue
        recorded.append((member, condition, msg))
    write_watchdog_state(state, state_path)
    if recorded:
        watchdog_notify(recorded)
    return [m["pane"] for m in members], recorded, True


def _json_frame(line):
    try:
        frame = json.loads(line.decode("utf-8", "replace"))
    except ValueError:
        return None
    return frame if isinstance(frame, dict) else None


def _await_status_event(path, pane_ids, deadline):
    """True when herdr reports an agent status change on one of these panes
    before the deadline. Never raises."""
    request = {"id": "crew-watchdog", "method": "events.subscribe",
               "params": {"subscriptions": [
                   {"type": "pane.agent_status_changed", "pane_id": pane}
                   for pane in sorted(set(pane_ids))]}}
    conn = None
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(max(0.1, deadline - time.monotonic()))
        conn.connect(path)
        conn.sendall((json.dumps(request) + "\n").encode())
        buffered = b""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            conn.settimeout(remaining)
            chunk = conn.recv(65536)
            if not chunk:
                return False
            buffered += chunk
            while b"\n" in buffered:
                line, buffered = buffered.split(b"\n", 1)
                frame = _json_frame(line)
                if frame is None:
                    continue
                # A pane that closed between the snapshot and this call answers
                # pane_not_found, which ends the subscription rather than the
                # watchdog: the next snapshot drops that pane.
                if frame.get("error"):
                    return False
                if frame.get("event") == "pane_agent_status_changed":
                    return True
    except (OSError, ValueError):
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass


def wait_for_status_change(pane_ids, timeout_s, socket_path=None):
    """Block until a crew pane changes agent status, or the tick elapses. True
    if an event woke it.

    The plan specified `events.wait --match pane.agent_status_changed`. Measured
    against herdr 0.8.0, that interface does not exist in any spelling:

    - There is NO `herdr events` CLI subcommand at all. `herdr api` exposes
      `snapshot` and `schema` only, and `herdr events --help` prints the
      top-level help and exits 0, so an unverified call would have looked
      like it worked.
    - `events.wait` and `events.subscribe` are socket methods. For
      `pane_agent_status_changed`, `events.wait` REQUIRES both a pane_id and the
      exact agent_status to wait for, so it can only answer "tell me when THIS
      pane reaches THIS state"; the subscription form requires a pane_id too.
      Both were confirmed live: omitting pane_id returns
      `invalid_request: missing field pane_id`.

    So there is no fleet-wide status stream to wait on, and this subscribes per
    pane to the panes the last snapshot already showed. It is only an
    accelerator: the snapshot reconcile is the authority, a stall is the ABSENCE
    of events and could never be waited for, and every failure here degrades to
    sleeping out the tick. A watchdog whose wait raises stops watching, which is
    the failure this component exists to prevent."""
    deadline = time.monotonic() + timeout_s
    woken = False
    path = socket_path or os.environ.get("HERDR_SOCKET_PATH", "")
    if path and pane_ids:
        woken = _await_status_event(path, pane_ids, deadline)
    remaining = deadline - time.monotonic()
    if not woken and remaining > 0:
        time.sleep(remaining)
    return woken


def _claim_watchdog_lock(path=None):
    """One watchdog, or none.

    Two would each alert on the same condition, and a doubled alert during a
    fleet-wide stall is exactly the noise that gets the watchdog ignored. The
    lock is non-blocking so a second invocation says so and exits, rather than
    waiting on a pane that never ends."""
    path = path or watchdog_path(WATCHDOG_LOCK_FILE)
    handle = open(path, "w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        handle.close()
        raise CrewError(
            "another crew watchdog already holds %s. One is enough, and two "
            "would alert twice on every condition. Use the pane that is "
            "already running it." % path)
    return handle


def _watchdog_interval(flag, value, default):
    if value is None:
        return default
    seconds = int(value)
    if seconds < 1:
        raise CrewError("%s must be at least 1 second, got %r" % (flag, value))
    return seconds


def cmd_watchdog(once=False, tick=None, stall=None):
    """The long-lived loop. One pane, no agent.

    It exists because three failure modes share one signal and none can be
    self-reported: blocked at a permission prompt, stalled while nominally
    working, and dead. A blocked crew member cannot tell anyone it is blocked.

    It cannot be a bare blocking wait, because a stall is the ABSENCE of events
    and a bare wait would never fire for the case it most needs to catch. So the
    tick is bounded and every tick reconciles against a full snapshot, which is
    also what makes a dropped event, a herdr restart and the gap before the
    watchdog started all non-fatal."""
    tick = _watchdog_interval("--tick", tick, WATCHDOG_TICK_SECONDS)
    stall = _watchdog_interval("--stall", stall, WATCHDOG_STALL_SECONDS)
    ensure_crew_dir()
    guard = _claim_watchdog_lock()
    own = calling_pane()
    print("watchdog: tick %ds, stall %ds, own pane %s. Alerts are appended to "
          "%s as kind=alert; the foreman reads them with crew mail unread."
          % (tick, stall, own or "(not in a herdr pane)", MAILBOX))
    if not own:
        print("watchdog: HERDR_PANE_ID is unset, so this cannot tell which pane "
              "is its own. Nothing is excluded from the fleet it watches. Run "
              "it inside a herdr pane.", file=sys.stderr)
    passes = 0
    try:
        while True:
            passes += 1
            panes, alerts, ok = watchdog_pass(own, stall)
            if ok:
                touch_heartbeat(time.time())
                print("watchdog: tick %d, %d crew pane(s), %d new alert(s)"
                      % (passes, len(panes), len(alerts)))
            if once:
                return 0 if ok else 3
            wait_for_status_change(panes, tick)
    finally:
        guard.close()


def _run(args):
    if not args:
        print("usage: crew <doctor|claim-foreman|ls|dispatch|peek|nudge|retire|"
              "mail|watch|watchdog|log|uninstall> [args]", file=sys.stderr)
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
    if verb == "watchdog":
        opts = {"--tick": None, "--stall": None}
        # --once is a bare flag, so it is taken out before take_flag walks the
        # rest looking for `--flag value` pairs.
        rest = [a for a in args[1:] if a != "--once"]
        while rest:
            flag, value, rest = take_flag(rest, tuple(opts))
            if flag is None:
                raise CrewError("unexpected argument: %s" % rest[0])
            opts[flag] = value
        return cmd_watchdog("--once" in args, opts["--tick"], opts["--stall"])
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
    if verb == "watch":
        if len(args) < 2:
            print("usage: crew watch <run-id> [--repo R]", file=sys.stderr)
            return 2
        run_id = require_positional(args[1], "watch run id")
        opts = {"--repo": None}
        rest = args[2:]
        while rest:
            flag, value, rest = take_flag(rest, tuple(opts))
            if flag is None:
                raise CrewError("unexpected argument: %s" % rest[0])
            opts[flag] = value
        return cmd_watch(run_id, opts["--repo"])
    if verb == "watch-run":
        # The loop `crew watch` runs inside the watcher pane. Listed so a human
        # reading a watcher pane can tell what is in it, not to be run by hand.
        if len(args) < 2:
            print("usage: crew watch-run <run-id>   (what crew watch runs in "
                  "the watcher pane)", file=sys.stderr)
            return 2
        return cmd_watch_run(require_positional(args[1], "watch-run run id"))
    if verb == "log":
        if len(args) < 2:
            print("usage: crew log <key> [--project P]", file=sys.stderr)
            return 2
        key = require_positional(args[1], "log key")
        opts = {"--project": None}
        rest = args[2:]
        while rest:
            flag, value, rest = take_flag(rest, tuple(opts))
            if flag is None:
                raise CrewError("unexpected argument: %s" % rest[0])
            opts[flag] = value
        return cmd_log(key, opts["--project"])
    if verb == "uninstall":
        rest = [a for a in args[1:] if a != "--confirm"]
        if rest:
            raise CrewError("unexpected argument: %s" % rest[0])
        return cmd_uninstall("--confirm" in args[1:])
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


def review_findings_name(key):
    """Where a reviewer writes, named after the key it reviews.

    A reviewer now shares one checkout with the implementer, so a generic
    findings filename could overwrite something the implementer wrote. One
    reviewer per key per root is enforced by find_member, so the key makes this
    unique among the sessions that can be in this worktree."""
    return "crew-review-%s.md" % sanitize_name(key)


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


def members_for(snap, root, key):
    """Every crew member holding this root and key, whatever its type.

    Match on the authoritative root, not the repo label: two repos can share a
    basename, and matching on the label alone rejected a legitimate dispatch and
    printed a resume command into the wrong repository.

    A `setup` pane is never included. An orphaned setup pane would otherwise
    make every retry of that key report a duplicate forever, and it carries no
    branch token so the resume command it printed was empty."""
    wanted = sanitize_name(key)
    return [member for member in crew_members(snap)
            if member["type"] != "setup" and member["root"] == root
            and member["key"] == wanted]


def find_member(snap, root, key, ctype):
    """The crew member whose identity is exactly (root, key, ctype), or None.

    Identity includes the TYPE so a reviewer can coexist with the implementer
    whose work it reviews. Keyed on (root, key) alone, a reviewer dispatch at a
    live implementer's key returned exit 5, the foreman reported the resume
    command that line prints, and that command resumed the implementer, so the
    review never happened.

    The type is a required argument rather than an optional one because every
    other reader of this identity, `crew ls` and `crew retire`, now has to agree
    with it: a caller that means "does anything hold this key" must say so by
    calling members_for, not by leaving the type off here."""
    for member in members_for(snap, root, key):
        if member["type"] == ctype:
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
    setup_pane = DRY_PANE if split is None else response_pane_id(
        split, ("result", "pane", "pane_id"), "pane split")
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
    pane = DRY_PANE if tab is None else response_pane_id(
        tab, ("result", "root_pane", "pane_id"), "tab create")

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
        # A reviewer is dispatched INTO the worktree of the member it reviews,
        # so this is the one type whose session shares a checkout with another
        # live session. The findings file is named here so it cannot collide
        # with the work being reviewed, and "do not change code" is the whole
        # reason two sessions in one checkout is tolerable.
        "reviewer": ("This worktree belongs to the member whose work you are "
                     "reviewing and may still have a live session in it, so do "
                     "not change code and run no git command that writes. "
                     "Write your findings to %s in the worktree, and report "
                     "done naming that file." % review_findings_name(key)),
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


def dispatch_reviewer(key, repo, repo_root, workspace, model, held, snap):
    """Dispatch a reviewer into the worktree of the member it reviews.

    This is NOT a setup path. The worktree already exists, and the point of a
    reviewer is to read what is in it, so going through /start-ticket again
    would spend a second paid Opus setup session and ask the human to answer the
    same prompts to reach the tree they already have. Working around the old
    exit 5 with a slug was worse: that branches off HEAD, so the reviewer was
    told not to change code in a worktree that did not contain the work.

    A key with no member has nothing to review, so it is refused rather than
    given a worktree of its own.

    The worktree is derived from the subject's tokens, not from its cwd, and it
    has to be there: the tokens are the authoritative record and a reviewer sent
    to a path that does not exist would fail inside `tab create`, after herdr
    had been asked to make something."""
    subject = held[0] if held else None
    if subject is None:
        raise CrewError(
            "nothing to review: no crew member holds %s in %s. A reviewer reads "
            "the worktree of the member whose work it reviews rather than "
            "creating one, so dispatch that member first, or name the key it was "
            "dispatched on. Nothing has been created." % (key, repo))
    worktree = subject["worktree"]
    if not worktree:
        raise CrewError(
            "crew member %s/%s in pane %s carries no branch token, so crew "
            "cannot derive the worktree for a reviewer to read. Nothing has "
            "been created." % (repo, subject["key"], subject["pane"]))
    if not os.path.isdir(worktree):
        raise CrewError(
            "the worktree %s recorded by %s/%s in pane %s does not exist, so "
            "there is nothing for a reviewer to read there. Nothing has been "
            "created." % (worktree, repo, subject["key"], subject["pane"]))
    print("reviewing in the existing worktree %s, held by the %s in pane %s. "
          "No setup pane is opened and no worktree is created."
          % (worktree, subject["type"], subject["pane"]))
    return _complete_dispatch(key, "reviewer", repo, repo_root, workspace,
                              model, worktree, snap)


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

        held = members_for(snap, repo_root, key)
        # Identity is (root, key, type), but only a REVIEWER may coexist with
        # another type on one key. Two writers in one worktree is the hazard,
        # and the reviewer's contract not to change code is the single exception
        # to it, so a reviewer is refused only by another reviewer while every
        # other type is refused by anything already holding the key. Letting a
        # second writer through would also send it down the setup path, which
        # spends another paid setup session on a worktree that already exists.
        if ctype == "reviewer":
            existing = find_member(snap, repo_root, key, "reviewer")
        else:
            existing = held[0] if held else None
        if existing is not None:
            print("already dispatched: %s/%s as %s in pane %s (%s). "
                  "Resume with: cd %s && claude --continue%s"
                  % (repo, existing["key"], existing["type"],
                     existing["pane"], existing["bucket"],
                     existing["worktree"],
                     "" if existing["type"] == ctype else
                     " Only a reviewer shares a key with another type, because "
                     "only a reviewer is contracted not to write, so this %s is "
                     "declined too." % ctype))
            # Contained. This notification makes a second herdr round trip and
            # writes to disk, so a socket failure or a mailbox OSError turned
            # the documented exit 5 into exit 3 after the refusal had already
            # printed, and the foreman then followed the exit 3 branch. The
            # refusal's exit code must not depend on a side effect.
            try:
                mail_send(key, repo, "duplicate",
                          "dispatch declined, a live session already holds "
                          "this key")
            except (CrewError, HerdrError, OSError, ValueError) as exc:
                print("the mailbox was not notified of this refusal (%s), so "
                      "crew mail unread will not show it. The dispatch was "
                      "still declined." % exc, file=sys.stderr)
            return 5

        if ctype == "reviewer":
            # No artifact is read and none is deleted on this path: the file is
            # keyed on the key alone, so the implementer's setup may still be
            # legitimately pending, and consuming it would cost that dispatch
            # its paid setup session.
            return dispatch_reviewer(key, repo, repo_root, workspace, model,
                                     held, snap)

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
            # The type is named because one key can hold two members, an
            # implementer and the reviewer reading its worktree, and without it
            # the ambiguity message lists two lines that differ only in the
            # pane id it is telling the human to use instead.
            labelled.append(("member", member,
                             "the %s crew member %s/%s in pane %s"
                             % (member["type"], member["repo"], member["key"],
                                member["pane"])))
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
        what = "the %s crew member %s/%s" % (payload["type"], payload["repo"],
                                             payload["key"])
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


# Aliases for the one set defined with the record contract above. Two names for
# one concept arrived from two parallel workstreams, and a second `record_kind`
# alongside them silently shadowed the first: its version did not collapse the
# value, so a newline in `kind` could forge a line in the foreman's terminal
# again, a defect the first version had already closed. One implementation only.
RECORD_KIND_REPORT = KIND_REPORT
RECORD_KIND_ACK = KIND_ACK
RECORD_KIND_ALERT = KIND_ALERT


def mailbox_entries():
    """Every readable mailbox record, or an empty list.

    Deliberately quiet about failure, because its callers must not fail with it:
    `crew ls` reports the fleet, which does not come from the mailbox at all, and
    an unreadable mailbox must not turn the one surface that must never lie about
    being empty into an error. The cost is that watchers then read as having
    written nothing, which reads as unfinished, and that is the safe direction:
    it can never propose closing a watcher still following a run."""
    if not os.path.exists(MAILBOX):
        return []
    try:
        with _locked(MAILBOX, "r") as handle:
            entries, _ = read_entries(handle.readlines())
    except (IOError, OSError):
        return []
    return entries


def append_alert_record(state, msg, key, repo, branch, allowed):
    """Append one `alert` record to the mailbox and return its seq.

    An alert is written ABOUT a pane, by something outside it, so it carries its
    own vocabulary rather than MAIL_STATES. That separation is the point: a
    session cannot report that it is blocked, or that its CI went red after it
    finished, and widening the states a crew member may claim about ITSELF would
    undo the one check that makes a report trustworthy.

    Same care as a report, for the same measured reasons. The seq is allocated
    under the same flock, so a watcher writing while a crew member reports cannot
    collide. Every string field is collapsed to one line, because json.dumps
    escapes a newline into valid JSONL that the foreman's terminal then renders
    as a second line crew appears to have printed. And the state is validated
    against a closed vocabulary, because it is the one field read as a machine
    value rather than as prose."""
    state = one_line(state)
    if state not in allowed:
        raise CrewError("%r is not a state crew alerts with; expected one of %s"
                        % (state, ", ".join(allowed)))
    if not key:
        raise CrewError("an alert record needs a key")
    ensure_crew_dir()
    record = {
        "v": 1,
        "kind": RECORD_KIND_ALERT,
        "ts": int(time.time()),
        "key": sanitize_name(key),
        "repo": repo or "",
        "pane": calling_pane(),
        # Branch, never a path: these records are permanent and get digested
        # into a git-tracked log, where an absolute path would leak a username
        # and directory layout into repository history.
        "branch": branch or "",
        "state": state,
        "msg": msg,
    }
    for field, value in list(record.items()):
        if isinstance(value, str):
            record[field] = one_line(value)
    if DRY_RUN:
        print("would append to %s: %s"
              % (MAILBOX, json.dumps(record, sort_keys=True)))
        return 0
    return append_record(lambda _entries: record)["seq"]


WATCH_TYPE = "watch"
WATCH_KEY_PREFIX = "watch-"
RUN_ID_RE = re.compile(r"^[0-9]{1,20}$")
WATCH_STATES = ("ci-passed", "ci-failed", "ci-inconclusive", "watch-failed")
CI_TERMINAL_STATUSES = ("completed",)
CI_PASSED_CONCLUSIONS = ("success",)
CI_FAILED_CONCLUSIONS = ("failure", "cancelled", "timed_out", "startup_failure",
                         "action_required", "stale")
GH_RUN_FIELDS = "status,conclusion,workflowName,headBranch"
WATCH_POLL_SECONDS = 20
WATCH_BUDGET_SECONDS = 6 * 60 * 60
WATCH_POLL_FAILURES = 5
WATCH_STARTED = "crew watch: following run"
WATCH_START_TIMEOUT_MS = "20000"


def watch_key(run_id):
    """The mailbox key a watcher reports under.

    A run id is not a crew key and a watcher is not a crew member, so it gets a
    key of its own shape. Prefixed rather than bare because `crew log` and the
    roster both match on keys, and a run id of digits alone would sanitise into
    something that reads like a ticket."""
    return sanitize_name(WATCH_KEY_PREFIX + str(run_id))


def watch_run_id(key):
    """The run id a watcher's key records, or an empty string."""
    key = one_line(key)
    return key[len(WATCH_KEY_PREFIX):] if key.startswith(WATCH_KEY_PREFIX) else ""


def ci_outcome(status, conclusion):
    """(state, sentence) once a CI run has reached a terminal state, else None.

    Every terminal outcome is reported, not only the one that matches success.
    A watcher that recognises `success` alone goes silent through a failure, and
    silence reads as still running, which is the whole failure mode this verb
    exists to remove.

    Terminal is decided by TWO independent signals, because either alone goes
    stale. A status crew has not seen before still terminates once GitHub sets a
    conclusion; a run `completed` with no conclusion at all still terminates on
    the status. And a conclusion crew does not classify is reported verbatim as
    inconclusive rather than dropped, so a value GitHub adds later cannot make
    this go quiet either. Being wrong about which BUCKET an outcome falls in is
    recoverable; saying nothing is not."""
    status = one_line(status).lower()
    conclusion = one_line(conclusion).lower()
    if status not in CI_TERMINAL_STATUSES and not conclusion:
        return None
    if conclusion in CI_PASSED_CONCLUSIONS:
        return "ci-passed", "concluded %s" % conclusion
    if conclusion in CI_FAILED_CONCLUSIONS:
        return "ci-failed", "concluded %s" % conclusion
    return "ci-inconclusive", (
        "reached %s with conclusion %s, which crew does not classify as passed "
        "or failed: look at the run"
        % (status or "no status", conclusion or "none"))


def _gh_run(run_id):
    """One `gh run view` poll, as (fields, error).

    Never raises. This is called from a loop whose only obligation is to end
    with a record in the mailbox, so a failure has to be a value the loop can
    count rather than an exception that ends it without writing."""
    argv = ["gh", "run", "view", run_id, "--json", GH_RUN_FIELDS]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        return None, "gh is not runnable: %s" % exc
    if proc.returncode != 0:
        return None, "gh run view %s exited %d: %s" % (
            run_id, proc.returncode,
            one_line(proc.stderr or proc.stdout)[:160])
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None, "gh run view %s returned non-JSON: %s" % (
            run_id, one_line(proc.stdout)[:160])
    if not isinstance(data, dict):
        return None, "gh run view %s returned %s, not an object" % (
            run_id, type(data).__name__)
    return data, None


def calling_pane_tokens():
    """This pane's own tokens, or {} when herdr cannot be reached.

    Tolerant on purpose, and the callers are the reason. A watcher must write its
    record whether or not herdr answers, because a watcher that goes quiet is the
    failure this design is removing; and `crew log` must work after the fleet is
    gone, which is when work usually gets logged."""
    pane_id = calling_pane()
    if not pane_id:
        return {}
    try:
        return _pane_tokens(pane_id)
    except (CrewError, HerdrError, OSError):
        return {}


def tokens_in(snap, pane_id):
    """One pane's tokens out of a snapshot already in hand."""
    for pane in snap["panes"]:
        if pane["pane_id"] == pane_id:
            return pane.get("tokens") or {}
    return {}


def crew_pane_refusal(tokens, what):
    """Why a crew member's own pane may not run this verb, or None.

    The guard hook is the enforcement, and it can act only on the commands its
    own table names, so a verb that table does not list is not denied at all.
    These three are new, so this is the backstop until the table catches up.

    calling_pane is spoofable, which is harmless here: this check can only ever
    prevent an action, so a spoofed value cannot cause one that would not
    otherwise happen."""
    if (tokens or {}).get("crew") != "true":
        return None
    return ("this pane carries crew tokens, so it is a crew member, and %s is "
            "the foreman's. Nothing has been done. Report what you need with "
            "crew mail send and let the foreman or the human act." % what)


def cmd_watch_run(run_id, poll=WATCH_POLL_SECONDS, budget=WATCH_BUDGET_SECONDS):
    """Follow a CI run from a pane with no agent and write its outcome to the
    mailbox. The loop `crew watch` starts; not something to run by hand.

    EVERY exit from here writes a record, including the exits that are the
    watcher's OWN failure: gh unusable, and the budget spent. The foreman learns
    a run went red by reading the mailbox, so a watcher that stops without
    writing is indistinguishable from one still watching. That is also why the
    failure state is `watch-failed` rather than `ci-failed`: crew stopped
    watching is a different claim from the run having failed, and a watcher that
    conflated them would report a red PR that is not red."""
    if not RUN_ID_RE.match(str(run_id)):
        raise CrewError("%r is not a run id" % run_id)
    tokens = calling_pane_tokens()
    key = watch_key(run_id)
    repo = tokens.get("repo", "")
    print("%s %s" % (WATCH_STARTED, run_id))
    sys.stdout.flush()

    deadline = time.time() + budget
    failures = []
    while time.time() < deadline:
        data, error = _gh_run(run_id)
        if error:
            failures.append(error)
            print("crew watch: %s" % error, file=sys.stderr)
            if len(failures) >= WATCH_POLL_FAILURES:
                return _watch_gave_up(
                    run_id, key, repo, tokens.get("branch", ""),
                    "gh could not be asked about it %d times in a row; last "
                    "error: %s" % (len(failures), failures[-1]))
        else:
            failures = []
            outcome = ci_outcome(data.get("status"), data.get("conclusion"))
            if outcome:
                state, sentence = outcome
                branch = one_line(data.get("headBranch")) or tokens.get("branch", "")
                seq = _record_outcome(
                    state,
                    "run %s (%s on %s) %s"
                    % (run_id, one_line(data.get("workflowName"))
                       or "an unnamed workflow", branch or "an unnamed branch",
                       sentence),
                    key, repo, branch)
                if seq is None:
                    return 3
                print("crew watch: run %s %s, recorded at mail seq %s"
                      % (run_id, state, seq))
                return 0
        time.sleep(poll)
    return _watch_gave_up(
        run_id, key, repo, tokens.get("branch", ""),
        "crew stopped watching after %d seconds without a terminal state; the "
        "run may still be going" % budget)


def _record_outcome(state, msg, key, repo, branch):
    """Write one watcher record, or say in the pane that it could not.

    The mailbox is the only channel a watcher has, so a failure to write it has
    nowhere to be reported except this pane's own text. Printing the outcome here
    is what stops the whole watch being lost: the pane is then the record, and it
    says so, rather than the run's outcome disappearing with the exit code."""
    try:
        return append_alert_record(state, msg, key, repo, branch, WATCH_STATES)
    except (CrewError, HerdrError, OSError, ValueError) as exc:
        print("WATCH RECORD LOST: %s could not be written to the mailbox (%s). "
              "The outcome was: %s. crew mail unread will not show it, so this "
              "pane is the only record of it." % (state, exc, msg),
              file=sys.stderr)
        return None


def _watch_gave_up(run_id, key, repo, branch, why):
    """Record that the WATCHER failed, not that the run did."""
    seq = _record_outcome("watch-failed", "run %s: %s" % (run_id, why),
                          key, repo, branch)
    print("crew watch: gave up on run %s, recorded at mail seq %s"
          % (run_id, seq), file=sys.stderr)
    return 3


def watch_command(run_id):
    """The command the watcher pane runs.

    THIS interpreter and THIS script, both by absolute path, rather than `crew`
    from PATH. `~/.local/bin/crew` is a symlink into a git worktree and need not
    be the version that opened the pane: a watcher whose `watch-run` verb does
    not exist there prints one unknown-verb line and then sits looking exactly
    like a watcher, which is the silence this verb exists to remove. The
    interpreter is named for the same reason the path is: it does not depend on
    the executable bit surviving a re-stow."""
    return "%s %s watch-run %s" % (shlex.quote(sys.executable),
                                   shlex.quote(os.path.abspath(__file__)),
                                   shlex.quote(str(run_id)))


def response_tab_id(payload):
    """The tab id a `tab create` response carries, or "" when it cannot be read.

    Tolerant where response_pane_id is fatal, and for the opposite reason: the
    pane id is what everything afterwards acts on, while this is only what the
    human is told to retire with. By the time it is read the tab already exists,
    so raising here would leak the very tab the id names."""
    node = payload
    for step in ("result", "tab", "tab_id"):
        node = node.get(step) if isinstance(node, dict) else None
    return node if isinstance(node, str) and node.strip() else ""


def watchers(snap):
    """Every watcher pane. A watcher is never a crew member.

    It holds no agent for its whole life, so as a member it would sit in the
    `recover` bucket forever and a healthy fleet would read as needing recovery.
    crew_members skips this type for that reason and `crew ls` gives watchers
    their own section.

    The type is read off the RAW token here, exactly as crew_members skips it,
    rather than off the version-checked type: a watcher tagged by an older crew
    would otherwise be skipped as a member AND missed as a watcher, which is the
    invisible-pane defect this design has already paid for once."""
    out = []
    for pane in snap["panes"]:
        tokens = pane.get("tokens") or {}
        if tokens.get("crew") != "true" or tokens.get("type") != WATCH_TYPE:
            continue
        key = tokens.get("key", "")
        out.append({
            "pane": pane["pane_id"],
            "tab": pane.get("tab_id") or "",
            "key": key,
            "run": watch_run_id(key),
            "repo": tokens.get("repo", "(no repo)"),
            "root": tokens.get("root", ""),
            "agent": pane_has_agent(snap, pane["pane_id"]),
        })
    out.sort(key=lambda w: (w["repo"], w["run"], w["pane"]))
    return out


def watcher_rows(watch_panes, entries):
    """Each watcher plus the outcome it has already written, if any.

    The mailbox is the ONLY thing that can say a watcher has finished. Its pane
    holds no agent, so there is no status to read, and the snapshot carries no
    foreground command, so nothing in herdr tells a shell still polling apart
    from one that exited. A watcher with no record is therefore reported as
    unfinished rather than as retirable: proposing the close of a watcher still
    following a run is the one wrong answer here.

    Only `alert` records count. A crew member's own report can carry the same key
    only by forgery, and an ack carries no state at all, so filtering by kind is
    what stops either being read as an outcome crew measured."""
    latest = {}
    for record in entries:
        if record_kind(record) != RECORD_KIND_ALERT:
            continue
        key = one_line(record.get("key"))
        if key not in latest or record["seq"] > latest[key]["seq"]:
            latest[key] = record
    rows = []
    for watcher in watch_panes:
        row = dict(watcher)
        record = latest.get(one_line(watcher["key"]))
        row["outcome"] = one_line(record.get("state")) if record else ""
        row["seq"] = record["seq"] if record else 0
        rows.append(row)
    return rows


def cmd_watch(run_id, repo_arg):
    """Open a pane with NO agent that follows a CI run and writes its outcome to
    the mailbox, so the foreman learns a PR went red without polling for it.

    A watcher is not a crew type. It has no agent, so it cannot occupy a bucket
    in the load report, and it is not work the human has to review.

    It gets its own TAB rather than a split of the caller's, and that is not
    cosmetic: a pane split into the foreman's tab could never be closed without
    closing the foreman's tab, so nothing could ever retire it. In a tab crew
    labelled and owns, `crew retire <tab id>` closes both.

    Everything that could refuse happens before anything is created. A pane
    created and then abandoned is the defect this codebase has paid for twice:
    an untagged pane nothing can see, and a pane running a command that was
    never there."""
    if not RUN_ID_RE.match(str(run_id)):
        raise CrewError(
            "%r is not a run id. crew watch takes the numeric id `gh run list` "
            "prints, not a URL and not a branch. The id is interpolated into a "
            "command that runs in another pane's shell, so anything else is "
            "refused rather than quoted and hoped for. Nothing has been created."
            % run_id)
    workspace = os.environ.get("HERDR_WORKSPACE_ID")
    if not workspace:
        raise CrewError("watch must run inside a herdr pane")
    ok, detail = _probe(["gh", "--version"])
    if not ok:
        raise CrewError(
            "crew watch needs the gh CLI and it is not usable here (%s). "
            "Nothing has been created: a pane opened to run a command that is "
            "not there would sit at a prompt looking exactly like a watcher, "
            "which is the silence this verb exists to remove." % detail)

    repo_root, repo = resolve_repo(repo_arg)
    key = watch_key(run_id)
    # Before the tab, the pane and the tokens exist. herdr truncates a token
    # value at 80 characters silently, and tag_pane's own check raises only after
    # there is a pane to leak.
    for name, value in (("root", repo_root), ("repo", repo), ("key", key)):
        problem = token_too_long(name, value)
        if problem:
            raise CrewError(
                "%s. Nothing has been created. Watch from a shorter path, or "
                "pass --repo pointing at one." % problem)

    defs = schema_defs()
    assert_schema_declares(defs)
    snap = snapshot()
    assert_snapshot_shape(snap, defs)
    refusal = crew_pane_refusal(tokens_in(snap, calling_pane()), "crew watch")
    if refusal:
        raise CrewError(refusal)
    for watcher in watchers(snap):
        if watcher["run"] == str(run_id):
            # Not an error: what was asked for is already in place. A second
            # watcher on one run would write the outcome twice and the foreman
            # would read one run going red as two.
            print("run %s is already being watched in pane %s. Nothing has "
                  "been created. If that watcher is spent, retire it first: "
                  "crew retire %s"
                  % (run_id, watcher["pane"], watcher["tab"] or watcher["pane"]))
            return 0

    tab = herdr("tab", "create", "--workspace", workspace,
                "--label", crew_tab_label(repo, key), "--cwd", repo_root,
                "--no-focus")
    pane = DRY_PANE if tab is None else response_pane_id(
        tab, ("result", "root_pane", "pane_id"), "tab create")
    # The TAB id, because a watcher is not a crew member and `crew retire` can
    # only resolve it by the tab crew labelled. Printing the label, or the pane
    # id, is a command the human cannot run.
    tab_id = response_tab_id(tab)
    retire = ("crew retire %s" % tab_id) if tab_id \
        else "crew ls, which names the handle to retire it with"
    # Tag before the command runs, exactly as dispatch tags before agent start:
    # an untagged pane is invisible to `crew ls` and nothing can retire it.
    tag_pane(pane, key, repo, WATCH_TYPE, "", repo_root)
    try:
        herdr("pane", "run", pane, watch_command(run_id))
        # Confirmed, not assumed. Text sent to a pane whose shell has not
        # settled is silently dropped, which is measured behaviour here for
        # `agent prompt` and would leave a tagged pane that watches nothing.
        if not DRY_RUN:
            herdr("pane", "wait-output", pane, "--match",
                  "%s %s" % (WATCH_STARTED, run_id),
                  "--timeout", WATCH_START_TIMEOUT_MS)
    except HerdrError as exc:
        print("WATCH UNCONFIRMED: the pane exists and is tagged, but the "
              "watcher did not report itself as started (%s), so nothing may be "
              "following run %s. Do not read the mailbox's silence as a green "
              "run. Retire it and try again with %s"
              % (exc, run_id, retire), file=sys.stderr)
        return 3
    print("watching run %s in pane %s (no agent, so it is not crew and not "
          "load). Its outcome lands in the mailbox whatever it is, pass or "
          "fail, so read it with crew mail unread. Retire it when it is spent "
          "with %s" % (run_id, pane, retire))
    return 0


PROJECTS_DIR = os.path.expanduser("~/Documents/Work/projects")
PROJECT_LOG = "log.md"
LOG_DONE = "### Done"
LOG_FOLLOWUPS = "### Follow-ups"
# Order matters: it is the order the log skill documents, and a digest that
# reordered the sections of an entry it is only appending to would rewrite it.
LOG_SECTION_ORDER = (LOG_DONE, LOG_FOLLOWUPS)
LOG_STATE_SECTION = {"done": LOG_DONE, "needs-input": LOG_FOLLOWUPS}
# Both spellings. The heading this writes is the bracketed one the log skill
# mandates, because its own audit greps for it, but existing logs hold entries
# written the other way and a reader that missed those would write a second
# heading for a date the file already has.
LOG_DATE_RE = re.compile(r"^##\s+\[?(\d{4}-\d{2}-\d{2})")


def log_date(ts):
    """A record's date as the log spells it. Local time, because the log is a
    human's day rather than a UTC one."""
    return time.strftime("%Y-%m-%d", time.localtime(int(ts)))


def log_marker(seq):
    """What makes an entry re-runnable.

    The mailbox is never pruned and this verb holds no cursor of its own, so
    `crew log` will be handed records it has already written. The marker is what
    an existing entry is searched for, which is why it carries the seq and why it
    ends in a word: `seq 1 on` cannot match inside `seq 12 on`."""
    return "crew mail seq %d on" % seq


def log_bullet(record):
    """One project-log bullet for one report record, or None for a record that
    is not project work.

    `duplicate` is not logged. It records a dispatch crew DECLINED, so a log
    entry claiming it as work would be false. It is counted on stdout instead,
    because a digest that drops records without saying so is how a digest starts
    lying.

    msg is collapsed on the way IN as well as on the way out. Records predating
    that rule are still in the mailbox and nothing stops one being edited in by
    hand, and a newline here would inject markdown into a git-tracked file."""
    section = LOG_STATE_SECTION.get(one_line(record.get("state")))
    if section is None:
        return None
    where = one_line(record.get("repo")) or "an unrecorded repo"
    branch = one_line(record.get("branch"))
    if branch:
        where = "%s (branch %s)" % (where, branch)
    return {
        "section": section,
        "marker": log_marker(record["seq"]),
        "line": "%screw %s in %s %s: %s (%s %s)" % (
            "- " if section == LOG_DONE else "- [ ] ",
            one_line(record.get("key")) or "an unrecorded key", where,
            "reported done" if section == LOG_DONE else "needs input",
            one_line(record.get("msg")) or "no message",
            log_marker(record["seq"]), log_date(record.get("ts") or 0)),
    }


def _log_entry_index(lines, date):
    for index, line in enumerate(lines):
        found = LOG_DATE_RE.match(line)
        if found and found.group(1) == date:
            return index
    return None


def _log_entry_end(lines, index):
    for after in range(index + 1, len(lines)):
        if lines[after].startswith("## "):
            return after
    return len(lines)


def _log_headings_ascend(lines):
    dates = [found.group(1) for found in
             (LOG_DATE_RE.match(line) for line in lines) if found]
    return len(dates) >= 2 and dates == sorted(dates)


def _log_entry_block(date, bullets):
    block = ["## [%s]" % date]
    for section in LOG_SECTION_ORDER:
        chosen = [b["line"] for b in bullets if b["section"] == section]
        if not chosen:
            # Never a hollow header. The log skill forbids one, and an empty
            # section reads as work that produced nothing.
            continue
        block += ["", section] + chosen
    return block


def _log_insert_entry(lines, block):
    """Put a new entry where this file puts its newest one.

    The two conventions in use disagree: the project logs in this wiki say
    newest first and the log skill describes appending at the end. Writing at
    the wrong end of either produces a log that reads backwards, so the FILE
    decides. Headings in ascending date order mean append; anything else,
    including a file with one entry or none, means insert above the first entry,
    which is what the logs actually on disk do."""
    first = next((index for index, line in enumerate(lines)
                  if line.startswith("## ")), None)
    if first is None or _log_headings_ascend(lines):
        head = list(lines)
        while head and not head[-1].strip():
            head.pop()
        return (head + [""] + block) if head else block
    head = lines[:first]
    while head and not head[-1].strip():
        head.pop()
    return (head + ([""] if head else []) + block + [""] + lines[first:])


def _log_fold_section(lines, index, section, chosen):
    """Add bullets to one section of an existing entry, in place.

    Existing lines are never rewritten, only moved further down the file. The
    section is looked for INSIDE this entry's span, so a `### Done` belonging to
    another date cannot swallow today's bullets."""
    end = _log_entry_end(lines, index)
    at = next((i for i in range(index + 1, end)
               if lines[i].strip() == section), None)
    if at is None:
        tail = end
        while tail - 1 > index and not lines[tail - 1].strip():
            tail -= 1
        return lines[:tail] + ["", section] + chosen + lines[tail:]
    stop = at + 1
    while stop < end and not lines[stop].startswith("### "):
        stop += 1
    while stop - 1 > at and not lines[stop - 1].strip():
        stop -= 1
    return lines[:stop] + chosen + lines[stop:]


def merge_log_entry(text, date, bullets):
    """The log file's text with these bullets folded into `date`'s entry, as
    (text, number added).

    Append-only in the strict sense: no existing line is rewritten, reordered or
    dropped, no heading is duplicated, and a bullet whose marker is already in
    the file is not added again. Nothing added means the ORIGINAL text is
    returned untouched, so the caller can tell there is nothing to write rather
    than rewriting a git-tracked file to no effect."""
    lines = text.splitlines()
    fresh = [b for b in bullets
             if not any(b["marker"] in line for line in lines)]
    if not fresh:
        return text, 0
    index = _log_entry_index(lines, date)
    if index is None:
        lines = _log_insert_entry(lines, _log_entry_block(date, fresh))
    else:
        for section in LOG_SECTION_ORDER:
            chosen = [b["line"] for b in fresh if b["section"] == section]
            if chosen:
                lines = _log_fold_section(lines, index, section, chosen)
    return "\n".join(lines).rstrip("\n") + "\n", len(fresh)


def infer_projects(key):
    """Every project whose own README or log names this key."""
    try:
        names = sorted(os.listdir(PROJECTS_DIR))
    except OSError as exc:
        raise CrewError(
            "cannot read %s (%s), so crew cannot tell which project this key "
            "belongs to. Name it with --project." % (PROJECTS_DIR, exc))
    needles = set([key, key.upper()])
    hits = []
    for name in names:
        directory = os.path.join(PROJECTS_DIR, name)
        if not os.path.isdir(directory):
            continue
        for filename in ("README.md", PROJECT_LOG):
            try:
                with open(os.path.join(directory, filename)) as handle:
                    body = handle.read()
            except (IOError, OSError):
                continue
            if any(needle in body for needle in needles):
                hits.append(name)
                break
    return hits


def project_log_path(key, project):
    """The project log this key's reports belong in.

    Named or inferred, and never created. These logs live in a git-tracked wiki
    repo, so a project directory crew invented would be committed as a project
    that does not exist.

    Inference is a search for the key in each project's own README and log, which
    is how the log skill says to map a ticket to a project. More than one match,
    or none, refuses and names what it found rather than picking: writing the
    right words into the wrong project's log is worse than writing nothing."""
    if project:
        directory = os.path.join(PROJECTS_DIR, project)
        if not os.path.isdir(directory):
            raise CrewError(
                "--project %s is not a directory under %s, and crew will not "
                "create one: these logs are git tracked, so an invented project "
                "would be committed as a project that does not exist."
                % (project, PROJECTS_DIR))
        return os.path.join(directory, PROJECT_LOG)
    hits = infer_projects(key)
    if len(hits) == 1:
        return os.path.join(PROJECTS_DIR, hits[0], PROJECT_LOG)
    if not hits:
        raise CrewError(
            "no project under %s names %s in its README or log, so crew cannot "
            "tell where this belongs. Name it with --project <name>."
            % (PROJECTS_DIR, key))
    raise CrewError(
        "%d projects name %s: %s. Crew will not pick one, because the right "
        "words in the wrong project's log is worse than none. Name it with "
        "--project <name>." % (len(hits), key, ", ".join(hits)))


def describe_worktree(key, records):
    """Where this key's work is, from the crew member's own tokens if a pane
    still holds them, and from the records if not.

    Never from cwd. This runs in the foreman's pane, whose checkout is a
    different one entirely, so cwd would name the wrong tree in a git-tracked
    file. The path is derived from the root and branch tokens, the same
    derivation every other reader uses, which is why the tokens store those two
    rather than a path herdr would truncate.

    The records are the ordinary fallback rather than the edge case: work is
    logged when it FINISHES, which is exactly when the pane has been retired.
    Which source produced the answer is named, so a reader can tell."""
    try:
        for member in crew_members(snapshot()):
            if member["key"] == key and member["type"] != "setup":
                return ("%s, branch %s, worktree %s (from the live pane %s)"
                        % (member["repo"], member["branch"] or "(none)",
                           member["worktree"] or "(none)", member["pane"]))
    except (CrewError, HerdrError, OSError):
        pass
    last = records[-1]
    return ("%s, branch %s (from the records: no live pane holds this key, so "
            "crew cannot derive the worktree path)"
            % (one_line(last.get("repo")) or "(no repo)",
               one_line(last.get("branch")) or "(none)"))


def cmd_log(key, project):
    """Digest one key's own reports into that project's log.md.

    Reports only, and only this key's. An ack is the foreman's bookkeeping and an
    alert is written ABOUT a pane from outside it, so neither is work this crew
    member did: a digest that mixed them would credit a CI failure, or the
    foreman's own cursor, to the crew member as something it landed.

    The file is written by replacing it whole from text this verb built by
    APPENDING to what was there. A partial write into a git-tracked log is worse
    than no write, so it goes through a temporary file and one rename."""
    refusal = crew_pane_refusal(calling_pane_tokens(), "crew log")
    if refusal:
        raise CrewError(refusal)
    wanted = sanitize_name(key)
    entries = mailbox_entries()
    records = sorted([e for e in entries
                      if record_kind(e) == RECORD_KIND_REPORT
                      and one_line(e.get("key")) == wanted],
                     key=lambda e: e["seq"])
    if not records:
        print("nothing to log: the mailbox holds %d record(s) and none is a "
              "report from %s. Acks, alerts and other keys are deliberately not "
              "digested here." % (len(entries), wanted))
        return 0

    bullets = []
    declined = 0
    for record in records:
        bullet = log_bullet(record)
        if bullet is None:
            declined += 1
        else:
            bullets.append(bullet)
    path = project_log_path(wanted, project)
    print("%s: %d report(s), %s" % (wanted, len(records),
                                    describe_worktree(wanted, records)))
    if declined:
        print("%d declined dispatch(es) not logged: a dispatch crew refused is "
              "not work this key did." % declined)
    if not bullets:
        return 0

    try:
        with open(path) as handle:
            before = handle.read()
    except IOError:
        before = ""
    after, added = merge_log_entry(before, log_date(time.time()), bullets)
    if not added:
        print("nothing new: every one of those reports is already in %s, "
              "matched by its mail seq. Nothing written." % path)
        return 0
    if DRY_RUN:
        print("would write %d bullet(s) to %s:" % (added, path))
        for bullet in bullets:
            print("  %s" % bullet["line"])
        return 0
    mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else None
    temporary = path + ".crew-tmp"
    with open(temporary, "w") as handle:
        handle.write(after)
        handle.flush()
        os.fsync(handle.fileno())
    if mode is not None:
        os.chmod(temporary, mode)
    os.replace(temporary, path)
    print("wrote %d bullet(s) to %s under %s. Re-running adds nothing: each "
          "bullet carries its mail seq and an entry already holding it is left "
          "alone." % (added, path, log_date(time.time())))
    return 0


# Every token tag_pane writes. Clearing a subset would leave a pane that still
# reads as crew's to something, which is the state this verb exists to remove.
CREW_TOKEN_NAMES = ("crew", "v", "key", "repo", "type", "branch", "root",
                    "dispatched")
# The skills crew installs. `herdr` is deliberately not one: it documents the
# CLI and is useful with no crew in the picture, so uninstalling crew must not
# take it.
CREW_SKILLS = ("foreman", "crew-member", "start-crew")
SKILLS_DIR = os.path.expanduser("~/.claude/skills")
WORKTREE_MARK = os.path.join(".claude", "worktrees")


def _worktree_note(path):
    """Why a path resolving inside a git worktree matters here.

    `crew` and the guard are symlinks into a worktree of the dotfiles repo, so
    `git worktree remove` takes out the CLI and the only enforcement of the crew
    boundary at once, silently, with nothing failing until a crew member is
    already unguarded. This verb exists partly so that is read here rather than
    discovered afterwards."""
    if WORKTREE_MARK in path:
        return ("resolves inside a git worktree, so removing that worktree "
                "takes it out silently")
    return ""


def uninstall_blockers(snap):
    """Why uninstall must not proceed at all.

    A live crew member first. Uninstall removes the mailbox its report lands in,
    the `crew` it reports THROUGH, and the guard that is the only thing stopping
    it dispatching paid sessions. Doing that under a running session loses the
    report and leaves it unable to send another.

    A watcher pane blocks for a mechanical reason: its shell keeps polling and
    writes through an appender that CREATES ~/.crew when it is missing, so a
    watcher would quietly rebuild the directory this verb just deleted. Nothing
    in the snapshot says whether that shell is still running, so presence is
    refused on rather than liveness, and the remedy is one `crew retire`.

    A crew member's own pane is refused outright. This is the one command whose
    whole purpose is removing the enforcement of the boundary, so it is the one
    a crew member most wants. calling_pane is spoofable, which is harmless here:
    this check can only ever prevent an action."""
    problems = []
    live = [m for m in crew_members(snap) if m["agent"]]
    if live:
        problems.append(
            "%d crew member(s) are still live: %s. Nothing has been changed. "
            "Uninstall removes the mailbox their reports land in, the crew they "
            "report through, and the guard that stops them dispatching paid "
            "sessions, so it refuses while any of them is running. Let them "
            "report, then retire them."
            % (len(live), ", ".join("%s/%s in %s" % (m["repo"], m["key"],
                                                    m["pane"]) for m in live)))
    watching = watchers(snap)
    if watching:
        problems.append(
            "%d watcher pane(s) are still there: %s. Nothing has been changed. "
            "A watcher's shell keeps polling and writes through an appender "
            "that recreates %s, so it would rebuild the directory this verb "
            "deletes. Crew cannot tell a shell still polling from one that "
            "exited, so it refuses on the pane being there. Retire them first: "
            "crew retire %s"
            % (len(watching), ", ".join(w["pane"] for w in watching), CREW_DIR,
               watching[0]["tab"] or watching[0]["pane"]))
    refusal = crew_pane_refusal(tokens_in(snap, calling_pane()),
                                "uninstalling the boundary it works inside")
    if refusal:
        problems.append(refusal)
    return problems


def uninstall_steps(snap):
    """What reversing the install actually means on THIS machine, read off disk
    rather than assumed.

    Split into what crew will do and what the human does, and the split is not
    timidity. Unstowing the claude package removes every other skill in it, and
    the hook registration lives in a settings file crew is not allowed to edit,
    so both are printed as exact commands and left alone."""
    steps = []
    for name in CREW_SKILLS:
        path = os.path.join(SKILLS_DIR, name)
        if not os.path.islink(path):
            if os.path.exists(path):
                steps.append({
                    "kind": "manual",
                    "what": "%s is a real directory, not a link, so crew will "
                            "not delete it: crew did not create it and cannot "
                            "show what else is in it" % path,
                    "command": "rm -r %s" % path})
            continue
        target = os.path.realpath(path)
        steps.append({"kind": "unlink", "path": path,
                      "what": "remove the %s skill link, which points at %s"
                              % (name, target),
                      "note": _worktree_note(target)})

    if os.path.islink(CREW_BIN):
        target = os.path.realpath(CREW_BIN)
        if os.path.basename(target) == "crew.py":
            steps.append({"kind": "unlink", "path": CREW_BIN,
                          "what": "remove %s, which points at %s"
                                  % (CREW_BIN, target),
                          "note": _worktree_note(target)})
        else:
            steps.append({
                "kind": "manual",
                "what": "%s points at %s, which is not a crew.py, so crew will "
                        "not remove it" % (CREW_BIN, target),
                "command": "rm %s" % CREW_BIN})
    elif os.path.exists(CREW_BIN):
        steps.append({"kind": "manual",
                      "what": "%s is a real file, not the install's symlink, so "
                              "crew will not remove it" % CREW_BIN,
                      "command": "rm %s" % CREW_BIN})

    if os.path.isdir(CREW_DIR):
        records = len(mailbox_entries())
        steps.append({
            "kind": "rmtree", "path": CREW_DIR,
            "what": "delete %s and everything in it" % CREW_DIR,
            "loss": "%d mailbox record(s), every report and alert crew ever "
                    "wrote, plus any pending dispatch artifact. Unrecoverable."
                    % records})

    tagged = [m["pane"] for m in crew_members(snap)] + \
             [w["pane"] for w in watchers(snap)]
    if tagged:
        steps.append({
            "kind": "tokens", "panes": sorted(set(tagged)),
            "what": "clear crew tokens on %d pane(s): %s"
                    % (len(set(tagged)), ", ".join(sorted(set(tagged)))),
            "loss": "those panes stop being recognisable as crew's, so nothing "
                    "will propose retiring them afterwards"})

    stowed = [os.path.realpath(os.path.join(SKILLS_DIR, name))
              for name in CREW_SKILLS
              if os.path.islink(os.path.join(SKILLS_DIR, name))]
    package = next((t.split(os.sep + "stow-packages" + os.sep)[0]
                    for t in stowed if os.sep + "stow-packages" + os.sep in t),
                   None)
    if package:
        steps.append({
            "kind": "manual",
            "what": "unstow the claude package, which is where crew's skills "
                    "come from. That removes EVERY other skill in that package "
                    "too, and the hook file with them, so it is yours to run",
            "command": 'cd %s/stow-packages && stow -D -t "$HOME" claude'
                       % package})
    elif stowed:
        steps.append({
            "kind": "manual",
            "what": "nothing to unstow: crew's skill links resolve outside "
                    "stow-packages (%s), so removing the links above is the "
                    "whole of it" % ", ".join(stowed),
            "command": ""})

    guard = ", ".join(sorted(set(path for _, path, _ in
                                 _guard_registrations(_settings_data()))))
    steps.append({
        "kind": "manual",
        "what": "remove the PreToolUse hook running %s from %s yourself. Crew "
                "does not edit that file. Left registered with the file gone, "
                "crew doctor reports it as dangling and nothing is enforced%s"
                % (GUARD_HOOK, SETTINGS_PATH,
                   (", and it currently resolves to %s" % guard) if guard
                   else ""),
        "command": ""})
    return steps


def _settings_data():
    """The settings file as data, or {}. Uninstall only READS it, and a file it
    cannot parse must not stop the proposal being printed."""
    try:
        with open(SETTINGS_PATH) as handle:
            data = json.load(handle)
    except (IOError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def render_uninstall(steps):
    lines = ["crew uninstall is a PROPOSAL. Nothing has been changed.",
             ""]
    mine = [s for s in steps if s["kind"] != "manual"]
    theirs = [s for s in steps if s["kind"] == "manual"]
    if mine:
        lines.append("crew will do these when you re-run it with --confirm:")
        for number, step in enumerate(mine, 1):
            lines.append("  %d. %s" % (number, step["what"]))
            if step.get("note"):
                lines.append("     %s" % step["note"])
            if step.get("loss"):
                lines.append("     LOSS: %s" % step["loss"])
    else:
        lines.append("crew has nothing of its own left to remove here.")
    if theirs:
        lines.append("")
        lines.append("you do these yourself; crew will not:")
        for number, step in enumerate(theirs, len(mine) + 1):
            lines.append("  %d. %s" % (number, step["what"]))
            if step.get("command"):
                lines.append("     %s" % step["command"])
    lines.append("")
    lines.append("This removes the guard, which is the only enforcement of the "
                 "crew boundary, so do it when no crew is running and not "
                 "before.")
    return "\n".join(lines)


def perform_uninstall(steps):
    """Do the steps crew owns. A failure warns and the rest continues.

    The doctrine the pane closes already follow: giving up halfway through a
    cleanup leaves exactly the debris the cleanup exists to remove, and here it
    would leave a half-uninstalled crew, which is worse than either end state."""
    done = True
    for step in steps:
        if step["kind"] == "manual":
            continue
        if DRY_RUN:
            print("would %s" % step["what"])
            continue
        try:
            if step["kind"] == "unlink":
                os.unlink(step["path"])
                print("removed %s" % step["path"])
            elif step["kind"] == "rmtree":
                shutil.rmtree(step["path"])
                print("removed %s" % step["path"])
            elif step["kind"] == "tokens":
                for pane_id in step["panes"]:
                    args = ["pane", "report-metadata", pane_id,
                            "--source", "crew"]
                    for name in CREW_TOKEN_NAMES:
                        args += ["--clear-token", name]
                    herdr(*args)
                    print("cleared crew tokens on %s" % pane_id)
        except (OSError, HerdrError) as exc:
            print("could not %s (%s), so it is still there. Carrying on with "
                  "the rest; finish that one yourself." % (step["what"], exc),
                  file=sys.stderr)
            done = False
    return done


def cmd_uninstall(confirm):
    """Reverse the install: the skill links, the crew symlink, ~/.crew, and the
    crew tokens on panes.

    It proposes and the human confirms, exactly as retirement does, and for a
    stronger reason: retirement destroys one session's context, while this
    destroys every record crew ever wrote AND removes the only enforcement of
    the boundary. It refuses outright while any crew member is live.

    Two steps are printed and never run: unstowing the claude package, which
    would take every other skill in it, and the hook registration, which lives
    in a settings file crew is not allowed to edit."""
    defs = schema_defs()
    assert_schema_declares(defs)
    snap = snapshot()
    assert_snapshot_shape(snap, defs)
    blockers = uninstall_blockers(snap)
    if blockers:
        raise CrewError("refusing to uninstall. " + " ".join(blockers))
    steps = uninstall_steps(snap)
    if not confirm:
        print(render_uninstall(steps))
        return 0
    done = perform_uninstall(steps)
    manual = [s for s in steps if s["kind"] == "manual"]
    if manual:
        print("")
        print("still yours to do, crew will not:")
        for step in manual:
            print("  - %s" % step["what"])
            if step.get("command"):
                print("    %s" % step["command"])
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
