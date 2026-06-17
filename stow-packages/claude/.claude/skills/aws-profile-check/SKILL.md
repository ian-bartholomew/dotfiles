---
name: aws-profile-check
description: Use BEFORE any `aws --profile <name>` call that targets a non-default account (perf, cicd-prod, inf-dev, etc.), or IMMEDIATELY AFTER any AWS call that returns ExpiredToken / Unable to locate credentials / The SSO session has expired. Verifies the profile is configured, refreshes SSO if expired, and confirms the resolved account ID matches expectation via `aws sts get-caller-identity` before running the real command. Triggers on "check aws profile <name>", "switch aws account", "aws sso expired", "am I authed for <account>", or any cross-account query (CLAUDE.md warns the MCP cannot reliably target a specific profile).
version: 0.1.0
allowed-tools: [Bash]
---

# AWS Profile Check

Preflight that catches silent SSO expiry and account-mismatch BEFORE the real AWS call fails. CLAUDE.md mandates the AWS CLI (not the MCP) for cross-account work and warns that SSO sessions expire silently — this skill is the gate that enforces that rule.

## When to Use

- About to run any `aws ... --profile <name>` where `<name>` is not the default dev profile
- Any AWS call just returned `ExpiredToken`, `Unable to locate credentials`, `The SSO session associated with this profile has expired`, or similar
- User says "switch to <profile>", "check I'm in <account>", "am I authed for perf / cicd-prod / inf-dev"
- Cross-account verification step in `/verify-status`, `/start-of-day`, or any incident workflow

## When NOT to Use

- The query is against the default dev account and the AWS MCP can handle it — use the MCP per CLAUDE.md preference
- The user is running `aws configure sso` (initial profile setup) — different workflow
- Read-only query is genuinely fine to fail and retry — but if the call is mid-pipeline, run this first

## Expected Account IDs

Confirm with the user if not stated. Common Fanatics targets seen in session history:

- `fanapp-cicd-prod` — central CI account
- `fanapp-perf` — perf environment
- `fanapp-inf-dev` / `fanapp-inf-prod` — shared infra
- Default dev — usually no `--profile` flag needed

If the user names a target ("am I in perf?") and the resolved account ID doesn't match the expected ID for that name, halt and surface the mismatch — do not proceed.

## Steps

### 1. Confirm the profile is configured

```bash
aws configure list-profiles | grep -Fx "<name>" \
  || { echo "FATAL: profile <name> not in ~/.aws/config. List with 'aws configure list-profiles' and re-check the name."; exit 1; }
```

### 2. Probe identity

```bash
aws sts get-caller-identity --profile <name>
```

### 3. If the probe fails, refresh SSO and re-probe

Trigger conditions on the probe output: `ExpiredToken`, `Unable to locate credentials`, `SSO session ... has expired`, `Token has expired and refresh failed`.

```bash
aws sso login --profile <name>
aws sts get-caller-identity --profile <name>
```

If the second probe still fails, halt — the profile may be misconfigured, the SSO start URL may have changed, or the user is offline. Surface the raw error rather than retrying again.

### 4. Quote the verified identity to the user

One-line summary:

```
profile=<name> account=<id> arn=<arn>
```

Then proceed with the real AWS call in the same parent turn.

## Mandatory Verification

- The probe in step 2 (or the re-probe in step 3) MUST succeed before any subsequent AWS call.
- Quote the actual `Account` field returned by `get-caller-identity` in the user-facing report.
- If the user named a target account and the returned account ID does not match the expected ID, **halt** — do not silently proceed with the "wrong but authed" profile. Surface the mismatch and ask the user to confirm or pick a different profile.

## Red Flags

- Running the real AWS call first "to see what happens" — that's exactly the failure pattern this skill prevents
- Retrying `aws sso login` more than once without surfacing the error — second failure means the problem is not session expiry
- Skipping `get-caller-identity` because "I just logged in" — SSO login can succeed while still resolving to the wrong account if the profile points at a different start URL
- Using `aws configure list` instead of `aws sts get-caller-identity` to confirm — `list` shows config, not the live resolved identity

## Related Skills

- `/verify-status` — calls this skill before any cross-account JIRA/PR/AWS step
- `aws-core:*` plugin skills — load AFTER this skill confirms the profile is good
