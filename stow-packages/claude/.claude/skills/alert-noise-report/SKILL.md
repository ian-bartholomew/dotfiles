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

### 1. Read the channel into page files (LLM)

Call `mcp__claude_ai_Slack__slack_read_channel` with `channel_id` C09HTMY4SP3,
`response_format` `detailed` (concise strips the attachment content),
`oldest` = now minus 7 days (`date -v-7d +%s`), `limit` 100.

Paginate with `latest` boundaries, NOT the returned cursor (cursors reset when
combined with `oldest`). For each next page set `latest` to the oldest
`Message TS` seen so far minus a tiny epsilon. Stop when `pagination_info` says
no more messages. At limit 100 a detailed page usually exceeds the tool-result
token cap and the harness saves it to a `tool-results/...txt` path; feed those
paths to `extract` in step 2.

### 2. Extract events (script)

```bash
python3 <skill-dir>/scripts/alert_noise.py extract <page-file>... > <scratchpad>/events.tsv
```

Spot-check the TSV head/tail. If a monitor family shows `env: unknown`, extend
`env_of` in the script rather than hand-editing rows. If a small final page came
back inline (not saved), hand-write a supplemental TSV for those events using the
same normalization the script applies, and `cat` it in.

### 3. Collect human signal (LLM)

Note every human (non-bot) message and any Datadog message with thread replies.
Read threads with `slack_read_thread` when they carry diagnosis or decisions.
Capture date, author, one-line gist, and resolution (mute / fix / ongoing).

### 4. Compute metrics and ranking (script)

```bash
python3 <skill-dir>/scripts/alert_noise.py report <scratchpad>/events.tsv
```

Prints a frontmatter metrics block and the noise-ranking table.

### 5. Write the dated report note (LLM)

Write `output/reports/alert-noise/<run-date>.md`. Frontmatter MUST include, so the
base picks them up: `type: alert-noise-report`, `window_start`, `window_end`,
`run_date` (all YYYY-MM-DD), and the metrics from step 4 (`total_events`,
`total_triggers`, `total_flaps`, `prod_triggers`, `noisiest_monitor`,
`noisiest_env`). Body: the ranking table, the human threads from step 3, and a
2-4 sentence Reading that calls out prod events explicitly and what deserves a
ticket or a mute-with-reason.

If `output/reports/alert-noise/alert-noise.base` does not exist, create it (see
the committed copy for the schema: filter `type == "alert-noise-report"`, one
table view sorted by `run_date` DESC).

### 6. Update the wiki synthesis (LLM)

In `wiki/company/fes-platform-alert-noise.md`: update the Chronic offenders list
(carry entries forward, mark resolved ones), bump `last_compiled`. The trend
table updates itself from the base, no manual edit needed. If the page is
missing, create it (domain `[fanatics, observability]`) and add it to
`wiki/_indexes/fanatics.md` and `wiki/_indexes/observability.md`.

### 7. Log

Append a `compile` entry to `wiki/_log.md`.

## Backfill (multiple past weeks at once)

To seed history, fetch a wide range and split it into weekly notes:

1. Paginate the whole range with `latest`-boundary pagination (oldest = range
   start epoch), collecting every saved page path. For a long range this is many
   pages; delegate the fetch to a subagent so the dumps stay out of context.
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
