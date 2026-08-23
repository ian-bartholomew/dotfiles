#!/usr/bin/env bash
# bootstrap.sh - slim orchestration entry point for dotfiles.
# Run from a cloned repo: git clone <repo> && cd dotfiles && ./stow-packages/bootstrap.sh
# Pure CLI orchestration; all data/logic lives in the dotctl binary.
set -uo pipefail  # not -e: main accumulates rc and exits non-zero on real failure

# shellcheck disable=SC2034  # consumed by functions defined later in this file
DOTFILES_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/dotctl"
mkdir -p "$STATE_DIR"
LOG="$STATE_DIR/bootstrap.log"
# shellcheck disable=SC2034  # consumed by functions defined later in this file
USER_NAME="${USER:-$(id -un)}"

log() { printf '%s\n' "$*" | tee -a "$LOG" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

# fetch <url> <dest>; FETCH_OVERRIDE is a test-only seam (copies a local file instead of downloading)
fetch() {
  if [ -n "${FETCH_OVERRIDE:-}" ]; then cp "$FETCH_OVERRIDE" "$2"; return; fi
  if have curl; then curl -fsSL "$1" -o "$2"
  elif have wget; then wget -qO "$2" "$1"
  else log "FATAL: need curl or wget"; return 1
  fi
}

main() {
  log "dotfiles bootstrap starting ($(uname -s) $(uname -m))"
  # steps wired in later tasks
}

main "$@"
