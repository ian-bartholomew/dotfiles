# End-of-Day Unattended Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/end-of-day` run unsupervised as a local Claude Code Desktop scheduled task with zero permission prompts and zero in-skill gates, auto-creating meeting-action-item todos that are deduped against the live Todoist list without ever silently burying a real commitment.

**Architecture:** Three independent workstreams. (A) A deterministic dedup backstop added to `meeting_action_items.py` in the `lyt-assistant` repo, protecting every caller. (B) An `--unattended` mode added to the `end-of-day` SKILL.md prompt in the `.dotfiles` repo, which removes interactive gates, keeps external writes draft/dry-run, auto-writes only the local daily note, and ends with a push notification. (C) A start-of-day dead-man's-switch check + local machine setup (settings allow/deny, scheduled-task body, plist removal) gated behind two clean scheduled runs.

**Tech Stack:** Python 3 stdlib (no deps), Claude Code skills (markdown prompts), `td` Todoist CLI, macOS `launchctl`, Claude Code Desktop scheduled tasks.

## Global Constraints

- `meeting_action_items.py` stays **Python 3 stdlib only** — no new dependencies. (verbatim from spec)
- Dedup match rule is **normalized-equality OR token-set overlap ≥ 0.85**, NEVER substring containment. (verbatim from spec)
- A live-todo dedup match is **non-terminal**: not created this run, not recorded in state, re-evaluated next run. (verbatim from spec)
- The dedup backstop is **best-effort**: any `td` failure yields an empty existing-titles list and dedup is skipped, never a hard error.
- `--unattended` must make **no `AskUserQuestion` calls** and **no continue/retry/halt prompts**; Step 0 pre-flight is the one hard halt.
- External/published writes (Confluence) are **draft/dry-run only** unattended; the local daily note is the one auto-write.
- No emojis and no em dashes in any saved/shared text (commits, file contents). (user CLAUDE.md)
- Do NOT delete the EOD LaunchAgent until **2 consecutive clean scheduled runs** are observed.
- Tests run as a plain script: `python3 test_meeting_action_items.py` (no pytest).

## File Structure

- `lyt-assistant` repo (`~/.claude/plugins/marketplaces/ian-bartholomew-lyt-assistant/`):
  - `skills/meeting-action-items/meeting_action_items.py` — MODIFY: add dedup functions + wire into `cmd_apply`.
  - `skills/meeting-action-items/test_meeting_action_items.py` — MODIFY: add dedup tests.
  - `skills/meeting-action-items/SKILL.md` — MODIFY: document the `duplicate` outcome + skip-not-dismiss rule for the model layer.
- `.dotfiles` repo (current worktree):
  - `stow-packages/claude/.claude/skills/end-of-day/SKILL.md` — MODIFY: frontmatter + Unattended Mode section.
  - `stow-packages/claude/.claude/skills/start-of-day/SKILL.md` — MODIFY: dead-man's-switch check.
- Local machine (not version-controlled):
  - `~/.claude/settings.json` — allow/deny edits.
  - `~/.claude/scheduled-tasks/end-of-day/SKILL.md` — task body → `/end-of-day --unattended`.
  - `~/Library/LaunchAgents/com.ian.{eod,sod,standup}.plist` — backup + remove.

---

## Workstream A — Todoist dedup backstop (lyt-assistant repo)

> All Task A steps run in the `lyt-assistant` repo. Create a branch first:
> `cd ~/.claude/plugins/marketplaces/ian-bartholomew-lyt-assistant && git checkout -b end-of-day-dedup-backstop`
> Paths below are relative to that repo root.

### Task A1: Pure dedup functions

**Files:**

- Modify: `skills/meeting-action-items/meeting_action_items.py`
- Test: `skills/meeting-action-items/test_meeting_action_items.py`

**Interfaces:**

- Produces: `dedup_normalize(s: str) -> str`, `dedup_tokens(s: str) -> set`, `title_similarity(a: str, b: str) -> float`, `find_duplicate(candidate: str, existing: list[str]) -> str | None`, module constant `DEDUP_THRESHOLD = 0.85`.

- [ ] **Step 1: Write the failing tests**

Add to `test_meeting_action_items.py` (before the `if __name__` block):

```python
def test_dedup_normalize():
    assert mai.dedup_normalize("- [ ] Email Bob!") == "email bob"
    assert mai.dedup_normalize("Email   Bob.") == "email bob"
    assert mai.dedup_normalize("Review PR #1842 (urgent)") == "review pr 1842 urgent"
    # NFKC fold + emoji/punct become separators, not retained
    assert mai.dedup_normalize("Ship — the 🚀 release") == "ship the release"


def test_title_similarity():
    assert mai.title_similarity("Email Bob", "email bob.") == 1.0
    # distinct short title must NOT merge into a longer one (data-loss guard)
    assert mai.title_similarity(
        "Email Bob", "Email Bob about the Q3 contract") < mai.DEDUP_THRESHOLD
    # reworded-but-same significant tokens scores high
    assert mai.title_similarity(
        "follow up with Dave on kafka rollout",
        "follow up with Dave on the kafka rollout") >= mai.DEDUP_THRESHOLD


def test_find_duplicate():
    existing = ["Email Bob about the Q3 contract", "Review the deploy runbook"]
    # normalized-equality match
    assert mai.find_duplicate("review the deploy runbook!", existing) == \
        "Review the deploy runbook"
    # distinct short title is NOT a duplicate (would have been with substring)
    assert mai.find_duplicate("Email Bob", existing) is None
    # nothing matches
    assert mai.find_duplicate("Write the design doc", existing) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/meeting-action-items && python3 -c "import test_meeting_action_items as t; t.test_dedup_normalize()"`
Expected: FAIL with `AttributeError: module 'meeting_action_items' has no attribute 'dedup_normalize'`

- [ ] **Step 3: Add `import unicodedata`**

In `meeting_action_items.py`, add to the import block (after `import tempfile`):

```python
import unicodedata
```

- [ ] **Step 4: Implement the dedup functions**

In `meeting_action_items.py`, add after the `normalize()` / `key_for()` block (after line ~59, before `body_of`):

```python
DEDUP_THRESHOLD = 0.85
DEDUP_STOPWORDS = {
    "the", "a", "an", "to", "of", "for", "and", "with",
    "on", "in", "re", "about", "please",
}


def dedup_normalize(s: str) -> str:
    """Aggressive normalization for cross-todo dedup. Distinct from
    normalize() (which mirrors the legacy state-key bash chain): NFKC fold,
    strip a leading bullet/checkbox, lowercase, replace every non-word char
    with a space, collapse whitespace."""
    s = unicodedata.normalize("NFKC", s)
    s = BULLET_RE.sub("", s)
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def dedup_tokens(s: str) -> set:
    return {t for t in dedup_normalize(s).split() if t not in DEDUP_STOPWORDS}


def title_similarity(a: str, b: str) -> float:
    """Jaccard over significant tokens. 1.0 == identical significant-token
    sets. Substring containment deliberately does NOT score high."""
    ta, tb = dedup_tokens(a), dedup_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_duplicate(candidate: str, existing: list[str]) -> str | None:
    """First existing title that is a normalized-equal or high-token-overlap
    match for candidate, else None. Never uses substring containment."""
    cnorm = dedup_normalize(candidate)
    if cnorm:
        for e in existing:
            if dedup_normalize(e) == cnorm:
                return e
    for e in existing:
        if title_similarity(candidate, e) >= DEDUP_THRESHOLD:
            return e
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd skills/meeting-action-items && python3 test_meeting_action_items.py`
Expected: all tests PASS, including the three new ones.

- [ ] **Step 6: Commit**

```bash
git add skills/meeting-action-items/meeting_action_items.py skills/meeting-action-items/test_meeting_action_items.py
git commit -m "feat(meeting-action-items): add token-set dedup primitives"
```

### Task A2: Wire dedup into `apply`

**Files:**

- Modify: `skills/meeting-action-items/meeting_action_items.py`
- Test: `skills/meeting-action-items/test_meeting_action_items.py`

**Interfaces:**

- Consumes: `find_duplicate`, `DEDUP_THRESHOLD` (Task A1).
- Produces: `fetch_open_titles(dry_run: bool) -> list[str]`; `apply` accepts `--existing-todos <path>` (JSON list of titles, test hook); a new `duplicate` outcome in results carrying `matched`.

- [ ] **Step 1: Write the failing tests**

Add to `test_meeting_action_items.py`:

```python
def test_apply_dedup_skips_existing():
    import shutil
    with tempfile.TemporaryDirectory() as td_dir:
        vault = Path(td_dir) / "vault"
        shutil.copytree(HERE / "test-fixtures" / "vault", vault)
        existing = Path(td_dir) / "existing.json"
        existing.write_text(json.dumps(["Send the quarterly report by Friday"]))
        payload = {"auto_checked": [], "decisions": [
            {"key": "dup1", "meeting_dir": "2026-06-01-standup",
             "raw": "- [ ] Send the quarterly report by Friday",
             "action": "todo", "title": "Send the quarterly report by Friday",
             "description": "d", "due": "Friday", "priority": "p2"}]}
        out = subprocess.run(
            [sys.executable, str(HERE / "meeting_action_items.py"),
             "--vault", str(vault), "apply", "--dry-run",
             "--existing-todos", str(existing)],
            input=json.dumps(payload), capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        res = json.loads(out.stdout)["results"][0]
        assert res["outcome"] == "duplicate"
        assert res["matched"] == "Send the quarterly report by Friday"
        state = json.loads(
            (vault / ".lyt-assistant" / "_action-item-state.json").read_text())
        assert "dup1" not in state["items"]          # non-terminal: not recorded
        assert state["last_run"] is not None          # run still completes


def test_apply_dedup_distinct_short_title_not_skipped():
    import shutil
    with tempfile.TemporaryDirectory() as td_dir:
        vault = Path(td_dir) / "vault"
        shutil.copytree(HERE / "test-fixtures" / "vault", vault)
        existing = Path(td_dir) / "existing.json"
        existing.write_text(json.dumps(["Email Bob about the Q3 contract"]))
        payload = {"auto_checked": [], "decisions": [
            {"key": "short1", "meeting_dir": "2026-06-01-standup",
             "raw": "- [ ] Email Bob", "action": "todo", "title": "Email Bob",
             "description": "d", "due": "", "priority": "p2"}]}
        out = subprocess.run(
            [sys.executable, str(HERE / "meeting_action_items.py"),
             "--vault", str(vault), "apply", "--dry-run",
             "--existing-todos", str(existing)],
            input=json.dumps(payload), capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert json.loads(out.stdout)["results"][0]["outcome"] == "created"


def test_apply_dedup_batch_self():
    import shutil
    with tempfile.TemporaryDirectory() as td_dir:
        vault = Path(td_dir) / "vault"
        shutil.copytree(HERE / "test-fixtures" / "vault", vault)
        existing = Path(td_dir) / "existing.json"
        existing.write_text(json.dumps([]))     # nothing pre-existing
        payload = {"auto_checked": [], "decisions": [
            {"key": "a", "meeting_dir": "2026-06-01-standup", "raw": "- [ ] x",
             "action": "todo", "title": "Draft the launch checklist",
             "description": "d", "due": "", "priority": "p2"},
            {"key": "b", "meeting_dir": "2026-06-01-standup", "raw": "- [ ] y",
             "action": "todo", "title": "draft the launch checklist!",
             "description": "d", "due": "", "priority": "p2"}]}
        out = subprocess.run(
            [sys.executable, str(HERE / "meeting_action_items.py"),
             "--vault", str(vault), "apply", "--dry-run",
             "--existing-todos", str(existing)],
            input=json.dumps(payload), capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        res = {r["key"]: r["outcome"] for r in json.loads(out.stdout)["results"]}
        assert res["a"] == "created"
        assert res["b"] == "duplicate"      # matched the just-created sibling
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/meeting-action-items && python3 test_meeting_action_items.py`
Expected: FAIL — `--existing-todos` is an unrecognized argument / `duplicate` outcome not produced.

- [ ] **Step 3: Add `fetch_open_titles` and the `--existing-todos` arg**

In `meeting_action_items.py`, add after `td_add()` (before `cmd_apply`):

```python
def fetch_open_titles(dry_run: bool) -> list[str]:
    """Open Work-project task titles via td. Empty list on ANY failure:
    dedup is a best-effort backstop, never a hard dependency. Uses --all to
    page past the 300-task default."""
    if dry_run:
        return []
    try:
        r = subprocess.run(
            ["td", "task", "list", "--project", "Work", "--all", "--json"],
            capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0:
        return []
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []
    results = data.get("results", []) if isinstance(data, dict) else data
    return [t.get("content", "") for t in results
            if isinstance(t, dict) and t.get("content")]
```

In `main()`, add the arg to the `apply` parser (after the `--dry-run` line):

```python
    pa.add_argument("--existing-todos")
```

- [ ] **Step 4: Load existing titles in `cmd_apply` and dedup in the todo branch**

In `cmd_apply`, after the `td auth status` check block and before `results = []`, add:

```python
    if args.existing_todos:
        with open(args.existing_todos) as f:
            existing_titles = list(json.load(f))
    else:
        existing_titles = fetch_open_titles(args.dry_run)
```

Then replace the `if action == "todo":` block body so it dedups first:

```python
            if action == "todo":
                matched = find_duplicate(d["title"], existing_titles)
                if matched is not None:
                    # non-terminal: skip creation, do NOT record state, so it
                    # resurfaces if the matched live todo is later completed.
                    results.append({"key": d["key"], "outcome": "duplicate",
                                    "matched": matched})
                    continue
                ok, info = td_add(d["title"], d.get("priority", "p2"),
                                  d["description"], d.get("due", ""),
                                  args.dry_run)
                if ok:
                    record(d["key"], d["meeting_dir"], d["raw"],
                           "todoed", info)
                    existing_titles.append(d["title"])  # dedup later batch items
                    results.append({"key": d["key"], "outcome": "created",
                                    "url": info})
                else:
                    results.append({"key": d["key"], "outcome": "failed",
                                    "error": info})
                continue
```

- [ ] **Step 5: Run the full test suite**

Run: `cd skills/meeting-action-items && python3 test_meeting_action_items.py`
Expected: all PASS, including the three new dedup-apply tests and the pre-existing `test_apply_dry_run` / `test_apply_failing_td` / `test_apply_malformed_decision`.

- [ ] **Step 6: Commit**

```bash
git add skills/meeting-action-items/meeting_action_items.py skills/meeting-action-items/test_meeting_action_items.py
git commit -m "feat(meeting-action-items): dedup new todos against live Work project"
```

### Task A3: Document the dedup contract in the skill

**Files:**

- Modify: `skills/meeting-action-items/SKILL.md`

**Interfaces:**

- Consumes: the `duplicate` outcome + non-terminal rule (Task A2). No code.

- [ ] **Step 1: Add a Dedup subsection**

In `skills/meeting-action-items/SKILL.md`, in the `### Step 4: Apply` section, after the sentence describing `apply` outputs, append:

```markdown
`apply` also dedups every `todo` against the live Todoist Work project
(`td task list --project Work --all --json`) before creating it. A candidate
whose title is a normalized-equal or high-token-overlap (>= 0.85) match for an
existing open task returns outcome `duplicate` (with the matched title) and is
**not created and not recorded in state** — so it resurfaces next run if the
matched live todo is later completed. Substring containment is never used, so a
short title ("Email Bob") is not swallowed by a longer one ("Email Bob about
the Q3 contract"). Callers driving non-interactive runs MUST mark a
model-detected semantic duplicate as `skip` (non-terminal), never `dismiss`
(terminal) — permanent suppression of an action item is reserved for an
explicit user decision.
```

- [ ] **Step 2: Bump the skill version**

In the frontmatter, bump `version:` by a patch (e.g. `0.8.0` -> `0.9.0`).

- [ ] **Step 3: Commit**

```bash
git add skills/meeting-action-items/SKILL.md
git commit -m "docs(meeting-action-items): document live-Todoist dedup contract"
```

- [ ] **Step 4: Open the PR**

```bash
git push -u origin end-of-day-dedup-backstop
gh pr create --title "meeting-action-items: dedup new todos against live Todoist" --body "Adds a deterministic, best-effort dedup backstop in apply: normalized-equality or token-set overlap >= 0.85 (never substring containment), non-terminal duplicate state so commitments resurface if the matched todo is completed. Tests cover the match rule, normalization vectors, batch self-match, and the short-title data-loss guard."
```

---

## Workstream B — `--unattended` mode (.dotfiles, current worktree)

### Task B1: end-of-day `--unattended` mode

**Files:**

- Modify: `stow-packages/claude/.claude/skills/end-of-day/SKILL.md`

**Interfaces:**

- Consumes: the `meeting_action_items.py` `duplicate` outcome and the model `skip`-not-`dismiss` rule (Workstream A).

- [ ] **Step 1: Frontmatter — argument hint + PushNotification tool**

In the SKILL.md frontmatter: add an `argument-hint`, and add `PushNotification` to `allowed-tools`.

Change:

```yaml
version: 0.3.0
allowed-tools: [Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion, Skill, Agent, TaskStop]
```

to:

```yaml
version: 0.4.0
argument-hint: "[--unattended]"
allowed-tools: [Read, Write, Edit, Bash, Grep, Glob, AskUserQuestion, Skill, Agent, TaskStop, PushNotification]
```

- [ ] **Step 2: Add the Unattended Mode section**

Insert a new `## Unattended Mode` section immediately before `## Pipeline Overview`:

````markdown
## Unattended Mode

When invoked as `/end-of-day --unattended` (the scheduled Desktop task uses
this), the pipeline runs with no human present. Manual `/end-of-day` is
unchanged. Both modes share every step body; they differ ONLY at the points
listed here. If the `--unattended` token is absent, ignore this entire section.

### Global rules (unattended)

- **No `AskUserQuestion` calls. No continue/retry/halt prompts.** Every place
  the interactive flow would ask, take the documented auto-default and continue.
- **Best-effort.** A mid-run sub-skill failure is logged to the report and the
  run continues. The single exception is Step 0 pre-flight, which still HALTS.
- **External/published writes are draft/dry-run only** (see Step 2). The local
  daily note (Step 9) is the one surface auto-written, because it is local,
  reversible, and idempotently upserted.
- **End every run with a notification** (Observability, below).

### Concurrency lock (first action, unattended)

Before Step 0, acquire an exclusive lock so a catch-up run and a manual run
cannot double-write the shared `/tmp` caches and `_action-item-state.json`:

```bash
if ! mkdir /tmp/eod.lock 2>/dev/null; then
  echo "end-of-day already running (/tmp/eod.lock present); aborting."; exit 0
fi
```

Remove `/tmp/eod.lock` (`rmdir /tmp/eod.lock`) as the final action of the run,
including on any failure path that ends the run early.

### Run-date under catch-up

A Desktop task missed because the Mac was asleep fires a single catch-up within
7 days. The cached `date +%Y-%m-%d` therefore reflects the **actual run day**,
not the missed day. All source steps are "since last run", so nothing is lost;
the daily-note section and report are labeled with the run day. This is expected
behavior, not an error.

### Per-step deltas (unattended)

- **Step 0 (pre-flight):** unchanged; still HALTS on failure. On halt, send the
  failure notification and release the lock before exiting.
- **Step 2 (FES support -> Confluence):** the background subagent must run
  **draft/hold only** -- do NOT publish a live Confluence page. Instruct it: if
  the fes-support-learnings skill supports draft pages, publish as DRAFT;
  otherwise write the would-be page content to
  `raw/support_learnings/_pending_confluence/<date>.md` and report it as
  deferred. Live Confluence publish is for interactive runs.
- **Steps 3, 4 (support / internal -> raw/):** run non-interactively using the
  exact auto-default convention Step 2 already documents (classification ->
  `knowledge`, resolution -> `unresolved`, domain -> keyword-map else
  `general`, duplicate -> `skip`, any other -> the skip/no-action option).
  Record every auto-default for the report. (These write to local `raw/`, which
  is reviewable and compiled later, so auto-defaulting is acceptable.)
- **Step 5 (meeting action items):** run inline, no AskUserQuestion:
  1. `python3 <skill-dir>/meeting_action_items.py list` -> candidates with
     suggested title/due/priority.
  2. `td task list --project Work --all --json` -> existing open todos.
  3. Semantic pass: for each candidate that MEANS THE SAME as an existing open
     todo (reworded duplicate the string layer would miss), mark it `skip`
     (NON-terminal -- never `dismiss`). Only skip on high confidence; when
     unsure, let it through as a `todo` (a near-dupe is recoverable; a dropped
     commitment is not). Log each skip with the matched todo title.
  4. Build the decisions JSON: surviving candidates -> `todo` with the script's
     suggested due/priority; semantic dupes -> `skip`.
  5. `python3 <skill-dir>/meeting_action_items.py apply --input <file>`. The
     script independently dedups each `todo` against the live Work project and
     returns `duplicate` (non-terminal) for any it catches -- this is the
     deterministic backstop beneath the semantic pass.
  Report created vs `skip` (semantic dupe) vs `duplicate` (script backstop);
  never silently drop. (`<skill-dir>` is the meeting-action-items skill dir
  announced when that skill loads.)
- **Step 6 (project-log gate):** run the audit (detection) ONLY. Do NOT
  auto-write `log.md` entries. Record each gap in the report and add it to the
  Step 9 daily-note Follow-ups for the next interactive session.
- **Step 7 (compile):** run non-interactively. (If the compile sub-skill exposes
  any prompt, take its default.)
- **Step 9 (daily-note synthesis):** auto-approve and write the drafted section.
  The upsert and `grep -c '<!-- eod:begin'` post-write check are unchanged. If
  the check returns `0` or `>1`, do NOT proceed silently: record a failure
  marker and include it in the notification.
- **Step 10 (report):** after writing the `wiki/_log.md` entry, send the
  end-of-run notification (below), then release the lock.

### Observability (unattended)

- **End-of-run notification.** Send a `PushNotification` with a one-line status:
  `end-of-day <date>: ok` / `end-of-day <date>: degraded (<N> step failures)` /
  `end-of-day <date>: halted (pre-flight)`. Include the deduped/deferred counts
  and any step failures in the body.
- **Permission self-check.** If any tool call in the run was blocked or timed
  out waiting on a permission decision (the symptom of the Desktop
  `bypassPermissions` mode having silently reverted), treat the run as
  `degraded` and say so in the notification -- this is how a reverted permission
  mode gets surfaced instead of discovered later.

### Success checklist (what "ran cleanly" means)

A clean unattended run satisfies all of: pre-flight passed; Slack steps 2/3/4
reached and produced output or an honest nothing-to-do; Confluence stayed
draft/deferred; Step 5 reported created vs skip vs duplicate with no silent
drop; the daily-note section was written and the post-write check returned
exactly 1; the notification status is `ok`; and `wiki/_log.md` has the run entry
for the resolved date.
````

- [ ] **Step 3: Cross-reference the dedup in Step 5's existing body**

In the existing `### Step 5: Meeting Action Items` section, after the
"**Must run interactively in the foreground session.**" paragraph, append:

```markdown
In `--unattended` mode this step does NOT run interactively -- see Unattended
Mode > Per-step deltas > Step 5 for the inline auto-todo + dedup flow.
```

- [ ] **Step 4: Verify no AskUserQuestion leaks into the unattended path**

Run:

```bash
cd /Users/ian.bartholomew/.dotfiles/.claude/worktrees/end-of-day-unattended
awk '/^## Unattended Mode/{f=1} f' stow-packages/claude/.claude/skills/end-of-day/SKILL.md | grep -c AskUserQuestion
```

Expected: `0` (the Unattended Mode section must reference no AskUserQuestion).
Also confirm the section names every interactive step (2,3,4,5,6,9) plus Step 0:

```bash
awk '/^## Unattended Mode/{f=1} f' stow-packages/claude/.claude/skills/end-of-day/SKILL.md | grep -oE 'Step [0-9]+' | sort -u
```

Expected: includes Step 0, Step 2, Step 3, Step 4, Step 5, Step 6, Step 7, Step 9, Step 10.

- [ ] **Step 5: Commit**

```bash
git add stow-packages/claude/.claude/skills/end-of-day/SKILL.md
git commit -m "feat(end-of-day): add --unattended mode for scheduled runs"
```

### Task B2: start-of-day dead-man's-switch

**Files:**

- Modify: `stow-packages/claude/.claude/skills/start-of-day/SKILL.md`

- [ ] **Step 1: Add the heartbeat check step**

In `start-of-day/SKILL.md`, after `### Step 2.5: Work-board sync (live)` and before `### Step 3: Invoke the render script`, insert:

````markdown
### Step 2.6: End-of-day heartbeat check

Confirm the previous business day actually ran its end-of-day pipeline. A
missed unattended EOD leaves no `wiki/_log.md` entry -- a silent non-event.
Surface its absence in the morning.

```bash
PREV=$(date -v-1d +%Y-%m-%d)         # Mon-Thu; on Monday use Friday:
[ "$(date +%u)" = "1" ] && PREV=$(date -v-3d +%Y-%m-%d)
grep -q "^## \[$PREV\] end-of-day" ~/Documents/Work/wiki/_log.md \
  && echo "EOD heartbeat: $PREV ok" \
  || echo "EOD heartbeat: WARNING no end-of-day entry for $PREV"
```

If the check warns, add a one-line "Yesterday's end-of-day did not run -- check
the Desktop scheduled task" note to the start-of-day terminal summary (Step 5).
Do not block the morning routine on it.
````

- [ ] **Step 2: Commit**

```bash
git add stow-packages/claude/.claude/skills/start-of-day/SKILL.md
git commit -m "feat(start-of-day): warn when prior end-of-day run is missing"
```

- [ ] **Step 3: Open the .dotfiles PR**

```bash
cd /Users/ian.bartholomew/.dotfiles/.claude/worktrees/end-of-day-unattended
git push -u origin worktree-end-of-day-unattended
gh pr create --title "end-of-day: unattended mode for scheduled runs" --body "Adds --unattended mode (no in-skill prompts, draft/dry-run external writes, auto daily-note, end-of-run notification, concurrency lock, catch-up date handling) and a start-of-day dead-man's-switch that warns when the prior day's EOD produced no wiki/_log.md entry. Spec + adversarial review in docs/superpowers/."
```

---

## Workstream C — Local setup + migration (machine config, not PRs)

> Do these AFTER both PRs are merged and the skills are re-stowed/active.

### Task C1: settings.json allow + deny

**Files:**

- Modify: `~/.claude/settings.json`

- [ ] **Step 1: Add the scoped allow entries**

Add to `permissions.allow` (if not already present):

```
"Bash(rm -rf /tmp/eod-cache-threads:*)",
"Bash(rm -f /tmp/eod-fes-support-cache.json:*)"
```

Confirm `Bash(td:*)` and `Bash(python3:*)` are already present (they are).

- [ ] **Step 2: Audit the deny-list**

Confirm `permissions.deny` still blocks the destructive patterns (`rm -rf /`,
`rm -rf /*`, `mkfs`, `dd if=… of=/dev/*`, `curl|bash`, force-push to main).
These remain the hard floor under `bypassPermissions`. No removals.

- [ ] **Step 3: Validate JSON**

Run: `python3 -m json.tool ~/.claude/settings.json > /dev/null && echo OK`
Expected: `OK`

### Task C2: Point the scheduled task at unattended mode

**Files:**

- Modify: `~/.claude/scheduled-tasks/end-of-day/SKILL.md`

- [ ] **Step 1: Update the task body**

Change the body from `/end-of-day` to `/end-of-day --unattended`. Keep the
frontmatter `name`/`description`.

### Task C3: Desktop UI permission mode (USER MANUAL STEP)

- [ ] **Step 1: Set the task to bypassPermissions**

In the Claude Code Desktop app: Routines/Scheduled tasks -> `end-of-day` ->
edit -> set Permission mode to `bypassPermissions`; confirm schedule weekdays
16:30; confirm the prompt body is `/end-of-day --unattended`. Save.

Caveat: Desktop may silently revert this to `acceptEdits` (known bug). The
allowlist backstop (C1) and the run's permission self-check (B1) exist for that
case. Re-verify the mode after the first run.

### Task C4: Backup the LaunchAgents

**Files:**

- Create: `~/Library/LaunchAgents-backup-<date>/`

- [ ] **Step 1: Snapshot all three plists before any change**

```bash
BK=~/Library/LaunchAgents-backup-$(date +%Y%m%d-%H%M%S)
mkdir -p "$BK"
cp ~/Library/LaunchAgents/com.ian.eod.plist \
   ~/Library/LaunchAgents/com.ian.sod.plist \
   ~/Library/LaunchAgents/com.ian.standup.plist "$BK"/
ls -la "$BK"
```

Expected: three plists copied. (Honors the "backup before destructive moves" rule.)

### Task C5: Supervised first run + gate

- [ ] **Step 1: Supervised dry run**

With the Desktop task configured, manually run `/end-of-day --unattended` while
watching. Walk the success checklist (Task B1, Step 2). Fix anything that
stalls or fails; repeat until clean.

- [ ] **Step 2: Two clean scheduled runs**

Let the Desktop task fire on its own schedule. Confirm each run via the
notification + the `wiki/_log.md` entry + the checklist. Require **2 consecutive
clean scheduled runs** before proceeding.

### Task C6: Remove the LaunchAgents

> GATE: only after Task C5 Step 2 (two clean scheduled runs).

- [ ] **Step 1: Unload and delete all three**

```bash
for j in eod sod standup; do
  launchctl bootout gui/$(id -u)/com.ian.$j 2>/dev/null \
    || launchctl unload ~/Library/LaunchAgents/com.ian.$j.plist 2>/dev/null
  rm -f ~/Library/LaunchAgents/com.ian.$j.plist
done
launchctl list | grep com.ian || echo "no com.ian jobs remain"
```

Expected: `no com.ian jobs remain`; backups from C4 still exist.

- [ ] **Step 2: Note the sod/standup follow-up**

sod and standup are now off their always-on runner but have NOT received
`--unattended` treatment, so their Desktop scheduled runs may stall on their own
in-skill prompts. Run them manually if a scheduled run stalls; track converting
start-of-day and daily-standup to `--unattended` as a follow-up.

---

## Self-Review notes

- Spec coverage: `--unattended` (B1), three-layer auth (C1 allow/deny + C3
  bypassPermissions + B1 flag), threat model / deny floor (C1 Step 2 + B1
  observability self-check), Todoist dedup non-terminal + match rule (A1/A2),
  one terminal-state authority + model skip-not-dismiss (A3 + B1 Step 5),
  external draft/dry-run (B1 Step 2 delta), observability + dead-man's-switch
  (B1 + B2), safe migration sequencing + rollback via backups (C4/C5/C6),
  concurrency lock (B1), catch-up date (B1), success checklist + DoD (B1 +
  this plan's ordering). The `.mcp.json` sizing and auto-log out-of-scope items
  are documentation-only in the spec, no task needed.
- No placeholders: all code shown in full; all commands exact.
- Type consistency: `find_duplicate`/`DEDUP_THRESHOLD`/`fetch_open_titles`
  names match between A1, A2, and their tests; `duplicate` outcome string
  matches between A2 code, A2 tests, A3 docs, and B1 Step 5.
