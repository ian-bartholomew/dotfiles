#!/usr/bin/env python3
"""Close a settled crew member's session so its pane can be retired.

    close-crew.py <key> [--allow-open-pr] [--dry-run]

Identifies the session by its own command line, refuses if it still owns an open
PR, sends SIGTERM, and confirms the process exited. Retiring the pane afterwards
is deliberately left to `crew retire`, so the handle comes from `crew ls` rather
than from a guess: a key holding both an implementer and a reviewer needs a pane
id, not the key.

Why SIGTERM and not -9: measured 2026-08-14, SIGTERM runs the session's
SessionEnd hooks (honcho registers one) and -9 skips them.

Exit codes
  0  session closed, pane should now read `recover`
  1  bad usage
  2  could not identify exactly one session for that key
  3  refused: the crew member still owns an open PR
  4  SIGTERM sent but the process did not exit
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time

WAIT_SECS = 30


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def find_sessions(key):
    """Return [(pid, worktree)] for sessions whose own prompt names this key.

    Matching on the command line is the only trustworthy identification. A
    reviewer shares the implementer's worktree, so matching on cwd returns two
    crew members and picking one is a coin flip.
    """
    p = run(["ps", "-eo", "pid=,command="])
    out = []
    needle = f"crew member `{key}`"
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line or needle not in line:
            continue
        pid_str, _, cmd = line.partition(" ")
        if not pid_str.isdigit():
            continue
        if not re.search(r"(^|/)claude\b", cmd.split(" ", 1)[0]):
            continue  # skip wrappers and greps that merely mention the key
        m = re.search(r"worktree (\S+)", cmd)
        # the prompt ends the sentence, so the captured path can carry trailing
        # punctuation; a stray '.' silently breaks the isdir check below
        wt = m.group(1).rstrip(".,;:'\")") if m else None
        t = re.search(r"\btype (\w+)", cmd)
        ctype = t.group(1) if t else None
        out.append((int(pid_str), wt, ctype))
    return out


def open_prs(worktree):
    """Open PRs whose head is this worktree's branch. [] if none, None if unknown."""
    if not worktree or not os.path.isdir(worktree):
        return None
    b = run(["git", "-C", worktree, "rev-parse", "--abbrev-ref", "HEAD"])
    if b.returncode != 0:
        return None
    branch = b.stdout.strip()
    g = run(["gh", "pr", "list", "--head", branch, "--state", "open",
             "--json", "number,url,title"], cwd=worktree)
    if g.returncode != 0:
        return None
    try:
        return json.loads(g.stdout or "[]")
    except json.JSONDecodeError:
        return None


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--allow-open-pr", action="store_true",
                    help="close even though the crew member still owns an open PR")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    found = find_sessions(a.key)
    if len(found) != 1:
        print(f"REFUSED: found {len(found)} sessions naming '{a.key}', need exactly 1")
        for pid, wt, ct in found:
            print(f"  pid={pid} type={ct} worktree={wt}")
        print("A reviewer's key carries a -2 suffix; close each by its own key.")
        return 2

    pid, worktree, ctype = found[0]
    print(f"key={a.key} pid={pid} type={ctype or 'unknown'}")
    print(f"worktree={worktree}")

    # A reviewer does not own the PR: it joins the implementer's worktree, so a
    # branch-based PR lookup finds the IMPLEMENTER's PR and refuses wrongly. Its
    # deliverable is its report, which is already in the mailbox by the time we
    # get here. Only gate the crew member that actually owns the branch.
    if ctype == "reviewer":
        print("type=reviewer, so the PR gate does not apply: a reviewer shares the")
        print("implementer's worktree and owns no PR of its own. Skipping PR check.")
        prs = []
    else:
        prs = open_prs(worktree)

    if prs is None:
        # Fail closed. An unreadable worktree or a gh failure is not evidence of
        # no PR, and this gate exists precisely to stop a session being closed
        # while it still owns unmerged work.
        print(f"REFUSED: could not determine PR state for {worktree}")
        print("Unknown is not the same as none. Check by hand, then pass")
        print("--allow-open-pr if you are sure there is nothing unmerged.")
        if not a.allow_open_pr:
            return 3
        print("--allow-open-pr given, proceeding on your word")
    elif prs:
        print(f"REFUSED: {len(prs)} open PR(s) still owned by this crew member:")
        for pr in prs:
            print(f"  #{pr['number']}  {pr['url']}")
        if not a.allow_open_pr:
            print("A crew member owns its PR until merged and applied. Pass")
            print("--allow-open-pr only if you are deliberately abandoning it.")
            return 3
        print("--allow-open-pr given, proceeding anyway")
    else:
        print("open PRs: none")

    if a.dry_run:
        print(f"DRY RUN: would send SIGTERM to {pid}")
        return 0

    os.kill(pid, signal.SIGTERM)
    print(f"SIGTERM sent to {pid}, waiting up to {WAIT_SECS}s")
    for _ in range(WAIT_SECS):
        time.sleep(1)
        if not alive(pid):
            print(f"pid {pid} exited")
            print(f"Now run: crew ls   (expect {a.key} as `recover`)")
            print("then retire it with the handle crew ls proposes")
            return 0
    print(f"pid {pid} still alive after {WAIT_SECS}s. Do NOT escalate to -9:")
    print("that skips SessionEnd hooks. Look at the pane instead.")
    return 4


if __name__ == "__main__":
    sys.exit(main())
