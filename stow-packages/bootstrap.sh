#!/usr/bin/env bash
# bootstrap.sh - slim orchestration entry point for dotfiles.
# Run from a cloned repo: git clone <repo> && cd dotfiles && ./stow-packages/bootstrap.sh
# Pure CLI orchestration; all data/logic lives in the dotctl binary.
set -uo pipefail  # not -e: main accumulates rc and exits non-zero on real failure

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

BIN_DIR="$HOME/.local/bin"
DOTCTL="$BIN_DIR/dotctl"

asset_name() {
  local os="" arch=""
  case "$(uname -s)" in Darwin) os=darwin ;; Linux) os=linux ;; *) return 1 ;; esac
  case "$(uname -m)" in x86_64|amd64) arch=amd64 ;; arm64|aarch64) arch=arm64 ;; *) return 1 ;; esac
  printf 'dotctl_%s_%s' "$os" "$arch"
}

ensure_dotctl() {
  local version asset tmp want got
  version="$(cat "$DOTFILES_ROOT/dotctl/VERSION" 2>/dev/null)" || true
  [ -n "$version" ] || { log "FATAL: no pinned dotctl version (dotctl/VERSION missing)"; return 1; }
  if [ -x "$DOTCTL" ] && [ "$("$DOTCTL" version 2>/dev/null)" = "$version" ]; then
    log "dotctl $version already installed"; return 0
  fi
  [ -f "$DOTFILES_ROOT/dotctl/checksums/$version.txt" ] || { log "FATAL: no pinned checksums for $version"; return 1; }
  mkdir -p "$BIN_DIR"
  asset="$(asset_name)" || { log "FATAL: unsupported platform for dotctl asset"; return 1; }
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN
  local base="https://github.com/ian-bartholomew/dotfiles/releases/download/$version"
  fetch "$base/$asset" "$tmp/dotctl" || { log "FATAL: download failed"; return 1; }
  want="$(awk -v a="$asset" '$2==a{print $1}' "$DOTFILES_ROOT/dotctl/checksums/$version.txt")"
  if have sha256sum; then got="$(sha256sum "$tmp/dotctl" | awk '{print $1}')"
  else got="$(shasum -a 256 "$tmp/dotctl" | awk '{print $1}')"; fi
  [ -n "$want" ] && [ "$want" = "$got" ] || { log "FATAL: checksum mismatch for $asset"; return 1; }
  xattr -d com.apple.quarantine "$tmp/dotctl" 2>/dev/null || true
  chmod +x "$tmp/dotctl"; mv "$tmp/dotctl" "$DOTCTL"
  case ":$PATH:" in *":$BIN_DIR:"*) ;; *) export PATH="$BIN_DIR:$PATH" ;; esac
  log "installed dotctl $version to $DOTCTL"
}

main() {
  log "dotfiles bootstrap starting ($(uname -s) $(uname -m))"
  # steps wired in later tasks
}

main "$@"
