---
name: meeting-section-extract
description: This skill should be used when pulling a named section (Action Items, Decisions, Summary, Risks, Next Steps) out of one or many meeting summary files under meetings/<date>-<topic>/. Triggers on "what did <meeting> decide", "pull decisions from this week's meetings", "extract action items from <date> meetings", or any synthesis step (standup, EOD, one-pager, RFC) that needs the body of a named section across recent meetings without scraping by hand.
version: 0.1.0
allowed-tools: [Bash, Read, Glob]
---

# Meeting Section Extract

Read-only primitive that robustly extracts a named section from Obsidian meeting summary files. Handles the `summary-default.md` vs `summary-*.md` fallback and the `##` vs `###` heading-level ambiguity that real summaries contain.

This is the *extraction* primitive — it pulls structured bullets out, but does not act on them. Downstream skills (`meeting-action-items`, `daily-standup`, `end-of-day`, `one-pager`) compose on top of it.

## When to Use

- Synthesizing across recent meetings: standup, EOD, one-pager, RFC background
- User asks "what did <meeting> decide", "pull action items from this week", "what came out of the <topic> meetings"
- Any step that needs section bodies from multiple summary files

## When NOT to Use

- Need full meeting context, not a single section → read the summary file directly
- Need transcript-level detail → `transcript-*.md`, not `summary-*.md`
- Want to *convert* action items into Todoist tasks interactively → use `meeting-action-items` (this skill feeds it)
- Meeting hasn't been ingested from Zoom yet → run `meeting-ingest` first

## Inputs

- **Section name** (required): one of `Action Items`, `Decisions`, `Summary`, `Risks`, `Next Steps`, `Key Takeaways`, or any custom heading text (case-insensitive, trailing colon/whitespace tolerated)
- **Date range** (optional, default last 7 days): `--since YYYY-MM-DD`, `--on YYYY-MM-DD`, or `--last <N> days`
- **Topic filter** (optional): substring match on the folder name (e.g. `load-testing`, `standup`)

## Pipeline

### 1. Resolve summary files

```bash
# Build the date glob — accept --since, --on, --last N
for d in meetings/<date-glob>-*/; do
  [[ -d "$d" ]] || continue
  f="$d/summary-default.md"
  [[ -f "$f" ]] || f=$(ls "$d"/summary-*.md 2>/dev/null | head -1)
  if [[ -f "$f" ]]; then
    echo "$f"
  else
    echo "MISSING_SUMMARY: $d" >&2
  fi
done
```

Folders without any `summary-*.md` are reported on stderr at the end so the caller knows transcripts may need running through Zoom summary generation (`meeting-ingest`).

### 2. Extract the section

Use this awk pattern — it has survived the heading-level and numbered-prefix variations actually observed in `meetings/`:

```bash
SECTION="action items"  # lowercased; passed as awk var

awk -v want="$SECTION" '
  BEGIN { in_section = 0; want_depth = 0 }
  /^#{1,6} / {
    # Header line — normalize and compare
    h = $0
    gsub(/\*\*/, "", h)
    # Capture depth (number of leading #)
    match(h, /^#+/)
    depth = RLENGTH
    sub(/^#+ +/, "", h)
    sub(/^[0-9]+\.? +/, "", h)
    sub(/:?[[:space:]]*$/, "", h)
    if (in_section && depth <= want_depth) {
      exit
    }
    if (tolower(h) == want) {
      in_section = 1
      want_depth = depth
      next
    }
    next
  }
  in_section { print }
' "$f"
```

Why this works:

- Matches `## Action Items`, `### Action Items`, `**Action Items**` (after `**` strip), and `1. Action Items`
- Stops at the next header of the same OR shallower depth (so `### Action Items` ends at the next `###` or `##`, not at deeper `####`)
- Tolerates trailing colons (`### Action Items:`) and trailing whitespace
- Case-insensitive comparison via `tolower()`

### 3. Emit per-file output

```
=== meetings/2026-05-28-platform-standup/summary-default.md ===
- <bullet 1>
- <bullet 2>
  - <nested bullet>

=== meetings/2026-05-29-load-testing-daily-checkin/summary-default.md ===
(no Action Items section found)
```

Preserve bullet structure (indentation, nesting). If a file has the heading but an empty body, emit `(section present but empty)` — distinct from "section not found".

### 4. Summarize at the end

```
Scanned 7 meetings (2026-05-26 → 2026-06-01)
- 5 had the requested section
- 2 had no section
- 0 had no summary file
```

Send `MISSING_SUMMARY:` warnings as a final stderr block so the user can decide whether to re-ingest.

## Read-only Guarantee

This skill MUST NOT:

- Edit any file under `meetings/` — user owns that folder
- Write to Todoist, JIRA, Slack, or any external system
- Create derivative files (`*.extracted.md`, etc.)

Hand the extracted content back to the caller as terminal output for them to use.

## Composition

Downstream skills that compose on this:

- `meeting-action-items` — feeds extracted action items into an interactive Todoist conversion loop
- `daily-standup` — pulls action items + decisions from the lookback window
- `end-of-day` — pulls decisions for the day's project log entries
- `one-pager` / `rfc` — pulls relevant decisions and risks as background context

## Common Mistakes

| Mistake | Reason |
|---|---|
| `awk '/^## Action Items/,/^## /{print}'` (the naive range) | Misses `### Action Items` summaries, and stops too early when the section is nested deeper |
| Hard-coding `summary-default.md` only | Some meetings have differently-suffixed summary files (`summary-AI.md`, etc.) |
| Editing `meetings/` files to "normalize" headings | User-owned folder; don't mutate it. Make the awk more robust instead |
| Conflating empty section with missing section | Distinct cases; downstream callers need to tell them apart |
| Silent skip of folders missing summaries | Always surface them on stderr — user may need to re-ingest from Zoom |
| Using the skill to *act* (create todos, send messages) | Out of scope. Hand bullets back to caller; let composing skill act |
