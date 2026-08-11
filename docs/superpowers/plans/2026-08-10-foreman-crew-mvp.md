# Foreman and Crew MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make live agent sessions first-class: a `crew` CLI that names, tags, lists and dispatches herdr-hosted Claude sessions, plus a foreman role that reports on them.

**Architecture:** Three layers. herdr 0.7.5 is the substrate (panes, agents, pane metadata, notifications). `crew.py` is the only thing that talks to herdr, and it is deterministic with no LLM in the loop. Two skill files carry judgment. State is derived from `herdr api snapshot` plus pane tokens on every query; the only files are a locked mailbox and a read cursor.

**Tech Stack:** Python 3.9.6 (system `python3`, stdlib only), `unittest`, `fcntl.flock`, herdr 0.7.5 socket CLI, GNU Stow, Claude Code CLI.

**Source spec:** `docs/superpowers/specs/2026-08-10-foreman-crew-design.md`

**Scope:** Spec build-order steps 1 to 7 (the MVP). Steps 8 to 10 (`crew watchdog`, `crew watch`, `crew retire/recover/log/uninstall`) are a separate plan that depends on this one.

## Global Constraints

- Python 3.9.6. No 3.10+ syntax: no `match`, no `X | Y` type unions, no `dict` pattern matching.
- Standard library only. No pip installs. Tests use `unittest` and run as `python3 test_crew.py`.
- herdr 0.7.5, protocol 17. `crew doctor` asserts this and refuses to run on a mismatch.
- Script location follows existing convention: `stow-packages/claude/.claude/skills/foreman/scripts/crew.py`, with `test_crew.py` beside it.
- `~/.local/bin/crew` is a symlink to that file. A skill's `scripts/` directory is NOT on PATH; `~/.local/bin` is. Crew members invoke `crew mail send` from their own panes, so this is mandatory.
- No em dashes in any file content, commit message, or output string. Use commas, colons, parentheses or hyphens.
- No emojis anywhere.
- Comment sparingly. Only where logic is non-obvious.
- Commit messages use Conventional Commits: `<type>(<scope>): <description>`. Scope is `crew`, `foreman`, or `herdr`.
- Never reference plan task numbers in code, comments, or commit messages.
- Agent names must match `[a-z][a-z0-9_-]{0,31}`, lowercase, unique among live agents.
- Pane token keys must match `^[A-Za-z0-9_-]{1,32}$`, max 16 keys per pane. Token **values** are unconstrained strings.
- Do NOT dispatch crew onto real sprint tickets until Task 6's smoke test passes.

---

## File Structure

| Path (relative to `~/.dotfiles`) | Responsibility |
| --- | --- |
| `stow-packages/claude/.claude/skills/herdr/SKILL.md` | herdr CLI reference. Existing content, relocated so it actually loads. |
| `stow-packages/claude/.claude/skills/foreman/SKILL.md` | The foreman role and its verb vocabulary. |
| `stow-packages/claude/.claude/skills/foreman/scripts/crew.py` | Every herdr call. All verbs. The only executable. |
| `stow-packages/claude/.claude/skills/foreman/scripts/test_crew.py` | `unittest` suite over the pure functions. |
| `stow-packages/claude/.claude/skills/crew-member/SKILL.md` | The crew member reporting contract. |

Runtime files, not in the repo: `~/.crew/mailbox.jsonl` (0600), `~/.crew/cursor`, `~/.crew/dispatch-<key>.json`, `~/.crew/dispatch-<key>.lock`, in `~/.crew` (0700).

---

### Task 1: Worktree and herdr skill discovery fix

The `herdr.md` reference exists but never loads, because Claude Code discovers skills as `<name>/SKILL.md`. It is also untracked, so it is not backed up. This task is independent of everything else and delivers value alone.

**Files:**

- Create: `~/.dotfiles/.claude/worktrees/foreman-crew-mvp` (worktree)
- Create: `stow-packages/claude/.claude/skills/herdr/SKILL.md` (moved, content unchanged)
- Delete: `~/.claude/skills/herdr.md`

**Interfaces:**

- Consumes: nothing.
- Produces: a working worktree at `~/.dotfiles/.claude/worktrees/foreman-crew-mvp` on branch `foreman-crew-mvp`, which every later task works inside.

- [ ] **Step 1: Create the worktree off main**

The repo is on `finish-work-verification-fixes` with ten modified files. A worktree makes that irrelevant. Prefer the `superpowers:using-git-worktrees` skill; the raw fallback is:

```bash
cd ~/.dotfiles
git fetch origin
git worktree add .claude/worktrees/foreman-crew-mvp -b foreman-crew-mvp origin/main
cd ~/.dotfiles/.claude/worktrees/foreman-crew-mvp
git status --porcelain   # expect empty
```

- [ ] **Step 2: Confirm there is nothing to `git mv`**

This is the check that makes the next step correct rather than a guess.

```bash
git -C ~/.dotfiles ls-files | grep -i herdr        # expect no output
ls stow-packages/claude/.claude/skills/ | grep -i herdr  # expect no output
ls -l ~/.claude/skills/herdr.md                    # expect a regular file, ~10K
ls -ld ~/.claude/skills                            # expect a real directory, not a symlink
```

Expected: the first two produce nothing, the third shows a real file, the fourth shows a directory. `~/.claude/skills` being a real directory of per-skill symlinks is why stowing a new `herdr/` package will not displace the flat file on its own.

- [ ] **Step 3: Move the file into the stow package**

```bash
mkdir -p stow-packages/claude/.claude/skills/herdr
mv ~/.claude/skills/herdr.md stow-packages/claude/.claude/skills/herdr/SKILL.md
git add stow-packages/claude/.claude/skills/herdr/SKILL.md
```

- [ ] **Step 4: Stow and assert the result**

Claude Code loads skills only from `~/.claude/skills/<name>/SKILL.md`, so a file written in the worktree is invisible until it is linked in. Point the dev symlink at the **worktree**, not the main checkout: nothing outside this worktree is written, and stow repoints it after merge.

```bash
ln -sfn /Users/ian.bartholomew/.dotfiles/.claude/worktrees/foreman-crew-mvp/stow-packages/claude/.claude/skills/herdr ~/.claude/skills/herdr

test ! -e ~/.claude/skills/herdr.md   && echo "OK: flat file gone"
test -f ~/.claude/skills/herdr/SKILL.md && echo "OK: skill resolves"
head -3 ~/.claude/skills/herdr/SKILL.md
```

Expected: both `OK:` lines print, and the head shows the `name: herdr` frontmatter.

- [ ] **Step 5: Commit**

```bash
git add -A stow-packages/claude/.claude/skills/herdr
git commit -m "fix(herdr): relocate reference to herdr/SKILL.md so the skill loads

The reference sat at ~/.claude/skills/herdr.md. Claude Code discovers
skills as <name>/SKILL.md, so it never loaded, and it was untracked so
it was not backed up."
```

- [ ] **Step 6: Verify discovery in a fresh session**

Skill discovery happens at session start, so this cannot be checked in the current session. In a new Claude Code session, confirm `herdr` appears in the available skills list. Record the result; if it does not appear, stop and diagnose before Task 2.

---

### Task 2: crew.py skeleton, `--dry-run`, and `crew doctor`

Establishes the executable, the herdr wrapper every later verb uses, the pure helpers the tests pin, and a preflight that fails loudly on substrate drift.

**Files:**

- Create: `stow-packages/claude/.claude/skills/foreman/scripts/crew.py`
- Create: `stow-packages/claude/.claude/skills/foreman/scripts/test_crew.py`
- Create: `~/.local/bin/crew` (symlink)

**Interfaces:**

- Consumes: nothing.
- Produces:
  - `sanitize_name(key: str) -> str`
  - `pick_name(key: str, live_names: set) -> str`
  - `bucket(agent_status: str) -> str` returning one of `"working"`, `"awaiting"`, `"blocked"`, `"recover"`
  - `herdr(*args, capture=True) -> dict or None`, raising `HerdrError`
  - `snapshot() -> dict` returning the inner `snapshot` object
  - `CREW_DIR`, `MAILBOX`, `CURSOR` path constants
  - `ensure_crew_dir() -> None`
  - `HerdrError`, `CrewError` exception classes
  - `DRY_RUN` module-level flag
  - `main(argv) -> int`

- [ ] **Step 1: Write the failing test**

Create `stow-packages/claude/.claude/skills/foreman/scripts/test_crew.py`:

```python
"""Tests for the pure decision logic in crew.py."""
import unittest

from crew import sanitize_name, pick_name, bucket


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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ~/.dotfiles/.claude/worktrees/foreman-crew-mvp/stow-packages/claude/.claude/skills/foreman/scripts
python3 test_crew.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'crew'`.

- [ ] **Step 3: Write crew.py**

```python
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
SCHEMA_OPTIONAL_PANE_FIELDS = ("tokens", "cwd", "foreground_cwd")
SCHEMA_OPTIONAL_AGENT_FIELDS = ("name", "terminal_title_stripped",
                                "state_change_seq")


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
    # exist_ok because many crew call this concurrently on a fresh install.
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


def main(argv):
    global DRY_RUN
    args = list(argv)
    if "--dry-run" in args:
        DRY_RUN = True
        args.remove("--dry-run")
    if not args:
        print("usage: crew <doctor|ls|dispatch|peek|nudge|mail> [args]", file=sys.stderr)
        return 2
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


def _run(args):
    """Verb dispatch. Errors are handled centrally in main, so a verb that
    forgets its own try/except cannot dump a traceback at a crew member."""
    verb = args[0]
    if verb == "doctor":
        return doctor()
    print("unknown verb: %s" % verb, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python3 test_crew.py -v
```

Expected: PASS, 15 tests.

- [ ] **Step 5: Install the symlink and run doctor**

```bash
chmod +x crew.py
mkdir -p ~/.local/bin
ln -sfn /Users/ian.bartholomew/.dotfiles/.claude/worktrees/foreman-crew-mvp/stow-packages/claude/.claude/skills/foreman/scripts/crew.py ~/.local/bin/crew
crew doctor
```

Expected: `OK` with herdr version, protocol 17, and a snapshot line. If it reports FAIL, fix the listed problems before continuing; a red `doctor` means later tasks are building on drift.

- [ ] **Step 6: Verify `--dry-run` prints instead of acting**

```bash
crew --dry-run doctor 2>&1 | head -5
```

Expected: doctor still runs its read-only probes. `--dry-run` suppresses only mutating `herdr()` calls, of which doctor has none, so this confirms the flag parses without breaking the verb.

- [ ] **Step 7: Commit**

```bash
git add stow-packages/claude/.claude/skills/foreman/scripts/crew.py \
        stow-packages/claude/.claude/skills/foreman/scripts/test_crew.py
git commit -m "feat(crew): add crew.py skeleton with doctor and dry-run

doctor asserts herdr protocol, required snapshot fields, claude CLI flags,
the ~/.local/bin symlink, ~/.crew permissions, and that no flat herdr.md
shadows the stowed skill."
```

---

### Task 3: `crew ls` and `crew ls --json`, failing closed

The primary UI. It must never print counts it did not measure: a renamed herdr field would otherwise show `0 working / 0 awaiting / 0 blocked`, indistinguishable from a quiet crew.

**Files:**

- Modify: `stow-packages/claude/.claude/skills/foreman/scripts/crew.py`
- Modify: `stow-packages/claude/.claude/skills/foreman/scripts/test_crew.py`

**Interfaces:**

- Consumes: `snapshot()`, `assert_snapshot_shape()`, `bucket()`, `CrewError` from Task 2.
- Produces:
  - `_probe(argv) -> (bool, str)` running a read-only external command for `doctor`, failing on OSError, a nonzero exit, or empty output
  - `crew_members(snap) -> list` of dicts with keys `name`, `key`, `repo`, `type`, `worktree`, `pane`, `status`, `bucket`
  - `untagged_agents(snap) -> list` of dicts with keys `pane`, `title`, `status`
  - `render_ls(members, untagged) -> str`
  - `cmd_ls(as_json: bool) -> int`

- [ ] **Step 1: Write the failing test**

Append to `test_crew.py`, and extend the import at the top of the file to include `_probe`, `crew_members`, `untagged_agents`, `render_ls`, `assert_snapshot_shape`, `assert_schema_declares`, `CrewError`, `HerdrError`. Also add `import crew` and `from unittest import mock` so the CLI wiring can be exercised without a live herdr:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
python3 test_crew.py -v
```

Expected: FAIL with `ImportError: cannot import name 'crew_members'`.

- [ ] **Step 3: Implement**

Add to `crew.py` above `main`:

```python
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
            "worktree": tokens.get("worktree", ""),
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
```

Wire it into `main`, and make `CrewError` fail closed rather than printing zeros:

```python
    if verb == "doctor":
        return doctor()
    if verb == "ls":
        return cmd_ls("--json" in args)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python3 test_crew.py -v
```

Expected: PASS, 36 tests.

- [ ] **Step 5: Harden doctor to use `_probe`**

`doctor` currently calls three external commands with ad hoc error handling. Two of them fail badly: `herdr --version` prints an empty version and adds no problem when the binary is on PATH but exits nonzero, and the `claude --help` call has no guard at all, so a missing `claude` crashes `doctor` with a traceback instead of listing problems. A preflight that cannot report is worse than no preflight.

Replace the three call sites in `doctor()` with `_probe`, exactly as shown:

```python
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
```

and

```python
    ok, help_text = _probe(["claude", "--help"])
    if not ok:
        problems.append(help_text)
    else:
        for flag in ("--append-system-prompt", "--continue", "--model",
                     "--permission-mode"):
            if flag not in help_text:
                problems.append("claude CLI is missing %s" % flag)
```

Then prove the crash is gone. Create a stub `herdr` that exits nonzero in a scratch directory and run `doctor` with a PATH that has neither a working herdr nor claude:

```bash
SCRATCH=$(mktemp -d)
printf '#!/bin/sh\nexit 1\n' > "$SCRATCH/herdr" && chmod +x "$SCRATCH/herdr"
PATH="$SCRATCH:/usr/bin:/bin" python3 crew.py doctor; echo "exit=$?"
rm -rf "$SCRATCH"
```

Expected: no traceback. A `FAIL` block listing `herdr exited 1: ...` and `claude not runnable: ...`, and `exit=1`.

- [ ] **Step 6: Verify against the live fleet**

```bash
crew ls
crew ls --json | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
```

Expected: `0 working / 0 awaiting you / 0 blocked`, then an untagged section listing the currently live agent panes with their titles. `--json` prints `0`. Nothing is tagged yet, which is correct: greenfield only.

- [ ] **Step 7: Verify it fails closed**

```bash
PATH="/tmp/fakeherdr:$PATH"
mkdir -p /tmp/fakeherdr
printf '#!/bin/sh\necho "{\\"result\\":{\\"snapshot\\":{\\"agents\\":[{\\"pane_id\\":\\"x\\"}],\\"panes\\":[]}}}"\n' > /tmp/fakeherdr/herdr
chmod +x /tmp/fakeherdr/herdr
crew ls; echo "exit=$?"
rm -rf /tmp/fakeherdr
```

Expected: a message containing `UNPARSED` on stderr, prefixed `crew: `, and `exit=3`. It must not print a zeroed load report. The exact layer that trips (schema parse or snapshot shape) depends on the stub, and either is a pass.

- [ ] **Step 8: Commit**

```bash
git add stow-packages/claude/.claude/skills/foreman/scripts/
git commit -m "feat(crew): add ls and ls --json with fail-closed snapshot assertions

Tokens are authoritative, so an untagged pane is not crew whatever its
cwd, and the worktree comes from the token rather than foreground_cwd
which follows the transient foreground process. A renamed herdr field
exits non-zero instead of reporting an empty fleet."
```

---

### Task 4: `crew mail` with locking, sequencing, and a gap-tolerant cursor

The only persistent state. Several crew append concurrently, so it is locked; a damaged record must not wedge the reader forever.

**Files:**

- Modify: `stow-packages/claude/.claude/skills/foreman/scripts/crew.py`
- Modify: `stow-packages/claude/.claude/skills/foreman/scripts/test_crew.py`

**Interfaces:**

- Consumes: `ensure_crew_dir()`, `MAILBOX`, `CURSOR`, `snapshot()` from Task 2.
- Produces:
  - `read_entries(lines) -> (list, int)` returning parsed records and an unreadable count
  - `next_seq(entries) -> int`
  - `select_unread(entries, cursor) -> (list, int, int)` returning fresh records, the new cursor, and a missing count
  - `mail_send(key, repo, state, msg) -> int`
  - `mail_unread() -> int`
  - `mail_ack(seq) -> int`
  - `is_foreman_pane() -> bool`

- [ ] **Step 1: Write the failing test**

Extend the `crew` import to include `read_entries`, `next_seq`, `select_unread`, then append:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
python3 test_crew.py -v
```

Expected: FAIL with `ImportError: cannot import name 'read_entries'`.

- [ ] **Step 3: Implement**

Add `import time` to the imports, then add above `main`:

```python
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
    ensure_crew_dir()
    pane_id = os.environ.get("HERDR_PANE_ID", "")
    tokens = _pane_tokens(pane_id) if pane_id else {}
    record = {
        "v": 1,
        "ts": int(time.time()),
        "key": key or tokens.get("key", ""),
        "repo": repo or tokens.get("repo", ""),
        "pane": pane_id,
        "worktree": tokens.get("worktree", ""),
        "state": state,
        "msg": msg,
    }
    if not record["key"]:
        raise CrewError("mail send needs --key, or a pane carrying a key token")
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
    try:
        with open(CURSOR) as handle:
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


def is_foreman_pane():
    me = os.environ.get("HERDR_PANE_ID")
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
    with _locked(CURSOR, "w") as handle:
        handle.write("%d\n" % seq)
    return 0
```

Wire into `main`:

```python
    if verb == "mail":
        sub = args[1] if len(args) > 1 else ""
        if sub == "send":
            key = repo = None
            rest = args[2:]
            while True:
                flag, value, rest = take_flag(rest, ("--key", "--repo"))
                if flag is None:
                    break
                if flag == "--key":
                    key = value
                else:
                    repo = value
            if len(rest) < 2:
                print("usage: crew mail send [--key K] [--repo R] <state> <msg>",
                      file=sys.stderr)
                return 2
            return mail_send(key, repo, rest[0], " ".join(rest[1:]))
        if sub == "unread":
            return mail_unread()
        if sub == "ack":
            if len(args) < 3:
                print("usage: crew mail ack <seq>", file=sys.stderr)
                return 2
            return mail_ack(int(args[2]))
        print("usage: crew mail <send|unread|ack>", file=sys.stderr)
        return 2
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python3 test_crew.py -v
```

Expected: PASS, 51 tests.

- [ ] **Step 5: Verify concurrent writers do not interleave**

`PIPE_BUF` on macOS is 512 bytes and there is no `flock` binary, so this is the check that the Python lock actually holds.

```bash
rm -f ~/.crew/mailbox.jsonl ~/.crew/cursor
for i in $(seq 1 40); do
  crew mail send --key "load-$i" --repo testrepo done "$(python3 -c 'print("x"*400)')" &
done
wait
python3 - <<'PY'
import json, os
path = os.path.expanduser("~/.crew/mailbox.jsonl")
lines = [l for l in open(path).read().splitlines() if l.strip()]
seqs = []
bad = 0
for l in lines:
    try:
        seqs.append(int(json.loads(l)["seq"]))
    except Exception:
        bad += 1
print("lines=%d parsed=%d unparseable=%d unique_seqs=%d" % (
    len(lines), len(seqs), bad, len(set(seqs))))
assert bad == 0, "interleaved or truncated writes"
assert len(set(seqs)) == len(seqs), "duplicate seq assigned"
assert sorted(seqs) == list(range(1, len(seqs) + 1)), "seq not contiguous"
print("OK")
PY
ls -l ~/.crew/mailbox.jsonl   # expect -rw-------
ls -ld ~/.crew                # expect drwx------
```

Expected: `lines=40 parsed=40 unparseable=0 unique_seqs=40`, then `OK`, and the two permission lines showing 600 and 700.

- [ ] **Step 6: Verify a punched gap advances rather than wedging**

```bash
python3 - <<'PY'
import os
path = os.path.expanduser("~/.crew/mailbox.jsonl")
lines = open(path).read().splitlines()
del lines[19]                       # remove seq 20, creating a gap
open(path, "w").write("\n".join(lines) + "\n")
open(os.path.expanduser("~/.crew/cursor"), "w").write("0\n")
PY
crew mail unread | tail -3
```

Expected: the listing ends with `1 unreadable, 1 missing` replaced by `0 unreadable, 1 missing`, and an `ack with: crew mail ack 40` line. The cursor advances past the gap; it does not stop at 19.

- [ ] **Step 7: Verify ack is foreman-scoped**

```bash
crew mail ack 40; echo "exit=$?"
```

Expected: `refusing: crew mail ack is foreman-only ...` and `exit=4`, because no agent is currently named `foreman`.

- [ ] **Step 8: Clean up and commit**

```bash
rm -f ~/.crew/mailbox.jsonl ~/.crew/cursor
git add stow-packages/claude/.claude/skills/foreman/scripts/
git commit -m "feat(crew): add mail with flock, monotonic seq, gap-tolerant cursor

Every read and write takes an exclusive fcntl.flock: PIPE_BUF here is 512
bytes and macOS has no flock binary, so bare appends are not safe. The
cursor tracks the highest seq reported rather than the highest contiguous
one, so a record damaged by a killed writer cannot wedge the reader.
ack refuses unless the calling pane hosts the agent named foreman."
```

---

### Task 5: The crew member contract

Small but must exist before dispatch, because dispatch injects a pointer to it.

**Files:**

- Create: `stow-packages/claude/.claude/skills/crew-member/SKILL.md`

**Interfaces:**

- Consumes: nothing.
- Produces: a file at `~/.claude/skills/crew-member/SKILL.md` that `crew dispatch` references by absolute path in `--append-system-prompt`.

- [ ] **Step 1: Write the file**

```markdown
---
name: crew-member
description: "Read this when your system prompt identifies you as a named crew member dispatched by a foreman, with a key, repo and worktree. Defines your reporting obligation and boundaries. Does not apply to a session that was not dispatched as crew, and must not be loaded by inference."
---

# Crew Member

You are one crew member in a fleet coordinated by a foreman. Your name, type,
key, repo and worktree are in your system prompt. If they are not there, you
are not crew and nothing in this file applies to you.

## Your obligations

Scope is exactly one key. Do not widen it. If the work turns out to need a
second ticket, report that and stop rather than starting it.

When you settle, send exactly ONE of these, whichever matches your outcome:

```bash
crew mail send --key <your-key> done "one sentence on what landed"
crew mail send --key <your-key> needs-input "one sentence on what you need"
```

Never send both. The foreman treats your line as a single signal, and two
contradictory lines are worse than none.

Then confirm it landed. `crew mail send` exits 0 on success. A nonzero exit
means your report did not reach the foreman: retry once, and if it still
fails, say so in your session and stop. Do not close a pane whose report
never arrived.

This is your only obligation to the fleet. Silence is a bug.

You cannot report `blocked` yourself. If you are stopped at a permission
prompt you are mid-turn and cannot run anything; a watchdog reports that
from outside. Do not try to pre-announce it.

Never put command output, credentials, ARNs, account ids, tokens, hostnames,
IP addresses, stack traces or file contents in a mail line. State plus one
human-readable sentence only. Detail belongs in a file in your worktree, not
in the mailbox: the mailbox is never pruned and gets digested into a
git-tracked project log. When in doubt, leave it out.

## Boundaries

Subagents: spawn them freely. Crew members: never. Only the foreman
dispatches. If you think another crew member is needed, say so in a mail
line and let the human decide.

You may close your own pane, but only after `crew mail send` has exited 0.

A reviewer closes itself in this order: write the findings to a file inside
your own worktree, mail a one-line `done` naming that file path, confirm the
send exited 0, then close. The findings go in the file, never in the mail
line.

You own nothing beyond your own pane, so there is nothing else to lose. You
may never close another session; that is the foreman's to propose and the
human's to confirm, because another crew member's context is unsaved work.

Never force-push. Never merge. The human merges.

## These override the global CLAUDE.md for you

- Do NOT run `/start-ticket`. It already ran before you existed; your
  worktree and plan are in place.
- Do NOT write to any project `log.md`. The foreman digests the mailbox.
  Several crew writing to one log interleaves.
- Do NOT run `/finish-work`. Report `done` and stop. It would delete the
  worktree the foreman is still tracking.

Everything else in the global CLAUDE.md still applies to you.

## Phase skills are already available

Use them rather than improvising: `superpowers:brainstorming` and
`superpowers:writing-plans` for design, `superpowers:test-driven-development`
for build, `terraform-review` or `feature-dev:code-reviewer` before a PR,
then `commit-commands:commit-push-pr` and `pr-gate`.

```

- [ ] **Step 2: Verify it resolves at the path dispatch will reference**

```bash
ln -sfn /Users/ian.bartholomew/.dotfiles/.claude/worktrees/foreman-crew-mvp/stow-packages/claude/.claude/skills/crew-member ~/.claude/skills/crew-member
test -f ~/.claude/skills/crew-member/SKILL.md && echo "OK: contract resolves"
```

Expected: `OK: contract resolves`.

- [ ] **Step 3: Commit**

```bash
git add stow-packages/claude/.claude/skills/crew-member/SKILL.md
git commit -m "feat(crew): add crew-member contract skill

Explicitly overrides the global CLAUDE.md mandates to run start-ticket,
write project log.md, and run finish-work. Crew inherit that file, so
without the override every crew member would re-run setup, write
concurrently to one log, and delete a worktree still being tracked."
```

---

### Task 6: `crew dispatch`

The largest task. Creates the worktree via an ephemeral setup pane that hands off a JSON artifact, tags the pane before any agent exists, then starts and assigns the crew member.

**Files:**

- Modify: `stow-packages/claude/.claude/skills/foreman/scripts/crew.py`
- Modify: `stow-packages/claude/.claude/skills/foreman/scripts/test_crew.py`

**Interfaces:**

- Consumes: `pick_name()`, `snapshot()`, `crew_members()`, `_locked()`, `herdr()`, `CREW_DIR` from Tasks 2 to 4.
- Produces:
  - `contract_pointer(name, ctype, key, repo, worktree) -> str`
  - `tag_pane(pane_id, key, repo, ctype, worktree) -> None`
  - `find_member(snap, repo, key) -> dict or None`
  - `setup_worktree(key, repo, repo_root) -> str`
  - `cmd_dispatch(key, ctype, repo, model) -> int`

- [ ] **Step 1: Verify the mutating herdr CLI forms before wiring them**

Nested `herdr --help` falls through to top-level help, so flag forms must be observed. Confirm each against a scratch pane and record the working invocation. Do this before writing code against a guess.

```bash
# tab create: confirm the flag names and the result path for the root pane
herdr tab create --workspace "$HERDR_WORKSPACE_ID" --label "scratch/probe" \
      --cwd "$HOME" --no-focus | python3 -m json.tool | head -30
```

Note the JSON path to the new pane id (the herdr skill documents `.result.root_pane`). If `--no-focus` is rejected, drop it: `TabCreateParams.focus` defaults to `false`.

```bash
# agent start with passthrough args, into the pane just created
herdr agent start probe-crew --kind claude --pane <root_pane_from_above> -- \
      --model sonnet --append-system-prompt "You are a probe. Reply OK and stop."
herdr agent prompt probe-crew "Reply OK and stop."
herdr agent read probe-crew --source detection --lines 20
herdr tab close <tab_id_from_above>
```

Record the exact accepted forms in the commit message for this task. If any differ from the code below, adjust the code to match the observed form rather than the other way round.

- [ ] **Step 2: Write the failing test**

Extend the `crew` import with `contract_pointer` and `find_member`, then append:

```python
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
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
python3 test_crew.py -v
```

Expected: FAIL with `ImportError: cannot import name 'contract_pointer'`.

- [ ] **Step 4: Implement**

Add above `main`:

```python
CONTRACT_PATH = os.path.expanduser("~/.claude/skills/crew-member/SKILL.md")
SETUP_TIMEOUT = 900
MODEL_BY_TYPE = {"implementer": "opus", "planner": "opus", "reviewer": "opus"}

SETUP_PROMPT = (
    "You are a short-lived setup agent. Do exactly this and nothing else.\n"
    "1. Run /start-ticket {key} and answer its prompts with the human.\n"
    "2. When the worktree exists, write this JSON to {artifact} and stop:\n"
    '   {{"worktree": "<absolute worktree path>", '
    '"branch": "<branch name>", "repo": "{repo}"}}\n'
    "Do not implement anything. Do not open a PR."
)


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
        % (name, ctype, key, repo, worktree, CONTRACT_PATH, key)
    )


def tag_pane(pane_id, key, repo, ctype, worktree):
    args = [
        "pane", "report-metadata", pane_id, "--source", "crew",
        "--token", "crew=true",
        "--token", "v=%s" % TOKEN_VERSION,
        "--token", "key=%s" % sanitize_name(key),
        "--token", "repo=%s" % repo,
        "--token", "type=%s" % ctype,
        "--token", "dispatched=%d" % int(time.time()),
    ]
    if worktree:
        args += ["--token", "worktree=%s" % worktree]
    herdr(*args)


def find_member(snap, repo, key):
    wanted = sanitize_name(key)
    for member in crew_members(snap):
        if member["repo"] == repo and member["key"] == wanted:
            return member
    return None


def repo_root_for(path):
    proc = subprocess.run(
        ["git", "-C", path, "rev-parse", "--show-toplevel"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise CrewError("%s is not inside a git repository" % path)
    return proc.stdout.strip()


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
    if split is None:
        raise CrewError("cannot create a setup pane in dry-run")
    setup_pane = split["result"]["pane"]["pane_id"]
    tag_pane(setup_pane, key, repo, "setup", "")

    setup_name = ("setup-" + sanitize_name(key))[:32]
    herdr("agent", "start", setup_name, "--kind", "claude",
          "--pane", setup_pane, "--", "--model", "opus")
    herdr("agent", "prompt", setup_name,
          SETUP_PROMPT.format(key=key, artifact=artifact, repo=repo))

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

    repo_root = repo_root_for(os.getcwd())
    repo = repo or os.path.basename(repo_root)
    model = model or MODEL_BY_TYPE[ctype]

    ensure_crew_dir()
    lock_path = os.path.join(CREW_DIR, "dispatch-%s.lock" % sanitize_name(key))
    with _locked(lock_path, "w"):
        snap = snapshot()
        assert_snapshot_shape(snap, schema_defs())

        existing = find_member(snap, repo, key)
        if existing:
            print("already dispatched: %s/%s in pane %s (%s). "
                  "Resume with: cd %s && claude --continue"
                  % (repo, existing["key"], existing["pane"],
                     existing["bucket"], existing["worktree"]))
            mail_send(key, repo, "duplicate",
                      "dispatch declined, a live session already holds this key")
            return 5

        worktree = setup_worktree(key, repo, repo_root)

        tab = herdr("tab", "create", "--workspace", workspace,
                    "--label", "%s/%s" % (repo, sanitize_name(key)),
                    "--cwd", worktree, "--no-focus")
        pane = tab["result"]["root_pane"]["pane_id"]

        # Tag before agent.start. Tokens are authoritative, so an untagged
        # pane is invisible to ls; a tag that failed afterwards would leave a
        # live session unowned and burning shared quota.
        tag_pane(pane, key, repo, ctype, worktree)

        live = set(a.get("name") for a in snap["agents"] if a.get("name"))
        name = pick_name(key, live)

        start = ["agent", "start", name, "--kind", "claude", "--pane", pane,
                 "--", "--model", model,
                 "--append-system-prompt",
                 contract_pointer(name, ctype, key, repo, worktree)]
        if ctype == "planner":
            start += ["--permission-mode", "plan"]
        herdr(*start)

        assignment = (
            "You are dispatched on %s in %s. Your worktree is %s and the plan "
            "from /start-ticket is already there. Read it, then begin. Report "
            "with crew mail send --key %s when you settle." % (
                key, repo, worktree, sanitize_name(key)))
        try:
            herdr("agent", "prompt", name, assignment)
        except HerdrError as exc:
            if "stall" in str(exc).lower():
                print("DISPATCH FAILED: assignment did not land for %s. The "
                      "pane stays tagged, so crew ls shows it with no "
                      "assignment." % name, file=sys.stderr)
                return 6
            raise

        print("dispatched %s as %s in pane %s" % (key, name, pane))
        return 0
```

Wire into `main`:

```python
    if verb == "dispatch":
        if len(args) < 2:
            print("usage: crew dispatch <key> --type T [--repo R] [--model M]",
                  file=sys.stderr)
            return 2
        key = args[1]
        opts = {"--type": "implementer", "--repo": None, "--model": None}
        rest = args[2:]
        while rest:
            flag, value, rest = take_flag(rest, tuple(opts))
            if flag is None:
                raise CrewError("unexpected argument: %s" % rest[0])
            opts[flag] = value
        try:
            return cmd_dispatch(key, opts["--type"], opts["--repo"],
                                opts["--model"])
        except (CrewError, HerdrError) as exc:
            print("dispatch failed: %s" % exc, file=sys.stderr)
            return 1
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
python3 test_crew.py -v
```

Expected: PASS, 62 tests.

- [ ] **Step 6: Verify the dry-run sequence, especially the tag order**

```bash
crew --dry-run dispatch FANDEVX-0000 --type implementer --repo scratch 2>&1 | head -20
```

Expected: the printed `herdr` lines show `pane report-metadata` for the setup pane, and for the crew pane the `report-metadata` line appears **before** the `agent start` line. If that order is reversed, stop and fix it: it is the difference between a recoverable empty pane and an orphaned live session.

- [ ] **Step 7: Smoke test end to end on a scratch repo**

Do not use a sprint ticket for this.

```bash
mkdir -p /tmp/crew-smoke && cd /tmp/crew-smoke
git init -q && git commit -q --allow-empty -m "init"
crew dispatch spike-crew-smoke --type planner --repo crew-smoke
crew ls
```

Expected: `crew ls` shows one member, repo `crew-smoke`, key `spike-crew-smoke`, type `planner`, in the `awaiting` or `working` bucket. The setup pane is gone. Then, from the crew pane, confirm it reports:

```bash
crew mail unread
```

Expected: a `done` or `needs-input` line for `spike-crew-smoke` that the crew member sent unprompted, proving the contract pointer landed and survived into the session.

- [ ] **Step 8: Verify duplicate dispatch is declined, not duplicated**

```bash
crew dispatch spike-crew-smoke --type planner --repo crew-smoke; echo "exit=$?"
```

Expected: `already dispatched: ... Resume with: cd <worktree> && claude --continue` and `exit=5`. No second pane is created.

- [ ] **Step 9: Tear down the smoke test**

```bash
herdr tab list --workspace "$HERDR_WORKSPACE_ID"    # find the smoke tab id
herdr tab close <smoke_tab_id>
rm -rf /tmp/crew-smoke ~/.crew/dispatch-spike-crew-smoke.json
rm -f ~/.crew/mailbox.jsonl ~/.crew/cursor
```

- [ ] **Step 10: Commit**

```bash
git add stow-packages/claude/.claude/skills/foreman/scripts/
git commit -m "feat(crew): add dispatch with setup-pane handoff and tag-before-start

/start-ticket is interactive so a script cannot invoke it. It runs in an
ephemeral pane that hands off a JSON artifact rather than terminal output,
which would mean parsing ANSI from a REPL that never exits.

The crew pane is tagged before agent.start. Tokens are authoritative, so
a tag applied afterwards that failed would leave a live session invisible
to ls, unowned, and consuming shared quota."
```

If any herdr invocation observed in Step 1 differed from what this task's code assumes, record the correction as a second commit against `crew.py` rather than editing the message above:

```bash
git commit -m "fix(crew): correct herdr tab create flag form

Observed form differs from the herdr skill reference. Nested herdr --help
falls through to top-level help, so flag names have to be observed."
```

---

### Task 7: `crew peek` and `crew nudge`

Bounded reads and one-way messages. Small, and separable from dispatch.

**Files:**

- Modify: `stow-packages/claude/.claude/skills/foreman/scripts/crew.py`
- Modify: `stow-packages/claude/.claude/skills/foreman/scripts/test_crew.py`

**Interfaces:**

- Consumes: `herdr()` from Task 2.
- Produces:
  - `clamp_lines(requested) -> int`
  - `cmd_peek(name, lines) -> int`
  - `cmd_nudge(name, text) -> int`

- [ ] **Step 1: Write the failing test**

Extend the `crew` import with `clamp_lines`, then append:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
python3 test_crew.py -v
```

Expected: FAIL with `ImportError: cannot import name 'clamp_lines'`.

- [ ] **Step 3: Implement**

```python
PEEK_DEFAULT = 40
PEEK_CAP = 200


def clamp_lines(requested):
    if requested is None:
        return PEEK_DEFAULT
    return max(1, min(int(requested), PEEK_CAP))


def cmd_peek(name, lines):
    payload = herdr("agent", "read", name, "--source", "detection",
                    "--lines", str(clamp_lines(lines)))
    if payload is None:
        return 0
    text = payload.get("result", {}).get("output", "")
    if not text:
        print("(no output; the pane may be on an alternate screen)")
        return 0
    print(text)
    return 0


def cmd_nudge(name, text):
    herdr("agent", "prompt", name, text)
    print("nudged %s" % name)
    return 0
```

Wire into `main`:

```python
    if verb == "peek":
        if len(args) < 2:
            print("usage: crew peek <name> [--lines N]", file=sys.stderr)
            return 2
        lines = None
        if "--lines" in args:
            lines = args[args.index("--lines") + 1]
        try:
            return cmd_peek(args[1], lines)
        except HerdrError as exc:
            print("peek failed: %s" % exc, file=sys.stderr)
            return 1
    if verb == "nudge":
        if len(args) < 3:
            print("usage: crew nudge <name> \"<text>\"", file=sys.stderr)
            return 2
        try:
            return cmd_nudge(args[1], " ".join(args[2:]))
        except HerdrError as exc:
            print("nudge failed: %s" % exc, file=sys.stderr)
            return 1
```

The `result` key holding the text may differ from `output`. Confirm it against a live read and correct the code if so:

```bash
herdr agent read <some-live-agent> --source detection --lines 5 | python3 -m json.tool | head -20
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python3 test_crew.py -v
```

Expected: PASS, 66 tests.

- [ ] **Step 5: Verify peek does not clear the `done` state**

This is the property that keeps the load report honest. Using any live agent currently in `awaiting`:

```bash
crew ls | head -1
crew peek <name> --lines 10 | head -5
crew ls | head -1
```

Expected: the two `crew ls` count lines are identical. A CLI read must not mark the pane seen.

- [ ] **Step 6: Verify the cap**

```bash
crew --dry-run peek somebody --lines 99999
```

Expected: the printed command shows `--lines 200`, not `99999`.

- [ ] **Step 7: Commit**

```bash
git add stow-packages/claude/.claude/skills/foreman/scripts/
git commit -m "feat(crew): add peek and nudge with a bounded line count

peek defaults to 40 lines and caps at 200, and prefers the compact
detection snapshot, so an unbounded transcript cannot land in foreman
context by accident. CLI reads do not mark a pane seen, so peeking does
not clear the done state the load report depends on."
```

---

### Task 8: The foreman role

The last MVP piece. Turns the verbs into a role.

**Files:**

- Create: `stow-packages/claude/.claude/skills/foreman/SKILL.md`

**Interfaces:**

- Consumes: every `crew` verb from Tasks 2 to 7.
- Produces: a loadable skill that responds to "you're my foreman".

- [ ] **Step 1: Write the file**

```markdown
---
name: foreman
description: "Coordinate a fleet of herdr-hosted crew sessions. Use when the user says you are their foreman, asks for crew status, or asks to dispatch, peek at, nudge or retire a crew member. Runs every action through the `crew` CLI; requires HERDR_ENV=1."
---

# Foreman

You coordinate. You do not implement.

You read bounded crew output on demand and report it. You never ingest diffs,
plans, or full review transcripts. Those live in crew worktrees, which is the
point of having crew.

First, confirm you can act:

```bash
test "${HERDR_ENV:-}" = 1 && crew doctor
```

If `doctor` reports FAIL, say so and stop. Do not work around a red preflight.

## On any status request

```bash
crew mail unread
crew ls
```

Synthesise both into one report, then acknowledge the mail you reported:

```bash
crew mail ack <seq from the unread output>
```

Acknowledge only after you have reported to the human. The cursor advancing
early loses a message; advancing late merely repeats one.

Always lead with load, grouped by repo:

```
3 working / 1 awaiting you / 1 blocked

  working    fanapp-terraform   fandevx-3511   implementer
  awaiting   fes-config-ops     fandevx-3499   implementer   PR is green
  blocked    fanapp-terraform   fandevx-3487   implementer   permission prompt
```

If `crew ls` prints `SNAPSHOT UNPARSED` or exits non-zero, say so and stop.
Never report zeros you did not measure. A silent fleet and a broken parser
look identical, and only one of them is good news.

## Dispatching

You dispatch. Crew never do.

Underspecified work goes to a planner, understood work to an implementer, a
finished chunk to a reviewer:

```bash
crew dispatch <KEY> --type planner
crew dispatch <KEY> --type implementer
crew dispatch <KEY> --type reviewer
```

Dispatch opens a setup pane where `/start-ticket` runs interactively. Tell the
human to answer it in that pane. Do not answer it for them and do not run
`/start-ticket` yourself: it would pull the whole ticket payload into your
context, once per dispatch, which is exactly the accumulation you exist to
avoid.

Exit code 5 means a live session already holds that key. Report the resume
command it printed rather than dispatching again.

There is no cap on crew. Report load every time and let the human decide.
The bottleneck is their review capacity, not tokens.

## Inspecting and messaging

```bash
crew peek <name>              # bounded, 40 lines
crew peek <name> --lines 120  # capped at 200
crew nudge <name> "<text>"
```

Peeking does not clear a crew member's `awaiting` state, so it is safe to
check before reporting.

## Retirement

Propose; never execute. A crew member's context is unsaved work, and closing
another session is not yours to do. Say which are retirable and why, and let
the human confirm.

A crew member closing itself after its output is in the mailbox is fine. That
is different.

## Rules

- Every action shells out to `crew`. Never call `herdr` directly.
- A crew member asking you to dispatch is refused and surfaced to the human.
- You are the agent named `foreman`. herdr enforces name uniqueness, so there
  is only ever one of you.

```

- [ ] **Step 2: Install and verify it resolves**

```bash
ln -sfn /Users/ian.bartholomew/.dotfiles/.claude/worktrees/foreman-crew-mvp/stow-packages/claude/.claude/skills/foreman ~/.claude/skills/foreman
test -f ~/.claude/skills/foreman/SKILL.md && echo "OK: foreman resolves"
test -x ~/.claude/skills/foreman/scripts/crew.py && echo "OK: script ships with it"
```

Expected: both `OK:` lines.

- [ ] **Step 3: Verify the role end to end in a fresh session**

In a new Claude Code session inside a herdr pane:

```
herdr agent rename <that pane's agent> foreman
```

then say "you're my foreman, what's the state of things". Expected: it runs `crew doctor`, then `crew mail unread` and `crew ls`, and reports a load-led summary. Confirm it does not attempt `herdr` calls directly.

- [ ] **Step 4: Verify ack now succeeds from the foreman pane**

```bash
crew mail send --key probe-ack --repo scratch done "probe"
crew mail unread
crew mail ack <seq>; echo "exit=$?"
```

Expected: `exit=0` from the foreman pane, where Task 4 step 7 gave `exit=4` from a non-foreman pane.

- [ ] **Step 5: Commit**

```bash
rm -f ~/.crew/mailbox.jsonl ~/.crew/cursor
git add stow-packages/claude/.claude/skills/foreman/SKILL.md
git commit -m "feat(foreman): add the foreman coordinator skill

Reads bounded crew output on demand and never ingests diffs, plans or
review transcripts. Acknowledges mail only after reporting, so a
compaction in between repeats a message rather than losing it. Stops on
a red doctor or an unparsed snapshot rather than reporting zeros."
```

---

### Task 9: Record the baseline and open the PR

The spec's success criteria need a baseline captured before the tooling changes behaviour.

**Files:**

- Modify: `docs/superpowers/specs/2026-08-10-foreman-crew-design.md`

**Interfaces:**

- Consumes: `crew ls` from Task 3.
- Produces: a dated baseline in the spec, and a PR.

- [ ] **Step 1: Capture the baseline**

```bash
crew ls > /tmp/crew-baseline.txt
cat /tmp/crew-baseline.txt
```

- [ ] **Step 2: Record it in the spec's Success criteria section**

Append the captured untagged count and date under the existing baseline paragraph, so the 2026-08-24 review compares against a measured number rather than a remembered one.

- [ ] **Step 3: Run the full check one more time**

```bash
cd ~/.dotfiles/.claude/worktrees/foreman-crew-mvp/stow-packages/claude/.claude/skills/foreman/scripts
python3 test_crew.py
crew doctor
```

Expected: all tests pass, `doctor` prints `OK`.

- [ ] **Step 4: Local code review before the PR**

Per the user's git conventions, run the `feature-dev:code-reviewer` agent over the branch diff and address any high-confidence findings, or record why they are being ignored.

- [ ] **Step 5: Commit and open the PR**

```bash
git add docs/superpowers/specs/2026-08-10-foreman-crew-design.md
git commit -m "docs(crew): record the measured baseline for the review date"
git push -u origin foreman-crew-mvp
gh pr create --title "feat(crew): foreman and crew session orchestration over herdr" --body "$(cat <<'BODY'
Makes live agent sessions first-class. A `crew` CLI names, tags, lists and
dispatches herdr-hosted Claude sessions, and a foreman role reports on them.

Shipped (spec build-order steps 1 to 7):

- `herdr/SKILL.md` relocated so the reference actually loads. It sat at
  `~/.claude/skills/herdr.md`, which Claude Code never discovers, and was
  untracked so it was not backed up.
- `crew doctor`, `crew ls`, `crew ls --json`, `crew mail`, `crew dispatch`,
  `crew peek`, `crew nudge`, and `--dry-run`.
- `foreman/SKILL.md` and `crew-member/SKILL.md`.

Not shipped, deferred to a follow-on plan: `crew watchdog`, `crew watch`,
`crew retire`, `crew recover`, `crew log`, `crew uninstall`. Until the
watchdog exists, blocked crew are visible in `crew ls` but nobody is
notified, and a stalled or dead crew member reads as idle.

Design decisions worth knowing:

- Pane tokens are the authoritative record, not path derivation. A pane's
  `foreground_cwd` follows the transient foreground process, so a crew
  member that changes directory would make derivation return the wrong key
  rather than no key.
- The crew pane is tagged before `agent.start`, because an untagged pane is
  invisible to `crew ls` by design and a failed tag afterwards would orphan
  a live session.
- There is no session id. `claude --continue` is directory-scoped and a crew
  member is one-to-one with its worktree, so resume needs no stored handle.
- `crew ls` fails closed. A renamed herdr field exits non-zero rather than
  printing a zeroed load report indistinguishable from a quiet fleet.

Spec: `docs/superpowers/specs/2026-08-10-foreman-crew-design.md`, reviewed
over two adversarial rounds. Success criteria review date is 2026-08-24.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

Then print the PR URL as a bare link on its own line, not markdown link syntax, so it is clickable in the terminal.

- [ ] **Step 6: Verify the PR**

Dispatch the `pr-gate` subagent to poll every CI check to terminal state. Do not call the PR mergeable while checks are red or still running.

---

## Deferred to the next plan

Spec build-order steps 8 to 10, which depend on this one:

- `crew watchdog`: the timeout-loop plus snapshot reconcile, `~/.crew/watchdog.state`, the heartbeat that `crew ls` reports as stale, and the liveness probe. Note the open risk: targeting another pane with `pane process-info` has no confirmed CLI flag, so this may need a direct socket call.
- `crew watch`: the CI watcher pane and its escalation to a Sonnet diagnostic agent.
- `crew retire`, `crew recover`, `crew log`, `crew uninstall`.
- Wiring `crew log` into `finish-work` Step 7 and `end-of-day`, once the `finish-work-verification-fixes` branch lands.

Until the watchdog exists, `blocked` crew are visible in `crew ls` but nobody is notified about them, and a stalled or dead crew member reads as idle. That gap is known and is why the watchdog is the first item in the follow-on plan.
