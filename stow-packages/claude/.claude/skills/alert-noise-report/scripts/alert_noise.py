#!/usr/bin/env python3
"""Alert-noise extraction and ranking for #fes-platform-alerts.

Each weekly run produces one dated report NOTE whose frontmatter carries the
summary metrics; an Obsidian Base (alert-noise.base) renders those notes as a
table so trends read down the columns over time. This script only parses Slack
and computes one week's numbers - no persistent store, the notes are the store.

Subcommands:
  extract <page-file>...      Slack 'detailed' page dumps -> event TSV (stdout)
  report  <tsv>               frontmatter metrics block + noise-ranking table (stdout)
  pending <out-dir> [now]     completed Thu-weeks with no note yet, one per line:
                              wend  wstart  margin_oldest  window_start  window_end
                              (epochs; margin = window_start - 24h fetch overlap)
"""
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

FLAP_WINDOW_S = 30 * 60
TRIGGER_STATES = ("Triggered", "Re-Triggered", "No-Data")
ENV_MAP = {"fanapp-dev-1": "dev", "fanapp-test-1": "test",
           "fanapp-prod-1": "prod", "fanapp-inf-dev-1": "inf-dev"}

# ---------- extract (Slack detailed-page dumps -> normalized events) ----------

MSG_SPLIT = re.compile(r"=== Message from (.+?) \((U[A-Z0-9]+)\) at [^=]+===")
TS_RE = re.compile(r"Message TS: (\d+\.\d+)")
ATT_RE = re.compile(r"Attachment: (Triggered|Re-Triggered|Recovered|Warn|No data|No-Data): (.+?) \(https?://")


def env_of(block):
    m = (re.search(r"(?:kube_)?cluster_name%3A(fanapp-[a-z-]+-1)\b", block)
         or re.search(r"(?:kube_)?cluster_name:(fanapp-[a-z-]+-1)\b", block)
         or re.search(r"clustername:fanapp-(dev|test|prod|inf-dev)-1", block))
    if m:
        return ENV_MAP.get(m.group(1), m.group(1))
    m = re.search(r"env:(inf-dev|dev|test|prod|fanapp-[a-z-]+-1)", block)
    if m:
        return ENV_MAP.get(m.group(1), m.group(1))
    for tok, env in (("[inf-dev]", "inf-dev"), ("[dev]", "dev"),
                     ("[test]", "test"), ("[prod]", "prod")):
        if tok in block:
            return env
    m = re.search(r"\b(DEV|TEST|PROD)\b", block)  # word-bounded so FANDEVX != dev
    if m:
        return {"DEV": "dev", "TEST": "test", "PROD": "prod"}[m.group(1)]
    return "unknown"


def service_of(block):
    m = re.search(r"[Ss]ervice%3A([a-z0-9-]+)", block) or re.search(r"service:([a-z0-9-]+)", block)
    if m:
        return m.group(1)
    m = re.search(r"kube_namespace%3A([a-z0-9-]+)", block) or re.search(r"kube_namespace:([a-z0-9-]+)", block)
    if m:
        return m.group(1)
    m = re.search(r"pod_name:([a-z0-9-]+)", block)
    if m:
        return re.sub(r"(-[a-f0-9]{8,10})?-[a-z0-9]{5}$", "", m.group(1))
    m = re.search(r"kube_deployment:([a-z0-9-]+)", block)
    if m:
        return m.group(1)
    m = re.search(r"\bname:([a-z0-9.-]+)", block)
    if m:
        return m.group(1)
    return "-"


def monitor_of(title):
    title = re.sub(r":[a-z0-9_+-]+:\s*", "", title)  # strip slack emoji shortcodes
    title = re.sub(r" on (kube_cluster_name|env|[a-z_]+):.*$", "", title)
    title = re.sub(r" - fanapp-[a-z-]+-1$", "", title)
    title = re.sub(r" - [a-z0-9-]+/[a-z0-9-]+$", "", title)
    title = re.sub(r"\s+-\s+/\s*$", "", title)
    title = re.sub(r"^\[(dev|test|prod|inf-dev|fanapp-[a-z-]+-1)\]\s*", "", title)
    return title.strip()


def extract(paths):
    seen, rows = set(), []
    for path in paths:
        text = (open(path).read().replace("\\/", "/").replace("\\u2014", "-")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))
        parts = MSG_SPLIT.split(text)
        for i in range(1, len(parts) - 2, 3):
            author, block = parts[i], parts[i + 2]
            if author != "Datadog":
                continue
            ts_m, att_m = TS_RE.search(block), ATT_RE.search(block)
            if not ts_m or not att_m or ts_m.group(1) in seen:
                continue
            seen.add(ts_m.group(1))
            status = att_m.group(1).replace("No data", "No-Data")
            rows.append((ts_m.group(1), status, monitor_of(att_m.group(2)), env_of(block), service_of(block)))
    rows.sort(key=lambda r: float(r[0]))
    for r in rows:
        print("\t".join(r))


# ---------- report (TSV -> summary metrics + ranking table) ----------

def load(tsv_path):
    rows = []
    with open(tsv_path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            assert len(parts) == 5, f"bad row: {line!r}"
            rows.append(parts)
    rows.sort(key=lambda r: float(r[0]))
    return rows


def aggregate(rows):
    groups = defaultdict(list)
    for ts, status, monitor, env, service in rows:
        groups[(monitor, env, service)].append((float(ts), status))
    agg = defaultdict(lambda: {"trig": 0, "rec": 0, "warn": 0, "flap": 0, "services": set()})
    for (monitor, env, service), events in groups.items():
        a = agg[(monitor, env)]
        a["services"].add(service)
        open_trigger = None
        for ts, status in events:
            if status in TRIGGER_STATES:
                a["trig"] += 1
                if open_trigger is None:  # anchor to the episode's FIRST trigger
                    open_trigger = ts     # (renotify/re-trigger must not reset it)
            elif status == "Warn":
                a["warn"] += 1
            elif status == "Recovered":
                a["rec"] += 1
                if open_trigger is not None and ts - open_trigger <= FLAP_WINDOW_S:
                    a["flap"] += 1
                open_trigger = None
    return agg


def report(tsv_path):
    rows = load(tsv_path)
    agg = aggregate(rows)
    total_trig = sum(a["trig"] for a in agg.values())
    total_flap = sum(a["flap"] for a in agg.values())
    prod_trig = sum(a["trig"] for (m, e), a in agg.items() if e == "prod")
    (nm, ne), _ = max(agg.items(), key=lambda kv: kv[1]["trig"]) if agg else (("-", "-"), None)

    print("# frontmatter metrics (copy into the report note)")
    print(f"total_events: {len(rows)}")
    print(f"total_triggers: {total_trig}")
    print(f"total_flaps: {total_flap}")
    print(f"prod_triggers: {prod_trig}")
    print(f'noisiest_monitor: "{nm}"')
    print(f"noisiest_env: {ne}")
    print()
    print("# noise ranking (copy into the report note body)")
    print("| Monitor | Env | Triggers | Recoveries | Flaps (<30m) | Services/groups |")
    print("|---|---|---|---|---|---|")
    for (monitor, env), a in sorted(agg.items(), key=lambda kv: -kv[1]["trig"]):
        svc_list = sorted(s for s in a["services"] if s != "-")
        svcs = ", ".join(svc_list[:4]) or "-"
        if len(svc_list) > 4:
            svcs += f" (+{len(svc_list) - 4} more)"
        print(f"| {monitor} | {env} | {a['trig']} | {a['rec']} | {a['flap']} | {svcs} |")


def pending(out_dir, now_epoch):
    """Completed Thu-weeks (window [Thu 00:00, next Thu 00:00)) that have no note.

    Walks back from the most recently completed week until it hits a week that
    already has a note (older weeks are assumed present) or a 14-week cap.
    """
    now = datetime.fromtimestamp(now_epoch)
    d = now.replace(hour=0, minute=0, second=0, microsecond=0)
    while d.weekday() != 3:  # 3 = Thursday; most recent completed week-end
        d -= timedelta(days=1)
    rows = []
    for i in range(14):
        wend_dt = d - timedelta(days=7 * i)
        wend = wend_dt.strftime("%Y-%m-%d")
        if os.path.exists(os.path.join(out_dir, wend + ".md")):
            break
        wstart_dt = wend_dt - timedelta(days=7)
        margin_dt = wstart_dt - timedelta(days=1)
        rows.append((wend, wstart_dt.strftime("%Y-%m-%d"),
                     int(margin_dt.timestamp()), int(wstart_dt.timestamp()), int(wend_dt.timestamp())))
    for r in reversed(rows):  # oldest first
        print("\t".join(map(str, r)))


def _selfcheck():
    """Guard the flap-pairing rule: a flap is a Recovered within 30m of the
    episode's FIRST trigger, so a renotify mid-incident must not create one."""
    rows = [
        ("100", "Triggered", "m1", "test", "s"),      # quick flap
        ("160", "Recovered", "m1", "test", "s"),      #   +60s  -> flap
        ("1000", "Triggered", "m2", "test", "s"),     # long incident
        ("8200", "Re-Triggered", "m2", "test", "s"),  #   +2h renotify
        ("8600", "Recovered", "m2", "test", "s"),     #   +7600s from first trigger -> NOT a flap
        ("20000", "Triggered", "m3", "test", "s"),    # slow recovery
        ("28000", "Recovered", "m3", "test", "s"),    #   +8000s -> NOT a flap
    ]
    agg = aggregate(rows)
    flaps = sum(a["flap"] for a in agg.values())
    assert flaps == 1, f"expected 1 flap, got {flaps}"
    assert agg[("m2", "test")]["trig"] == 2, "renotify must still count as 2 triggers"
    assert agg[("m2", "test")]["flap"] == 0, "long incident must not be a flap"
    print("selfcheck OK")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "extract":
        extract(sys.argv[2:])
    elif cmd == "report":
        report(sys.argv[2])
    elif cmd == "pending":
        now_epoch = float(sys.argv[3]) if len(sys.argv) > 3 else time.time()
        pending(sys.argv[2], now_epoch)
    elif cmd == "selfcheck":
        _selfcheck()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
