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
"""
import json
import os
import re
import sys

# Commands a crew member must not run. Each spends money, steers another
# session, or destroys one.
FORBIDDEN = (
    (r"\bcrew\s+dispatch\b", "dispatching spends a paid session, and only the foreman dispatches"),
    (r"\bcrew\s+nudge\b", "nudging steers another session"),
    (r"\bcrew\s+mail\s+ack\b", "only the foreman acknowledges mail"),
    (r"\bherdr\s+agent\s+start\b", "starting an agent spends a paid session"),
    (r"\bherdr\s+agent\s+prompt\b", "prompting another agent steers it"),
    (r"\bherdr\s+agent\s+send-keys\b", "sending keys drives another session"),
    (r"\bherdr\s+(pane|tab|workspace)\s+close\b", "closing a pane is the human's to confirm"),
    (r"\bherdr\s+server\s+stop\b", "stopping the server kills every pane"),
)

WORKTREE_MARKER = os.path.join(".claude", "worktrees")


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
    for pattern, why in FORBIDDEN:
        if re.search(pattern, command):
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "crew-guard: %s. This session is a crew member, working "
                        "in %s. Report what you need with `crew mail send` and "
                        "let the foreman or the human act."
                        % (why, payload.get("cwd", "a worktree"))
                    ),
                }
            }))
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
