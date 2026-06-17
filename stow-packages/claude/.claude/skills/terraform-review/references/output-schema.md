# Output schema and rubrics

All reviewer agents return a YAML list of findings. Each finding follows this shape:

```yaml
- file: services/foo/main.tf      # path relative to repo root, from the diff
  line: 42                         # line in the post-change file (use diff +N anchor)
  lens: security                   # style | module-api | security | correctness
  rule: iam-wildcard-action        # short kebab-case identifier; stable across runs
  severity: high                   # critical | high | medium | low | info
  confidence: 90                   # 0-100 integer
  message: "IAM policy uses wildcard 'ec2:*' on Resource '*' in a role attached to prod EKS nodegroup."
  suggestion: "Scope actions to those actually used; if intentional, justify in a comment."
  references:                      # optional list of wiki anchors / URLs
    - "[[terraform-improvements]]"
```

Return *only* the YAML list. No prose. No headers. No code fences. If you have nothing to flag at ≥80 confidence, return `[]`.

## Severity rubric

Severity is about *consequence if merged*, not about how clever the finding is.

- **critical** — will break production or expose secrets if merged:
  - plaintext credentials or tokens in HCL
  - public S3 / public-access flags on non-public data
  - resource rename without a `moved` block (causes destroy-then-recreate)
  - IAM `*:*` or `*` action with `*` resource on real environments
- **high** — significant correctness or security concern; very likely a real bug or material risk:
  - missing `moved` block on a resource address change
  - IAM wildcard actions scoped to wide resources in prod
  - `lifecycle.ignore_changes` masking intended drift
  - untyped sensitive variable (e.g. a password input without `type` or `sensitive`)
  - missing `provider` / `required_version` pin in a root module
- **medium** — quality and maintainability:
  - variable with no `description`
  - `count` used where `for_each` on a map would be safer (re-ordering bugs)
  - missing or inconsistent resource tags
  - hardcoded values that should be locals or variables
  - large monolithic resource that should be a module
- **low** — style nits beyond `terraform fmt`:
  - inconsistent naming (snake_case vs kebab-case where convention is one)
  - missing `output` description
  - block ordering that violates HashiCorp style
- **info** — observation, not an issue:
  - "this module pins provider to `~> 4.0` — consider bumping to `~> 5.0` in a follow-up"
  - "duplicate logic in two services; potential extract to a shared module later"

## Confidence rubric

Confidence is your honest self-assessment of *how likely this is a real issue given only what's visible in the diff and the supplied tool output*. Do not inflate to make a finding sound important — severity carries that.

- **90–100** — directly observable in the diff with no plausible counter-explanation.
- **80–89** — strong inference; small chance there's a contextual reason the reviewer can't see.
- **60–79** — pattern match, defensible either way. **Filtered out by default** — only emit if asked for full output.
- **<60** — speculation, do not emit.

## Calibration tips

- Two lenses surfacing the same `(file, line)` is a stronger signal than either alone — the skill bumps cross-lens hits by +10. Don't try to game that yourself; just call it as you see it.
- The deterministic tools (`terraform fmt`, `terraform validate`, `tflint`) are pre-run and supplied to you. **Do not re-derive what they already report.** Use their output as input context. Your job is the qualitative layer they can't reach.
- If the diff renames a resource (`aws_X.old → aws_X.new`) and you see no `moved` block in the same diff, that's a CRITICAL — emit at confidence 95+.
- If you see `sensitive = true` but the value comes from a hardcoded string, that's a HIGH at 90+. `sensitive` is presentation, not protection.
- When in doubt about a stylistic choice, *do not emit*. Style nits that aren't in the repo's own conventions waste the reviewer's attention.
