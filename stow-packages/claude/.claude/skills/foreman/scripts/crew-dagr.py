#!/usr/bin/env python3
"""crew-dagr — a tiny run-state contract for a crew fleet: validate and view.

Ours, not aemrebarut/herdr-dagr. We borrowed its load-bearing ideas (the
task/attempt split, evidence tiers, liveness, backward-pointing causes, and a
lint that catches a clean-but-wrong file) and dropped everything that needed a
Rust toolchain or a live TUI. Pure stdlib.

    crew-dagr.py check <run.json> [--strict] [--json]
    crew-dagr.py view  <run.json>
    crew-dagr.py test

The producer (the foreman) owns the file; this tool never writes run state. It
validates (`check`) and renders (`view`). Schema and finding codes: CONTRACT.md.

Exit codes: 0 clean · 1 findings (errors, or warnings under --strict) · 2 the
file could not be read at all (path/tooling), never a document problem.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

# One global run file, not per-repo: the fleet is global, so the foreman writes
# the same file wherever its cwd is, and the herdr pane always reads that path.
# Override with $CREW_DAGR_RUN or a positional arg.
DEFAULT_RUN = os.path.expanduser(os.environ.get("CREW_DAGR_RUN") or "~/.crew/dagr.json")
BACKLOG_PATH = os.path.expanduser("~/.crew/backlog.jsonl")  # queued tickets, written by `crew backlog`

# For the "open in browser" keys. Overridable by env; repo defaults per task.
JIRA_BASE = os.environ.get("CREW_DAGR_JIRA_BASE") or "https://betfanatics.atlassian.net/browse/"
GH_ORG = os.environ.get("CREW_DAGR_GH_ORG") or "fanatics-gaming"
DEFAULT_REPO = os.environ.get("CREW_DAGR_REPO") or "fanapp-terraform"


def jira_url(task_id):
    """Ticket URL if the id looks like a JIRA key (e.g. fandevx-3631). Pure."""
    m = re.match(r"^([A-Za-z]+)-(\d+)$", task_id or "")
    return JIRA_BASE + f"{m.group(1).upper()}-{m.group(2)}" if m else None


def pr_url(task):
    """PR URL if the task carries a pr number. Repo defaults to DEFAULT_REPO. Pure."""
    pr = task.get("pr")
    if pr in (None, "", 0):
        return None
    return f"https://github.com/{GH_ORG}/{task.get('repo') or DEFAULT_REPO}/pull/{pr}"


def _open_url(url):
    if not url:
        return False
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False

VERSION = 1
TASK_STATES = {"queued", "working", "awaiting", "blocked", "done", "failed", "abandoned"}
ATTEMPT_STATES = {"working", "awaiting", "done", "failed", "lost"}
EVIDENCE = {"verified", "reported", "heuristic", "asserted"}
CAUSES = {"initial", "sent_back", "nudged", "gate_failed", "followup"}

STATE_GLYPH = {"queued": "○", "working": "◐", "awaiting": "⋯", "blocked": "⊘",
               "done": "●", "failed": "✗", "abandoned": "⊗"}
ATT_GLYPH = {"working": "◐", "awaiting": "⋯", "done": "●", "failed": "✗", "lost": "⌁"}
EV_GLYPH = {"verified": "◆", "reported": "◇", "heuristic": "≈", "asserted": "!"}
CAUSE_GLYPH = {"initial": " ", "sent_back": "↩", "nudged": "↩", "gate_failed": "⨯", "followup": "↳"}


class Finding:
    __slots__ = ("code", "level", "path", "msg")

    def __init__(self, code, level, path, msg):
        self.code, self.level, self.path, self.msg = code, level, path, msg

    def as_dict(self):
        return {"code": self.code, "level": self.level, "path": self.path, "msg": self.msg}

    def line(self):
        return f"{self.level} {self.code}  {self.path}: {self.msg}"


def _latest(atts):
    real = [a for a in atts if isinstance(a, dict) and isinstance(a.get("n"), int)]
    return max(real, key=lambda a: a["n"]) if real else None


def _projection(tp, st, atts):
    latest = _latest(atts)
    has_working = any(isinstance(a, dict) and a.get("state") == "working" for a in atts)
    ls = latest.get("state") if latest else None

    def bad(why):
        return [Finding("E150", "E", f"{tp}.state", why)]

    if st == "working" and not has_working:
        return bad("task 'working' but no working attempt")
    if st == "done" and ls != "done":
        return bad("task 'done' but latest attempt is not done")
    if st == "awaiting" and ls != "awaiting":
        return bad("task 'awaiting' but latest attempt is not awaiting")
    if st == "failed" and ls not in ("failed", "lost"):
        return bad("task 'failed' but latest attempt is not failed/lost")
    if st == "queued" and (has_working or ls == "done"):
        return bad("task 'queued' but has a working or done attempt")
    if st == "abandoned" and ls in ("working", "done"):
        return bad("task 'abandoned' but latest attempt is working/done")
    return []


def _cycles(tasks, task_ids):
    graph = {t["id"]: [d for d in (t.get("deps") or []) if d in task_ids]
             for t in tasks if isinstance(t, dict) and t.get("id")}
    color = {k: 0 for k in graph}  # 0 white, 1 grey, 2 black
    found = []

    def dfs(u, stack):
        color[u] = 1
        stack.append(u)
        for v in graph.get(u, []):
            if color.get(v) == 1:
                cyc = stack[stack.index(v):] + [v]
                found.append(" -> ".join(cyc))
                return True
            if color.get(v) == 0 and dfs(v, stack):
                return True
        stack.pop()
        color[u] = 2
        return False

    for k in graph:
        if color[k] == 0 and dfs(k, []):
            break
    return [Finding("E122", "E", "$.tasks", "dependency cycle: " + found[0])] if found else []


def check(doc):
    out = []
    if not isinstance(doc, dict):
        return [Finding("E001", "E", "$", "run file is not a JSON object")]
    if doc.get("version") != VERSION:
        out.append(Finding("E100", "E", "$.version", f"version must be {VERSION}, got {doc.get('version')!r}"))
    run = doc.get("run")
    if not isinstance(run, dict) or not run.get("id"):
        out.append(Finding("E101", "E", "$.run.id", "run.id is required"))
    tasks = doc.get("tasks")
    if not isinstance(tasks, list):
        out.append(Finding("E102", "E", "$.tasks", "tasks[] is required"))
        return out

    task_ids = set()
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            out.append(Finding("E111", "E", f"$.tasks[{i}]", "task is not an object"))
            continue
        tid = t.get("id")
        if tid in task_ids:
            out.append(Finding("E110", "E", f"$.tasks[{i}].id", f"duplicate task id {tid!r}"))
        elif tid:
            task_ids.add(tid)

    att_ids = set()
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            continue
        tp = f"$.tasks[{i}]"
        for req in ("id", "title", "kind", "state"):
            if not t.get(req):
                out.append(Finding("E111", "E", f"{tp}.{req}", f"missing required field {req}"))
        st = t.get("state")
        if st is not None and st not in TASK_STATES:
            out.append(Finding("E112", "E", f"{tp}.state", f"unknown task state {st!r}"))
        for d in (t.get("deps") or []):
            if d not in task_ids:
                out.append(Finding("E120", "E", f"{tp}.deps", f"dangling dep {d!r}"))
        if st == "blocked" and not t.get("unblock"):
            out.append(Finding("W205", "W", f"{tp}.unblock", "blocked task names no unblocker"))

        atts = t.get("attempts") or []
        ns = set()
        for j, a in enumerate(atts):
            ap = f"{tp}.attempts[{j}]"
            if not isinstance(a, dict):
                out.append(Finding("E131", "E", ap, "attempt is not an object"))
                continue
            aid = a.get("id")
            if aid in att_ids:
                out.append(Finding("E130", "E", f"{ap}.id", f"duplicate attempt id {aid!r}"))
            elif aid:
                att_ids.add(aid)
            if aid in task_ids:
                out.append(Finding("E113", "E", f"{ap}.id", f"attempt id {aid!r} collides with a task id"))
            for req in ("id", "n", "state"):
                if a.get(req) in (None, ""):
                    out.append(Finding("E131", "E", f"{ap}.{req}", f"missing required field {req}"))
            ast = a.get("state")
            if ast is not None and ast not in ATTEMPT_STATES:
                out.append(Finding("E132", "E", f"{ap}.state", f"unknown attempt state {ast!r}"))

            cause = a.get("cause")
            if not isinstance(cause, dict) or not cause.get("type"):
                out.append(Finding("E131", "E", f"{ap}.cause", "missing cause.type"))
                ctype = None
            else:
                ctype = cause.get("type")
                if ctype not in CAUSES:
                    out.append(Finding("E133", "E", f"{ap}.cause.type", f"unknown cause type {ctype!r}"))

            n = a.get("n")
            if isinstance(n, int):
                if n in ns:
                    out.append(Finding("E131", "E", f"{ap}.n", f"duplicate attempt n {n} in task"))
                ns.add(n)
                if ctype is not None:
                    if n == 1 and ctype != "initial":
                        out.append(Finding("E135", "E", f"{ap}.cause", "first attempt must have cause 'initial'"))
                    if n > 1:
                        if ctype == "initial":
                            out.append(Finding("E135", "E", f"{ap}.cause", "attempt n>1 must not be 'initial'"))
                        else:
                            ref = cause.get("ref")
                            refa = next((x for x in atts if isinstance(x, dict) and x.get("id") == ref), None)
                            if not refa:
                                out.append(Finding("E135", "E", f"{ap}.cause.ref",
                                                    f"cause.ref {ref!r} is not a declared attempt in this task"))
                            elif isinstance(refa.get("n"), int) and refa["n"] >= n:
                                out.append(Finding("E136", "E", f"{ap}.cause.ref",
                                                   f"cause.ref points forward (n {refa['n']} >= {n})"))

            if ast == "done":
                oc = a.get("outcome")
                if not isinstance(oc, dict):
                    out.append(Finding("E140", "E", f"{ap}.outcome", "done attempt needs an outcome"))
                else:
                    ev = oc.get("evidence")
                    if ev is None:
                        out.append(Finding("W201", "W", f"{ap}.outcome.evidence", "done without an evidence tier (renders !)"))
                    elif ev not in EVIDENCE:
                        out.append(Finding("E142", "E", f"{ap}.outcome.evidence", f"unknown evidence tier {ev!r}"))
            if ast == "working":
                if not (a.get("locator") or {}).get("pane"):
                    out.append(Finding("W204", "W", f"{ap}.locator", "working attempt without a locator"))
                if not isinstance(a.get("liveness"), dict):
                    out.append(Finding("W208", "W", f"{ap}.liveness", "working attempt with no liveness"))

        out += _projection(tp, st, atts)

    out += _cycles([t for t in tasks if isinstance(t, dict)], task_ids)
    return out


def parse_ts(s):
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def ago(ts, now):
    secs = int((now - ts).total_seconds())
    if secs < 0:
        return "future?"
    if secs < 90:
        return f"{secs}s"
    if secs < 5400:
        return f"{secs // 60}m"
    if secs < 172800:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def view(doc):
    now = datetime.now(timezone.utc)
    run = doc.get("run") or {}
    tasks = [t for t in (doc.get("tasks") or []) if isinstance(t, dict)]
    by_id = {t.get("id"): t for t in tasks}

    head = f"run: {run.get('id', '?')}"
    if run.get("title"):
        head += f" — {run['title']}"
    gen = parse_ts(doc.get("generated_at"))
    if gen:
        head += f"   (generated {ago(gen, now)} ago)"
    lines = [head, ""]

    for t in tasks:
        st = t.get("state", "?")
        deps = t.get("deps") or []
        depchip = ""
        if deps:
            depchip = "  ← " + " ".join(
                f"{STATE_GLYPH.get((by_id.get(d) or {}).get('state'), '?')}{d}" for d in deps)
        wait = f"  waits: {t['unblock']}" if st == "blocked" and t.get("unblock") else ""
        lines.append(f"{STATE_GLYPH.get(st, '?')} {t.get('id', '?'):<24} {t.get('title', ''):<34} {st}{depchip}{wait}")

        for a in sorted((t.get("attempts") or []), key=lambda x: x.get("n", 0) if isinstance(x, dict) else 0):
            if not isinstance(a, dict):
                continue
            ast = a.get("state", "")
            cg = CAUSE_GLYPH.get((a.get("cause") or {}).get("type"), "")
            ev = ""
            if ast == "done":
                tier = (a.get("outcome") or {}).get("evidence")
                ev = "  " + EV_GLYPH.get(tier, "!") + " " + (tier or "asserted")
            live = a.get("liveness") or {}
            ts = parse_ts(live.get("last_output_at")) or parse_ts(a.get("ended_at")) or parse_ts(a.get("started_at"))
            stale = ""
            if ts:
                stale = f"  {ago(ts, now)} silent" if ast == "working" else f"  {ago(ts, now)} ago"
            qi = live.get("queued_input")
            qtag = f"  [{qi} unsent]" if isinstance(qi, int) and qi > 0 else ""
            c = a.get("cause") or {}
            ctxt = ""
            if c.get("type") not in (None, "initial"):
                ctxt = f"  ← {c.get('type')} {c.get('ref', '')}".rstrip()
                if c.get("reason"):
                    ctxt += f' "{c["reason"]}"'
            short = str(a.get("id", "?")).split("·")[-1]
            lines.append(f"  {cg}{ATT_GLYPH.get(ast, '?')} {short:<3} {ast:<8}{ev}  {a.get('actor', '')}{stale}{qtag}{ctxt}")
    return "\n".join(lines)


# ---- TUI: pure rendering helpers (testable without a terminal) ----

def _tasks(doc):
    return [t for t in (doc.get("tasks") or []) if isinstance(t, dict)]


def _by_id(doc):
    return {t.get("id"): t for t in _tasks(doc)}


def _attempt_ts(a):
    live = a.get("liveness") or {}
    return (parse_ts(live.get("last_output_at")) or parse_ts(a.get("ended_at"))
            or parse_ts(a.get("started_at")))


def task_summary(task, by_id, now):
    """One-line summary of a task for the trace list. Pure."""
    parts = [f"{STATE_GLYPH.get(task.get('state'), '?')} {task.get('id', '?')}",
             task.get("title", "")]
    deps = task.get("deps") or []
    if deps:
        parts.append("← " + " ".join(
            f"{STATE_GLYPH.get((by_id.get(d) or {}).get('state'), '?')}{d}" for d in deps))
    if task.get("state") == "blocked" and task.get("unblock"):
        parts.append(f"waits: {task['unblock']}")
    latest = _latest(task.get("attempts") or [])
    if latest:
        ts = _attempt_ts(latest)
        if ts:
            parts.append(f"{ago(ts, now)} silent" if latest.get("state") == "working" else ago(ts, now))
        qi = (latest.get("liveness") or {}).get("queued_input")
        if isinstance(qi, int) and qi > 0:
            parts.append(f"[{qi} unsent]")
    return "  ".join(p for p in parts if p)


def detail_lines(task, by_id, now):
    """Multi-line detail of the selected task for the footer. Pure."""
    out = [f"{task.get('id', '?')}  ·  {task.get('kind', '?')}  ·  "
           f"owner {task.get('owner', '?')}  ·  {task.get('state', '?')}"]
    if task.get("note"):
        out.append(f"note: {task['note']}")
    deps = task.get("deps") or []
    if deps:
        out.append("deps: " + "   ".join(
            f"{d} [{(by_id.get(d) or {}).get('state', '?')}]" for d in deps))
    if task.get("state") == "blocked" and task.get("unblock"):
        out.append(f"blocked on: {task['unblock']}")
    for a in sorted((task.get("attempts") or []),
                    key=lambda x: x.get("n", 0) if isinstance(x, dict) else 0):
        if not isinstance(a, dict):
            continue
        ast = a.get("state", "")
        line = f"  {ATT_GLYPH.get(ast, '?')} a{a.get('n', '?')} {ast}"
        oc = a.get("outcome") or {}
        if ast == "done":
            tier = oc.get("evidence")
            line += f"  {EV_GLYPH.get(tier, '!')} {tier or 'asserted'}"
            rc = oc.get("receipt") or oc.get("reason")
            if rc:
                line += f"  ({rc})"
        if a.get("actor"):
            line += f"  {a['actor']}"
        c = a.get("cause") or {}
        if c.get("type") not in (None, "initial"):
            line += f"  ← {c.get('type')} {c.get('ref', '')}".rstrip()
            if c.get("reason"):
                line += f' "{c["reason"]}"'
        ts = _attempt_ts(a)
        if ts:
            line += f"  · {ago(ts, now)}" + (" silent" if ast == "working" else "")
        qi = (a.get("liveness") or {}).get("queued_input")
        if isinstance(qi, int) and qi > 0:
            line += f"  [{qi} unsent]"
        out.append(line)
    return out


def run_stats(doc, now):
    """One-line run summary for the header. Pure."""
    tasks = _tasks(doc)

    def c(*sts):
        return sum(1 for t in tasks if t.get("state") in sts)

    run = doc.get("run") or {}
    bits = []  # no run id: the fleet is global, not a per-run/per-epic identity
    st = parse_ts(run.get("started_at"))
    if st:
        bits.append(f"+{ago(st, now)}")
    bits += [f"wip {c('working')}", f"awaiting {c('awaiting')}",
             f"blk {c('blocked')}", f"done {c('done', 'failed', 'abandoned')}/{len(tasks)}"]
    return "  ·  ".join(bits)


def attention(doc, now):
    """Tasks that need eyes, most urgent first. Pure. Returns [(task, glyph, note)]."""
    out = []
    for t in _tasks(doc):
        st = t.get("state")
        latest = _latest(t.get("attempts") or [])
        if st == "blocked":
            out.append((0, t, "⊘", f"blocked: {t.get('unblock', '?')}"))
        elif latest and latest.get("state") == "lost":
            out.append((0, t, "⌁", "pane lost, needs recovery"))
        elif st == "awaiting":
            out.append((1, t, "⋯", "awaiting you"))
        elif latest and latest.get("state") == "working":
            live = latest.get("liveness") or {}
            qi = live.get("queued_input")
            ts = _attempt_ts(latest)
            if isinstance(qi, int) and qi > 0:
                out.append((1, t, "◐", f"{qi} unsent line(s)"))
            elif ts and (now - ts).total_seconds() > 1800:
                out.append((2, t, "◐", f"silent {ago(ts, now)}"))
    out.sort(key=lambda r: r[0])
    return [(t, g, note) for _, t, g, note in out]


def age_bucket(secs):
    """Semantic age colour, normal -> red as it grows. Pure.
    '' normal (<1h) · yellow (<6h) · red (<24h) · red-bold (>=24h)."""
    if secs is None:
        return ""
    if secs < 3600:
        return ""
    if secs < 6 * 3600:
        return "yellow"
    if secs < 24 * 3600:
        return "red"
    return "red-bold"


def trace_columns(task, now):
    """The evenly-spaced trace columns for one task. Pure. Returns a dict:
    glyph, word (state), id, title, age, age_secs, model."""
    latest = _latest(task.get("attempts") or [])
    ts = _attempt_ts(latest) if latest else None
    secs = (now - ts).total_seconds() if ts else None
    pr = task.get("pr")
    return {
        "glyph": STATE_GLYPH.get(task.get("state"), "?"),
        "word": task.get("state", "?"),
        "id": task.get("id", "?"),
        "pr": f"#{pr}" if pr not in (None, "", 0) else "",
        "title": task.get("title", ""),
        "age": ago(ts, now) if ts else "",
        "age_secs": secs,
        "model": ((latest or {}).get("model") or "") if latest else "",
    }


def focus_card_lines(task, by_id, now):
    """Horizontal, labelled body for the focus card. No ticket id: the card
    title already carries it. Pure."""
    out = []
    meta = [f"kind {task.get('kind', '?')}", f"owner {task.get('owner', '?')}"]
    pr = task.get("pr")
    if pr not in (None, "", 0):
        st = task.get("_pr_status")
        meta.append(f"pr #{pr}" + (f" {st}" if st else ""))
    out.append("   ".join(meta))
    if task.get("note"):
        out.append(f"note  {task['note']}")
    deps = task.get("deps") or []
    if deps:
        out.append("deps  " + "  ".join(
            f"{d}[{(by_id.get(d) or {}).get('state', '?')}]" for d in deps))
    if task.get("state") == "blocked" and task.get("unblock"):
        out.append(f"blocked on  {task['unblock']}")
    for a in sorted((task.get("attempts") or []),
                    key=lambda x: x.get("n", 0) if isinstance(x, dict) else 0):
        if not isinstance(a, dict):
            continue
        ast = a.get("state", "")
        seg = [f"a{a.get('n', '?')}", f"{ATT_GLYPH.get(ast, '?')} {ast}"]
        oc = a.get("outcome") or {}
        if ast == "done":
            seg.append(f"{EV_GLYPH.get(oc.get('evidence'), '!')} {oc.get('evidence', 'asserted')}")
        if a.get("actor"):
            seg.append(a["actor"])
        c = a.get("cause") or {}
        if c.get("type") not in (None, "initial"):
            s = f"← {c.get('type')}"
            if c.get("reason"):
                s += f' "{c["reason"]}"'
            seg.append(s)
        ts = _attempt_ts(a)
        if ts:
            seg.append(ago(ts, now) + (" silent" if ast == "working" else ""))
        out.append("  ".join(seg))
    return out


# ---- live PR status (polled off-thread; classification is pure) ----

PR_ICON = {"draft": "✎", "open": "○", "approved": "✔", "merged": "⬤", "closed": "✗"}
PR_PCOLOR = {"draft": "def", "open": "yellow", "approved": "green", "merged": "magenta", "closed": "red"}


def pr_state(d):
    """Normalise a `gh pr view --json` payload to one status word. Pure."""
    if not isinstance(d, dict):
        return None
    if d.get("mergedAt"):
        return "merged"
    if d.get("state") == "CLOSED":
        return "closed"
    if d.get("isDraft"):
        return "draft"
    if d.get("reviewDecision") == "APPROVED":
        return "approved"
    return "open"


def _gh_pr_status(repo, num):
    p = subprocess.run(
        ["gh", "pr", "view", str(num), "-R", f"{GH_ORG}/{repo}",
         "--json", "state,isDraft,reviewDecision,mergedAt"],
        capture_output=True, text=True)
    if p.returncode != 0:
        return None  # gh missing / auth / 503 / unknown -> no icon, keep last known
    try:
        return pr_state(json.loads(p.stdout))
    except json.JSONDecodeError:
        return None


class PRPoller(threading.Thread):
    """Daemon thread: every `interval`s, read the run file's PRs and refresh a
    status cache via gh. Never touches curses; the render just reads the cache."""

    def __init__(self, path, interval=60):
        super().__init__(daemon=True)
        self.path, self.interval = path, interval
        self._lock = threading.Lock()
        self._status = {}  # {(repo, num): status word}

    def get(self, repo, num):
        with self._lock:
            return self._status.get((repo, num))

    def run(self):
        while True:
            try:
                doc = json.load(open(self.path))
                for t in doc.get("tasks", []):
                    pr = t.get("pr")
                    if pr in (None, "", 0):
                        continue
                    repo = t.get("repo") or DEFAULT_REPO
                    st = _gh_pr_status(repo, pr)
                    if st is not None:
                        with self._lock:
                            self._status[(repo, pr)] = st
            except (OSError, json.JSONDecodeError):
                pass
            time.sleep(self.interval)


HELP = [
    ("STATES", None),
    ("● done", "latest attempt finished"),
    ("◐ working", "an attempt is running now"),
    ("⋯ awaiting", "alive, waiting on you (open PR)"),
    ("○ queued", "dispatched, not started"),
    ("⊘ blocked", "stuck, needs an unblocker"),
    ("✗ failed", "latest attempt failed"),
    ("⊗ abandoned", "given up"),
    ("⌁ lost", "the runtime died mid-attempt"),
    ("", ""),
    ("EVIDENCE (on done)", None),
    ("◆ verified", "a named mechanical check passed"),
    ("◇ reported", "a tool said success, not confirmed"),
    ("≈ heuristic", "inferred from a runtime signal"),
    ("! asserted", "a bare claim, no structure"),
    ("", ""),
    ("PR ICON", None),
    ("✎ draft  ○ open", "✔ approved   ⬤ merged   ✗ closed"),
    ("age colour", "normal <1h · yellow <6h · red <1d · bold older"),
    ("", ""),
    ("KEYS", None),
    ("j/k", "move     tab  next needing attention"),
    ("o", "open JIRA ticket     p  open PR"),
    ("r", "reload     ?  toggle help     q  quit"),
]


def read_backlog():
    """The queued tickets `crew backlog` maintains. [] if none. Never raises."""
    out = []
    try:
        with open(BACKLOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return out


def _load_doc(path):
    try:
        with open(path) as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, f"not found: {path}"
    except json.JSONDecodeError as e:
        return None, f"parse error: {e}"


def _tui(path, interval):
    import curses
    import locale
    locale.setlocale(locale.LC_ALL, "")
    curses.wrapper(_tui_loop, path, max(1, interval))
    return 0


def _tui_loop(stdscr, path, interval):
    import curses
    curses.curs_set(0)
    stdscr.timeout(interval * 1000)  # getch returns -1 after the interval -> reload
    # Two pairs per colour: fg on the default bg, and fg on a grey bg for the
    # selected row. The highlight is a grey bar, and item text keeps its own
    # foreground; nothing gives an item its own coloured background.
    norm, selp = {}, {}  # selp not `sel`: `sel` is the selection index below
    topbar = curses.A_REVERSE  # header bar; overridden to herdr-blue when colour is available
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        grey = 238 if curses.COLORS >= 256 else -1  # 256-colour grey; else no bar, marker only
        cmap = {"green": curses.COLOR_GREEN, "yellow": curses.COLOR_YELLOW,
                "cyan": curses.COLOR_CYAN, "red": curses.COLOR_RED,
                "magenta": curses.COLOR_MAGENTA, "blue": curses.COLOR_BLUE, "def": -1}
        pid = 1
        for nm, c in cmap.items():
            curses.init_pair(pid, c, -1)
            norm[nm] = curses.color_pair(pid)
            pid += 1
            curses.init_pair(pid, c, grey)
            selp[nm] = curses.color_pair(pid)
            pid += 1
        # herdr-style blue header bar. 111 ~= herdr's #89b4fa on a 256-colour
        # terminal; ANSI blue otherwise. Change HERDR_BLUE to retune.
        HERDR_BLUE = 111 if curses.COLORS >= 256 else curses.COLOR_BLUE
        curses.init_pair(pid, curses.COLOR_BLACK, HERDR_BLUE)
        topbar = curses.color_pair(pid)
        pid += 1

    state_name = {"done": "green", "working": "yellow", "awaiting": "cyan",
                  "blocked": "red", "failed": "red", "abandoned": "magenta", "queued": "blue"}

    def pair(name, selected):
        table = selp if selected else norm
        return table.get(name, table.get("def", 0))

    def age_name(secs):
        return {"yellow": "yellow", "red": "red", "red-bold": "red"}.get(age_bucket(secs), "def")

    def put(y, x, s, attr, maxx):
        if y < 0 or x >= maxx:
            return
        try:
            stdscr.addstr(y, x, s[:maxx - x], attr)
        except curses.error:
            pass

    last, err = _load_doc(path)
    sel = top = 0
    help_open = False
    flash = ""
    poller = PRPoller(path)
    poller.start()
    while True:
        stdscr.erase()
        H, W = stdscr.getmaxyx()
        doc = last or {"tasks": []}
        now = datetime.now(timezone.utc)
        # Attach live PR status per task. The status column keeps the producer's
        # state; the PR icon and the focus card carry the PR status. Display-only;
        # the run file on disk is untouched.
        tasks = []
        for t in _tasks(doc):
            prn = t.get("pr")
            st = poller.get(t.get("repo") or DEFAULT_REPO, prn) if prn not in (None, "", 0) else None
            tasks.append({**t, "_pr_status": st})
        edoc = {**doc, "tasks": tasks}
        by_id = {t["id"]: t for t in tasks if t.get("id")}
        n = len(tasks)
        sel = max(0, min(sel, n - 1)) if n else 0
        run = doc.get("run") or {}

        # top bar: title left, run stats right (herdr-blue)
        put(0, 0, " " * W, topbar, W)
        put(0, 0, f" {run.get('title') or 'crew-dagr'}", topbar | curses.A_BOLD, W)
        stats = run_stats(edoc, now) + (f"  [{err}]" if err else "") + " "
        if len(stats) + 2 < W:
            put(0, W - len(stats), stats, topbar, W)

        # sidecar (trace left, attention + focus card right) when wide; tig below that
        wide = W >= 118 and n > 0
        left_w = int(W * 0.60) if wide else W
        if wide:
            list_h = H - 2
        else:
            detail = detail_lines(tasks[sel], by_id, now) if n else ["(no tasks in run file)"]
            detail_h = min(len(detail) + 1, max(3, H // 2))
            list_h = max(1, H - 1 - detail_h - 1)

        if sel < top:
            top = sel
        elif sel >= top + list_h:
            top = sel - list_h + 1
        top = max(0, min(top, max(0, n - list_h)))

        # left column: evenly-spaced trace columns
        # status(icon+word) · ticket · PR(+status icon) · title · age · model
        SW, IW, PW, AW, MW = 11, 14, 8, 6, 12
        PR_NUMW = 6  # fixed width for "#NNNN" so the status icon column lines up
        x_status = 2
        x_id = x_status + SW + 1
        x_pr = x_id + IW + 1
        x_title = x_pr + PW + 1
        title_w = max(6, left_w - x_title - (AW + 1) - (MW + 1) - 1)
        x_age = x_title + title_w + 1
        x_model = x_age + AW + 1
        for row in range(list_h):
            idx = top + row
            if idx >= n:
                break
            t = tasks[idx]
            y = 1 + row
            selected = idx == sel
            if selected:
                put(y, 0, " " * left_w, pair("def", True), left_w)  # grey highlight bar
            c = trace_columns(t, now)
            put(y, 0, "▍" if selected else " ", pair("def", selected) | curses.A_BOLD, left_w)
            put(y, x_status, f"{c['glyph']} {c['word']}"[:SW],
                pair(state_name.get(t.get("state"), "def"), selected) | curses.A_BOLD, left_w)
            put(y, x_id, c["id"][:IW], pair("def", selected), left_w)
            put(y, x_pr, c["pr"][:PR_NUMW], pair("cyan", selected), left_w)
            if c["pr"]:
                st = t.get("_pr_status")
                if st:
                    put(y, x_pr + PR_NUMW, PR_ICON.get(st, "?"),
                        pair(PR_PCOLOR.get(st, "def"), selected), left_w)
            put(y, x_title, c["title"][:title_w], pair("def", selected), left_w)
            age_extra = curses.A_BOLD if age_bucket(c["age_secs"]) == "red-bold" else 0
            put(y, x_age, c["age"][:AW], pair(age_name(c["age_secs"]), selected) | age_extra, left_w)
            model_attr = pair("def", selected) | (0 if selected else curses.A_DIM)
            put(y, x_model, c["model"][:MW], model_attr, left_w)

        if wide:
            for y in range(1, H - 1):
                put(y, left_w, "│", curses.A_DIM, W)
            rx, rw = left_w + 2, W - (left_w + 2)
            att = attention(edoc, now)
            put(1, rx, "ATTENTION", curses.A_BOLD, W)
            tag = f"{len(att)} need eyes"
            if rw - len(tag) > 11:
                put(1, rx + rw - len(tag), tag, curses.A_DIM, W)
            y = 2
            if not att:
                put(y, rx, "(all quiet)", curses.A_DIM, W)
                y += 1
            for t, glyph, note in att[:max(2, (H - 3) // 3)]:
                put(y, rx, f"{glyph} {t.get('id', '?')}  {note}",
                    pair(state_name.get(t.get("state"), "def"), False), W)
                y += 1
            # backlog panel: queued tickets not yet dispatched
            backlog = read_backlog()
            y += 1
            put(y, rx, "BACKLOG", curses.A_BOLD, W)
            btag = f"{len(backlog)} queued"
            if rw - len(btag) > 9:
                put(y, rx + rw - len(btag), btag, curses.A_DIM, W)
            y += 1
            if not backlog:
                put(y, rx, "(empty)", curses.A_DIM, W)
                y += 1
            else:
                show = max(1, (H - 4) // 4)
                for it in backlog[:show]:
                    put(y, rx, f"○ {it.get('key', '?')}", pair("blue", False), W)
                    y += 1
                if len(backlog) > show:
                    put(y, rx, "  +%d more" % (len(backlog) - show), curses.A_DIM, W)
                    y += 1
            # focus card for the selected task
            cy = y + 1
            card = tasks[sel]
            put(cy, rx, ("┌─ " + f"{card.get('id', '?')} · {str(card.get('state', '?')).upper()} ")
                .ljust(rw - 1, "─") + "┐", curses.A_BOLD, W)
            body = focus_card_lines(card, by_id, now)
            room = max(0, (H - 2) - (cy + 1) + 1)  # rows for body + bottom border
            shown = body[:max(0, room - 1)]
            for i, line in enumerate(shown):
                put(cy + 1 + i, rx, ("│ " + line).ljust(rw - 1) + "│", 0, W)
            put(cy + 1 + len(shown), rx, "└".ljust(rw - 1, "─") + "┘", curses.A_BOLD, W)
        else:
            sep = 1 + list_h
            put(sep, 0, "─" * W, curses.A_DIM, W)
            for j, line in enumerate(detail[:detail_h - 1]):
                put(sep + 1 + j, 0, line, 0, W)

        foot = (f" {flash} " if flash
                else " ? help · j/k move · tab attn · o ticket · p PR · r reload · q quit ")
        put(H - 1, 0, foot.ljust(W) if wide else foot.rjust(W),
            curses.A_BOLD if flash else curses.A_DIM, W)

        # help overlay, drawn last so it sits on top of everything
        if help_open:
            bw = min(64, W - 2)
            by = max(0, (H - (len(HELP) + 2)) // 2)
            bx = max(0, (W - bw) // 2)
            put(by, bx, "┌" + "─ help ".ljust(bw - 2, "─") + "┐", topbar | curses.A_BOLD, W)
            for i, (label, desc) in enumerate(HELP):
                ry = by + 1 + i
                if ry >= H - 1:
                    break
                txt = label if desc is None else (f"{label:<17} {desc}" if label else "")
                put(ry, bx, ("│ " + txt).ljust(bw - 1) + "│", curses.A_BOLD if desc is None else 0, W)
            put(by + 1 + len(HELP), bx, "└" + "─" * (bw - 2) + "┘", curses.A_BOLD, W)

        ch = stdscr.getch()
        flash = ""  # cleared each key/tick unless an action re-sets it
        if ch == ord("?"):
            help_open = not help_open
        elif ch == 27:  # Esc: close help if open, else quit
            if help_open:
                help_open = False
            else:
                return
        elif ch == ord("q"):
            return
        elif help_open:
            pass  # swallow navigation while the help panel is up
        elif ch in (ord("j"), curses.KEY_DOWN):
            sel += 1
        elif ch in (ord("k"), curses.KEY_UP):
            sel -= 1
        elif ch == ord("g"):
            sel = 0
        elif ch == ord("G"):
            sel = n - 1 if n else 0
        elif ch == 9:  # tab: jump to the next task needing attention
            ids = {t.get("id") for t, _, _ in attention(edoc, now)}
            order = [i for i, t in enumerate(tasks) if t.get("id") in ids]
            after = [i for i in order if i > sel]
            if order:
                sel = after[0] if after else order[0]
        elif ch == ord("o"):  # open the JIRA ticket for the selected row
            u = jira_url(tasks[sel].get("id")) if n else None
            flash = "opening ticket..." if _open_url(u) else "no JIRA ticket for this row"
        elif ch == ord("p"):  # open the PR for the selected row
            u = pr_url(tasks[sel]) if n else None
            flash = "opening PR..." if _open_url(u) else "no PR for this row"
        elif ch == ord("r") or ch == -1:
            d, e = _load_doc(path)
            if d is not None:
                last, err = d, None
            else:
                err = e  # keep the last good doc on screen, flag the error


def _watch(path, interval):
    """Re-render on an interval until ctrl-c. Re-reads the file each tick so it
    picks up the producer's writes; a mid-rename read just shows an error and
    recovers on the next tick."""
    try:
        while True:
            try:
                with open(path) as f:
                    body = view(json.load(f))
            except FileNotFoundError:
                body = f"cannot read {path}: not found"
            except json.JSONDecodeError as e:
                body = f"cannot parse {path}: {e}"
            sys.stdout.write("\x1b[2J\x1b[H")  # clear + cursor home
            sys.stdout.write(body + f"\n\n(every {interval}s · ctrl-c to stop)\n")
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return 0


def _emit(fs, as_json, strict=False):
    errs = [f for f in fs if f.level == "E"]
    warns = [f for f in fs if f.level == "W"]
    if as_json:
        print(json.dumps([f.as_dict() for f in fs], indent=2))
    elif not fs:
        print("clean: []")
    else:
        for f in fs:
            print(f.line())
        print(f"\n{len(errs)} error(s), {len(warns)} warning(s)")
    if errs:
        return 1
    if warns and strict:
        return 1
    return 0


def _selftest():
    good = {"version": 1, "run": {"id": "r"}, "tasks": [
        {"id": "t1", "title": "x", "kind": "impl", "state": "done", "pr": 42, "attempts": [
            {"id": "t1·a1", "n": 1, "cause": {"type": "initial"}, "state": "done", "model": "opus5·max",
             "outcome": {"result": "done", "evidence": "verified", "receipt": "tests 5/5"}}]}]}
    assert check(good) == [], [f.line() for f in check(good)]

    cases = [
        ({"version": 2, "run": {"id": "r"}, "tasks": []}, "E100"),
        ({"version": 1, "run": {}, "tasks": []}, "E101"),
        ({"version": 1, "run": {"id": "r"}, "tasks": [
            {"id": "a", "title": "t", "kind": "impl", "state": "queued", "deps": ["nope"], "attempts": []}]}, "E120"),
        ({"version": 1, "run": {"id": "r"}, "tasks": [
            {"id": "a", "title": "t", "kind": "impl", "state": "queued", "deps": ["b"], "attempts": []},
            {"id": "b", "title": "t", "kind": "impl", "state": "queued", "deps": ["a"], "attempts": []}]}, "E122"),
        ({"version": 1, "run": {"id": "r"}, "tasks": [
            {"id": "a", "title": "t", "kind": "impl", "state": "done", "attempts": [
                {"id": "a·a1", "n": 1, "cause": {"type": "initial"}, "state": "done",
                 "outcome": {"result": "done", "evidence": "bogus"}}]}]}, "E142"),
        # task 'done' but the only attempt is working -> projection
        ({"version": 1, "run": {"id": "r"}, "tasks": [
            {"id": "a", "title": "t", "kind": "impl", "state": "done", "attempts": [
                {"id": "a·a1", "n": 1, "cause": {"type": "initial"}, "state": "working",
                 "locator": {"pane": "w1:p1"}, "liveness": {"prompt_acknowledged": True}}]}]}, "E150"),
        # a2 references a3 -> forward cause
        ({"version": 1, "run": {"id": "r"}, "tasks": [
            {"id": "a", "title": "t", "kind": "impl", "state": "working", "attempts": [
                {"id": "a·a1", "n": 1, "cause": {"type": "initial"}, "state": "lost"},
                {"id": "a·a2", "n": 2, "cause": {"type": "followup", "ref": "a·a3"}, "state": "working",
                 "locator": {"pane": "w1:p1"}, "liveness": {"x": 1}},
                {"id": "a·a3", "n": 3, "cause": {"type": "followup", "ref": "a·a1"}, "state": "working",
                 "locator": {"pane": "w1:p1"}, "liveness": {"x": 1}}]}]}, "E136"),
        # blocked without an unblocker -> warning
        ({"version": 1, "run": {"id": "r"}, "tasks": [
            {"id": "a", "title": "t", "kind": "impl", "state": "blocked", "attempts": []}]}, "W205"),
    ]
    for doc, want in cases:
        got = [f.code for f in check(doc)]
        assert want in got, (want, got)

    # TUI pure helpers render the right facts
    t = good["tasks"][0]
    bi = {t["id"]: t}
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert "t1" in task_summary(t, bi, now), task_summary(t, bi, now)
    dl = detail_lines(t, bi, now)
    assert any("verified" in ln for ln in dl), dl
    assert any("a1" in ln for ln in dl), dl
    assert "done 1/1" in run_stats(good, now), run_stats(good, now)
    assert attention(good, now) == [], attention(good, now)
    blk = {"version": 1, "run": {"id": "r"}, "tasks": [
        {"id": "b", "title": "x", "kind": "impl", "state": "blocked", "unblock": "PR 9", "attempts": []}]}
    aq = attention(blk, now)
    assert len(aq) == 1 and aq[0][0]["id"] == "b", aq
    tc = trace_columns(t, now)
    assert tc["id"] == "t1" and tc["word"] == "done" and tc["model"] == "opus5·max", tc
    assert tc["pr"] == "#42", tc
    assert trace_columns({"id": "x", "state": "queued", "attempts": []}, now)["pr"] == "", "no-pr should be blank"
    assert age_bucket(0) == "" and age_bucket(3 * 3600) == "yellow"
    assert age_bucket(10 * 3600) == "red" and age_bucket(48 * 3600) == "red-bold"

    fc = focus_card_lines(t, bi, now)
    assert fc[0].startswith("kind") and "t1" not in fc[0], fc  # no ticket id in the body
    assert pr_state({"mergedAt": "x"}) == "merged"
    assert pr_state({"state": "CLOSED"}) == "closed"
    assert pr_state({"isDraft": True}) == "draft"
    assert pr_state({"reviewDecision": "APPROVED"}) == "approved"
    assert pr_state({"state": "OPEN"}) == "open"
    assert jira_url("fandevx-3631") == JIRA_BASE + "FANDEVX-3631"
    assert jira_url("gate-inf-dev") is None
    assert pr_url({"pr": 3126}).endswith("/fanapp-terraform/pull/3126")
    assert pr_url({"pr": 742, "repo": "fes-identity"}).endswith("/fes-identity/pull/742")
    assert pr_url({}) is None

    print("crew-dagr selftest: OK")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="crew-dagr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("run", nargs="?", help=f"run file (default {DEFAULT_RUN})")
    c.add_argument("--strict", action="store_true")
    c.add_argument("--json", action="store_true")
    v = sub.add_parser("view")
    v.add_argument("run", nargs="?", help=f"run file (default {DEFAULT_RUN})")
    v.add_argument("--watch", action="store_true", help="re-render on an interval until ctrl-c")
    v.add_argument("--interval", type=int, default=10, metavar="SECS")
    t = sub.add_parser("tui")
    t.add_argument("run", nargs="?", help=f"run file (default {DEFAULT_RUN})")
    t.add_argument("--interval", type=int, default=10, metavar="SECS")
    sub.add_parser("test")
    a = ap.parse_args()

    if a.cmd == "test":
        return _selftest()
    path = a.run or DEFAULT_RUN
    if a.cmd == "tui":
        return _tui(path, a.interval)
    if a.cmd == "view" and a.watch:
        return _watch(path, a.interval)

    try:
        with open(path) as f:
            doc = json.load(f)
    except FileNotFoundError:
        print(f"cannot read {path}: not found", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        if a.cmd == "check":
            return _emit([Finding("E001", "E", "$", f"invalid JSON: {e}")], a.json)
        print(f"cannot parse {path}: {e}", file=sys.stderr)
        return 2

    if a.cmd == "check":
        return _emit(check(doc), a.json, a.strict)
    print(view(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
