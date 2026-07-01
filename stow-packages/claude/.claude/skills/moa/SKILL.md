---
name: moa
description: |
  Mixture of Agents woven into an agentic task. At each genuinely hard decision point,
  consult the reference trio (codex, antigravity, claude-sonnet) as parallel proposers
  across multiple Together-style refinement layers, then act as the aggregator: verify,
  synthesize, and take the real action yourself. Use for hard tasks that benefit from
  multiple model perspectives at each fork. Triggers on "moa", "mixture of agents",
  "run this with MoA", or "/moa". For a one-shot cross-model answer use /council; for a
  two-model debate use /consensus.
version: 1.0.0
argument-hint: "<task>"
allowed-tools: [Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion]
---

# MoA (in-the-loop mixture of agents)

Run an agentic task where the *thinking* at each hard step is a Mixture of Agents. You
(Opus 4.8) are the **aggregator / acting model**: at each decision point you fan the
sub-question out to reference proposers, refine their answers over layers, then verify and
act yourself. Reference seats are the council trio: codex (OpenAI), antigravity (`agy`,
Google), and claude pinned to `claude-sonnet-4-6`.

**What this is not:** Claude Code cannot intercept its own inference, so this is not a
model-provider swap (as Hermes does) and it does not fire on every model call. It is a
*consult-before-you-act protocol* that fires at the genuinely hard forks. Mechanical steps
act directly with no consult. That selectivity is the cost governor: a consult is trio x
layers CLI invocations, so firing it on every `ls` would be absurd.

**Safety** (inherited from `/council`): members run read-only from the working directory
with your full credentials, network, and any repo/MCP instructions, and their answers are
written to disk. Do not consult over secrets you would not want read and saved. Keep
transcript dirs out of commits (step 5 handles the `.gitignore`).

## Workflow

### 1. Scope the task

Use the argument as the task. If empty, ask the user for it (AskUserQuestion or chat).
State the task back in one line, then begin working it like a normal agentic task.

### 2. Identify decision points as you go

A **decision point** is a genuinely hard fork where multiple model perspectives change the
outcome: choosing an approach/architecture, an ambiguous design or judgment call, a risky
or hard-to-reverse edit, a subtle correctness question. **Not** decision points: reading a
file, `ls`/`grep`, an obvious rename, a mechanical edit with one right answer. Act on those
directly. Only decision points trigger a consult.

### 3. At a decision point, run a MoA consult

Write the sub-question plus the **minimal** relevant context (paths, constraints, the exact
choice) to a prompt file with the **Write tool** (never a bash heredoc: arbitrary text and
terminator collisions). If the sub-question is about repo code, say so and cite the files
so proposers read them. Then:

```bash
OUT_DIR="$(mktemp -d /tmp/moa-consult.XXXXXX)"
bash ~/.claude/skills/moa/scripts/moa-consult.sh \
  --prompt-file "$PROMPT_FILE" --out-dir "$OUT_DIR" --layers 2
```

`moa-consult.sh` runs the proposer layers deterministically: layer 1 fans the question out
to the trio in parallel; each later layer feeds the prior layer's proposals back to the
proposers to refine (Together-style). It prints the path to `moa-final.md` (last line) and
handles timeouts, quorum, and degradation. `--layers 2` is the default; use `--layers 1`
for a cheaper single fan-out, `--layers 3` only for a genuinely hard fork. `--members`
overrides the trio if you must.

Read `moa-final.md`: the refined proposals from the last successful layer, labeled per
member, with a per-layer status summary. If a whole layer failed it falls back to the last
good layer; if every layer failed the script exits 1 and says so, so drop back to deciding
unaided and note it.

### 4. Aggregate, verify, then act

You are the aggregator, not a vote-counter. **Verify before you adopt:** for any
load-bearing claim (anything that changes the decision or the action), confirm it yourself,
read the cited file:line, or run a quick check. Tag load-bearing points
**verified / refuted / unverified**. A proposer's confidence is not evidence; proposers in
this setup have been confidently wrong on claims a ten-second check refutes.

Then state your synthesized decision briefly (what you took from whom, what you refuted and
why), and **take the actual action** under the normal permission system: the edit, the
command, the file. Continue the task to the next step or decision point.

### 5. Log each consult

Reuse the council transcript pattern. Replace `SUBQ` with the actual sub-question.

```bash
SUBQ='...the sub-question...'
if ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  DEST="$ROOT/.claude/moa"
  IGN="$ROOT/.gitignore"
  grep -qxF '.claude/moa/' "$IGN" 2>/dev/null || printf '.claude/moa/\n' >>"$IGN"
else
  DEST="$HOME/.claude/moa-logs"
fi
mkdir -p "$DEST"
SLUG="$(printf '%s' "$SUBQ" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | cut -c1-50 | sed 's/-$//')"
TRANSCRIPT="$DEST/$(date +%Y%m%d-%H%M%S)-$$-$SLUG.md"
```

Write to `$TRANSCRIPT` (Write tool): the sub-question, the contents of `moa-final.md`, and
your aggregation + decision. **Only after** confirming it wrote (`[ -s "$TRANSCRIPT" ]`),
clean up: `rm -rf "$OUT_DIR" "$PROMPT_FILE"`. If the write failed, leave `$OUT_DIR` in place
(it holds the only copy of the proposals) and tell the user where it is.

### 6. Soft consult budget

Track a running consult count and state it (e.g. "MoA consult 2/5"). Multi-layer x trio x
many steps is the real cost. If a task looks like it will exceed **~5 consults**, stop and
surface that to the user (AskUserQuestion): raise the cap, drop to `--layers 1`, or finish
the rest unaided. Never silently fan out dozens of times.
