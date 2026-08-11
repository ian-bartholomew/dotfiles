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

HERDR_PROTOCOL = 17
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
        elif int(found.group(1)) != HERDR_PROTOCOL:
            problems.append(
                "herdr protocol is %s, crew expects %d"
                % (found.group(1), HERDR_PROTOCOL)
            )
        else:
            print("protocol: %d" % HERDR_PROTOCOL)

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
        for flag in ("--append-system-prompt", "--continue", "--model",
                     "--permission-mode"):
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


def render_ls(members, untagged):
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
    return "\n".join(lines)


def cmd_ls(as_json):
    defs = schema_defs()
    assert_schema_declares(defs)
    snap = snapshot()
    assert_snapshot_shape(snap, defs)
    members = crew_members(snap)
    if as_json:
        print(json.dumps(members, indent=2, sort_keys=True))
    else:
        print(render_ls(members, untagged_agents(snap)))
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


def mail_send(key, repo, state, msg):
    """The key is the CALLER'S OWN. A crew member could otherwise forge a
    `done` for a sibling's key and get the foreman to propose retiring a
    session that was still working, so an explicit --key that disagrees with
    the caller's pane token is refused rather than trusted.

    msg is collapsed to one line: a newline let a crew member append text that
    read like crew's own output, for instance a fake "ack with: crew mail ack
    999999" that would suppress every later report."""
    ensure_crew_dir()
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
    record["msg"] = " ".join(str(msg).split())
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
        print("%s  %-12s %-22s %-12s %s" % (
            record["seq"], record["state"], record.get("repo", ""),
            record.get("key", ""), record.get("msg", "")))
    if not fresh:
        print("no new mail")
    if unreadable or missing:
        print("%d unreadable, %d missing" % (unreadable, missing))
    print("ack with: crew mail ack %d" % new_cursor)
    return 0


def calling_pane():
    """Ask herdr which pane is calling, with HERDR_PANE_ID stripped.

    Measured against herdr 0.7.5: `pane current --current` HONOURS a spoofed
    HERDR_PANE_ID when the named pane is valid, and falls back to genuine
    caller detection when the variable is absent. Stripping it means a crew
    member cannot claim to be the foreman by setting one variable.

    This raises the bar against a confused or prompt-injected agent, which is
    the realistic threat. It does not stop a determined process, which can
    bypass this script and call herdr directly. Only a PreToolUse hook can."""
    env = dict(os.environ)
    env.pop("HERDR_PANE_ID", None)
    proc = subprocess.run(["herdr", "pane", "current", "--current"],
                          capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        return ""
    try:
        return json.loads(proc.stdout)["result"]["pane"]["pane_id"]
    except (ValueError, KeyError, TypeError):
        return ""


def is_foreman_pane():
    me = calling_pane()
    if not me:
        return False
    for agent in snapshot()["agents"]:
        if agent.get("name") == "foreman":
            return agent.get("pane_id") == me
    return False


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
        print("usage: crew <doctor|ls|dispatch|peek|nudge|mail> [args]", file=sys.stderr)
        return 2
    verb = args[0]
    if verb == "doctor":
        return doctor()
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
SETUP_TIMEOUT = 900
DRY_PANE = "wDRY:pDRY"
MODEL_BY_TYPE = {"implementer": "opus", "planner": "opus", "reviewer": "opus"}

SETUP_PROMPT = (
    "You are a short-lived setup agent. Do exactly this and nothing else.\n"
    "1. Run /start-ticket {key} and answer its prompts with the human.\n"
    "2. When the worktree exists, write this JSON to {artifact} and stop:\n"
    '   {{"worktree": "<absolute worktree path>", '
    '"branch": "<branch name>", "repo": "{repo}"}}\n'
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

    # herdr silently truncates a token VALUE at 80 characters. Tokens are the
    # authoritative record here, so a truncated value is a record that lies.
    # Refuse to write one rather than discover it later.
    for i in range(0, len(args)):
        if args[i] == "--token":
            name, _, value = args[i + 1].partition("=")
            if len(value) > TOKEN_VALUE_MAX:
                raise CrewError(
                    "token %s is %d chars, over herdr's %d limit, and would be "
                    "silently truncated: %r"
                    % (name, len(value), TOKEN_VALUE_MAX, value))
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


def setup_worktree(key, repo, repo_root):
    """Run /start-ticket in an ephemeral pane. Handoff is a JSON artifact,
    not scraped terminal output: an interactive REPL renders ANSI and does
    not exit on its own."""
    ensure_crew_dir()
    artifact = os.path.join(CREW_DIR, "dispatch-%s.json" % sanitize_name(key))
    if os.path.exists(artifact):
        os.unlink(artifact)

    split = herdr("pane", "split", "--current", "--direction", "down",
                  "--cwd", repo_root, "--no-focus")
    setup_pane = DRY_PANE if split is None else split["result"]["pane"]["pane_id"]
    tag_pane(setup_pane, key, repo, "setup", "", repo_root)

    setup_name = ("setup-" + sanitize_name(key))[:32]
    _start_agent(["agent", "start", setup_name, "--kind", "claude",
                 "--pane", setup_pane, "--", "--model", "opus"])
    herdr("agent", "prompt", setup_name,
          SETUP_PROMPT.format(key=key, artifact=artifact, repo=repo))

    if DRY_RUN:
        return os.path.join(repo_root, ".claude", "worktrees",
                            sanitize_name(key))

    deadline = time.time() + SETUP_TIMEOUT
    while time.time() < deadline:
        if os.path.exists(artifact):
            with open(artifact) as handle:
                data = json.load(handle)
            worktree = data.get("worktree", "")
            if not worktree or not os.path.isdir(worktree):
                raise CrewError(
                    "setup wrote %s but worktree %r does not exist"
                    % (artifact, worktree))
            herdr("pane", "close", setup_pane)
            return worktree
        time.sleep(3)

    raise CrewError(
        "setup pane %s did not produce %s within %ds. Left open for "
        "inspection." % (setup_pane, artifact, SETUP_TIMEOUT))


def cmd_dispatch(key, ctype, repo, model):
    if ctype not in MODEL_BY_TYPE:
        raise CrewError("unknown type %r; expected one of %s"
                        % (ctype, ", ".join(sorted(MODEL_BY_TYPE))))
    workspace = os.environ.get("HERDR_WORKSPACE_ID")
    if not workspace:
        raise CrewError("dispatch must run inside a herdr pane")

    repo_root, repo = resolve_repo(repo)
    model = model or MODEL_BY_TYPE[ctype]

    ensure_crew_dir()
    lock_path = os.path.join(CREW_DIR, "dispatch-%s.lock" % sanitize_name(key))
    with _locked(lock_path, "w"):
        snap = snapshot()
        assert_snapshot_shape(snap, schema_defs())

        existing = find_member(snap, repo_root, key)
        if existing:
            print("already dispatched: %s/%s in pane %s (%s). "
                  "Resume with: cd %s && claude --continue"
                  % (repo, existing["key"], existing["pane"],
                     existing["bucket"], existing["worktree"]))
            mail_send(key, repo, "duplicate",
                      "dispatch declined, a live session already holds this key")
            return 5

        if is_ticket(key):
            worktree = setup_worktree(key, repo, repo_root)
        else:
            worktree = plain_worktree(key, repo_root)

        tab = herdr("tab", "create", "--workspace", workspace,
                    "--label", "%s/%s" % (repo, sanitize_name(key)),
                    "--cwd", worktree, "--no-focus")
        pane = DRY_PANE if tab is None else tab["result"]["root_pane"]["pane_id"]

        # Tag before agent.start. Tokens are authoritative, so an untagged
        # pane is invisible to ls; a tag that failed afterwards would leave a
        # live session unowned and burning shared quota.
        tag_pane(pane, key, repo, ctype, os.path.basename(worktree), repo_root)

        live = set(a.get("name") for a in snap["agents"] if a.get("name"))
        name = pick_name(key, live)

        start = ["agent", "start", name, "--kind", "claude", "--pane", pane,
                 "--", "--model", model,
                 "--append-system-prompt",
                 contract_pointer(name, ctype, key, repo, worktree)]
        # A planner is an ordinary session told to plan. It must NOT run in
        # plan mode: that blocks bash, so it could never send its own report.
        _start_agent(start)

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
                  "tagged, so crew ls shows it. Resend with: crew nudge %s"
                  % (name, name), file=sys.stderr)
            return 6

        print("dispatched %s as %s in pane %s" % (key, name, pane))
        return 0


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
