#!/usr/bin/env python3
"""Run: python3 test_build_cache.py"""
import json, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent
BUILDER = HERE / "build_cache.py"


def test_assemble_and_validate():
    with tempfile.TemporaryDirectory() as d:
        tdir = Path(d) / "threads"
        tdir.mkdir()
        (tdir / "1781011575.151529.json").write_text(json.dumps({
            "parent": {"author": "Joey", "ts": "1781011575.151529",
                       "text": "migrations failing"},
            "replies": []}))
        (tdir / "1780949652.095489.json").write_text(json.dumps({
            "parent": {"author": "John", "ts": "1780949652.095489",
                       "text": "kafka creds"},
            "replies": [{"author": "Tony", "ts": "1780951901.495749",
                         "text": "which env"}]}))
        out_path = Path(d) / "cache.json"
        r = subprocess.run(
            [sys.executable, str(BUILDER), str(tdir), "-o", str(out_path),
             "--channel", "C06PUG6V6NT", "--window-oldest", "1780902000"],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        cache = json.loads(out_path.read_text())
        assert cache["channel_id"] == "C06PUG6V6NT"
        assert len(cache["threads"]) == 2
        assert cache["threads"]["1781011575.151529"]["parent"]["author"] == "Joey"


def test_rejects_corrupt_thread_file():
    with tempfile.TemporaryDirectory() as d:
        tdir = Path(d) / "threads"
        tdir.mkdir()
        (tdir / "123.456.json").write_text("{ not json")
        out_path = Path(d) / "cache.json"
        r = subprocess.run(
            [sys.executable, str(BUILDER), str(tdir), "-o", str(out_path),
             "--channel", "C06PUG6V6NT", "--window-oldest", "1"],
            capture_output=True, text=True)
        assert r.returncode != 0
        assert not out_path.exists(), "must not emit a cache on failure"


def test_rejects_missing_fields():
    with tempfile.TemporaryDirectory() as d:
        tdir = Path(d) / "threads"
        tdir.mkdir()
        (tdir / "123.456.json").write_text("{}")
        out_path = Path(d) / "cache.json"
        r = subprocess.run(
            [sys.executable, str(BUILDER), str(tdir), "-o", str(out_path),
             "--channel", "C06PUG6V6NT", "--window-oldest", "1"],
            capture_output=True, text=True)
        assert r.returncode != 0
        assert not out_path.exists(), "must not emit a cache on failure"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)} tests passed")
