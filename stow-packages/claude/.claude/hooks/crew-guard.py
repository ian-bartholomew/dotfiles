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


def is_crew_session(payload):
    """A crew member works inside <repo>/.claude/worktrees/<branch>.

    Deliberately conservative: if the cwd is unknown, treat the session as NOT
    crew and allow. A guard that blocks the human's own shell on ambiguity would
    be turned off within the hour, and an unenforced boundary is what this
    mitigates rather than a sandbox it replaces.
    """
    cwd = payload.get("cwd") or ""
    return WORKTREE_MARKER in cwd


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0
    if not is_crew_session(payload):
        return 0

    command = (payload.get("tool_input") or {}).get("command") or ""
    why = forbidden_reason(command)
    if why:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "crew-guard: %s. This session is a crew member, working in "
                    "%s. Report what you need with `crew mail send` and let the "
                    "foreman or the human act."
                    % (why, payload.get("cwd", "a worktree"))
                ),
            }
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
