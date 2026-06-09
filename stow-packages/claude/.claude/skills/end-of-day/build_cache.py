#!/usr/bin/env python3
"""Assemble the EOD #fes-platform-support thread cache from per-thread JSON
files written by the LLM (one file per slack_read_thread call, named
<ts>.json) into the single validated cache consumers trust.

Producer-side counterpart of the vendored fes_support_cache.py consumers.

Usage:
  python3 build_cache.py <threads-dir> -o /tmp/eod-fes-support-cache.json \
      --channel C06PUG6V6NT --window-oldest <unix-ts>

Exits non-zero (and writes nothing) if any thread file is malformed.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("threads_dir", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--channel", required=True)
    p.add_argument("--window-oldest", required=True)
    args = p.parse_args()

    threads = {}
    for f in sorted(args.threads_dir.glob("*.json")):
        ts = f.stem
        try:
            t = json.loads(f.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"FATAL: {f.name} is not valid JSON: {e}", file=sys.stderr)
            return 1
        parent = t.get("parent")
        if (not isinstance(parent, dict) or "ts" not in parent
                or "text" not in parent or not isinstance(t.get("replies"), list)):
            print(f"FATAL: {f.name} lacks parent.ts/parent.text/replies[]",
                  file=sys.stderr)
            return 1
        threads[ts] = t

    cache = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel_id": args.channel,
        "window_oldest": args.window_oldest,
        "threads": threads,
    }
    tmp = args.output.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=1))
    json.loads(tmp.read_text())          # final parse self-check
    tmp.replace(args.output)
    print(f"cache written: {args.output} ({len(threads)} threads)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
