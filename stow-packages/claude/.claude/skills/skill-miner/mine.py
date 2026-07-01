#!/usr/bin/env python3
"""skill-miner deterministic core: activity signals -> candidates.json.

No LLM here. Parses zsh history + Claude transcripts, redacts secrets,
finds repeated command sequences and near-duplicate questions, and scores
candidates against existing skills (down-rank, never drop). The LLM layer
(SKILL.md) makes the keep/draft judgments on top of this output.

Run: python3 mine.py --history-file ~/.zsh_history \
       --transcripts-dir ~/.claude/projects --skills-dir <stow>/skills -o candidates.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1

# Commands that are pure shell ceremony, never a skill on their own.
CEREMONY = {
    "ls", "cd", "pwd", "clear", "cat", "echo", "exit", "vim", "nvim", "vi",
    "less", "more", "man", "which", "history", "top", "htop", "code",
    "git status", "git diff", "git log", "git branch",
}

# Secret patterns. ponytail: denylist covers the obvious high-risk shapes;
# upgrade to an entropy scan if a real leak slips through.
_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                              # AWS access key id
    re.compile(r"(?i)(--?(?:password|token|secret|api[-_]?key)[=\s]+)(\S+)"),
    re.compile(r"(?i)([A-Z0-9_]*(?:SECRET|PASSWORD|TOKEN|CREDENTIAL|_KEY)[A-Z0-9_]*=)(\S+)"),
    re.compile(r"(?i)(Bearer\s+)(\S+)"),
    re.compile(r"(?i)(X-Amz-Signature=)([0-9a-f]+)"),
]
_REDACTED = "<redacted>"


def redact(text):
    """Scrub secret-shaped substrings, leaving the rest of the command intact."""
    out = text
    for pat in _SECRET_PATTERNS:
        if pat.groups >= 2:
            out = pat.sub(lambda m: m.group(1) + _REDACTED, out)
        else:
            out = pat.sub(_REDACTED, out)
    return out


_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SUBCMD_TOKEN = re.compile(r"^[A-Za-z][\w-]*$")


def normalize_command(cmd):
    """Reduce a command line to program + subcommand(s); drop args/flags/paths."""
    toks = cmd.split()
    while toks and _ENV_ASSIGN.match(toks[0]):
        toks = toks[1:]
    kept = []
    for t in toks:
        if _SUBCMD_TOKEN.match(t):
            kept.append(t.lower())
        else:
            break
    return " ".join(kept)


def is_ceremony(normalized):
    return normalized in CEREMONY


def find_repeated_sequences(items, n=2, min_count=2, require_distinct=True):
    """Consecutive n-grams that recur at least min_count times.

    require_distinct drops grams whose every element is the same command
    (`mv -> mv`, `claude -> claude`) — those are shell noise, not workflows.
    """
    counts = {}
    for i in range(len(items) - n + 1):
        gram = tuple(items[i:i + n])
        if require_distinct and len(set(gram)) == 1:
            continue
        counts[gram] = counts.get(gram, 0) + 1
    res = [{"sequence": list(g), "count": c}
           for g, c in counts.items() if c >= min_count]
    res.sort(key=lambda r: r["count"], reverse=True)
    return res


# Structural junk that Claude tags as type:user but isn't a real question:
# slash-command wrappers, local-command output, tool/system scaffolding.
_NOT_A_PROMPT = re.compile(
    r"<command-name|<command-message|<local-command|<task-notification|"
    r"<system-reminder|<bash-input|<bash-stdout|<bash-stderr|"
    r"\[request interrupted", re.IGNORECASE)


def is_real_prompt(text):
    """True if text looks like a genuine user question, not tool scaffolding."""
    return not _NOT_A_PROMPT.search(text)


def _tokens(text):
    return set(t for t in re.split(r"[^a-z0-9]+", text.lower()) if t)


def lexical_similarity(a, b):
    """Jaccard over word tokens. 1.0 identical, 0.0 disjoint."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_gap_candidates(prompts, threshold=0.6, min_tokens=4, max_tokens=60):
    """Cluster near-duplicate prompts. Trivial/huge prompts are dropped, and
    pairs are blocked by shared token so this stays near-linear instead of the
    O(n^2) cross-product. ponytail: per-token buckets are capped; a hyper-common
    token won't blow up, at the cost of missing matches that only share stopwords.
    """
    cleaned = []  # (prompt, tokens)
    for p in prompts:
        toks = _tokens(p)
        if min_tokens <= len(toks) <= max_tokens:
            cleaned.append((p, toks))

    index = {}  # token -> [indices into cleaned]
    for i, (_, toks) in enumerate(cleaned):
        for t in toks:
            index.setdefault(t, []).append(i)

    pairs = set()
    for ids in index.values():
        if len(ids) > 200:  # too common to discriminate; skip the bucket
            continue
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                pairs.add((ids[a], ids[b]))

    parent = list(range(len(cleaned)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    best = {}
    for i, j in pairs:
        sim = lexical_similarity(cleaned[i][0], cleaned[j][0])
        if sim >= threshold:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj
            best[find(i)] = max(best.get(find(i), 0.0), sim)

    groups = {}
    for idx in range(len(cleaned)):
        groups.setdefault(find(idx), []).append(cleaned[idx][0])
    return [{"prompts": members, "similarity": round(best.get(root, 0.0), 3)}
            for root, members in groups.items() if len(members) >= 2]


def score_against_skills(sequence, skills):
    """Coverage in [0,1]: how much of the sequence an existing skill already covers.

    This is a DOWN-RANK signal, not a filter. Candidates are never dropped here;
    the LLM layer decides using this score.
    """
    seq_tokens = set()
    for cmd in sequence:
        seq_tokens |= _tokens(cmd)
    if not seq_tokens:
        return 0.0
    best = 0.0
    for s in skills:
        skill_tokens = _tokens(s.get("name", "") + " " + s.get("description", ""))
        overlap = len(seq_tokens & skill_tokens) / len(seq_tokens)
        best = max(best, overlap)
    return round(best, 3)


_HIST_LINE = re.compile(r"^: (\d+):\d+;(.*)$")


def parse_zsh_history(text, since_ts=None):
    """Parse zsh extended history. ponytail: ignores multi-line continuations."""
    entries = []
    for line in text.splitlines():
        m = _HIST_LINE.match(line)
        if m:
            ts, cmd = int(m.group(1)), m.group(2)
        else:
            if not line.strip():
                continue
            ts, cmd = None, line
        if since_ts is not None and ts is not None and ts < since_ts:
            continue
        entries.append({"ts": ts, "cmd": cmd})
    return entries


# --- transcript + skill-frontmatter readers --------------------------------

def _content_text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict)]
        return " ".join(p for p in parts if p)
    return ""


def extract_user_prompts(transcripts_dir, since_ts=None):
    prompts = []
    for jf in sorted(Path(transcripts_dir).rglob("*.jsonl")):
        if since_ts is not None and jf.stat().st_mtime < since_ts:
            continue
        for line in jf.read_text(errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = evt.get("message") or {}
            if evt.get("type") == "user" and msg.get("role") == "user":
                txt = _content_text(msg).strip()
                if txt and is_real_prompt(txt):
                    prompts.append(redact(txt))
    return prompts


_FM_NAME = re.compile(r"^name:\s*(.+)$", re.MULTILINE)
_FM_DESC = re.compile(r"^description:\s*(.+)$", re.MULTILINE)


def load_existing_skills(skills_dir):
    skills = []
    for sk in sorted(Path(skills_dir).glob("*/SKILL.md")):
        text = sk.read_text(errors="ignore")
        name = _FM_NAME.search(text)
        desc = _FM_DESC.search(text)
        skills.append({
            "name": name.group(1).strip() if name else sk.parent.name,
            "description": desc.group(1).strip() if desc else "",
        })
    return skills


def mine(history_file, transcripts_dir, skills_dir, since_ts=None,
         n=2, min_count=2):
    skills = load_existing_skills(skills_dir) if skills_dir else []

    workflow_candidates = []
    if history_file and Path(history_file).exists():
        entries = parse_zsh_history(
            Path(history_file).read_text(errors="ignore"), since_ts=since_ts)
        normalized = []
        for e in entries:
            norm = normalize_command(redact(e["cmd"]))
            if norm and not is_ceremony(norm):
                normalized.append(norm)
        for cand in find_repeated_sequences(normalized, n=n, min_count=min_count):
            cand["coverage"] = score_against_skills(cand["sequence"], skills)
            cand["source"] = "shell"
            workflow_candidates.append(cand)

    gap_candidates = []
    if transcripts_dir and Path(transcripts_dir).exists():
        prompts = extract_user_prompts(transcripts_dir, since_ts=since_ts)
        gap_candidates = find_gap_candidates(prompts)

    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_candidates": workflow_candidates,
        "gap_candidates": gap_candidates,
        "existing_skills": skills,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="skill-miner deterministic core")
    ap.add_argument("--history-file")
    ap.add_argument("--transcripts-dir")
    ap.add_argument("--skills-dir")
    ap.add_argument("--since-ts", type=int, default=None)
    ap.add_argument("-n", type=int, default=2)
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args(argv)

    result = mine(args.history_file, args.transcripts_dir, args.skills_dir,
                  since_ts=args.since_ts, n=args.n, min_count=args.min_count)
    Path(args.out).write_text(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
