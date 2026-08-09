---
name: alert-noise-report
description: Use when the user asks for an alert noise report, "which alerts are noisy", "alert noise", "/alert-noise-report", or as a weekly step in /end-of-day. Covers Datadog alerts routed to #fes-platform-alerts, noise ranking, flap counts, trends over time, and the human diagnosis threads.
---

# Alert Noise Report

Digest of #fes-platform-alerts (C09HTMY4SP3): which Datadog monitors are noisy,
which flap, how they trend week over week, and what humans said about them.

Storage model (Obsidian Bases, not a separate database):

- Each run writes one dated report NOTE to `output/reports/alert-noise/<run-date>.md`,
  with the week's summary metrics in frontmatter and the noise ranking plus human
  threads in the body. The notes ARE the store.
- `output/reports/alert-noise/alert-noise.base` renders those notes as a table so
  trends read down the columns as reports accumulate.
- `wiki/company/fes-platform-alert-noise.md` is the synthesis: it embeds the base
  and carries the chronic-offenders list.

`scripts/alert_noise.py` parses Slack and computes one week's numbers. The LLM
fetches pages, reads threads, and writes notes. Requires the Bases core plugin
(enabled in this vault).

## Steps

The default job is catch-up: generate a note for every completed week that does
not have one yet. A manual run and the scheduled `/end-of-day` step share steps
1-5 and 7; they differ only at step 6 (wiki synthesis), which is interactive-only.

### 1. Pick target weeks (script)

```bash
python3 <skill-dir>/scripts/alert_noise.py pending output/reports/alert-noise
```

Each line is a completed Thursday-week with no note yet:
`wend  wstart  margin_oldest  window_start  window_end` (last three are epochs).
No lines means nothing to do: report `nothing-to-do` and stop. Otherwise take the
union fetch range: `oldest` = the smallest `margin_oldest`, newest = the largest
`window_end`. `margin_oldest` is `window_start - 24h`; that overlap is what keeps
boundary events from being clipped (a fetch that starts exactly at the window
start loses the window's first alerts, which is how an earlier run undercounted a
week by 21 events).

### 2. Fetch the union range into page files (LLM)

Call `mcp__claude_ai_Slack__slack_read_channel` with `channel_id` C09HTMY4SP3,
`response_format` `detailed` (concise strips the attachment content),
`oldest` = the union `margin_oldest`, `limit` 100.

Paginate with `latest` boundaries, NOT the returned cursor (cursors reset when
combined with `oldest`). For each next page set `latest` to the oldest
`Message TS` seen so far minus a tiny epsilon. Stop when `pagination_info` says
no more messages. At limit 100 a detailed page usually exceeds the tool-result
token cap and the harness saves it to a `tool-results/...txt` path; feed those
paths to `extract`.

### 3. Extract events (script)

```bash
python3 <skill-dir>/scripts/alert_noise.py extract <page-file>... > <scratchpad>/events.tsv
```

Spot-check the TSV head/tail. If a monitor family shows `env: unknown`, extend
`env_of` in the script rather than hand-editing rows. If a small final page came
back inline (not saved), hand-write a supplemental TSV for those events using the
same normalization the script applies, and `cat` it in.

### 4. Collect human signal (LLM)

Within the target window(s), note every human (non-bot) message and any Datadog
message with thread replies. Read threads with `slack_read_thread` when they
carry diagnosis or decisions. Capture date, author, one-line gist, and resolution
(mute / fix / ongoing).

### 5. Per target week: slice, compute, write the note (script + LLM)

For each line from step 1, slice the exact half-open window and compute metrics:

```bash
awk -F'\t' -v lo=<window_start> -v hi=<window_end> '($1+0)>=lo && ($1+0)<hi' \
  <scratchpad>/events.tsv > <scratchpad>/week.tsv
python3 <skill-dir>/scripts/alert_noise.py report <scratchpad>/week.tsv
```

Write `output/reports/alert-noise/<wend>.md`. Frontmatter MUST include (so the
base picks them up): `type: alert-noise-report`, `window_start`, `window_end`,
`run_date` (= `wend`), and the metrics from `report` (`total_events`,
`total_triggers`, `total_flaps`, `prod_triggers`, `noisiest_monitor`,
`noisiest_env`). Body: the ranking table, the human threads for that window, and a
2-4 sentence Reading that calls out prod events explicitly and what deserves a
ticket or a mute-with-reason.

If `alert-noise.base` does not exist, create it (see the committed copy: filter
`type == "alert-noise-report"`, one table view sorted by `run_date` DESC).

### 6. Update the wiki synthesis (LLM, interactive runs only)

In `wiki/company/fes-platform-alert-noise.md`: update the Chronic offenders list
(carry entries forward, mark resolved ones), bump `last_compiled`. The trend
table updates itself from the base, no manual edit needed. If the page is
missing, create it (domain `[fanatics, observability]`) and add it to
`wiki/_indexes/fanatics.md` and `wiki/_indexes/observability.md`.

**Scheduled/unattended runs SKIP this step** (chronic-offenders curation is a
judgment call left for interactive review; the Base trend still updates itself).

### 7. Log

Append a `compile` entry to `wiki/_log.md`.

## Scheduled use (via /end-of-day)

`/end-of-day` invokes this skill so it runs about weekly. It is self-gating:
step 1 (`pending`) returns nothing on days when no new week has completed, so the
daily cost is one script call. When a week has closed (or several, after a missed
run), it generates each missing note. Under `--unattended` it runs non-interactive
and best-effort: steps 1-5 and 7 only (skip step 6), writing local notes + a log
line, no prompts. The Slack MCP is already pre-flighted by end-of-day Step 0.

## Backfill (multiple past weeks at once)

To seed history, fetch a wide range and split it into weekly notes:

1. Paginate the whole range with `latest`-boundary pagination (oldest = range
   start minus 24h for margin), collecting every saved page path. For a long
   range this is many pages; delegate the fetch to a subagent so the dumps stay
   out of context.
2. `extract` all page files into one TSV (it dedupes across pages).
3. Slice per week and run `report` on each slice, using fixed midnight-Thursday
   windows `[Thu 00:00, next Thu 00:00)` so every week is the same shape:
   `awk -F'\t' -v lo=<start> -v hi=<end> '($1+0)>=lo && ($1+0)<hi' all.tsv`.
4. Assemble one note per week (frontmatter metrics + ranking + threads + reading).

Gotcha: the Bash tool here runs zsh (1-based arrays); do not loop bash-style
`${arr[$i]}` from 0, use explicit values or a Python helper for the slicing.

## Caveats

- The channel only shows what routed to `@slack-fes-platform-alerts`; for
  disputed counts, verify against Datadog monitor events via the Datadog MCP.
- The report notes are the durable record (Slack history ages out), keep them
  committed. There is no separate DB to back up.
- Mutes happen ad hoc (a bare "muted for 2 weeks" reply); tie each mute to the
  alert immediately above it and record which monitor was muted.
