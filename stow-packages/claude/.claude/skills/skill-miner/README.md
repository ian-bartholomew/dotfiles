# skill-miner (SHELVED — manual tool, not a skill)

Mines zsh history + Claude transcripts for repeated workflows and near-duplicate
questions. **Intentionally not a live skill**: no `SKILL.md`, not stowed, no
symlink in `~/.claude/skills/`. Claude does not load it.

## Why shelved (2026-06-24)

A 7-day dry-run on real activity failed the acceptance gate (≥1 applyable
proposal, <50% noise):

- **Workflow mining: 0 real.** Reusable work already lives in skills, so it
  doesn't show up as repeated raw shell commands.
- **Gap mining: ~70% noise** after filtering. Two genuine signals surfaced
  ("give me the link to the PR" ×8; "did you do a code review?" ×2) — both
  better solved directly than by a recurring miner.

Conclusion: not worth a recurring EOD step. Kept as an ad-hoc tool for the day
you actually suspect an uncaptured pattern.

## Run it manually

```bash
python3 mine.py \
  --history-file ~/.zsh_history \
  --transcripts-dir ~/.claude/projects \
  --skills-dir ~/.dotfiles/stow-packages/claude/.claude/skills \
  --since-ts "$(date -v-7d +%s)" \
  -o /tmp/candidates.json
```

Deterministic only — emits `candidates.json` (redacted). There is no LLM layer,
no `apply.py`, no EOD wiring; those were deferred behind the gate and never built.

## Tests

`python3 test_mine.py` (24 assert-based tests, no framework). Redaction is the
security-critical path and carries the heaviest coverage.
