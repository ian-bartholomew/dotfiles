#!/usr/bin/env python3
"""Deny a crew member the commands and tools that let it spend money or steer
its peers.

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

Tools come in two kinds, and a boundary drawn around one tool gets drawn by
accident. FORBIDDEN_TOOLS denies by tool name, for a tool whose whole effect is
the thing being denied. COMMAND_FIELDS names the field a tool carries a shell
command in, and the command decides, so the tool itself stays usable. Both are
one-line additions, which is the point: the next tool of either kind should not
need an `if` in here.

Whatever this hook is matched on in settings is the whole of what it can ever
see, so the matcher and both tables have to change together: an entry here that
the matcher does not deliver is inert, and looks enforced.

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

# Tool name -> why. `SendMessage` addresses another live Claude session, so a
# crew member using it reports outside the mailbox entirely: no seq, no cursor,
# no ack, and nothing in the JSONL the foreman's digest reads. The design's
# claim that `crew mail send` is a crew member's only outbound channel is true
# only while this entry exists. It is also the laundering route, since a peer
# can be asked to run the `crew dispatch` the sender is denied above.
#
# Deliberately absent, because the design depends on them: `Agent` and every
# other subagent-spawning tool, since a reviewer crew member fans out subagents
# and a subagent's own Bash calls arrive back here anyway; `ListAgents`, because
# discovery is not the boundary and sending is; and every ordinary file, search
# and task tool.
FORBIDDEN_TOOLS = {
    "SendMessage": "messaging another session reports outside the mailbox, "
                   "which is a crew member's only outbound channel",
}

# Tool name -> the tool_input field it carries a shell command in. The command
# decides, through the same FORBIDDEN table Bash uses, so the tool stays usable.
#
# `Monitor` is here rather than in FORBIDDEN_TOOLS because it is legitimate work:
# a crew member watching its own build or test log is what it is for. But its own
# description says the script "runs in the same shell environment as Bash", so
# the command it carries reaches every entry in FORBIDDEN, and it was the whole
# Bash table made optional by choosing a different tool name.
#
# A missing or empty field allows. `Monitor` also takes a `ws` form that carries
# no command at all, and denying a call for lacking a command it never has would
# break a legitimate watch.
COMMAND_FIELDS = {
    "Bash": "command",
    "Monitor": "command",
}

WORKTREE_MARKER = os.path.join(".claude", "worktrees")


def program_name(token):
    """The invoked program, normalised. A path, an interpreter prefix and the
    .py form all resolve to the same name."""
    name = os.path.basename(token)
    return name[:-3] if name.endswith(".py") else name


def forbidden_reason(command):
    r"""Two passes, because neither alone is enough.

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


def refusal_reason(payload):
    """Why a crew member is refused this call, or None.

    Name first, because it is a dict lookup and a tool denied outright has no
    command to parse. A tool in neither table is not this hook's business, which
    keeps `Agent`, `ListAgents` and the ordinary file and search tools out of the
    way even if the settings matcher starts delivering them.
    """
    tool = payload.get("tool_name")
    why = FORBIDDEN_TOOLS.get(tool)
    if why is not None:
        return why
    field = COMMAND_FIELDS.get(tool)
    if field is None:
        return None
    return forbidden_reason((payload.get("tool_input") or {}).get(field) or "")


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

    # The call is classified BEFORE membership, because membership costs a herdr
    # round trip and this hook runs on every Bash call. A call that is not
    # forbidden is allowed for crew and human alike, so the round trip would buy
    # nothing; ordering it second keeps the ordinary call decided by a dict
    # lookup and string work alone.
    why = refusal_reason(payload)
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
