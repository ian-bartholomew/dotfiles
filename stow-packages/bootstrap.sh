#!/usr/bin/env bash
# bootstrap.sh - slim orchestration entry point for dotfiles.
# Run from a cloned repo: git clone <repo> && cd dotfiles && ./stow-packages/bootstrap.sh
# Pure CLI orchestration; all data/logic lives in the dotctl binary.
set -uo pipefail  # not -e: main accumulates rc and exits non-zero on real failure

DOTFILES_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/dotctl"
mkdir -p "$STATE_DIR"
LOG="$STATE_DIR/bootstrap.log"
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

ensure_pkg_mgr() {
  case "$(uname -s)" in
    Darwin)
      have brew && return 0
      log "installing Homebrew (non-interactive)"
      local installer; installer="$(mktemp)"
      fetch "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh" "$installer" \
        || { log "FATAL: could not download Homebrew installer"; return 1; }
      NONINTERACTIVE=1 /bin/bash "$installer" || { log "FATAL: Homebrew install failed"; return 1; }
      local brew_bin=/opt/homebrew/bin/brew; [ -x "$brew_bin" ] || brew_bin=/usr/local/bin/brew
      [ -x "$brew_bin" ] && eval "$("$brew_bin" shellenv)"
      have brew || { log "FATAL: brew not on PATH after install"; return 1; }
      ;;
    Linux)
      if have pacman && ! have yay; then
        log "warning: yay not found; AUR packages will be skipped (required set is official pacman)"
      fi
      ;;
  esac
}

prompt_email() {
  if [ -n "${DOTCTL_EMAIL:-}" ]; then printf '%s' "$DOTCTL_EMAIL"; return; fi
  if [ -t 0 ]; then local e; read -r -p "git email for this machine: " e; printf '%s' "$e"; fi
  # non-interactive with no DOTCTL_EMAIL: print nothing (caller skips gitconfig)
}

uses_1password_signing() { grep -q 'op-ssh-sign' "$HOME/.config/git/config.machine" 2>/dev/null; }

print_key_instructions() {
  log "== Register ~/.ssh/id_ed25519.pub on GitHub in BOTH Authentication and Signing sections: https://github.com/settings/keys"
  [ -f "$HOME/.ssh/id_ed25519.pub" ] && log "$(cat "$HOME/.ssh/id_ed25519.pub")"
}

setup_ssh_key() {
  local email="${1:-}"
  mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
  if uses_1password_signing; then log "1Password signing detected (config.machine); skipping on-disk key generation"; return 0; fi
  [ -f "$HOME/.ssh/id_ed25519" ] || ssh-keygen -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519"
  if [ -n "$email" ]; then
    "$DOTCTL" allowed-signers add \
      -file "$DOTFILES_ROOT/stow-packages/git/.config/git/allowed_signers" \
      -email "$email" -pubkey "$HOME/.ssh/id_ed25519.pub" || true
  else
    log "no email available; skipping allowed-signers registration"
  fi
  print_key_instructions
  return 0
}

stow_all() {
  local rc=0 p target
  ( cd "$DOTFILES_ROOT/stow-packages" || return 1
    for p in */; do
      p="${p%/}"
      git -C "$DOTFILES_ROOT" check-ignore "stow-packages/$p" >/dev/null 2>&1 && continue
      if ! stow -v -t "$HOME" --ignore='\.DS_Store' "$p" 2>/dev/null; then
        # back up conflicting targets then retry (best-effort)
        # parses stow's own conflict wording ("... over existing target X since
        # neither a link nor a directory ..."); verified against installed Stow.pm
        while IFS= read -r target; do
          [ -e "$HOME/$target" ] && [ ! -L "$HOME/$target" ] && mv "$HOME/$target" "$HOME/$target.pre-dotctl.$$"
        done < <(stow -n -v -t "$HOME" --ignore='\.DS_Store' "$p" 2>&1 | sed -n 's/.*over existing target \(.*\) since neither a link.*/\1/p')
        stow -v -t "$HOME" --ignore='\.DS_Store' "$p" || { log "stow conflict: $p"; rc=1; }
      fi
    done
    return $rc )
}

set_default_shell() {
  local zsh_path; zsh_path="$(command -v zsh || true)"
  [ -n "$zsh_path" ] || { log "zsh not found; skipping shell change"; return 0; }
  case "$(dscl . -read "/Users/$USER_NAME" UserShell 2>/dev/null || getent passwd "$USER_NAME")" in
    *"$zsh_path"*|*/zsh) log "zsh already default"; return 0 ;;
  esac
  if ! grep -qx "$zsh_path" /etc/shells 2>/dev/null; then
    if [ -w /etc/shells ]; then printf '%s\n' "$zsh_path" >> /etc/shells
    elif have sudo && sudo -n true 2>/dev/null; then printf '%s\n' "$zsh_path" | sudo tee -a /etc/shells >/dev/null
    else log "cannot edit /etc/shells non-interactively; run: sudo sh -c 'echo $zsh_path >> /etc/shells' && chsh -s $zsh_path"; return 0; fi
  fi
  chsh -s "$zsh_path" 2>/dev/null || log "chsh needs interaction; run: chsh -s $zsh_path"
}

main() {
  local rc=0 email
  log "dotfiles bootstrap starting ($(uname -s) $(uname -m))"
  ensure_dotctl  || { log "FATAL: could not obtain dotctl"; exit 1; }
  ensure_pkg_mgr || { log "FATAL: package manager unavailable"; exit 1; }
  "$DOTCTL" check || { log "FATAL: preflight failed"; exit 1; }
  "$DOTCTL" install -file "$DOTFILES_ROOT/packages.csv" ${DOTCTL_YES:+--yes} || rc=1
  stow_all || rc=1
  email="$(prompt_email)"
  if [ -n "$email" ]; then "$DOTCTL" gitconfig -email "$email" || rc=1; else log "no email provided; skipping gitconfig (set DOTCTL_EMAIL or run interactively)"; fi
  setup_ssh_key "$email" || rc=1
  set_default_shell || rc=1
  "$DOTCTL" verify || rc=1
  if [ "$rc" -eq 0 ]; then log "bootstrap OK"; else log "bootstrap finished with errors (rc=$rc); see $LOG"; fi
  log "after registering the key on GitHub: git -C '$DOTFILES_ROOT' remote set-url origin git@github.com:ian-bartholomew/dotfiles.git && '$DOTCTL' verify --remote"
  exit "$rc"
}

main "$@"
