# crew-dagr contract — v1

A small run-state file the foreman maintains so a crew run can be **validated**
and **viewed** as a DAG. Ours, in Python stdlib. The load-bearing ideas are
borrowed from `aemrebarut/herdr-dagr` — the task/attempt split, evidence tiers,
liveness, backward-pointing causes, and a lint that catches a clean-but-wrong
file. Its Rust binary and live TUI are not; we chose a plain renderer and no
toolchain.

The producer (the foreman) is the single writer. `crew-dagr.py` never writes run
state: it `check`s and `view`s. Write the live file to `.dagr/run.json` in the
foreman's cwd (gitignore it). Validate a `.tmp` and rename over the live file,
never publish an unchecked one — the renderer shows whatever the file holds.

## Object model

**Task** — a work item with a stable id (the ticket key, e.g. `fandevx-3631`,
never a pane id).

- `id*` · `title*` · `kind*` (`impl · review · gate · question · investigation`,
  open set) · `owner` · `state*` · `deps[]` (task ids; a `gate`'s deps are its
  fan-in) · `unblock` (who/what, when blocked) · `note` · `pr` (PR number,
  display only, shown after the ticket id) · `attempts[]`.
- `state`: `queued · working · awaiting · blocked · done · failed · abandoned`.
  Terminal: `done · failed · abandoned`. `awaiting` is the **healthy** wait on a
  human (an open PR up for approval); `blocked` is stuck and must name an
  unblocker. That distinction is the anti-false-blocked lesson from the foreman
  contract, made structural.

**Attempt** — one try at a task; a record, never rewritten. A re-dispatch (new
pane/session) appends a new attempt. A nudge continues the same attempt, so it
is not a new one.

- `id*` (`<task>·aN`) · `n*` (1-based, unique in task) · `cause*` · `actor` ·
  `model` (display string, e.g. `opus5·max`, shown greyed in the trace) ·
  `locator {pane}` (volatile, live only) · `state*`
  (`working · awaiting · done · failed · lost`) · `started_at` · `ended_at` ·
  `outcome` · `liveness`.
- `cause`: `initial` (n=1 only) · `sent_back` · `nudged` · `gate_failed` ·
  `followup`. Every cause but `initial` names an **earlier** attempt in `ref`.
  Causes point backward in time.
- `outcome` (required on a `done` attempt): `{result, evidence, receipt, reason}`.
  `evidence`: `verified · reported · heuristic · asserted`. A missing tier
  renders `!` and warns.
- `liveness` (a live attempt): `prompt_acknowledged` (bool) · `last_output_at`
  (timestamp) · `queued_input` (count of composer lines typed but unsubmitted).

**Task state is a projection over its attempts** — the invariant `check` holds,
so a state can never quietly disagree with the record:

| task state | requires of attempts |
|---|---|
| `queued` | no working attempt; latest (if any) not `done` |
| `working` | at least one `working` attempt |
| `awaiting` | latest attempt `awaiting` |
| `blocked` | names an unblocker; no attempt-shape constraint |
| `done` | latest attempt `done` |
| `failed` | latest attempt `failed` or `lost` |
| `abandoned` | latest attempt `failed`/`lost`, or no attempts |

## Evidence tiers

The trust ladder on a settled `done`, mirroring the crew-member contract:

`◆ verified` (a named mechanical check) · `◇ reported` (a tool returned
structured success, relayed but not independently confirmed) · `≈ heuristic`
(inferred from a runtime signal) · `! asserted` (a bare claim). `verified`
requires a check you can name in the receipt.

## `check` — findings

Errors (exit 1): E001 parse · E100 version≠1 · E101 run.id · E102 tasks[] ·
E110/E130 duplicate task/attempt id · E111/E131 missing required field · E112/E132
bad state · E113 attempt id collides with a task id · E120 dangling dep · E122
dep cycle · E133 unknown cause type · E135 first attempt not `initial`, or n>1
without a backward `ref` · E136 cause `ref` points forward · E140 `done` attempt
without an outcome · E142 unknown evidence tier · E150 task state contradicts its
attempts.

Warnings (exit 0, or 1 with `--strict`): W201 `done` without an evidence tier
(renders `!`) · W204 working attempt without a locator · W205 `blocked` without
an unblocker · W208 working attempt with no liveness.

Exit 2 means the file could not be read at all (path/tooling), never a document
problem; stdout is empty on that path, so never read empty output as clean.

## Loop

```
write run.json.tmp -> crew-dagr.py check run.json.tmp --strict -> fix -> repeat until clean
                                                                -> then rename over run.json
```
