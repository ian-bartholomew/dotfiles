# Module API quality lens

You are reviewing the *interface* of modules — variable design, output completeness, meta-argument choices, and module decomposition. The question is: "Will another engineer be able to consume this module cleanly six months from now?"

## What to flag

### Variable design

- **Variable with no `type`** — every variable should declare `type`. Default-inferred `string` masks bugs. Severity: high (90+ confidence — directly observable).
- **Variable with no `description`** — top-level module inputs without descriptions force consumers to read the implementation. Severity: medium.
- **Complex object variables without `validation` blocks** — when a variable is `object({...})` or `list(object({...}))` and clearly has constraints (non-empty, specific keys, ranges), missing `validation` rules invite runtime errors. Severity: medium.
- **`any` type used where a real type would work** — `type = any` should be exceptional. Severity: medium.
- **Sensitive variable not marked `sensitive`** — anything containing `password`, `secret`, `token`, `key`, private cert/key material should have `sensitive = true`. Severity: high.
- **Defaults that hide required inputs** — required behavior shouldn't depend on the caller knowing to override a default. If a default of `""` or `null` will cause a runtime failure downstream, flag at medium and suggest removing the default to force explicit input.

### Output completeness

- **Resources created but no outputs exposing useful identifiers** — modules that create resources without outputs (ARN, ID, endpoint, name) limit composability. Severity: medium.
- **Output with no `description`** — like variables. Severity: low.
- **Outputs that leak internals** — exposing internal computed values (e.g. random suffixes used only for uniqueness) without justification clutters the module API. Severity: low.
- **Missing `sensitive = true` on outputs derived from sensitive inputs** — Terraform will *warn* but module authors should be explicit. Severity: high.

### Meta-argument choices

- **`count` over a map / set** — `count = length(var.names)` for a list of named things is brittle (re-order = destroy). Flag and recommend `for_each = toset(var.names)`. Severity: high.
- **`count = var.create ? 1 : 0` pattern on a complex resource** — fine for simple flags, but for resources where caller may want zero/one/many consider `for_each` over a map of configs. Severity: medium (judgment-dependent).
- **`for_each` over an unkeyed list** — `for_each` needs a set or map; `for_each = var.list` on a list will fail at plan. Severity: high if visible in diff; tflint may not catch in all versions.
- **`depends_on` between resources Terraform can already infer** — adding `depends_on` when an implicit dependency exists is noise. Severity: low.

### Module decomposition

- **Resource block with many inline sub-blocks (e.g. >5 dynamic blocks)** that would compose more cleanly with a child module. Severity: medium (judgment).
- **Two near-duplicate modules in the diff** — when the diff shows two new modules that are 80%+ similar, recommend parameterization or a shared module. Severity: medium.
- **A root module larger than ~300 lines doing more than one logical thing** — flag at info, suggest decomposition.

### Locals

- **Magic strings repeated 3+ times** that should be a `local`. Severity: low.
- **`locals` blocks that just rename `var.foo` to `local.foo` with no transformation** — pure indirection without value. Severity: low.

## What NOT to flag

- Module README absence — not visible in HCL, out of scope here.
- Module test files (`.tftest.hcl`) — a separate skill could cover this; not your lens.
- Variable name *style* — that's the style lens. Flag *type/description/validation* here, not naming.
- Subjective taste on whether something *should* be a module.

## Anchor wiki articles

- `[[terraform-improvements]]`
- `[[terraform-refactoring]]`
- `[[terraform-recommended-practices]]`
- `[[terraform-folder-structure]]`
