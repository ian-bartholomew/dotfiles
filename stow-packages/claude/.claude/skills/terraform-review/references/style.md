# Style & structure lens

You are reviewing for HCL style, naming, file organization, and provider/version pinning. `terraform fmt` and `tflint`'s formatting rules have already run — *do not re-emit what they caught*. Focus on the qualitative style concerns those tools miss.

## What to flag

### Naming and identifiers

- **Mixed casing in resource names** — Terraform convention is `snake_case` for resource names and labels. Flag camelCase / kebab-case in resource labels (e.g. `resource "aws_s3_bucket" "myBucket"` → should be `my_bucket`). Severity: low.
- **Names that include the type** — `resource "aws_s3_bucket" "user_bucket_s3"` is redundant. Flag suffixes that duplicate the resource type. Severity: low.
- **Provider-prefixed names** — `resource "aws_s3_bucket" "aws_user_bucket"` is similarly redundant. Severity: low.
- **Non-descriptive names** — `main`, `this`, `default` are fine for *single*-instance resources in a module. Flag when there are multiple resources of the same type all named `main`. Severity: low.

### File organization

- **Mixed concerns in `main.tf`** — when `main.tf` defines variables or outputs (instead of just resources/data/modules), flag with severity low: convention is `variables.tf`, `outputs.tf`, `versions.tf`, `main.tf`, `locals.tf`.
- **Missing `versions.tf` in a root module** — root modules should pin `required_version` and `required_providers`. Severity: high (it's a real reproducibility issue, not just style).
- **`terraform {}` block in `main.tf`** — should live in `versions.tf`. Severity: low.

### Provider and version pinning

- **Unpinned provider** — `required_providers` entries without a `version` constraint. Severity: high.
- **Loose pin in a root module** — `version = ">= 4.0"` with no upper bound risks silent major-version drift. Prefer `~> 5.0`. Severity: medium.
- **Mismatched provider versions across sibling modules** — if the diff touches two root modules and they pin incompatible majors of the same provider, flag at severity high.

### Block ordering and ergonomics

- **Meta-arguments not at the top** — `count`, `for_each`, `provider`, `depends_on`, `lifecycle` should appear at the top or bottom of a block per HashiCorp style, separated by a blank line from regular arguments. Severity: low.
- **`lifecycle` block with no comment explaining `ignore_changes` or `prevent_destroy`** — these are operational hazards; require justification in a comment. Severity: medium (escalates to correctness lens).
- **Long inline blocks that should be locals** — heredocs >20 lines inline in a resource argument are hard to diff. Flag with severity low.

### Deprecated syntax

- **Legacy interpolation** — `"${var.foo}"` where bare `var.foo` would work (Terraform 0.12+). Severity: low.
- **`null_resource` where a `terraform_data` would now serve** (Terraform ≥1.4). Severity: info.
- **Deprecated resource arguments** — when tflint flags a deprecated argument, escalate to severity medium and explain consequence (don't just repeat tflint).

## What NOT to flag

- Anything `terraform fmt` would catch (indentation, alignment, trailing commas). It's already run.
- Anything the deterministic tflint output already flagged. Use their output as input — your role is the layer above.
- Personal preference unsupported by HashiCorp style or the repo's CLAUDE.md.
- Comment style. Code-comment minimalism is a judgment call left to the author.

## Anchor wiki articles

When emitting findings, prefer these `references` entries when they apply:

- `[[terraform-improvements]]`
- `[[terraform-recommended-practices]]`
- `[[terraform-operations-best-practices]]`
