# Foreman and Crew: session-level orchestration over herdr

Status: reviewed. Two adversarial rounds, five reviewers each (three Claude personas, Codex, Antigravity). Round 1 produced 4 rejects and 18 findings; round 2 produced 2 rejects and 8 findings, all of which were consequences of the round 1 fixes. Both rounds are incorporated. Round 3 was deliberately skipped because the round 2 fixes were subtractive.
Date: 2026-08-10
Review date: 2026-08-24 (see Success criteria)
Origin: r/ClaudeCode thread by u/OnRedditAtWorkRN (<https://www.reddit.com/r/ClaudeCode/comments/1viwdnh/comment/p2gurp2/>), adapted rather than replicated

## Problem

Work is spread across many long-lived agent sessions with no coordinator. A live snapshot taken while writing this spec:

```
(unnamed)  idle     wQ:pE   Maintain backwards compatibility for existing subs
(unnamed)  idle     wQ:pT   Align inf-dev environment to dev
identity   idle     wQ:pW   identity
(unnamed)  idle     wS:p1   Understand cost item definition
(unnamed)  idle     wS:pM   Fix permissions checks on start-of-day routine
(unnamed)  working  wV:p1   Build workflow from Reddit user setup
```

Six sessions, five unnamed, three workspaces. Each holds real half-finished context that has to be rediscovered by hand.

The existing skill set does not close this gap. Every skill reasons about durable state (JIRA tickets, PRs, branches, worktrees, project logs). None of them know a live agent process exists. `verify-status` can report that FANDEVX-3511 is In Progress with no PR; it cannot report that a warm session is already 40 minutes into it, idle, waiting for input. So it recommends starting cold next to the session that was forgotten.

The missing primitive is the session dimension, plus a coordinator whose context stays clean enough to be useful across a week.

## Success criteria

The claim being tested is that naming and tracking sessions changes behaviour. It is falsifiable, and this section exists so the answer is not decided by sunk cost.

Baseline, 2026-08-10: six live agent sessions, five carrying no name, spread over three workspaces. Zero are attributable to a ticket without opening them.

Second measurement, 2026-08-11, taken from the finished tool: `0 working / 0 awaiting you / 0 blocked`, with three untagged sessions and zero tagged crew. Note what that comparison shows: the untagged count fell from five to three purely through ordinary work, with none of it attributable to this tooling. **A point-in-time count is therefore a weak metric.** The signal that actually matters is the first row of the table below, whether NEW sessions arrive dispatched-and-tagged or started by hand, and it must be read as a rate rather than a level.

Review on 2026-08-24. Measured from the mailbox (never pruned) and a `crew ls` snapshot:

| Signal | Source | Success | Falsified |
| --- | --- | --- | --- |
| **New** sessions started untagged after the tooling exists | `crew ls` untagged count, minus the six baseline panes | Near zero | New untagged panes still appearing, meaning dispatch is being bypassed |
| Cold restarts on work that already had a live crew session | A line in the mailbox written by `crew dispatch` when it declines a duplicate key | None | Recurring, meaning `crew ls` is not consulted before starting work |
| `crew ls` invocations per working day | NOT INSTRUMENTED. `crew ls` writes no counter, so this row cannot be measured as written. Count by hand or drop the row | Daily or better | Rarely, meaning the roster is not what was missing |
| Time from a crew reaching `done` to being acted on | `done` mail timestamp versus the `retire` or next-`nudge` timestamp for that key | Minutes | Hours, meaning notification is not the bottleneck |

Each row names the mechanism that measures it, because an earlier draft asserted metrics nothing collected.

The baseline six panes are excluded deliberately. The adoption decision was greenfield-only, so those five untagged sessions will never be tagged and can only be retired by hand. An earlier draft set "untagged sessions trending to zero" as a success signal, which that decision makes unachievable; the metric was wrong, not the decision.

Kill condition: if at review the author is still opening panes by hand to work out what they contain, the premise is wrong. Keep `crew ls` and `crew doctor`, delete the foreman and crew-member skills, and stop.

Explicit anti-goal: do not measure success by how much of the design got built.

## Non-goals

Out of scope because the capability already exists:

| Capability | Already provided by |
| --- | --- |
| Review fan-out across lenses | `terraform-review`, `feature-dev:code-reviewer`, `security-review` |
| PR-to-green loop | `pr-gate`, `gh-run-watch`, `fes-deployments` |
| Worktree lifecycle | `start-ticket`, `finish-work`, `superpowers:using-git-worktrees` |
| Decompose and fan out one task | `parallel-code-build`, `parallel-infra-build` |

Also out of scope: a web-based planning tool, a numeric concurrency cap, and any foreman-owned state file.

The source thread's author reports that decompose-and-fan-out is "more hype than effective" and works one contained feature or bug per session. This design follows that, and does not split a single feature across crew.

## Verified substrate

herdr 0.7.5, protocol 17, server running. Confirmed against the live socket API and `herdr api schema --json` on 2026-08-10, not assumed.

| Need | Mechanism | Notes |
| --- | --- | --- |
| Spawn a crew session | `tab.create`, then `agent.start` | `agent.start` requires `name`, `kind`, `pane_id`; accepts `args` after `--`; startup timeout 3000 to 300000 ms |
| Send work | `agent.prompt` | atomic text plus Enter; returns `agent_prompt_stalled` if no lifecycle change within 5s |
| Block until settled | `agent.wait --until idle,done,blocked` | |
| Peek | `agent.read --source detection\|recent-unwrapped --lines N` | CLI reads do **not** mark a pane seen |
| Roster and status | `herdr api snapshot` | per-agent `agent_status`, `name`, `cwd`, `foreground_cwd`, `terminal_title_stripped`, `pane_id`, `tab_id`, `workspace_id`, `state_change_seq` |
| Assignment record | `pane report-metadata <pane> --source crew --token k=v` | persists with `ttl_ms` omitted; round-trips as `tokens`; cleared with `--token k=`; 32 keys max per pane (a single `report-metadata` call carries at most 16), key pattern `^[A-Za-z0-9_-]{1,32}$`; **a value is SILENTLY TRUNCATED at 80 characters**, which the schema does not declare |
| State-change events | `events.wait --match pane.agent_status_changed` | the watchdog's only input |
| Notify the human | `notification.show --title --body --sound` | |
| Run a plain command in a pane | `pane.run`, `pane.wait-output --match\|--regex` | |

Measured on 2026-08-10, and the second measurement corrects the first.

Eight tokens persisted and round-tripped, and values are not subject to the key pattern. But a value is **silently truncated at 80 characters**: 79 and 80 store intact, 100 and 128 both store as 80. `herdr api schema --json` declares no length limit, so this is an empirical fact recorded only here.

The first measurement used a 43 character path and therefore proved nothing. The consequence was live: a worktree path of 91 characters stored as one ending `.claude/work` for a directory actually at `.claude/worktrees/help`, so the authoritative record pointed at a directory that did not exist. A real ticket path is longer still, for example 117 characters for `.../fanapp-terraform/.claude/worktrees/FANDEVX-3511-github-oidc-repository-claim-trust-policies`.

So the path is NOT stored. `tag_pane` records the repo `root` and the `branch`, both short, refuses to write any value that would be truncated, and the path is derived from the two.

herdr's `worktree` commands are deliberately unused. `worktree.remove` is keyed on `workspace_id`, so herdr binds each worktree to a workspace of its own, scattering crew across workspaces. Worktrees stay plain `git worktree add` at the CLAUDE.md convention path; topology comes from `tab.create`.

Constraints that matter:

- Agent names must match `[a-z][a-z0-9_-]{0,31}`, lowercase even when the pane title is not, and be unique among live agents. Recorded in the wiki from 2026-07-31.
- Name uniqueness makes "the foreman is the agent named `foreman`" self-enforcing, so two foremen cannot coexist.
- `--state-label` accepts only a fixed vocabulary. Arbitrary metadata must go in `--token`.
- `agent.prompt` returns `agent_prompt_stalled` when a prompt from a non-working state produces no lifecycle change within 5s.

Claude Code CLI flags available for passthrough, confirmed via `claude --help`: `--append-system-prompt`, `--model`, `--permission-mode`, `--session-id`, `--resume`, `--fallback-model`.

### Drift check: what to re-measure when herdr updates

herdr self-updates. It went from 0.7.5 (protocol 17) to 0.8.0 (protocol 19)
during this build, and `crew doctor` failed closed on the mismatch, which is
what it is for.

`HERDR_VERIFIED_PROTOCOLS` in `crew.py` is a set of protocols whose behaviour
has been MEASURED. Adding a number without re-measuring defeats the point,
because this design rests on five behaviours that are not documented anywhere
except here. Re-run all five, then add the number:

1. **Token value truncation.** Write an 80 character token value and a 100
   character one, read both back from `api snapshot`, compare lengths. Expect
   80 intact and 100 stored as 80.
2. **`agent read` returns text, not JSON.** Read a live pane and confirm the
   output is terminal text. Confirm `--format json` is rejected with
   `invalid read format: json`.
3. **`--until` is repeated, not comma joined.** `agent wait <t> --until
   working,idle` must be rejected with `invalid agent status`.
4. **Required snapshot fields.** Compare `AgentInfo.required` and
   `PaneInfo.required` from `api schema --json` against what
   `assert_snapshot_shape` expects, and confirm `tokens` is still declared but
   still NOT required.
5. **Caller identity.** Confirm the caller's pane comes from `HERDR_PANE_ID`,
   and that stripping it makes herdr report the UI-FOCUSED pane instead. This
   check is only meaningful when the focused pane is NOT the caller. If they
   happen to be the same pane the result is inconclusive, and saying so is the
   correct outcome: reading a coincidence as a pass is how the original
   caller-identity defect was introduced.

Also confirm every verb `crew` calls still exists, by reading `herdr agent`,
`herdr pane` and `herdr tab` with no subcommand.

Measured on 2026-08-11 against protocol 19: checks 1 to 4 identical to 17, the
full verb surface intact, and check 5 inconclusive because the focused pane was
the caller. So 19 is verified for everything the code depends on, with that one
gap recorded rather than papered over.

### Agent status vocabulary

herdr reports five states, and one distinction is load-bearing:

| herdr state | Bucket | Meaning |
| --- | --- | --- |
| `working` | working | actively turning |
| `done` | **awaiting you** | idle after work not yet seen. CLI reads do not mark a pane seen, so this survives `crew peek` |
| `idle` | awaiting you | ready for input, tab already seen |
| `blocked` | blocked | herdr recognised an approval or question UI |
| `unknown` | needs recovery | an agent is present but unclassifiable. Does not prove completion |

`done` surviving a CLI read is what makes both the load report and `crew peek` honest: inspecting a crew member does not silently mark its output as read.

## Architecture

Three layers. Only the middle two are built.

```
substrate   herdr 0.7.5                panes, agents, metadata, notifications, events
mechanism   bin/crew                   one script, owns every herdr call, no LLM
judgment    foreman/, crew-member/     two skills
```

`bin/crew` is symlinked into `~/.local/bin`. A user skill's `bin/` is not on PATH (verified; only plugin `bin/` dirs are, and `~/.local/bin` already is, where `herdr` lives). Crew invoke `crew mail send` from their own panes, so this is a hard requirement.

Topology: one workspace for the foreman, one tab per crew member, created with `tab.create --workspace "$HERDR_WORKSPACE_ID"` read from the foreman's own pane. Existing workspaces are untouched.

### Identity, and why it is repo-scoped

Crew always work in a git worktree, but in **any** of the author's repositories, not one. There are 36 candidate repos under `~/Dev`. That breaks the obvious naming scheme:

- `fes-platform-frontend-helloworld` is 32 characters on its own, exhausting the entire name budget before the key is appended.
- `fes-platform-backend-helloworld-fandevx-3511` is 44 characters, over the cap.
- Truncating the repo is ambiguous: `fes-platform-*` has six entries and `fes-loyalty*` three.
- `Hands-On-Large-Language-Models` contains uppercase, which herdr rejects.

So the repo does not go in the name. Resolution:

| Field | Value | Where |
| --- | --- | --- |
| Agent name | lowercased, sanitised key, suffixed `-2`, `-3` on live-name collision | herdr agent name |
| Repo | repo directory name | `repo` token, and the tab label |
| Tab label | `<repo>/<key>` | what you read in the sidebar |
| Worktree | absolute path | `worktree` token |

Collision detection is a snapshot read, which `crew ls` already performs. The suffix disambiguates a ticket touching two repos concurrently. It is a **live-name slot only** and carries no durable meaning.

`crew ls` must display the repo. Without it, `fandevx-3511` versus `fandevx-3511-2` is unreadable.

### There is no session id

An earlier draft assigned `--session-id uuid5(repo + key + gen)` so that resume would need no stored handle. Two independent reviewers broke it with the same sequence: dispatch a key in a repo (gen 1), retire it, redispatch in the same repo. The collision check sees only **live** agents, so gen 1 is reassigned, the identical uuid is recomputed, and it collides with the retired session's on-disk state. Generation was never durable identity.

Their proposed repairs were both worse than the mechanism. A random nonce destroys the derivability that was the entire justification, and a durable generation registry reintroduces the state file whose absence is this design's main virtue.

The mechanism is deleted instead. `claude --continue` is documented as continuing the most recent conversation **in the current directory**, and a crew member is one-to-one with its worktree. So resume is:

```sh
cd "$(crew ls --json | jq -r '.[]|select(.key=="fandevx-3511").worktree')" && claude --continue
```

or, after a reboot when no tokens survive, simply `cd <worktree> && claude --continue`.

This removes `--session-id`, removes the `crew uuid` verb, and removes two open risks: uuid collision on redispatch, and `--resume` failing against a session that was never created because `agent.start` timed out mid-startup. `--continue` in a directory with no prior session starts a fresh one, which is the correct behaviour in that case.

### State model

Stateless by derivation, with one exception, and the record is complete.

| What | Where | Why |
| --- | --- | --- |
| Roster, live status | `herdr api snapshot`, recomputed per query | cannot go stale |
| Assignment: `crew`, `v`, `key`, `repo`, `root`, `branch`, `type`, `dispatched` | herdr pane `tokens` (8 of 32 keys) | authoritative and complete; dies with the pane, so the substrate enforces the lifetime |
| Mailbox and cursor | `~/.crew/mailbox.jsonl`, `~/.crew/cursor` | the only files; the only genuinely non-derivable state |

**Pane tokens are the authoritative record. Path derivation is not used to decide anything.** An earlier draft had this backwards.

The reason is observable: a pane working FANDEVX-3511 reports `cwd` as the repo root but `foreground_cwd` as the worktree. The two diverge, and `foreground_cwd` follows the transient foreground process. A crew member that changes directory outside its worktree makes path derivation return **the wrong key rather than no key**. Silent misattribution is worse than a gap.

Because a token value truncates silently at 80 characters, the worktree path is NOT stored. The short `root` and `branch` are, and the path is derived from them. So:

- `crew ls` reports only panes carrying `crew=true`. An untagged pane is not crew, whatever its cwd.
- Resume and `crew log` read the worktree from the `worktree` token, never from cwd.
- Path derivation is used in exactly one place: `crew recover`, to **propose** a re-tag for a pane that lost its tokens. It proposes; it never re-tags silently.

### Versioning

Every token set carries `v=1`. Every mailbox line carries `"v":1`. `crew` refuses to act on a record whose `v` it does not recognise and says so rather than guessing.

This exists because `crew` is a single mutable script on PATH shared with live crew sessions: without a version, every edit is an unannounced live migration of running work.

### Roles

Foreman: a role a session assumes. Tracks what streams exist, what state each is in, and what was asked for. Reads bounded crew output on demand; never ingests diffs, plans, or full review transcripts.

Crew member: a peer session, not a subagent.

| | Subagent (`Agent`, `parallel-code-build`) | Crew member |
| --- | --- | --- |
| Lifetime | dies with parent | survives, resumable |
| Attachable | no | yes |
| Tool surface | restricted | full, including MCP and hooks |
| Can ask the human something | no | yes |
| Output | a report | committed work |

These layer rather than compete. A reviewer crew member itself fans out subagents; `terraform-review` already is that middle layer.

### Crew types

| Type | Model | Implementation |
| --- | --- | --- |
| Implementer | Opus | agent session, one key, own worktree |
| Planner | Opus | agent session, `--permission-mode plan` |
| Reviewer | Opus | agent session, fans out to existing review lenses, closes itself on handback |

A watcher is **not** a crew type. It has no agent and therefore no `agent_status`, so it cannot occupy a bucket in the load report, and an earlier draft listing it as `--type watcher` also produced an undocumented fifth type on escalation. Watching is `crew watch`, described separately, and reported in its own section of `crew ls`.

Teardown authority is asymmetric, and the asymmetry is the rule:

- A crew member may close **itself** once its output is in the mailbox. It owns nothing else. A reviewer does this on handback.
- The foreman may never close **another** session. It proposes; the human confirms. A crew member's context is unsaved work.

## The crew script

```
crew ls                            load report; leads with counts, shows repo
crew doctor                        preflight: herdr protocol, claude flags, perms, shadowed skill
crew dispatch <key> --type implementer|planner|reviewer [--repo R] [--model M]
crew peek <name> [--lines N]       default 40, hard cap 200
crew nudge <name> "<text>"
crew mail unread | ack <seq>       foreman only
crew mail send --key K <state> "<msg>"   crew side
crew watchdog                      long-lived pane; blocked, stall and liveness detection
crew watch <run-id>                CI watcher pane
crew log <key>                     digest mailbox into the project log
crew retire <name>                 proposes, never kills unprompted
crew recover                       lost tokens, dead worktrees, orphaned setup panes
crew uninstall                     unstow, remove symlink, drop ~/.crew, clear crew tokens
crew --dry-run <verb>              print the herdr commands instead of issuing them
```

`crew ls --json` emits the machine-readable roster, including each crew member's `worktree`, which is how resume finds its directory.

`<key>` is a JIRA ticket key or a free-form slug. Slugs satisfy the same sanitising rules.

### crew ls fails closed

herdr self-updates and has a preview channel. If a snapshot field is renamed, a naive filter matches nothing and prints `0 working / 0 awaiting you / 0 blocked`, which is indistinguishable from a quiet crew. That is the exact bug class the author's CLAUDE.md condemns in CI watchers, and it would sit in the primary UI.

So `crew ls` asserts the protocol version and the fields it depends on against `herdr api schema --json`. On any mismatch or parse failure it prints `SNAPSHOT UNPARSED` and exits non-zero. **It never prints zeros it did not measure.**

### Agent dispatch

`crew dispatch` does mechanics only. It cannot create the worktree itself, because that is bound up with `/start-ticket`, an interactive skill: it fetches the ticket, asks before transitioning JIRA, and plans the work. A shell script cannot invoke it.

An earlier draft had the foreman run `/start-ticket` itself. Two independent adversarial reviewers rejected that unprompted, and they were right: it pulls a full JIRA payload, git state and a plan into context once per dispatch. Over a week that is the accumulation the foreman exists to avoid. The context boundary cannot have an exception that fires every dispatch.

So `/start-ticket` runs in an **ephemeral setup pane**:

1. The foreman calls `crew dispatch <key> --type <type> [--repo R]`. Repo defaults to the repo containing the foreman's cwd.
2. `crew dispatch` opens a scratch pane, **tags it** `crew=true type=setup key=<key>`, and starts a short-lived agent that runs `/start-ticket <key>`. Interactive prompts surface to the human **in that pane**, not in the foreman.
3. The setup agent's final instruction is to write `~/.crew/dispatch-<key>.json` containing the worktree path, branch and repo, then exit.
4. `crew dispatch` polls for that file, then closes the setup pane and proceeds. Nothing from the ticket payload or the plan reaches the foreman; the plan lives in the worktree, where the crew member reads it.

**The handoff is a JSON artifact, not terminal output.** An earlier draft had `crew dispatch` read the worktree path from the setup pane's scrollback. That does not hold: the path would have to be parsed out of an interactive Claude Code REPL rendering ANSI, and an interactive REPL does not exit on its own to signal completion. A file the agent writes is unambiguous and its absence is a clean timeout condition.

For a ticketless slug, steps 2 to 4 collapse to a plain `git worktree add` with no pane and no agent.

The setup pane is bounded: if the artifact does not appear within its timeout, dispatch reports a failed dispatch and leaves the pane open for inspection. Because the pane carries `type=setup`, `crew recover` can list orphans; an earlier draft claimed recover found them while never tagging them.

Concurrent dispatch of the same key is serialised with a lock on `~/.crew/dispatch-<key>.lock`. Without it, two dispatches can select the same free name, both tag panes, and race `agent.start`.

Then the mechanical sequence. **Note the order: the pane is tagged before any agent exists.**

```sh
herdr tab create --workspace "$HERDR_WORKSPACE_ID" --label "<repo>/<key>" \
    --cwd <worktree> --no-focus
herdr pane report-metadata <root_pane> --source crew \
    --token crew=true --token v=1 --token key=<key> --token repo=<repo> \
    --token type=implementer --token worktree=<worktree> \
    --token dispatched=<epoch>
herdr agent start <name> --kind claude --pane <root_pane> -- \
    --model opus \
    --append-system-prompt "<pointer, see below>"
herdr agent prompt <name> "<assignment>"
```

Tagging first is not cosmetic. With tokens authoritative, an untagged pane is invisible to `crew ls` **by design**. If the tag ran after `agent.start` and failed, or the foreman died between the two, a live Opus session would hold a worktree, unowned, invisible, and burning shared quota. A tagged empty pane is recoverable; an untagged live agent is not.

Error paths dispatch must handle rather than assume away:

- **`agent.start` times out** (3s to 300s) after the worktree exists. Re-dispatch on the same `repo + key` reuses the existing worktree and tab, and starts the agent with `--continue`. Because `--continue` is directory-scoped, it resumes the prior session if one was written and starts fresh if the timeout happened before any session existed. Both cases are correct without a probe.
- **`agent.prompt` returns `agent_prompt_stalled`**, so the assignment may not have landed. Dispatch reports a failed dispatch and leaves the pane tagged, so `crew ls` shows a crew member with no assignment rather than implying work started.

### The watchdog

One long-lived pane running `crew watchdog`, no agent. It exists because three separate failure modes share one signal, and none of them can be self-reported by the crew:

1. **`blocked` is structurally unreportable by the crew.** The mailbox requires the crew to execute `crew mail send`. A crew member blocked on a permission prompt is mid-turn and cannot execute anything. So the mailbox covers `done` and `needs-input` only, and `blocked` is detectable **only** from `agent_status`.
2. **Quota stalls.** Matching limit or error text is matching a moving target, and false-positives on error text a crew member merely quotes. The sound signal is absence of state change over time.
3. **Liveness.** `agent_status` reflects herdr's view of the pane, not whether the process is alive. A crew member killed by OOM or signal can read as idle indefinitely.

**It cannot be a blocking wait.** A stall is the *absence* of events, so a bare `events.wait` would never fire for the case it most needs to catch. The loop is therefore:

```
every tick (events.wait --match pane.agent_status_changed --timeout 30s):
  reconcile against a full `herdr api snapshot`   # covers dropped events and the startup gap
  for each pane with crew=true:
    compare state_change_seq and first_seen against ~/.crew/watchdog.state
    emit at most one entry per pane per condition
  touch ~/.crew/watchdog.heartbeat
```

The timeout tick is what makes stalls detectable; the snapshot reconcile is what makes a dropped event or a restart non-fatal.

**State is persisted, not in memory.** `~/.crew/watchdog.state` holds `{pane: (state_change_seq, first_seen_ts, emitted_flags)}`. Without this, a watchdog crash wipes every stall timer and the restart declares every running pane stalled at once. A pane not present in the state file is treated as **newly seen**, never as stalled.

Conditions:

| Condition | Signal | Entry |
| --- | --- | --- |
| Blocked | `agent_status` becomes `blocked` | `blocked`, immediately |
| Stalled | `working` with unchanged `state_change_seq` beyond threshold | `stalled`, once |
| Dead | tagged pane whose foreground processes no longer include the agent | `dead` |

Liveness is grounded but has a CLI gap. `herdr pane process-info --current` returns `foreground_process_group_id` and `foreground_processes` with `pid`, `argv`, `cmdline` and `cwd`, and the socket schema's `PaneProcessInfoParams` accepts a `pane_id`. However the CLI exposes only `--current`: positional, `--pane`, `--target` and `--id` were all rejected on 2026-08-10. So inspecting another pane's processes may require a direct socket call rather than the CLI. Solvable, and to be settled during step 7 rather than assumed.

**Nothing watches the watchdog, so `crew ls` does.** The watchdog pane carries `crew=true type=watchdog` and touches `~/.crew/watchdog.heartbeat` each tick. `crew ls` reports the watchdog as stale or absent when the heartbeat is older than a few ticks. A silent watchdog otherwise looks exactly like a healthy fleet, which is the failure this whole component exists to prevent.

`crew ls` reports `blocked` from the snapshot, not the mailbox. The mailbox is the source for `done` and `needs-input` only. The asymmetry is deliberate rather than an inconsistency.

During a fleet-wide quota exhaustion every crew stalls at once, so the watchdog coalesces: one notification naming the count, not one per pane.

### CI watchers

No agent, and the pane self-reports so nothing blocks on a poll.

```sh
herdr pane split --target <pane> --cwd <repo> --no-focus
herdr pane report-metadata <pane> --source crew \
    --token crew=true --token v=1 --token type=watch \
    --token key=run-<id> --token repo=<repo> --token run=<id>
herdr pane run <pane> "crew watch <run-id>"
```

A watch pane carries `key` and `repo` like any other crew pane so it can satisfy the mandatory mail schema. `worktree` is the one field that is legitimately absent for `type=watch`, and the schema marks it optional for that type only.

`--target` is explicit. `pane split` without one uses ambient focus, which can land a watcher inside an implementer's tab.

`crew watch` checks the conclusion explicitly rather than trusting exit status alone, because `gh run watch --exit-status` is documented only as "exit with non-zero status if run fails" and does not commit on cancelled or timed-out:

```sh
gh run watch "$1" --exit-status; rc=$?
concl=$(gh run view "$1" --json conclusion -q .conclusion)
[ "$concl" = success ] && state=done || state=failed
crew mail send --key "run-$1" "$state" "run $1 conclusion=$concl rc=$rc"
herdr notification show --title "run $1 ${concl:-no-conclusion}"
if [ "$state" = failed ]; then
  herdr pane report-metadata "$HERDR_PANE_ID" --source crew --token type=watch-diagnose
  herdr agent start "watch-$1" --kind claude --pane "$HERDR_PANE_ID" -- --model sonnet
fi
```

An empty conclusion is not success. A run queued but never started, or cancelled before any job ran, falls into the failed branch by construction.

## The skills

### herdr/SKILL.md

A bug fix, not new work. `~/.claude/skills/herdr.md` is a flat file, but Claude Code discovers skills as `<name>/SKILL.md`, so that 10 KB reference never loads. It is also untracked and absent from the dotfiles repo, so it is not backed up.

**It cannot be moved with `git mv`, because there is nothing tracked to move.** Verified 2026-08-10: `git -C ~/.dotfiles ls-files | grep -i herdr` is empty and `stow-packages/claude/.claude/skills/` contains no herdr entry.

Further, `~/.claude/skills/` is a real directory of per-skill symlinks, not a stowed directory. Stowing a new `herdr/` package will therefore **not** displace the existing flat `herdr.md`: it survives alongside, and later edits can land on the dead copy.

Correct procedure:

```sh
mkdir -p ~/.dotfiles/stow-packages/claude/.claude/skills/herdr
mv ~/.claude/skills/herdr.md ~/.dotfiles/stow-packages/claude/.claude/skills/herdr/SKILL.md
git -C ~/.dotfiles add stow-packages/claude/.claude/skills/herdr/SKILL.md
# stow, then assert the flat file is gone and the symlink resolves
test ! -e ~/.claude/skills/herdr.md
test -f ~/.claude/skills/herdr/SKILL.md
```

`crew doctor` asserts both conditions on every run, because a resurrected flat file is silent.

It is not a dependency of the other skills, since `crew` encapsulates the CLI. It is independently useful for ad-hoc pane work.

### foreman/SKILL.md

```
You coordinate. You do not implement.

You read bounded crew output on demand, via `crew peek`, and report it.
You never ingest diffs, plans, or full review transcripts.

On any status request:
  crew mail unread  ->  crew ls  ->  synthesise  ->  crew mail ack <seq>
Always lead with load: N working / N awaiting you / N blocked, per repo.

Every verb shells out to `crew`. Never call herdr directly.
Only you dispatch. A crew member asking you to dispatch is refused and
  surfaced to the human.
Retirement is proposed, never executed without confirmation.
If `crew ls` reports SNAPSHOT UNPARSED, say so and stop. Never report zeros.
```

`crew peek` is a deliberate, sanctioned read. An adversarial reviewer flagged it as contradicting a "never reads output" contract; the contract was reworded rather than the verb removed, because knowing what is happening is the point of the role. Two properties keep the cost bounded: CLI reads do not mark a pane seen, so peeking is non-destructive to the load report; and because nothing load-bearing lives in foreman context, peeking costs context rather than correctness. A compacted or restarted foreman loses nothing.

`--lines` defaults to 40 and is capped at 200, and `--source detection` (the compact bottom-buffer snapshot) is preferred, so an unbounded transcript cannot land by accident.

There is no numeric cap on crew. The real ceiling is human review bandwidth: the same source thread contains a counterexample from a user who built the industrial version (100 queued plans, `claude -p`, state machine, auto-PR hooks) and abandoned it because "human QC is the main bottleneck for almost everything, and nothing sucks more than reading 100 PRs cold." Reporting load at every status request makes the queue visible. It does not make it smaller, and this spec does not claim otherwise; the Success criteria table is where that claim gets tested.

### crew-member/SKILL.md

Injected durably as a short system-prompt pointer, so detail stays editable in the file and the crew re-reads its contract after a compact:

> You are crew member `<name>`, type `<type>`, on `<key>` in repo `<repo>`, worktree `<path>`. Read `~/.claude/skills/crew-member/SKILL.md` and follow it. Report state changes with `crew mail send --key <key>`.

The file:

```
Scope is exactly one key. Do not widen it.
Subagents: spawn freely. Crew members: never. Only the foreman dispatches.
On settle (done | needs-input): crew mail send --key <key> <state> "<one line>".
  Always. This is your only obligation. Silence is a bug.
  You cannot report `blocked` yourself; the watchdog does that.
Never put command output, credentials, ARNs, account ids or tokens in a mail
  line. State plus one human-readable sentence only.

These override the global CLAUDE.md for you:
  - Do NOT run /start-ticket. It already ran; your worktree exists.
  - Do NOT write to any project log.md. The foreman digests the mailbox.
  - Do NOT run /finish-work. Report done and stop.
Never force-push. Never merge. The human merges.
```

The three overrides are not stylistic. The author's global CLAUDE.md mandates running `/start-ticket`, appending to project `log.md`, and closing out with `/finish-work`. Crew inherit that file, so without explicit override every crew member would re-run setup, write concurrently to the same `log.md` that the Audit trail section forbids, and delete its own worktree while the foreman still tracked it.

"Only the foreman dispatches" is a convention that keeps a well-behaved crew member well behaved. It is **not** a control, for the reason below.

## Trust boundary

herdr's socket has no authorization model. Tested 2026-08-10: with `HERDR_ENV`, `HERDR_PANE_ID`, `HERDR_TAB_ID`, `HERDR_WORKSPACE_ID` and `HERDR_SOCKET_PATH` all unset, a process still had full read and targeted access to the running server. The socket is mode `srw-------`, so the only boundary is the Unix user.

Stated plainly rather than mitigated away:

1. **Every crew member is fully trusted, equal to the author's own shell.** It has `herdr` on PATH whether or not it has `crew`. It can prompt the focused session, close any pane, send keys to a sibling, or forge mailbox entries.
2. **The crew-member contract is a convention, not an enforced boundary.** A confused or prompt-injected crew member is bounded only by Claude Code's permission system.
3. **One proposed mitigation was tested and rejected.** Stripping herdr credentials from the crew environment does not work: there are none, and the CLI resolves the socket from config.

This design does not introduce a new privilege boundary and does not weaken an existing one. It increases the number of concurrent instances of a boundary already crossed by every Claude Code session with Bash access. That is a real increase in blast radius, honestly bounded at N sessions rather than one.

The only genuine control is outside `crew` itself: a Claude Code `PreToolUse` hook. **This now exists**, at `stow-packages/claude/.claude/hooks/crew-guard.py`, installed as a `Bash` matcher in the user's global settings.

When the calling session's cwd is inside a `.claude/worktrees` path, it denies `crew dispatch`, `crew nudge`, `crew mail ack`, `herdr agent start`, `herdr agent prompt`, `herdr agent send-keys`, `herdr pane/tab/workspace close` and `herdr server stop`. Legitimate crew work is untouched and the foreman is unrestricted. Verified on 16 cases with no mismatches.

Its limits, stated so nobody mistakes it for a sandbox:

- It matches command text. A determined process evades it by encoding the command, writing a wrapper, or speaking to the socket directly.
- It fails **open** on ambiguity: an unknown cwd is allowed. A guard that blocked the human's own shell when it could not tell would be switched off within the hour.
- It identifies a crew member by cwd, so a foreman that changed directory into a worktree would be restricted, and a crew member that changed directory out of one would not.

What it does stop is a confused or prompt-injected agent following instructions in plain text, which is the realistic threat here: crew members read untrusted diffs, logs and error output, and `crew peek` pipes exactly that into the foreman's context.

## Audit trail and data handling

`crew mail send` performs one locked append to `~/.crew/mailbox.jsonl` and nothing else. It does not write to any project log: several crew appending to one `log.md` interleaves, a crew member does not know its project folder, and a per-state-change stream would bloat the log.

Line shape: `{"v":1,"seq":N,"ts":...,"key":...,"repo":...,"pane":...,"worktree":...,"state":...,"msg":...}`. The `key` field is mandatory. An earlier draft omitted it while `crew log <key>` filtered on it, so the digest would have matched nothing.

**Locking.** Every read and write takes an exclusive `fcntl.flock`. Bare `>>` is not sufficient: `PIPE_BUF` on this machine is **512 bytes**, which a mail line will exceed, and macOS ships no `flock` binary. Python's `fcntl.flock` is available and is what `crew` uses.

**Sequencing.** `seq` is a monotonic integer assigned under the same lock as the append. The cursor stores the highest seq **processed and reported**, not a timestamp. Timestamps are neither unique nor monotonic, so a message appended while the foreman processed a batch could be permanently skipped once a timestamp cursor advanced past it.

The cursor is **gap-tolerant**. An earlier draft acknowledged only the highest *contiguous* seq, which is a poison pill: a writer killed mid-append leaves seq 10 followed by seq 12, and a contiguous cursor sticks at 10 permanently, re-delivering the same batch on every call forever. Instead:

- Unparseable lines are skipped, counted, and reported. They never fail the whole read.
- A missing seq is reported as a gap, once, and the cursor advances past it.
- `crew mail unread` output ends with `N unreadable, M missing` when either is non-zero.

Surfacing an anomaly is correct; blocking on one is not.

**Cursor ownership.** `crew mail ack` refuses unless the calling pane hosts the agent named `foreman`, checked against the snapshot. Otherwise any crew member or the human running `mail unread`/`ack` would silently consume the foreman's batch. The cursor advances only after the foreman has reported to the human, so a compaction between read and report re-delivers.

**Permissions and secrets.** `~/.crew` is mode 700 and the mailbox 600. Default umask here is 022, which would otherwise leave the mailbox world-readable. Crew work in worktrees of arbitrary repositories, several of which involve cross-account AWS SSO sessions, so mail lines carry state plus one human-readable sentence and never command output.

`crew log <key>` reads the mailbox, filters on `key`, and appends a marker-guarded timeline block to the project `log.md`, replacing rather than duplicating on re-run. It writes state transitions only, never output, because `~/Documents/Work` is a git repository and the log is permanent history. Where a repo has no corresponding `~/Documents/Work/projects/<project>/` folder, which is the case for personal repos, `crew log` reports that and writes nothing.

`crew peek` output is a known exception: it enters the foreman's transcript by design, and the author's session hooks capture raw transcripts. Peek output is never written to the mailbox or to `log.md`, but it cannot be kept out of transcript capture. Accepted consequence of the peek decision.

Wiring `crew log` into `finish-work` Step 7 and `end-of-day` is deferred to a separate change, because `finish-work` is currently modified on an unlanded branch. Until then, `end-of-day` writes a durable daily record with crew activity missing, and that gap is known.

## Failure modes

**Rows marked DEFERRED name a component this MVP does not ship.** The watchdog, `crew watch`, `crew log`, `crew retire`, `crew recover` and `crew uninstall` are a separate plan. Until the watchdog exists nothing detects a blocked, stalled or dead crew member: `crew ls` shows `blocked` only when herdr itself classified the pane that way, and a crew member that dies silently reads as idle forever. Those rows describe the intended design, not current behaviour.


| # | Failure | Handling | Verified |
| --- | --- | --- | --- |
| 1 | Crew hits a usage limit mid-task and stalls silently | Watchdog detects absence of state change over time, not banner text | DEFERRED |
| 2 | Foreman compacts | Survivable by construction: roster from snapshot, assignment from tokens, cursor on disk | by design |
| 3 | Mailbox entry lost between read and report | Cursor advances only on `crew mail ack` after reporting; gap-tolerant, so a damaged record cannot wedge it | by design |
| 4 | Live pane, dead worktree after `finish-work` cleanup | `crew recover` compares the `worktree` token against disk and proposes closing | DEFERRED |
| 5 | Two foremen | Impossible: herdr enforces name uniqueness | yes |
| 6 | herdr server restart drops pane tokens | `crew recover` proposes a path-derived re-tag for the human to confirm; never silent, because derivation can be confidently wrong | DEFERRED |
| 7 | `agent.start` times out after the worktree exists | Idempotent on `repo + key`; re-dispatch uses `--continue`, correct whether or not a session was written | no |
| 8 | `agent.prompt` stalls, assignment never landed | Failed dispatch reported; pane stays tagged so it is visible | no |
| 9 | `crew log` run twice | Marker-guarded block, replaced not appended | DEFERRED |
| 10 | Crew changes directory outside its worktree | Tokens authoritative; derivation only ever proposes | yes, observed |
| 11 | Concurrent mailbox writes interleave | `fcntl.flock` on every read and write | yes, measured |
| 12 | herdr field rename breaks the snapshot filter | `crew ls` asserts protocol and fields, prints `SNAPSHOT UNPARSED`, exits non-zero | no |
| 13 | Tag fails after agent start, orphaning a live session | Tag before `agent.start` | by design |
| 14 | Crew blocked on a permission prompt cannot self-report | Watchdog reports `blocked` from `agent_status` | DEFERRED |
| 15 | Crew process killed by OOM or signal, pane persists reading idle | Watchdog emits `dead` | DEFERRED |
| 16 | Setup pane hangs on an unattended JIRA prompt | Bounded timeout, failed dispatch, pane left open, listed by `crew recover` | DEFERRED |
| 17 | Quota exhaustion takes the whole fleet including the foreman | See below | no |

Failure 17 is different in kind. The global model is `opus[1m]`, so crew, foreman and the author's scheduled agents draw on one bucket. A limit trip takes all of them at once, and `crew recover` and `crew ls` must therefore be runnable from **any** pane, not only the foreman, because the foreman is exactly what dies. Both are read-mostly and take no foreman-only lock; only `crew mail ack` is foreman-scoped.

There is no spend or headroom reporting, and no mechanism here prevents fleet exhaustion. That is a known gap, not a solved problem.

## Reboot and day-2

There is no launchd unit for herdr, so a reboot ends the server and every pane. Tokens die with panes, by design.

Recovery after a reboot is not automatic and does not need to be: the worktrees, branches, JIRA tickets and PRs all survive, and `crew ls` correctly reports an empty fleet rather than a stale one. Any session is resumable with `cd <worktree> && claude --continue`, which needs no record at all beyond the worktree path, and worktrees are discoverable with `git worktree list`. This is the payoff for deleting the session-id scheme: reboot recovery requires nothing that the reboot destroyed.

`crew doctor` is the day-2 entry point. It asserts: herdr version and protocol, the snapshot fields `crew ls` depends on, presence of `--append-system-prompt`, `--continue`, `--model` and `--permission-mode`, that `~/.local/bin/crew` resolves, that `~/.crew` is 700 and the mailbox 600, that the watchdog heartbeat is fresh or absent-by-design, and that no flat `herdr.md` shadows the stowed skill.

`crew uninstall` is the rollback: unstow, remove the symlink, drop `~/.crew`, and clear `crew=*` tokens from every pane. Tokens persist with `ttl_ms` omitted, so uninstall must clear them explicitly or they outlive the tooling.

Known interaction, unresolved: the author's global `Stop` and `PermissionRequest` hooks play audio per session. With N crew the audio channel loses meaning and collides with the watchdog's `notification.show`, and neither carries pane attribution. Not addressed here.

## Testing

- `crew --dry-run <verb>` prints the herdr commands it would issue instead of issuing them.
- A `demo()` self-check asserting the things with a single correct answer: name sanitising and lowercasing (including an uppercase repo name), collision suffixing, gap-tolerant cursor arithmetic across a punched gap and an unparseable line, and load-report bucketing including the `done` versus `idle` split.
- Snapshot field assertions run against `herdr api schema --json` rather than a captured fixture. A fixture is stale by construction on a self-updating tool.
- One real smoke test: dispatch a throwaway planner against a scratch repo, confirm it appears in `crew ls` with the right repo, mails on settle, and retires cleanly.

Do not dispatch crew onto sprint tickets until the smoke test passes. A dispatch bug otherwise becomes a sprint failure.

## Build order

Built in a worktree off main, per the author's own convention. The dotfiles repo currently sits on `finish-work-verification-fixes` with ten modified files; a worktree makes that irrelevant rather than blocking. An earlier draft listed landing that branch as a prerequisite, which was unnecessary.

| Step | Work | Size | Acceptance check |
| --- | --- | --- | --- |
| 1 | `herdr/SKILL.md` move, stow, assert no shadow | XS | `/herdr` appears in the skill list; flat file gone |
| 2 | `crew` skeleton: `doctor`, `--dry-run`, `demo()` | S | `crew doctor` passes; self-check green |
| 3 | `crew ls` and `crew ls --json`, fail-closed schema assertions | M | Reports the six live sessions as untagged; exits non-zero on a mangled snapshot |
| 4 | `crew mail` with flock, seq, gap-tolerant foreman-scoped ack | M | Concurrent-writer test yields no interleaving; a hand-punched gap advances the cursor and reports the gap |
| 5 | `crew-member/SKILL.md` | XS | Contract exists for dispatch to inject |
| 6 | `crew dispatch` for implementer, plus `peek` and `nudge` | L | Smoke test end to end: dispatched crew appears in `crew ls` with the right repo and mails on settle unprompted |
| 7 | `foreman/SKILL.md` | S | Saying "you're my foreman" yields a load report leading with counts |
| 8 | `crew watchdog` | M | Blocked, stalled and dead each produce exactly one entry; killing the watchdog makes `crew ls` report it stale; restarting it does not declare running panes stalled |
| 9 | `crew watch`, planner and reviewer types | M | A failing run escalates and mails once |
| 10 | `crew retire`, `crew recover`, `crew log`, `crew uninstall` | M | Recover proposes and never acts; uninstall leaves no tokens |

The honest MVP is steps 1 to 7. Two earlier drafts got this wrong in opposite directions: the first claimed the work could stop after step 2, which was false because nothing writes a `crew=true` token until dispatch exists; the second put the crew-member contract *after* the dispatch smoke test that depends on it, and excluded the foreman whose behaviour the Success criteria exist to test. The contract is now step 5, before dispatch, and the foreman is inside the MVP.

Steps 3 and 4 remain independently useful even if the foreman role is never built.

A note on step 7, recorded because it was raised and rejected during design: `crew ls` run from any session already delivers the roster, so the foreman role could be dropped and the script treated as the whole product. It is kept deliberately, because the context-hygiene argument only pays off with a dedicated coordinator. The Success criteria table is what decides whether that was right.

## Open risks

1. Pane-token persistence across a herdr **server** restart is untested. Mitigated by `crew recover` proposing a path-derived re-tag, but the mitigation is also untested.
2. Whether a compacted crew member reliably re-reads its contract from the system-prompt pointer is a behavioural assumption, not a guarantee.
3. Whether the crew-member overrides actually beat the global CLAUDE.md mandates in practice is untested. If they lose, crew will write to `log.md` concurrently.
4. Every herdr behaviour relied on is unversioned API on a pre-1.0 self-updating tool with a preview channel. `crew doctor` detects drift; it cannot prevent it.
5. Responsibility for one concept is spread across the script, two skill files, a system-prompt string and pane tokens. That is four places to keep consistent, and the versioning scheme covers only the last.
6. Targeting another pane with `pane process-info` has no confirmed CLI flag, so the watchdog's liveness check may need a direct socket call. Settle in step 8; if it proves impossible, failure mode 15 stays open rather than being silently dropped.
7. `events.wait` delivery guarantees under socket congestion are unknown. The 30s reconcile tick is the mitigation, so a dropped event costs latency rather than correctness.
8. Whether the author's global `Stop` and `PermissionRequest` audio hooks remain useful with N crew is untested, and the collision with the watchdog's notifications is unresolved.

Two risks from the previous draft were removed rather than mitigated: uuid collision on redispatch, and `--resume` against a session that never existed. Deleting the session-id scheme eliminated both.
