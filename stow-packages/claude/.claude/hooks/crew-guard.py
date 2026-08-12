#!/usr/bin/env python3
"""Deny a crew member the commands that let it spend money or steer its peers.

The crew-member contract asks a crew session not to dispatch, nudge or close
another session. That is a convention: herdr's socket has no authorization, so
any process running as the user has full control, and `crew`'s own foreman check
reads an environment variable the caller can set. Nothing inside `crew` can
enforce the boundary. This hook is the enforcement.

Scope, stated honestly. It stops a confused or prompt-injected agent following
instructions in plain text, which is the realistic threat: peeked terminal
output reaches the foreman's context, and a crew member reads untrusted diffs
and logs. It does NOT stop a determined process, which can evade any
string-matching gate by encoding the command, writing a wrapper, or calling the
socket directly. Treat it as a guard rail, not a sandbox.

`herdr pane run` is blocked below, but understand what that buys: the command
it carries would execute in a pane shell, where no PreToolUse hook fires at
all. Blocking the invocation stops the accident. Nothing here stops the intent.
"""
import json
import os
import shlex
import subprocess
import sys

# (program, verb tuple, why). Each spends money, steers another session, or
# destroys one.
#
# The pane-level verbs are here because every one of them reaches an effect
# already blocked one layer up: `pane run` runs the blocked command, `pane
# send-keys` types it, `pane report-metadata --clear-token` erases the record
# crew treats as authoritative, and `pane report-agent` fakes the lifecycle
# state the foreman reads. Blocking `agent send-keys` while allowing `pane
# send-keys` gates the label, not the effect.
FORBIDDEN = (
    ("crew", ("dispatch",),
     "dispatching spends a paid session, and only the foreman dispatches"),
    ("crew", ("nudge",), "nudging steers another session"),
    ("crew", ("retire",),
     "retiring closes a pane and a tab, which is the human's to confirm"),
    ("crew", ("mail", "ack"), "only the foreman acknowledges mail"),
    ("crew", ("claim-foreman",), "only the foreman claims the foreman name"),
    ("herdr", ("agent", "start"), "starting an agent spends a paid session"),
    ("herdr", ("agent", "prompt"), "prompting another agent steers it"),
    ("herdr", ("agent", "rename"), "renaming an agent is the foreman's to do"),
    ("herdr", ("agent", "send-keys"), "sending keys drives another session"),
    ("herdr", ("pane", "close"), "closing a pane is the human's to confirm"),
    ("herdr", ("pane", "run"),
     "running a command in another pane runs it as that session"),
    ("herdr", ("pane", "send-keys"), "sending keys drives another session"),
    ("herdr", ("pane", "send-text"), "sending text drives another session"),
    ("herdr", ("pane", "report-metadata"),
     "pane tokens are crew's authoritative record of who owns what"),
    ("herdr", ("pane", "report-agent"),
     "reporting a lifecycle state fakes what the foreman reads"),
    ("herdr", ("pane", "report-agent-session"),
     "reassigning an agent session rewrites who a pane belongs to"),
    ("herdr", ("pane", "release-agent"),
     "releasing an agent detaches it from the fleet"),
    ("herdr", ("pane", "move"), "moving a pane relocates another session"),
    ("herdr", ("pane", "swap"), "swapping panes relocates another session"),
    ("herdr", ("tab", "close"), "closing a tab is the human's to confirm"),
    ("herdr", ("workspace", "close"),
     "closing a workspace is the human's to confirm"),
    ("herdr", ("server", "stop"), "stopping the server kills every pane"),
)

WORKTREE_MARKER = os.path.join(".claude", "worktrees")


def program_name(token):
    """The invoked program, normalised. A path, an interpreter prefix and the
    .py form all resolve to the same name."""
    name = os.path.basename(token)
    return name[:-3] if name.endswith(".py") else name


def forbidden_reason(command):
    """Two passes, because neither alone is enough.

    The first version matched raw text with patterns like `\bcrew\s+dispatch`.
    That is bypassed by `.py`, which sits between the name and the space:
    verified, `python3 .../crew.py dispatch K` was ALLOWED, so the one
    enforcement of this boundary was defeated by five characters.

    Matching is on the tokenised program name, normalised, so the .py form, a
    bare path and an interpreter prefix all resolve to the same program.

    A raw substring scan was tried as a second pass and removed. It caught
    `sh -c "crew dispatch"`, but that is the wrapper evasion the module
    docstring already accepts, and it also denied
    `git commit -m "crew dispatch mentioned in a message"`. Blocking real work
    to catch an evasion already conceded is a bad trade: a guard that stops
    legitimate commits gets switched off.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    for index, token in enumerate(tokens):
        name = program_name(token)
        if name not in ("crew", "herdr"):
            continue
        rest = [t for t in tokens[index + 1:] if not t.startswith("-")]
        for program, verbs, why in FORBIDDEN:
            if name == program and tuple(rest[:len(verbs)]) == verbs:
                return why

    return None


SNAPSHOT_TIMEOUT = 5


def pane_crew_token():
    """Whether the pane hosting this session carries crew's own `crew=true`
    token: True, False, or None when that cannot be established.

    The pane token is the record this design calls authoritative, and it is the
    only signal that sees the setup pane. `start_setup` splits that pane with
    `--cwd <repo root>`, so the cwd test below reads an ordinary checkout while
    the pane is tagged `crew=true, type=setup` and hosts a live paid session
    that can dispatch, close panes and prompt peers. It is also the session most
    exposed to untrusted input, because `/start-ticket` pulls JIRA descriptions
    and comments into its context.

    The same token fixes the opposite error, where any ordinary session that
    happened to be in a worktree was classified as crew.

    None means unknown, not false: no pane id, no herdr on PATH, a socket that
    does not answer, or a pane the snapshot does not list. The caller then falls
    back to the cwd test, so an unreadable snapshot from a crew worktree still
    denies. A crew member that strips HERDR_PANE_ID lands on that same fallback,
    and one that also leaves its worktree is the determined evasion this
    module's docstring already concedes.
    """
    pane_id = os.environ.get("HERDR_PANE_ID", "")
    if not pane_id:
        return None
    try:
        proc = subprocess.run(["herdr", "api", "snapshot"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=SNAPSHOT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        panes = json.loads(proc.stdout)["result"]["snapshot"]["panes"]
    except (ValueError, KeyError, TypeError):
        return None
    for pane in panes:
        if pane.get("pane_id") == pane_id:
            return (pane.get("tokens") or {}).get("crew") == "true"
    return None


def is_crew_session(payload):
    """Membership from the pane token, falling back to the cwd.

    The cwd test alone is wrong in both directions: it misses the setup pane,
    which is crew by design and sits at the repo root, and it claims any session
    working in any worktree.

    Still deliberately conservative where nothing can be established: with no
    token and no worktree cwd, treat the session as NOT crew and allow. A guard
    that blocks the human's own shell on ambiguity would be turned off within
    the hour, and an unenforced boundary is what this mitigates rather than a
    sandbox it replaces.
    """
    token = pane_crew_token()
    if token is not None:
        return token
    return WORKTREE_MARKER in (payload.get("cwd") or "")


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    # The command is classified BEFORE membership, because membership now costs
    # a herdr round trip and this hook runs on every Bash call. A command that
    # is not forbidden is allowed for crew and human alike, so the round trip
    # would buy nothing; ordering it second keeps the ordinary call decided by
    # string work alone.
    command = (payload.get("tool_input") or {}).get("command") or ""
    why = forbidden_reason(command)
    if why and is_crew_session(payload):
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "crew-guard: %s. This session is a crew member, working in "
                    "%s. Report what you need with `crew mail send` and let the "
                    "foreman or the human act."
                    # The token path can deny a session whose cwd is unknown or
                    # is an ordinary checkout, so this cannot assume a worktree.
                    % (why, payload.get("cwd") or "an unreported directory")
                ),
            }
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
