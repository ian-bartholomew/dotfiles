---
name: start-crew
description: "Start a foreman session and dispatch one crew member per ticket. Use when the user runs /start-crew, or says to start crew on some tickets, dispatch crew for these stories, or spin up crew members for a list of keys. Takes one or more JIRA keys or free-form slugs as arguments. Requires HERDR_ENV=1. This is the entry point for the foreman workflow; do not hand-roll the wording."
---

# Start Crew

Turns this session into a foreman and dispatches one crew member per ticket
given. It exists so the exact wording and the bootstrap order do not have to be
remembered: getting either wrong produces a foreman that implements code itself,
or a mailbox that silently repeats forever.

Arguments are the tickets to work, space separated:

```
/start-crew FANDEVX-3401 FANDEVX-3402
/start-crew FANDEVX-3401 --type planner
/start-crew spike-cassandra-tombstones
```

Parse these strictly: a token that does not start with `-` is a key, and
`--type X` applies to every key given, not just the one before it. In the
second example above, `planner` is the VALUE of `--type`, not a second key;
dispatching it as one starts a paid session on a branch named `planner`,
because `planner` is otherwise a perfectly valid slug. A token that looks
like a flag but is not recognised is an error worth stopping on, not
something to guess your way past.

**One ticket per crew member. Never an epic, never a subtask.** Subtasks are a
crew member's own to-do list. An epic is the container you pick stories from,
and decomposing it is `/decompose-ticket`, which runs BEFORE this skill and not
inside it.

## Step 1: Refuse early if this cannot work

```bash
test "${HERDR_ENV:-}" = 1 || echo "not inside a herdr pane"
crew doctor
```

If not inside herdr, stop: there is no pane to name and no fleet to dispatch
into. If `doctor` reports FAIL, report the failing lines and stop. Do not work
around a red preflight; it is checking herdr's protocol, the claude CLI flags
this depends on, and the file permissions on the mailbox.

## Step 2: Claim the name

```bash
crew claim-foreman
```

Not optional and not one-time. A herdr agent name binds to the AGENT occupying
a pane, not to the pane, and is cleared when that agent exits. Verified:
renaming a pane that holds no agent fails with `agent_not_found`. So this cannot
be done before the session starts, and it must be redone after a `/clear` or a
restart.

Skip it and `crew mail ack` refuses with exit 4, so the same crew reports get
read out every single time without ever clearing.

If it refuses because another pane already holds the name, stop and tell the
user which pane. There is one foreman by design.

## Step 3: Adopt the role

Read `~/.claude/skills/foreman/SKILL.md` and follow it for the rest of the
session. The parts that matter most, so they are not lost:

- You coordinate. You do NOT implement. No editing files, no builds, no tests,
  no git, no pull requests. If you catch yourself about to change a file,
  dispatch instead.
- You read bounded crew output on demand with `crew peek`, and never ingest
  diffs, plans or full review transcripts.
- Every action shells out to `crew`. Never call `herdr` directly.
- Retirement is proposed, never executed. The human confirms.

## Step 4: Dispatch, one per ticket

`crew dispatch` defaults `--repo` to the repo you are running in, which is the
foreman's own. A key for any other repository needs `--repo` explicitly, or
the dispatch lands in the wrong repository and its tokens disagree with the
worktree `/start-ticket` actually made, so `find_member` cannot see the
result and a retry spends a second paid session. If the user did not say
which repo a key belongs to, ask before dispatching it.

For each key given, in order:

```bash
crew dispatch <KEY> --type implementer --repo <repo-name>
```

Use `--type planner` for a ticket the user says is underspecified, and
`--type reviewer` for a finished chunk that needs review. Default to
`implementer` when the user did not say.

For a JIRA key this returns immediately with exit 7 and opens a short-lived
setup pane where `/start-ticket` runs interactively. **Tell the user to answer
it in that pane.** Do not answer it for them, and do not run `/start-ticket`
yourself: it pulls a whole ticket payload into your context, once per
dispatch, which is the accumulation you exist to avoid. Once they have
answered it, re-run the exact same `crew dispatch` command to finish: it
picks up the artifact `/start-ticket` wrote and completes without opening a
second setup pane. A free-form slug has no ticket to fetch, so no setup pane
appears, nothing needs answering, and dispatch completes in this one call.

Handle these exit codes rather than retrying blindly:

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | dispatched | continue to the next key |
| 5 | a live session already holds this key | report the resume command it printed; do NOT dispatch again |
| 6 | started but never reacted, delivery unconfirmed | the pane is tagged and visible; suggest `crew nudge <name> "<text>"` |
| 7 | setup is pending on a JIRA key | tell the user to answer the prompt in the named pane, then re-run the exact same dispatch command |
| 3 | a herdr, crew or filesystem error | report the message and stop |

## Step 5: Report and hand back

```bash
crew ls
```

Report the load first, grouped by repo, then say plainly what the user needs to
do next. That is usually one of: answer a setup prompt in a named pane, or wait.

Then stop and let them drive. Do not poll, and do not dispatch anything they did
not name.

## What to push back on

**More than about three implementers at once.** There is no cap in the tool by
design, but each crew member becomes a pull request the user has to read. The
pattern this came from was abandoned by its author for exactly this reason:
"human QC is the main bottleneck for almost everything, and nothing sucks more
than reading 100 PRs cold." If the user names more than three, say so once, then
do what they asked.

**An epic key.** Dispatching an epic gives one crew member an unbounded job.
Suggest `/decompose-ticket` first.

**A ticket with no plan and no detail.** Suggest `--type planner` for that one.

## Gaps the user should know about, once

Say these only if relevant, and only once per session:

- **Nothing pings them.** There is no watchdog yet, so a crew member stopped at
  a permission prompt sits silently, and status has to be asked for. A crew
  member killed by the OS does not read as idle forever: its pane loses its
  agent, `crew ls` buckets it to `recover`, and the report shows "need
  recovery".
- **The guard hook needs a fresh session to take effect.** If `crew-guard` was
  installed during this session, it is not live until `/hooks` is opened or
  Claude restarts. Nothing in this build registers the hook, and `crew doctor`
  does not check that it is registered, so a green preflight says nothing about
  it. Confirm it in `/hooks`. Until it is live, nothing stops a crew member
  dispatching more paid sessions.
- **This skill cannot be run from inside a worktree.** The guard hook decides
  who is a crew member by testing whether the cwd contains
  `.claude/worktrees`, as a substring, so any ordinary session in any worktree
  is classified as crew and denied `crew dispatch` and `crew claim-foreman`.
  Start the foreman from the repo root, or anywhere outside a worktree. If
  those two commands are being denied and the session is not crew, this is
  why.

## Related

- `/decompose-ticket`: run first, to turn an epic into stories
- `~/.claude/skills/foreman/SKILL.md`: the role this skill adopts
- `~/.claude/skills/crew-member/SKILL.md`: the contract each crew member gets
