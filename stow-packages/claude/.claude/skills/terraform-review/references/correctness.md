# Correctness & operability lens

You are reviewing for changes that will silently behave wrong on apply, cause unintended replacement, or fight Terraform later. This is the most consequential lens — `terraform validate` ensures syntax compiles, but it cannot tell you whether the *intent* matches the diff.

## What to flag

### State address changes

- **Resource address rename without a `moved` block** — when the diff shows `resource "aws_X" "old_name"` removed and `resource "aws_X" "new_name"` added (or `module.X` renamed to `module.Y`) with no corresponding `moved { from = ... to = ... }` block, this will cause **destroy-then-recreate**. Severity: critical. Confidence: 95+ when the rename is visible in-diff.
- **Resource that moved between modules without `moved` block scoped to the new module** — flag at critical.
- **`removed` block missing for a resource being deleted from config but expected to remain in production** — when comments / context suggest the resource itself shouldn't be destroyed (`removed { ... lifecycle { destroy = false } }`). Severity: high; judgment-dependent.

### `lifecycle` correctness

- **`prevent_destroy = true` on a *new* resource** — fine for stateful resources (DBs, key material). Flag at info to confirm intent.
- **`prevent_destroy = true` *removed*** — diff drops the guard. Severity: high. The PR is unlocking a production safety net.
- **`ignore_changes = all`** — almost always wrong; means Terraform never converges drift. Severity: high.
- **`ignore_changes = [tags]` while CLAUDE.md or repo convention enforces tags** — flag at medium with note: tag drift goes silently.
- **`ignore_changes` listing an attribute that does not exist on the resource** — `validate` may not catch this in all versions. Severity: medium if visible.
- **`create_before_destroy` without an obvious need** — usually fine for stateless, dangerous for stateful. Flag at low for review.

### Meta-argument bugs

- **`for_each` over a value that is `null` or depends on a resource attribute computed at apply** — will fail with "for_each value depends on resource attributes". Severity: high if visible.
- **`count` and `for_each` both set on the same resource** — Terraform error, but worth flagging. Severity: critical (would fail validate, but call out in case validate didn't run).
- **`depends_on` listing a resource that is already an implicit reference** — noise; severity low.
- **`depends_on` *missing* where a data source needs a resource to exist first** — common bug pattern (data source reads before resource creates). Severity: high.

### Provider and version

- **`required_version` constraint loosened** — e.g. `~> 1.5` → `>= 1.0`. Severity: medium with note: easy regression vector.
- **`provider` alias added but not referenced** — dead config. Severity: low.
- **A resource referencing a provider alias that isn't passed through to a module's `providers` argument** — will run against the default provider unintentionally. Severity: high.

### Tags and labels

- **New AWS resource missing required tags** — when the repo has a tagging convention (look in repo CLAUDE.md, root locals, `default_tags`), new resources that don't inherit it. Severity: medium.
- **`default_tags` overridden inline on a resource without justification** — drift between intent and reality. Severity: medium.

### Data sources

- **`data` block with hardcoded ID that should be looked up by name/tag** — brittle across environments. Severity: medium.
- **`data` block missing `count`/`for_each` while the consumer uses `count`/`for_each`** — common mismatch causing index errors. Severity: high.

### Operability

- **New resources with no observability hooks** in a context where the wiki recommends them (e.g. new RDS without CloudWatch alarms, new ALB without access logs). Severity: medium; flag as judgment-dependent.
- **Stateful resources without `backup` / `point_in_time_recovery` configuration** — RDS without `backup_retention_period`, DynamoDB without `point_in_time_recovery`. Severity: high.
- **Resources that imply blast-radius escalation** — anything that affects a shared cluster, control plane, DNS zone, or IAM trust used widely. Severity escalates by one tier above the bare finding (e.g. wildcard IAM on shared role = critical, not high).

### Cross-environment divergence

- **A change to a shared module that affects multiple envs but only one env's invocation was modified in the diff** — suggests the change will land asymmetrically. Severity: high.
- **Environment-specific values hardcoded in a shared module** — `environment = "prod"` baked into a module under `modules/` instead of passed as a variable. Severity: high.

## What NOT to flag

- Anything `terraform validate` already errored on. The deterministic output is in your context.
- Aesthetic issues — those belong to style and module-api lenses.
- Cost / sizing concerns.

## Anchor wiki articles

- `[[terraform-improvements]]`
- `[[terraform-refactoring]]`
- `[[fes-terraform-plan-risk]]`
- `[[terraform-operations-best-practices]]`
- `[[fanatics-terraform-deployment-process]]`
- `[[terraform-tagging-strategy]]`
