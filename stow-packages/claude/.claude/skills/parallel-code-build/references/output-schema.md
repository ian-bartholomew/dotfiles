# Output schema and rubrics

All code-lens-reviewer agents return a YAML list of findings. Each finding follows this shape:

```yaml
- file: src/skills/research/index.ts   # path relative to repo root, from the diff
  line: 42                              # line in the post-change file (use diff +N anchor)
  lens: correctness                     # correctness | tests-tdd | simplicity-architecture
  rule: unhandled-promise-rejection     # short kebab-case identifier; stable across runs
  severity: high                        # critical | high | medium | low | info
  confidence: 90                        # 0-100 integer
  owner: group-b                        # owning scope/teammate from the ownership map
  message: "fetchSummaries() awaits inside a loop with no try/catch; one failed fetch rejects the whole batch."
  suggestion: "Wrap per-item await in try/catch or use Promise.allSettled and surface per-item failures."
```

Return *only* the YAML list. No prose. No headers. No code fences. If you have nothing to flag at >= 80 confidence, return `[]`.

## Severity rubric

Severity is about *consequence if merged*, not about how clever the finding is.

- **critical** - will break users or lose data if merged:
  - data loss or corruption paths (destructive file ops without guard, overwriting user content)
  - unhandled error that crashes the main flow of the artifact
  - change that silently breaks an existing public API or CLI contract
- **high** - very likely a real bug or material gap:
  - logic error on a reachable path (off-by-one, inverted condition, wrong operator)
  - missing error handling where failure is plausible (network, file I/O, subprocess)
  - race condition or unsafe concurrent access
  - resource leak (unclosed file/process/connection) on a repeated path
  - changed behavior with no test that would catch a regression
- **medium** - quality and maintainability:
  - edge case unhandled (empty input, missing key, unicode, large input) on a plausible path
  - test exists but cannot fail (asserts nothing meaningful, mocks the code under test)
  - duplicated logic that should be extracted
  - new abstraction layer with a single caller and no second use in sight
- **low** - minor improvements:
  - naming inconsistent with the repo's own conventions
  - dead code introduced by the change
  - overly broad exception catch that still propagates correctly
- **info** - observation, not an issue:
  - "this pattern appears three times now; candidate for extraction in a follow-up"

## Confidence rubric

Confidence is your honest self-assessment of *how likely this is a real issue given only what's visible in the diff and the repo context you grounded in*. Do not inflate to make a finding sound important - severity carries that.

- **90-100** - directly observable in the diff with no plausible counter-explanation.
- **80-89** - strong inference; small chance there's a contextual reason the reviewer can't see.
- **60-79** - pattern match, defensible either way. **Filtered out by default** - only emit if asked for full output.
- **<60** - speculation, do not emit.

## Calibration tips

- Two lenses surfacing the same `(file, line)` is a stronger signal than either alone. Don't try to game that yourself; just call it as you see it.
- Ground findings before emitting: `Read` the surrounding file, `Grep` for the symbol's other call sites. If grounding would take more than a couple of lookups, the confidence probably isn't there.
- The verifier already ran the build, lint, and full test suite. **Do not re-derive what those tools report.** Your job is the qualitative layer they can't reach.
- When in doubt about a stylistic choice, *do not emit*. Nits that aren't in the repo's own conventions waste the reviewer's attention.
