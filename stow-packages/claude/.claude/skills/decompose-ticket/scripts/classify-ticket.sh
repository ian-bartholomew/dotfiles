#!/usr/bin/env bash
# classify-ticket.sh — decision core for the decompose-ticket skill.
# Maps a parent Jira issue type to its one-level-down child tier and the
# routing used to create that child, or refuses (exit 2) when the type is not
# decomposable within this skill's scope.
#
# stdout (exit 0): three key=value lines — child_type, child_project, creator.
# stderr (exit 2): a one-line reason.
#
# creator routing:
#   jira-skill          -> create via the fes-platform-jira-tickets contract
#   direct-mcp-subtask  -> create via direct Atlassian MCP (that skill has no
#                          sub-task path); omit the Team field per FANDEVX rules.
#
# ponytail: scope is Feature/Epic/Story only (the requested tiers). Task is
# refused even though Task->Sub-task is valid — add a `task)` case if that need
# ever shows up rather than guessing at it now.
set -u

[ "$#" -eq 1 ] || { echo "usage: classify-ticket.sh <parent-issue-type>" >&2; exit 2; }

# normalize: lowercase, strip whitespace, unify sub-task spellings
t="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
case "$t" in subtask) t="sub-task" ;; esac

case "$t" in
  feature)
    echo "child_type=Epic"
    echo "child_project=FANDEVX"
    echo "creator=jira-skill"
    ;;
  epic)
    echo "child_type=Story"
    echo "child_project=FANDEVX"
    echo "creator=jira-skill"
    ;;
  story)
    echo "child_type=Sub-task"
    echo "child_project=FANDEVX"
    echo "creator=direct-mcp-subtask"
    ;;
  bug)
    echo "not decomposable: Bug is a leaf work item with no child tier." >&2
    exit 2
    ;;
  sub-task)
    echo "not decomposable: Sub-task is already the lowest hierarchy level." >&2
    exit 2
    ;;
  "")
    echo "no issue type provided." >&2
    exit 2
    ;;
  *)
    echo "unsupported issue type: '$1' (decomposes Feature, Epic, or Story only)." >&2
    exit 2
    ;;
esac
