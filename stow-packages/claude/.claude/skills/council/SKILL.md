---
name: council
description: |
  Convene a council of other LLM CLIs (codex, gemini, claude-sonnet) to answer a
  question in parallel, then synthesize their answers as chairman. Use for
  brainstorming, research, planning, and review where a cross-model second opinion
  helps. Triggers on "ask the council", "council", "convene the council", "get a
  second opinion from the council", or "/council". For a multi-round debate to
  consensus, use /consensus instead.
version: 1.0.0
argument-hint: "<question>"
allowed-tools: [Bash, Read, Write, AskUserQuestion]
---

# Council (single-pass)

Fan a question out to three member LLM CLIs running in parallel and read-only, then
act as **chairman** (Opus 4.8): read all answers and synthesize one verdict. Members
are codex (OpenAI), gemini (Google), and claude pinned to `claude-sonnet-4-6`. Opus
chairs only — it does not submit a member answer.

The members run from the current working directory in read-only mode, so for review
tasks they can see the repo. They never modify the tree.

**Safety:** read-only stops writes, not disclosure. Members run with your full
credentials, network access, and any repo/MCP instructions, and their answers are
saved to the transcript. Do not council over secrets you would not want read and
written to disk, and keep transcript dirs out of commits (see step 6).

## Workflow

### 1. Get the question

Use the argument as the question. If empty, ask the user for it via AskUserQuestion
(or just ask in chat).

### 2. Build the round prompt

Pick a temp path (`PROMPT_FILE=$(mktemp /tmp/council-prompt.XXXXXX)`), then use the
**Write tool** to write the prompt to it. Use Write, not a bash heredoc — heredocs
break if the question contains a line that matches the terminator, and Write avoids all
shell-quoting hazards with arbitrary user text.

The prompt is the user's question followed by:

> Answer independently and concisely. State your reasoning and your bottom-line
> recommendation. If you are uncertain, say so.

If the task is about code in the current repo, say so in the prompt so members read the
relevant files.

### 3. Run one round

```bash
OUT_DIR="$(mktemp -d /tmp/council-round.XXXXXX)"
bash ~/.claude/skills/council/scripts/council-round.sh \
  --prompt-file "$PROMPT_FILE" --out-dir "$OUT_DIR"
```

The script prints a manifest (one line per member: `member<TAB>ok|failed|failed(timeout)<TAB>path`)
and runs all three in parallel with a per-member timeout. It exits 0 if at least one
member answered, 1 if all failed. Models are pinned (sonnet = `claude-sonnet-4-6`,
gemini = `gemini-2.5-flash`, codex = CLI default); override with
`COUNCIL_CODEX_MODEL` / `COUNCIL_GEMINI_MODEL` / `COUNCIL_SONNET_MODEL`.

### 4. Read the answers

Read each `ok` member's `.out` file (codex.out / gemini.out / sonnet.out). If a member
`failed` or `failed(timeout)`, read the tail of its `.log` file (the manifest gives the
path), note the actual reason (timeout, model 404, auth, crash) in the synthesis, and
continue with the survivors — never block on one member.

**Quorum:** if only one member answered, say so plainly and label the result
low-confidence — a council of one has no cross-model signal. If all failed (script exit
1), report the failure and stop; do not fabricate a synthesis.

### 5. Chairman synthesis

As chairman, write a synthesis that covers:

- **Consensus** — what the members agree on.
- **Disagreements** — where they split, and the substance of each side's reasoning.
- **Verdict** — your own bottom line. Where the members split, pick and justify. Where
  they are all wrong or all miss something, say so. You are not a vote-counter.

Keep it tight. Attribute claims to the member that made them.

### 6. Save the transcript

Replace `QUESTION` below with the actual user question (it is a placeholder, not a
literal). Run:

```bash
QUESTION='...the user question...'
if ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  DEST="$ROOT/.claude/council"
  # transcripts can hold proprietary code/findings; make sure they can't be committed
  IGN="$ROOT/.gitignore"
  grep -qxF '.claude/council/' "$IGN" 2>/dev/null || printf '.claude/council/\n' >>"$IGN"
else
  DEST="$HOME/.claude/council-logs"
fi
mkdir -p "$DEST"
SLUG="$(printf '%s' "$QUESTION" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | cut -c1-50 | sed 's/-$//')"
TRANSCRIPT="$DEST/$(date +%Y%m%d-%H%M%S)-$$-$SLUG.md"
```

`printf` (not `echo`) and the `-$$` suffix avoid flag/backslash mangling and sub-second
filename collisions. When `DEST` is inside a repo the snippet auto-adds `.claude/council/`
to the repo `.gitignore` so transcripts are never committed.

Write to `$TRANSCRIPT` (with the Write tool): the question, then each member's raw
answer under a labeled heading (note any that failed/timed out), then your chairman
synthesis.

**Only after** confirming the transcript was written (`[ -s "$TRANSCRIPT" ]`), clean up:
`rm -rf "$OUT_DIR" "$PROMPT_FILE"`. If the write failed, leave `$OUT_DIR` in place — it
holds the only copy of the members' answers — and tell the user where it is.
