# User Instructions for Claude

Always check the wiki (`~/Documents/Work/wiki/_index.md`) before web search, Context7, or general knowledge. The wiki is the primary source of truth for all questions — use it first, fall back to external sources only if the wiki has no relevant content.

Always use Context7 when I need library/API documentation, code generation, setup or configuration steps without me having to explicitly ask.

## Style

- Do not use emojis in any output — chat responses, code, comments, commit messages, PR descriptions, file contents, or anything else — unless I explicitly ask for them. This applies even when a tool, skill, or template suggests emojis.

## Git Conventions

- Branch names: `<ticket-id>-<ticket-name>`, e.g. `FANDEVX-2592-fbg-fanflow-kafka-dev`
- Worktrees: always create git worktrees in `EnterWorktree`'s default location — `<repo-root>/.claude/worktrees/<branch-name>` (the `.claude/` directory at the repo root, NOT `~/.claude/`). Do not override this default. Never place worktrees outside the repo, in sibling directories, or in a top-level `.worktrees/` directory. When falling back to raw `git worktree add` (no `EnterWorktree` available), mirror the same `<repo-root>/.claude/worktrees/<branch-name>` path.
- Code review before PR: always run a local code review using the `feature-dev:code-reviewer` agent before pushing a branch and opening a PR. Address any high-confidence issues it surfaces (or explicitly justify ignoring them) before the PR goes up.

## GitHub Identity

- For any repo in the `fanatics-gaming` org, work always happens under the `ian-at-fes` GitHub identity. Personal account is `ian-bartholomew`.
- Before any repo-scoped query, run `gh auth status` and confirm `ian-at-fes` is the active account.
- Do NOT use `--author=@me` in `gh search prs` / `gh search issues` — it resolves to the personal account even when `ian-at-fes` is active. Use `--author=ian-at-fes` explicitly.
- Prefer the `gh` CLI over the GitHub MCP for queries against `fanatics-gaming` org repos — the MCP has shown gaps in org-scoped PR visibility.
- For SSH, the `github-work` host alias is the work identity; never use the bare `github.com` remote.

## Confluence

- Always use the `claude-atlassian:confluence-editor` skill (or `/confluence-editor`) for ALL Confluence page edits — creating new pages, updating existing pages, draft promotions, title changes, anything that mutates a Confluence page. Do not bypass it with direct MCP calls (`createConfluencePage`, `updateConfluencePage`, etc.).
- Reading Confluence (e.g. `getConfluencePage`, `getConfluenceSpaces`) does not require the skill — direct MCP calls are fine for read operations.

## JIRA Defaults

When creating or working with JIRA tickets:

- Default to the FanApp DevX space
- Assign tickets to the FES Platform team
- Always use the `fes-platform-jira-tickets` skill (or `/fes-platform-jira-tickets`) when creating, filing, or generating any JIRA tickets (Epics, Stories, Bugs in FANDEVX; Features in FESFEAT). Do not bypass it with direct MCP calls.
- Always use the `/start-ticket` skill when starting work on a JIRA ticket — it fetches ticket details, creates the worktree on a properly named branch, and produces an initial plan.
- Bugs cannot be children of Stories — both are hierarchy level 0. Parent Bugs under an Epic or Feature; use a "Relates" link to associate with a Story.
- Work Category is required when creating Stories AND Bugs (not Stories only). Don't omit it on bug creation.

## Projects

When working on anything tied to a project under `~/Documents/Work/projects/<project>/`, always append an entry to that project's `log.md` capturing what was done, decisions made, and any follow-ups. Update `log.md` as work progresses, not just at the end of the session.

## AWS Guidance

- Prefer the AWS MCP Server for AWS interactions — it provides sandboxed execution, observability, and audit logging. If unavailable, use the AWS CLI directly.
- Prefer the AWS Documentation Server for AWS documentation questions.
- Before starting a task, check whether a relevant AWS skill is available. Load the skill with `retrieve_skill` and prefer its guidance over general knowledge.
- When uncertain about specific AWS details (API parameters, permissions, limits, error codes), verify against documentation via the AWS Documentation MCP server, rather than guessing. State uncertainty explicitly if you cannot confirm.
- Do not directly create infrastructure directly, only through Terraform
- When working with or creating infrastructure, follow AWS Well-Architected Framework principles. If a plan violates any of the Well-Architected Framework, call it out
- Do not use em dashes in AWS resource names or descriptions. Use hyphens instead.
- For cross-account verification (anything beyond the default dev account), the AWS MCP cannot reliably target a specific profile. Fall back to the AWS CLI with an explicit `--profile`, and re-authenticate SSO (`aws sso login --profile <name>`) before querying — SSO sessions expire silently.

## Verification

Before reporting work as done — implementation, fix, apply, merge, anything — run the relevant verification command and quote the output. If verification isn't possible (e.g. cross-account AWS lookup blocked by MCP credential handling), say so explicitly rather than assuming success. Use the `superpowers:verification-before-completion` skill when in doubt.
