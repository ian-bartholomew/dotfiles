---
name: project-log-entry
description: This skill should be used when work has been done that is tied to a project under projects/<name>/ and needs to be recorded in that project's log.md, or before reporting work done to confirm the log is updated. Also use to audit which projects are missing today's entry. Enforces the CLAUDE.md rule that any project work must produce a log.md entry. Triggers on "log this", "update the project log", "did I log today's work", or at the end of any session whose changes touched files under projects/.
version: 0.1.0
allowed-tools: [Bash, Read, Edit, Write, Glob, Grep]
---

# Project Log Entry

Appends a properly-formatted entry to the matching project's `projects/<name>/log.md`, or reports which active projects are missing an entry for a given date. Tightly tied to the CLAUDE.md rule:

> When working on anything tied to a project under `~/Documents/Work/projects/<project>/`, always append an entry to that project's `log.md`.

This skill is the mechanical wrapper around that rule.

## When to Use

- Session involved edits or activity under `projects/<name>/`
- Before reporting any non-trivial project work as done
- End-of-day, to surface projects touched but unlogged
- User explicitly says "log this", "update the project log", "did I log <project>"

## When NOT to Use

- Work was wiki-only (`wiki/`) — that's captured in `wiki/_log.md`, not project logs
- Work was meeting note ingestion only — `meetings/` is user-owned
- The session produced *no* concrete project activity (don't manufacture an entry)

## Modes

### Mode A: Append (default)

Append today's entry to a specified or inferred project.

### Mode B: Audit

List all `projects/*/log.md` files and report which have / don't have an entry for a given date (defaults to today).

## Pipeline (Append Mode)

### 1. Determine target project

Try in order, stop at the first hit:

1. **Explicit:** user named the project
2. **Branch:** parse JIRA ticket key from current branch, map to project via the project's `README.md` "Tickets" or "Epic" reference, or `decisions.md`
3. **Touched files:** look for the most-recently-edited file under `projects/`:

   ```bash
   git status --porcelain projects/ | head
   git diff --name-only HEAD~10..HEAD -- projects/ 2>/dev/null | sort -u
   ```

4. **wiki/_log.md hint:** the last `_log.md` entry often names the project
5. **Last resort:** list candidate projects and ask the user

If ambiguous, list candidates (with last-modified-date) and stop.

### 2. Check for today's heading

```bash
DATE=$(date +%Y-%m-%d)
PROJ=projects/<name>/log.md
grep -nE "^## \\[?${DATE}" "$PROJ" || echo "NO_HEADING"
```

Heading format must be `## [YYYY-MM-DD]` with square brackets — that's the form existing project logs and the audit grep pattern depend on.

### 3. Build the entry

Compose with these subsections, **omit any that would be empty** — never leave hollow headers:

```markdown
## [YYYY-MM-DD]

### Done
- <concrete action> ([file path:line] or [PR #N] or [TICKET-KEY])
- ...

### Decisions
- <decision> — <one-line rationale>
  (also mirror to decisions.md if non-trivial)

### Follow-ups
- [ ] <open item> (owner: <who>, due: <when> if known)
```

Decision entries that are non-trivial (architectural, scope-changing, irreversible) should *also* be added to `projects/<name>/decisions.md` with a back-link to this log entry's date.

### 4. Apply

- **Heading missing:** append the full block (heading + subsections) to end of file with a leading blank line
- **Heading exists:** insert new bullets under the existing subsections; create subsections that don't yet exist; preserve existing content verbatim

Use `Edit` or heredoc-append (`cat >> file <<'EOF'`). Never `Write` over `log.md` (would destroy history).

### 5. Verify

```bash
tail -40 "$PROJ"
```

Confirm the new entry is present and the file ends with a single trailing newline.

## Pipeline (Audit Mode)

```bash
DATE=${1:-$(date +%Y-%m-%d)}
for f in projects/*/log.md; do
  if grep -qE "^## \\[?${DATE}" "$f" 2>/dev/null; then
    echo "OK:     $f"
  else
    echo "MISSING: $f"
  fi
done
```

For each `MISSING` project, check whether the project saw activity that day (git diff, file mtimes) — only flag missing entries on projects that were actually touched. Don't nag about dormant projects.

## Append-only Discipline

- Never delete or rewrite earlier dated entries
- If correcting a previous day, add a new dated entry that references the prior one
- Preserve closed follow-up sections: annotate the header (e.g. `### Follow-ups (closed 2026-06-01)`) and add a closing note, but don't delete the body. See the `feedback_preserve_closed_followups` memory.

## Common Mistakes

| Mistake | Reason |
|---|---|
| Using `## YYYY-MM-DD` without brackets | Breaks every grep pattern that audits logs — including this skill's audit mode |
| `Write`-ing log.md to "fix formatting" | Destroys the append-only audit trail |
| Empty `### Done` / `### Decisions` headers | Forbidden — omit the subsection instead |
| Logging the same activity twice across two projects | Pick the primary project and link from the other's log if needed |
| Inventing a project entry when nothing actually happened | The rule is "any work" not "any session" — be honest |
| Forgetting to mirror decisions to decisions.md | Architectural calls live in both files; log is chronological, decisions.md is the canonical decision register |
