#!/usr/bin/env bash
#
# Symlink the Claude config into the locations Codex reads, so Codex shares the
# same skills, agents, and instructions. Claude is the source of truth (I use
# Claude Code mostly); this just points Codex at it. Idempotent, safe to re-run,
# and a no-op when the codex CLI isn't installed.
#
#   skills   ~/.claude/skills/<n>   -> ~/.agents/skills/<n>    symlinked live from Claude (same SKILL.md format)
#   instr    ./AGENTS.md            -> ~/.codex/AGENTS.md       committed source (trimmed CLAUDE.md)
#
# Skills are symlinked so they never drift. AGENTS.md needs a real content
# transform, so it lives here as committed source; update it by hand when
# CLAUDE.md changes materially.

set -euo pipefail

command -v codex >/dev/null 2>&1 || { echo "codex not installed; nothing to link."; exit 0; }

DOT="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_SKILLS="$DOT/stow-packages/claude/.claude/skills"

# Global instructions
ln -sfn "$DOT/AGENTS.md" "$HOME/.codex/AGENTS.md"

# Skills (linked live from the Claude source)
mkdir -p "$HOME/.agents/skills"
for d in "$CLAUDE_SKILLS"/*/; do
  ln -sfn "${d%/}" "$HOME/.agents/skills/$(basename "$d")"
done

echo "Linked Codex config -> ~/.codex/AGENTS.md, ~/.agents/skills/"
