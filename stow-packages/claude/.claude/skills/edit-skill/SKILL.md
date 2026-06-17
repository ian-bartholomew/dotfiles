---
name: edit-skill
description: Use when the user asks to "edit", "update", "open", "modify", or "tweak" an existing user skill by name (e.g. "edit my /what-next skill", "update the start-of-day skill"). Resolves the canonical SKILL.md path through the dotfiles → stow → ~/.claude/skills symlink chain in one shot and confirms the active copy is the stowed source before any Edit call. Skip for plugin-provided skills (those live under ~/.claude/plugins/cache/) — this is for user-owned skills only.
version: 0.1.0
allowed-tools: [Bash, Read, Edit]
---

# Edit Skill

Resolves the SKILL.md path for a user-owned skill deterministically, so editing a skill is one tool call to locate plus one to edit — not five to confirm which copy is canonical.

User skills are stowed: the source of truth lives under `~/.dotfiles/stow-packages/claude/.claude/skills/<name>/SKILL.md`, and `~/.claude/skills/<name>` is a symlink into that directory. Editing the symlinked copy works (same inode), but the dotfiles source is the version-controlled path and the one `git status` will surface. Always edit the dotfiles source.

## When to Use

- User says "edit", "update", "open", "modify", or "tweak" an existing skill by name
- Any Edit call about to touch a SKILL.md path
- Before suggesting a change to a skill's frontmatter, body, or version

## When NOT to Use

- The skill is plugin-provided (path contains `~/.claude/plugins/cache/`) — those are not user-editable; redirect the user to the upstream repo
- Creating a brand-new skill — use `/writing-skills` (TDD discipline for new skills)
- The user is not in `~/Documents/Work` or any repo context — still works, the paths are absolute

## Canonical Paths

- **Dotfiles source (edit this):** `~/.dotfiles/stow-packages/claude/.claude/skills/<name>/SKILL.md`
- **Active symlink (do not edit directly):** `~/.claude/skills/<name>/SKILL.md`
- **Plugin skills (out of scope):** `~/.claude/plugins/cache/.../skills/<name>/SKILL.md`

## Steps

### 1. Resolve the dotfiles source

```bash
SRC="$HOME/.dotfiles/stow-packages/claude/.claude/skills/<name>/SKILL.md"
test -f "$SRC" || { echo "FATAL: $SRC not found — is <name> a user skill? Check ~/.claude/skills/ first."; exit 1; }
```

If the file doesn't exist, the skill may be plugin-provided or misspelled. Run `ls ~/.claude/skills/` to confirm, and check `~/.claude/plugins/cache/` if the name looks plugin-shaped.

### 2. Confirm the active copy is symlinked to the source

```bash
readlink "$HOME/.claude/skills/<name>" 2>/dev/null
```

Expected output (relative): `../../.dotfiles/stow-packages/claude/.claude/skills/<name>`.

If `readlink` returns empty or the wrong target, the stow link is broken — halt and report. Do not edit blindly; the user needs to re-run `stow` first.

### 3. Edit the dotfiles source

Use the `Edit` tool on `$SRC`. Never edit `~/.claude/skills/<name>/SKILL.md` directly — even though the inode is shared, treating the symlinked path as the edit target masks the dotfiles change from `git status`.

### 4. Surface the diff to the user

```bash
cd "$HOME/.dotfiles" && git status --short "stow-packages/claude/.claude/skills/<name>/"
```

Report which file changed. Do NOT commit unless the user explicitly asks.

## Mandatory Verification

- The path passed to `Edit` MUST start with `$HOME/.dotfiles/stow-packages/claude/`.
- `readlink ~/.claude/skills/<name>` MUST resolve back to that source path.
- If either check fails, halt and surface the drift rather than editing the active copy.

## Red Flags

- Running `find ~/.claude -name SKILL.md` — you don't need to search, the path is canonical
- Running `stat -f "%i %N"` to compare inodes — `readlink` is the right check, not inode equality
- Editing `~/.claude/skills/<name>/SKILL.md` then "checking if it took" — edit the source, the symlink follows
- Running `diff` between the two locations — they're the same inode; the diff is always empty

## Related Skills

- `/writing-skills` — use when CREATING a new skill (TDD with subagent baselines)
- `/update-config` — for `settings.json` / hooks, not skills
