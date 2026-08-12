---
name: start-ticket
description: Start working on a JIRA ticket — fetches ticket details, creates a git worktree on a new branch, and plans the implementation using superpowers.
arguments:
  - name: ticket
    description: JIRA ticket key (e.g. FANDEVX-1234)
    required: true
---

# Start Ticket Workflow

You are starting work on JIRA ticket `$ARGUMENTS.ticket`. Follow these steps in order:

## Step 1: Fetch the JIRA ticket

Use the Atlassian MCP tools to fetch the ticket details:

- Get the issue summary, description, acceptance criteria, and status
- If the ticket is a sub-task, also fetch the parent story for context
- If the ticket is a story, also fetch the parent epic for context
- Note any linked/blocking tickets
- If the ticket has subtasks, prompt the user if they want to work on one task in particular.
  - If the user does want to work on one ticket in particular, focus the current work on that ticket.
    - Use the Ticket Workflow outlined in this skill to work on the ticket (assign the ticket to the user, worktree, plan, etc)
    - When work is finished on that ticket, return to the parent ticket and check if there are any remaining subtasks.
    - If there are remaining subtasks, ask the user if they want to work on one task in particular.
    - Loop this step until all subtasks are complete.
    - When all subtasks are complete, inspect the parent ticket for AC and evaluate if the AC is met, and the ticket can be closed
- Assign the ticket to the user.
- Transition the ticket status as you work: "In Progress" while you are working on it, "In Code Review" when the PR is up, "Blocked" if work is blocked, and "Done" when the PR is merged

Present a brief summary of the ticket to the user before proceeding.

## Step 2: Create a git worktree

Create an isolated git worktree for this work:

- Detect the current git repository from the working directory
- Create a new branch named `{ticket-key}-{slugified-summary}` (e.g. `FANDEVX-2505-submit-aws-account-request`)
  - Slugify the summary: lowercase, replace spaces with hyphens, remove special characters, truncate to 50 chars
  - Separate the key from the slug with a hyphen, not a slash. This is the convention in CLAUDE.md, and a slash makes the branch a nested directory once a worktree is derived from it
- Use the `using-git-worktrees` skill from superpowers to create the worktree safely
- If the worktree skill is not available, fall back to manual `git worktree add` commands

## Step 3: Plan the work

Once in the worktree:

- Use the `superpowers:brainstorm` skill from superpowers to create an implementation plan
- Feed the JIRA ticket details (summary, description, AC, parent context) into the brainstorm
- The plan should identify:
  - What files need to be created or modified
  - What the implementation approach is
  - How to verify the work is complete (based on AC)
- Use `adversarial-review` skill to validate the plan
  - If the review comes back with feedback, integrate it and run a review again
  - Do a maximum of 2 review rounds

## Important Notes

- Always use superpowers to plan the work
- Always do a code review before making a PR
- If the ticket is already "In Progress", warn the user that work may already be underway
- If the ticket has blockers that aren't resolved, warn the user
- Transition the ticket to "In Progress" after the worktree is created (ask the user first)
