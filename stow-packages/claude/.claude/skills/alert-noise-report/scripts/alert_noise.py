#!/usr/bin/env python3
"""Alert-noise extraction and ranking for #fes-platform-alerts.

Each weekly run produces one dated report NOTE whose frontmatter carries the
summary metrics; an Obsidian Base (alert-noise.base) renders those notes as a
table so trends read down the columns over time. This script only parses Slack
and computes one week's numbers - no persistent store, the notes are the store.

Subcommands:
  extract <page-file>...   Slack 'detailed' page dumps -> event TSV (stdout)
  report  <tsv>            frontmatter metrics block + noise-ranking table (stdout)
"""
import re
import sys
from collections import defaultdict

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
    for tok, env in (("[inf-dev]", "inf-dev"), ("[dev]", "dev"), ("[test]", "test"),
                     ("[prod]", "prod"), ("DEV", "dev"), ("TEST", "test"), ("PROD", "prod")):
        if tok in block:
            return env
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
        text = open(path).read().replace("\\/", "/")
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
                open_trigger = ts
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


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("extract", "report"):
        sys.exit(__doc__)
    if sys.argv[1] == "extract":
        extract(sys.argv[2:])
    else:
        report(sys.argv[2])


if __name__ == "__main__":
    main()
