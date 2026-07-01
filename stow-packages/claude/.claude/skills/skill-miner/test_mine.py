#!/usr/bin/env python3
"""Run: python3 test_mine.py

Tests the deterministic core of skill-miner. No framework, matching the
test_build_cache.py convention. Redaction is the security-critical path and
gets the heaviest coverage.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
MINER = HERE / "mine.py"

import mine  # noqa: E402


# --- redaction (security path) ---------------------------------------------

def test_redact_aws_access_key():
    assert "AKIAIOSFODNN7EXAMPLE" not in mine.redact(
        "aws s3 ls --profile foo # AKIAIOSFODNN7EXAMPLE")


def test_redact_password_flag():
    out = mine.redact("psql --password hunter2trustno1 -h db")
    assert "hunter2trustno1" not in out
    assert "psql" in out  # only the secret is scrubbed, not the whole line


def test_redact_secret_env_assignment():
    out = mine.redact(
        "export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in out


def test_redact_bearer_token():
    out = mine.redact('curl -H "Authorization: Bearer eyJabc.def.ghijkl"')
    assert "eyJabc.def.ghijkl" not in out


def test_redact_presigned_url_signature():
    out = mine.redact(
        "curl 'https://x.s3.amazonaws.com/y?X-Amz-Signature=deadbeefcafe1234'")
    assert "deadbeefcafe1234" not in out


def test_redact_leaves_benign_command_untouched():
    cmd = 'git commit -m "fix the parser"'
    assert mine.redact(cmd) == cmd


# --- command normalization --------------------------------------------------

def test_normalize_strips_args_keeps_program_and_subcommand():
    assert mine.normalize_command('git commit -m "x"') == "git commit"
    assert mine.normalize_command("gh pr view 123 --repo a/b") == "gh pr view"


def test_normalize_strips_leading_env_assignment():
    assert mine.normalize_command("AWS_PROFILE=foo terraform plan") == "terraform plan"


def test_normalize_single_token():
    assert mine.normalize_command("ls -la /tmp") == "ls"


# --- ceremony stopword filter ----------------------------------------------

def test_is_ceremony_filters_noise():
    for c in ["ls", "cd", "pwd", "clear", "git status", "cat"]:
        assert mine.is_ceremony(c), c


def test_is_ceremony_passes_real_work():
    for c in ["terraform apply", "gh pr create", "kubectl rollout"]:
        assert not mine.is_ceremony(c), c


# --- repeated sequence detection -------------------------------------------

def test_find_repeated_sequences_returns_recurring_bigram():
    seq = ["terraform plan", "terraform apply",
           "terraform plan", "terraform apply"]
    res = mine.find_repeated_sequences(seq, n=2, min_count=2)
    sequences = [tuple(r["sequence"]) for r in res]
    assert ("terraform plan", "terraform apply") in sequences
    hit = next(r for r in res if tuple(r["sequence"]) == ("terraform plan", "terraform apply"))
    assert hit["count"] == 2


def test_find_repeated_sequences_drops_below_threshold():
    seq = ["a", "b", "c", "d"]
    assert mine.find_repeated_sequences(seq, n=2, min_count=2) == []


# --- lexical similarity + gap detection ------------------------------------

def test_lexical_similarity_bounds():
    assert mine.lexical_similarity("alpha beta", "alpha beta") == 1.0
    assert mine.lexical_similarity("alpha beta", "gamma delta") == 0.0


def test_find_gap_candidates_clusters_near_duplicates():
    prompts = [
        "how do I auth to the perf account",
        "how to auth to perf account",
        "what is the capital of france",
    ]
    clusters = mine.find_gap_candidates(prompts, threshold=0.5)
    assert len(clusters) == 1
    members = clusters[0]["prompts"]
    assert any("perf account" in p for p in members)
    assert all("france" not in p for p in members)


# --- dedup is DOWN-RANK, not drop ------------------------------------------

def test_score_against_skills_ranks_covered_higher_than_novel():
    skills = [{"name": "terraform-review",
               "description": "review terraform plan and apply changes"}]
    covered = mine.score_against_skills(["terraform plan", "terraform apply"], skills)
    novel = mine.score_against_skills(["docker build", "docker push"], skills)
    assert covered > novel
    # nothing is dropped: both return a real score in [0, 1]
    assert 0.0 <= novel <= covered <= 1.0


# --- zsh history parsing ----------------------------------------------------

def test_parse_zsh_history_extended_format():
    text = ": 1719230000:0;terraform plan\n: 1719230005:0;terraform apply\n"
    entries = mine.parse_zsh_history(text)
    assert [e["cmd"] for e in entries] == ["terraform plan", "terraform apply"]
    assert entries[0]["ts"] == 1719230000


def test_parse_zsh_history_filters_by_watermark():
    text = ": 1000:0;old cmd\n: 2000:0;new cmd\n"
    entries = mine.parse_zsh_history(text, since_ts=1500)
    assert [e["cmd"] for e in entries] == ["new cmd"]


# --- end-to-end CLI: emits a schema'd, redacted candidates.json ------------

def test_cli_emits_redacted_candidates_json():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        hist = d / "histfile"
        hist.write_text(
            ": 1719230000:0;terraform plan # AKIAIOSFODNN7EXAMPLE\n"
            ": 1719230005:0;terraform apply\n"
            ": 1719230100:0;terraform plan\n"
            ": 1719230105:0;terraform apply\n")
        tdir = d / "transcripts"
        tdir.mkdir()
        (tdir / "s.jsonl").write_text(
            json.dumps({"type": "user", "message": {"role": "user",
                        "content": "how do I auth to the perf account"}}) + "\n" +
            json.dumps({"type": "user", "message": {"role": "user",
                        "content": "how to auth to perf account"}}) + "\n")
        sdir = d / "skills"
        (sdir / "terraform-review").mkdir(parents=True)
        (sdir / "terraform-review" / "SKILL.md").write_text(
            "---\nname: terraform-review\n"
            "description: review terraform plan and apply\n---\n")
        out = d / "candidates.json"
        r = subprocess.run(
            [sys.executable, str(MINER),
             "--history-file", str(hist),
             "--transcripts-dir", str(tdir),
             "--skills-dir", str(sdir),
             "-o", str(out)],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        data = json.loads(out.read_text())
        assert data["schema_version"] >= 1
        for key in ("workflow_candidates", "gap_candidates", "existing_skills"):
            assert key in data, key
        # redaction held end-to-end
        assert "AKIAIOSFODNN7EXAMPLE" not in out.read_text()
        # the repeated terraform sequence surfaced
        seqs = [tuple(c["sequence"]) for c in data["workflow_candidates"]]
        assert ("terraform plan", "terraform apply") in seqs
        # gap detection caught the near-duplicate question
        assert len(data["gap_candidates"]) >= 1


def test_is_real_prompt_rejects_scaffolding():
    for junk in [
        "<command-name>/exit</command-name>",
        "<local-command-stdout>Bye!</local-command-stdout>",
        "<task-notification>\n<task-id>abc</task-id>",
        "<system-reminder>the user named this session</system-reminder>",
        "[Request interrupted by user]",
        "<bash-input>echo test</bash-input>",
    ]:
        assert not mine.is_real_prompt(junk), junk


def test_is_real_prompt_keeps_genuine_questions():
    for ok in ["give me the link to the PR",
               "did you do a code review of the work?"]:
        assert mine.is_real_prompt(ok), ok


def test_find_repeated_sequences_rejects_same_command_repeat():
    # "mv -> mv" / "claude -> claude" are noise, not workflows
    assert mine.find_repeated_sequences(["mv", "mv", "mv", "mv"]) == []
    # a genuine distinct sequence still surfaces
    res = mine.find_repeated_sequences(
        ["terraform plan", "terraform apply"] * 2)
    assert any(tuple(r["sequence"]) == ("terraform plan", "terraform apply")
               for r in res)


def test_extract_user_prompts_windows_by_mtime():
    import os
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        old = d / "old.jsonl"
        new = d / "new.jsonl"
        old.write_text(json.dumps({"type": "user", "message": {
            "role": "user", "content": "old question about kafka topics"}}) + "\n")
        new.write_text(json.dumps({"type": "user", "message": {
            "role": "user", "content": "new question about kafka topics"}}) + "\n")
        os.utime(old, (1000, 1000))  # ancient
        prompts = mine.extract_user_prompts(d, since_ts=1_000_000)
        joined = " ".join(prompts)
        assert "new question" in joined
        assert "old question" not in joined


def test_find_gap_candidates_filters_trivial_short_prompts():
    # "yes" / "run it" are noise, not knowledge gaps, even if repeated
    prompts = ["yes", "yes", "run it", "run it"]
    assert mine.find_gap_candidates(prompts, threshold=0.5) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)} tests passed")
