# dotctl Bootstrap and Distribution Implementation Plan (revised)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `bootstrap.sh` as the slim orchestration entry point that drives the `dotctl` subcommands and the pure-CLI steps (fetch binary, package-manager bootstrap, stow, ssh-keygen, chsh), add the small dotctl changes the bootstrap and CI depend on (`version` subcommand, `verify --skip`, root-aware install), set up distribution (goreleaser + GitHub Actions) and CI, verify cross-distro parity in containers, then cut over from the old scripts.

**Architecture:** `bootstrap.sh` is pure bash orchestration, run from a cloned repo (not `curl | bash`): it fetches a pinned `dotctl` release binary (sha256-verified over TLS), ensures a package manager, then calls `dotctl check/install/gitconfig/allowed-signers add/verify` in a fail-fast, rc-accumulating flow with the bash-native steps interleaved. Every bootstrap step's failure is surfaced (the script exits non-zero if any step fails). goreleaser cross-compiles static binaries; a tag-triggered workflow publishes them from `main`; a push/PR workflow runs Go tests, `dotctl lint`, shellcheck, and a container e2e matrix.

**Tech Stack:** bash, GNU stow, ssh-keygen, chsh; Go (small dotctl additions); goreleaser + GitHub Actions; shellcheck; Docker. `dotctl` from Plans 1 and 2 (already merged on this branch).

**Spec:** `docs/superpowers/specs/2026-08-21-dotctl-bootstrap-design.md`

**Depends on:** Plans 1 and 2 (merged on this branch): all `dotctl` subcommands exist; the layered base gitconfig and this machine's `~/.config/git/config.machine` (1Password signing) are already committed/seeded.

## Revision note

This plan was adversarially reviewed (4 reviewers, all reject) before implementation. The rewrite folds in every confirmed finding: rc-accumulating fail-fast `main`; Homebrew `shellenv` + fetch-rc capture; missing-`yay` is a warning not fatal; goreleaser de-nesting + `formats:` + pinned version + version ldflags; Go installed via pinned tarball in the e2e (Debian 12 ships Go 1.19); a real `verify --skip` task (was only a parenthetical); root-aware `install` so containers/root need no `sudo`; a `dotctl version` subcommand so the VERSION pin actually triggers upgrades; gated `ssh-keygen` (skip on 1Password machines) with `mkdir -p ~/.ssh`; idempotent, backup-guarded, already-done-aware migration; `git rm` that locates tracked-vs-untracked scripts; `.gitignore` for build artifacts + explicit `git add`; `${USER:-$(id -un)}`; a clone-first invocation contract; robust checksum parsing; `sudo -n` guard; merge-to-main-before-release ordering.

## Global Constraints

- `bootstrap.sh` is run from a cloned repo (`git clone https://github.com/ian-bartholomew/dotfiles && cd dotfiles && ./stow-packages/bootstrap.sh`). It is NOT a `curl | bash` target (`DOTFILES_ROOT` is derived from `$0`).
- `set -uo pipefail` (not `-e`): the script must survive `read` EOF and expected non-zero (guard with `|| true`), but `main` accumulates an rc and exits non-zero if any real step failed. Never report success on a failed step.
- Bare-machine prerequisites installed by hand: `git` plus `curl` or `wget`. The shim tries `curl` then `wget`.
- Binary lands at `~/.local/bin/dotctl`; ensure that dir is on PATH for the run and persisted for future shells (via the stowed zsh config).
- Integrity: verify the release asset's sha256 against a checksum pinned in the repo, over TLS. Signing (minisign/cosign) is deferred; this is the spec's stated alternative.
- Release discipline: `packages.csv` schema, `dotctl/VERSION`, and `dotctl/checksums/<version>.txt` are committed to `main`; the tag/release is cut from `main` so a fresh clone of `main` matches the pinned binary.
- `chsh`/`/etc/shells` steps are non-fatal, skipped when non-interactive, no passwordless sudo, or already correct.
- Do not use em dashes or emojis in any file content, comment, or commit message. Do not hardcode secrets; public SSH keys are not secret but use placeholders in docs.

---

### Task 1: dotctl `version` subcommand (+ ldflags)

**Files:**

- Modify: `dotctl/main.go` (register `version`; add a `version` var)
- Test: `dotctl/version_test.go`

**Interfaces:**

- Produces: a package-level `var version = "dev"` overridable at build time via `-ldflags "-X main.version=..."`; `case "version"` prints it. `ensure_dotctl` (Task 5) compares `dotctl version` output to the pin.

- [ ] **Step 1: Write the failing test**

```go
package main

import (
 "bytes"
 "strings"
 "testing"
)

func TestDispatchVersion(t *testing.T) {
 var out bytes.Buffer
 if code := dispatch([]string{"version"}, &out, &out); code != 0 {
  t.Fatalf("exit=%d, want 0", code)
 }
 if strings.TrimSpace(out.String()) == "" {
  t.Fatal("version printed nothing")
 }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dotctl && go test ./... -run TestDispatchVersion -v` -> FAIL (unknown command).

- [ ] **Step 3: Implement**

In `main.go`, add at package level `var version = "dev"`, and in `dispatch`'s switch:

```go
 case "version":
  fmt.Fprintln(stdout, version)
  return 0
```

Add `version` to the usage string.

- [ ] **Step 4: Run test** -> PASS. Then `go test ./...` (whole suite green).

- [ ] **Step 5: Commit**

```bash
git add dotctl/main.go dotctl/version_test.go
git commit -m "feat(dotctl): version subcommand for the release pin"
```

---

### Task 2: dotctl `verify --skip`

**Files:**

- Modify: `dotctl/verify.go` (add `-skip` flag), `dotctl/verify_test.go`

**Interfaces:**

- Produces: `runVerify` accepts `-skip <comma-list>` naming local checks to skip (`stow`, `shell`, and/or `packages`). Skipped checks print a `verify: skipped <name>` line and add no error. Used by the container e2e (no stow symlinks, non-zsh shell in a bare container).

- [ ] **Step 1: Write the failing test**

```go
func TestVerifySkipParsing(t *testing.T) {
 got := parseSkips("stow,shell")
 if !got["stow"] || !got["shell"] || got["packages"] {
  t.Fatalf("parseSkips = %v", got)
 }
 if len(parseSkips("")) != 0 {
  t.Fatal("empty skip should be empty set")
 }
}
```

- [ ] **Step 2: Run** -> FAIL (`parseSkips` undefined).

- [ ] **Step 3: Implement**

Add `func parseSkips(s string) map[string]bool` (reuse the `splitList` shape). Add a `-skip` string flag to `runVerify`; before each of the stow/shell/packages local checks, `if skips["stow"] { fmt.Fprintln(stdout, "verify: skipped stow"); } else { ...run check... }` (same for shell/packages).

- [ ] **Step 4: Run** the new test and `go test ./...` -> all PASS.

- [ ] **Step 5: Commit**

```bash
git add dotctl/verify.go dotctl/verify_test.go
git commit -m "feat(dotctl): verify --skip for bare-container checks"
```

---

### Task 3: root-aware install (no sudo when euid 0)

**Files:**

- Modify: `dotctl/install.go` (drop the `sudo` prefix when running as root), `dotctl/install_test.go`

**Interfaces:**

- Produces: `installCmd(plat, r)` gains awareness of effective uid. Signature becomes `installCmd(plat Platform, r Resolved, root bool)`; when `root` is true, the pacman/apt-get argvs omit `sudo` (yay/brew never had it). `Install` passes `os.Geteuid() == 0`. This lets the container e2e (root, no sudo) and root users work, while non-root keeps `sudo`.

- [ ] **Step 1: Write the failing test**

```go
func TestInstallCmdRootDropsSudo(t *testing.T) {
 got := installCmd(PlatformUbuntu, Resolved{Name: "bat", Kind: KindNormal}, true)
 want := [][]string{{"apt-get", "install", "-y", "bat"}}
 if !reflect.DeepEqual(got, want) {
  t.Fatalf("root ubuntu = %v, want %v (no sudo)", got, want)
 }
 got = installCmd(PlatformArch, Resolved{Name: "bat", Kind: KindNormal}, false)
 want = [][]string{{"sudo", "pacman", "-S", "--needed", "--noconfirm", "bat"}}
 if !reflect.DeepEqual(got, want) {
  t.Fatalf("non-root arch = %v, want sudo-prefixed", got)
 }
}
```

- [ ] **Step 2: Run** -> FAIL (arity). Update the existing `TestInstallCmd`/`Install` call sites for the new `root` parameter (non-root = current behavior).

- [ ] **Step 3: Implement**

Add a `root bool` param to `installCmd`; guard the `sudo` prefix on the pacman and apt-get branches with `if !root`. In `Install`, compute `root := os.Geteuid() == 0` once and pass it. Update all callers and the existing tests to the new signature (existing tests pass `false`).

- [ ] **Step 4: Run** `go test ./...` -> all PASS (existing sudo tests still green with `false`).

- [ ] **Step 5: Commit**

```bash
git add dotctl/install.go dotctl/install_test.go
git commit -m "fix(dotctl): omit sudo for installs when running as root"
```

---

### Task 4: bootstrap.sh safe scaffold

**Files:**

- Modify: the repo bootstrap script (locate first; expected `stow-packages/bootstrap.sh`)
- Test: `bash -n` + `shellcheck`

**Interfaces:**

- Produces: `log()`, `have(cmd)`, `fetch(url,dest)` (curl-or-wget), and a `main()` filled in later. Logging tees to `$STATE_DIR/bootstrap.log`.

- [ ] **Step 1: Locate the script**

Run: `git ls-files | grep -n bootstrap.sh` (expected `stow-packages/bootstrap.sh`). Use that path as `$BOOTSTRAP`.

- [ ] **Step 2: Write the scaffold**

```bash
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

fetch() { # fetch <url> <dest>; returns non-zero on failure
  if have curl; then curl -fsSL "$1" -o "$2"
  elif have wget; then wget -qO "$2" "$1"
  else log "FATAL: need curl or wget"; return 1
  fi
}

main() {
  log "dotfiles bootstrap starting ($(uname -s) $(uname -m))"
  # steps wired in Task 7
}

main "$@"
```

- [ ] **Step 3: Gate** `bash -n "$BOOTSTRAP" && shellcheck "$BOOTSTRAP"` (address any SC warnings, e.g. avoid `cat file | tee`; here `printf | tee` is fine).

- [ ] **Step 4: Commit**

```bash
git add "$BOOTSTRAP"
git commit -m "refactor(bootstrap): slim scaffold with logging and curl/wget fetch"
```

---

### Task 5: ensure_dotctl (fetch + verify + PATH + version recheck)

**Files:**

- Modify: `$BOOTSTRAP`; Create: `dotctl/VERSION`
- Test: `bash -n` + `shellcheck` + mocked mismatch smoke

**Interfaces:**

- Produces: `ensure_dotctl()` that reads the pin from `dotctl/VERSION`, and if the installed `dotctl version` already equals the pin, returns; otherwise downloads the asset, verifies sha256 against `dotctl/checksums/<version>.txt` (robust awk parse), strips quarantine, installs to `~/.local/bin/dotctl`, prepends `~/.local/bin` to PATH.

- [ ] **Step 1: Add the pin**

`dotctl/VERSION` = `v0.1.0`.

- [ ] **Step 2: Write ensure_dotctl** (before `main`)

```bash
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
  version="$(cat "$DOTFILES_ROOT/dotctl/VERSION")"
  if [ -x "$DOTCTL" ] && [ "$("$DOTCTL" version 2>/dev/null)" = "$version" ]; then
    log "dotctl $version already installed"; return 0
  fi
  mkdir -p "$BIN_DIR"
  asset="$(asset_name)" || { log "FATAL: unsupported platform for dotctl asset"; return 1; }
  tmp="$(mktemp -d)"
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
```

- [ ] **Step 3: Gate + mocked smoke**

`bash -n` + `shellcheck`. Then a no-network smoke: create `dotctl/checksums/v0.1.0.txt` with a bogus line, add a `FETCH_OVERRIDE` env hook in `fetch` (if set, `cp "$FETCH_OVERRIDE" "$2"`), point it at a local file, and confirm `ensure_dotctl` returns non-zero on checksum mismatch. Document the hook in a comment.

- [ ] **Step 4: Commit**

```bash
git add "$BOOTSTRAP" dotctl/VERSION
git commit -m "feat(bootstrap): ensure_dotctl fetch, pinned sha256 verify, version recheck"
```

---

### Task 6: ensure_pkg_mgr

**Files:** Modify `$BOOTSTRAP`. Test: `bash -n` + `shellcheck`.

**Interfaces:**

- Produces: `ensure_pkg_mgr()` that on macOS installs Homebrew non-interactively AND puts it on PATH (`brew shellenv`), re-asserting `have brew`; on Arch warns (does not fail) if `yay` is missing (the required set is official pacman); Debian/Ubuntu is a no-op.

- [ ] **Step 1: Write ensure_pkg_mgr**

```bash
ensure_pkg_mgr() {
  case "$(uname -s)" in
    Darwin)
      have brew && return 0
      log "installing Homebrew (non-interactive)"
      local installer; installer="$(mktemp)"
      fetch "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh" "$installer" \
        || { log "FATAL: could not download Homebrew installer"; return 1; }
      NONINTERACTIVE=1 /bin/bash "$installer" || { log "FATAL: Homebrew install failed"; return 1; }
      # put brew on PATH for the rest of this run (Apple Silicon: /opt/homebrew)
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
```

- [ ] **Step 2: Gate** `bash -n` + `shellcheck`.

- [ ] **Step 3: Commit**

```bash
git add "$BOOTSTRAP"
git commit -m "feat(bootstrap): ensure_pkg_mgr with brew shellenv and non-fatal yay"
```

---

### Task 7: main flow, shell, and key handling

**Files:** Modify `$BOOTSTRAP`. Test: `bash -n` + `shellcheck` + mocked dry-run.

**Interfaces:**

- Produces: `set_default_shell()` (non-fatal, `sudo -n` guarded, `${USER_NAME}`), `print_key_instructions()`, `stow_all()` (captures per-package rc, backs up conflicts), `setup_ssh_key()` (gated on absence of a 1Password signer; `mkdir -p ~/.ssh`), and an rc-accumulating `main()` that exits non-zero if any step failed.

- [ ] **Step 1: Write the helpers**

```bash
uses_1password_signing() {
  # this machine signs via 1Password if config.machine names op-ssh-sign
  grep -q 'op-ssh-sign' "$HOME/.config/git/config.machine" 2>/dev/null
}

setup_ssh_key() {
  mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
  if uses_1password_signing; then
    log "1Password signing detected (config.machine); skipping on-disk key generation"
    return 0
  fi
  [ -f "$HOME/.ssh/id_ed25519" ] || ssh-keygen -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519"
  "$DOTCTL" allowed-signers add \
    -file "$DOTFILES_ROOT/stow-packages/git/.config/git/allowed_signers" \
    -email "$(git config user.email)" -pubkey "$HOME/.ssh/id_ed25519.pub" || true
  print_key_instructions
}

print_key_instructions() {
  log "== Register ~/.ssh/id_ed25519.pub on GitHub in BOTH Authentication and Signing sections: https://github.com/settings/keys"
  [ -f "$HOME/.ssh/id_ed25519.pub" ] && log "$(cat "$HOME/.ssh/id_ed25519.pub")"
}

stow_all() {
  local rc=0 p
  ( cd "$DOTFILES_ROOT/stow-packages" || return 1
    for p in */; do
      p="${p%/}"
      git -C "$DOTFILES_ROOT" check-ignore "$p" >/dev/null 2>&1 && continue
      stow -v -t "$HOME" --ignore='\.DS_Store' "$p" || { echo "stow conflict: $p" >&2; rc=1; }
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
```

- [ ] **Step 2: Wire main() with rc accumulation**

```bash
main() {
  local rc=0
  log "dotfiles bootstrap starting ($(uname -s) $(uname -m))"
  ensure_dotctl  || { log "FATAL: could not obtain dotctl"; exit 1; }
  ensure_pkg_mgr || { log "FATAL: package manager unavailable"; exit 1; }
  "$DOTCTL" check || { log "FATAL: preflight failed"; exit 1; }
  "$DOTCTL" install -file "$DOTFILES_ROOT/packages.csv" ${DOTCTL_YES:+--yes} || rc=1
  stow_all || rc=1
  "$DOTCTL" gitconfig ${DOTCTL_EMAIL:+-email "$DOTCTL_EMAIL"} || rc=1
  setup_ssh_key || rc=1
  set_default_shell || rc=1
  "$DOTCTL" verify || rc=1   # local checks; GitHub checks pending until key registered
  if [ "$rc" -eq 0 ]; then log "bootstrap OK"; else log "bootstrap finished with errors (rc=$rc); see $LOG"; fi
  log "after registering the key on GitHub, run: git -C '$DOTFILES_ROOT' remote set-url origin git@github.com:ian-bartholomew/dotfiles.git && '$DOTCTL' verify --remote"
  exit "$rc"
}
```

Note: `dotctl gitconfig` prompts for email interactively in a normal (attended) bootstrap; `DOTCTL_EMAIL`/`DOTCTL_YES` make it scriptable.

- [ ] **Step 3: Gate + mocked dry-run**

`bash -n` + `shellcheck`. Then stub `dotctl`, `stow`, `ssh-keygen`, `chsh`, `git` as fake PATH executables that echo+exit 0, run `bash "$BOOTSTRAP"` in a temp HOME (with `config.machine` present in one run and absent in another) and confirm: correct ordering; `setup_ssh_key` skips keygen when `config.machine` has op-ssh-sign; non-interactive run does not hang; `main` exits 0 on all-success and non-zero when a stub returns non-zero.

- [ ] **Step 4: Commit**

```bash
git add "$BOOTSTRAP"
git commit -m "feat(bootstrap): rc-accumulating flow, gated ssh key, safe shell change, stow conflict handling"
```

---

### Task 8: goreleaser + release workflow

**Files:** Create `dotctl/.goreleaser.yaml`, `.github/workflows/release.yml`. Test: `goreleaser check` + `goreleaser release --snapshot --clean`.

- [ ] **Step 1: `.goreleaser.yaml`** (no `dir:` since the workflow sets `workdir: dotctl`; `formats:`; version ldflags)

```yaml
version: 2
project_name: dotctl
builds:
  - main: .
    binary: dotctl
    env: [CGO_ENABLED=0]
    goos: [darwin, linux]
    goarch: [amd64, arm64]
    ldflags:
      - -s -w -X main.version={{ .Tag }}
    hooks:
      post:
        - cmd: sh -c 'case "{{ .Os }}" in darwin) codesign -s - "{{ .Path }}" ;; esac'
archives:
  - formats: [binary]
    name_template: "dotctl_{{ .Os }}_{{ .Arch }}"
checksum:
  name_template: "checksums.txt"
```

- [ ] **Step 2: `.github/workflows/release.yml`** (pin goreleaser)

```yaml
name: release
on:
  push:
    tags: ["v*"]
permissions:
  contents: write
jobs:
  release:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: "1.22" }
      - uses: goreleaser/goreleaser-action@v6
        with: { version: "~> v2", args: release --clean, workdir: dotctl }
        env: { GITHUB_TOKEN: "${{ secrets.GITHUB_TOKEN }}" }
```

- [ ] **Step 3: Validate** `cd dotctl && goreleaser check && goreleaser release --snapshot --clean`. Confirm `dotctl/dist/` has the four `dotctl_<os>_<arch>` binaries + `checksums.txt`, and that a built binary reports the injected version (`dist/.../dotctl version`).

- [ ] **Step 4: Commit** (dist/ is gitignored in Task 9)

```bash
git add dotctl/.goreleaser.yaml .github/workflows/release.yml
git commit -m "ci(dotctl): goreleaser config and tag-triggered release"
```

---

### Task 9: CI, gitignore, merge, cut the first release

**Files:** Create `.github/workflows/ci.yml`, `dotctl/.gitignore`, `dotctl/checksums/v0.1.0.txt`.

- [ ] **Step 1: `dotctl/.gitignore`**

```
/dotctl
/dist/
```

- [ ] **Step 2: `.github/workflows/ci.yml`** (go job; shell job)

```yaml
name: ci
on: [push, pull_request]
jobs:
  go:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: "1.22" }
      - run: cd dotctl && gofmt -l . | tee /tmp/f && test ! -s /tmp/f
      - run: cd dotctl && go vet ./... && go test ./...
      - run: cd dotctl && go run . lint -file ../packages.csv
  shell:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: sudo apt-get update && sudo apt-get install -y shellcheck
      - run: shellcheck stow-packages/bootstrap.sh
```

(The `e2e` job is added in Task 10.)

- [ ] **Step 3: Verify CI green on the branch**, then get the branch merged to `main` (open the PR, let CI pass, merge). The release is cut from `main` so fresh clones match.

- [ ] **Step 4: Cut the first release from main and pin its checksums**

```bash
git checkout main && git pull
git tag v0.1.0 && git push origin v0.1.0
# after the release workflow publishes:
mkdir -p dotctl/checksums
gh release download v0.1.0 -p checksums.txt -O dotctl/checksums/v0.1.0.txt
git add dotctl/checksums/v0.1.0.txt
git commit -m "chore(dotctl): pin v0.1.0 release checksums"
git push
```

(This is an outward action; do it only with explicit go-ahead.)

- [ ] **Step 5: Real fetch smoke** against the cut release: on a scratch checkout, run `ensure_dotctl` for real (no override) and confirm it downloads, checksum-verifies, and `dotctl version` prints `v0.1.0`. This is the first real exercise of the download/verify path.

---

### Task 10: containerized end-to-end tests

**Files:** Create `dotctl/test/Dockerfile`, `dotctl/test/e2e.sh`, `dotctl/test/Makefile`; modify `.github/workflows/ci.yml`.

**Interfaces:**

- Produces: a real-distro e2e that builds `dotctl` from source (Go via a pinned tarball, since Debian 12 ships Go 1.19), runs `dotctl install --yes` as root (root-aware, no sudo needed), and asserts `dotctl verify --skip=stow,shell` passes. Linux only (macOS is a separate manual/macOS-runner smoke).

- [ ] **Step 1: `dotctl/test/e2e.sh`**

```bash
#!/usr/bin/env bash
set -uo pipefail
fail() { echo "E2E FAIL: $*" >&2; exit 1; }
cd /repo/dotctl || fail "repo not mounted at /repo"
go build -o /usr/local/bin/dotctl . || fail "go build"
dotctl lint -file /repo/packages.csv          || fail "lint"
dotctl check                                  || fail "check"
dotctl install -file /repo/packages.csv --yes || fail "install (required-only)"
for b in git jq tmux nvim go; do command -v "$b" >/dev/null 2>&1 || fail "required binary missing: $b"; done
dotctl verify -file /repo/packages.csv --skip=stow,shell || fail "verify (local, minus stow/shell)"
echo "E2E OK on $(. /etc/os-release; echo "$ID")"
```

- [ ] **Step 2: `dotctl/test/Dockerfile`** (pinned Go tarball; arch-aware)

```dockerfile
ARG BASE=ubuntu:24.04
FROM ${BASE}
ARG GO_VERSION=1.22.12
RUN if command -v apt-get >/dev/null; then apt-get update && apt-get install -y git curl ca-certificates; \
    elif command -v pacman >/dev/null; then pacman -Sy --noconfirm git curl; fi
RUN set -eux; a="$(uname -m)"; case "$a" in x86_64) g=amd64;; aarch64|arm64) g=arm64;; esac; \
    curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${g}.tar.gz" | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:${PATH}"
COPY . /repo
RUN chmod +x /repo/dotctl/test/e2e.sh
CMD ["/repo/dotctl/test/e2e.sh"]
```

- [ ] **Step 3: `dotctl/test/Makefile`**

```make
REPO := $(shell cd ../.. && pwd)
e2e: e2e-ubuntu e2e-debian e2e-arch
e2e-%:
 docker build -t dotctl-e2e-$* --build-arg BASE=$(BASE_$*) -f Dockerfile $(REPO) && docker run --rm dotctl-e2e-$*
BASE_ubuntu := ubuntu:24.04
BASE_debian := debian:12
BASE_arch := archlinux:latest
```

- [ ] **Step 4: Run locally** `cd dotctl/test && make e2e-ubuntu e2e-debian e2e-arch` (needs Docker). Each prints `E2E OK on <id>`. Fix any package-name resolution failures surfaced here (this is where a wrong apt/pacman name in `packages.csv` shows up).

- [ ] **Step 5: Add the CI e2e job** to `.github/workflows/ci.yml`

```yaml
  e2e:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        base: ["ubuntu:24.04", "debian:12", "archlinux:latest"]
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t dotctl-e2e --build-arg BASE=${{ matrix.base }} -f dotctl/test/Dockerfile .
      - run: docker run --rm dotctl-e2e
```

- [ ] **Step 6: Verify + commit**

```bash
git add dotctl/test/Dockerfile dotctl/test/e2e.sh dotctl/test/Makefile .github/workflows/ci.yml
git commit -m "test(dotctl): containerized cross-distro end-to-end harness"
```

---

### Task 11: migration cutover (idempotent, backup-guarded)

**Files:** delete `install.sh` (tracked) and `preflight.sh` (untracked); update README refs. Per-machine files (`~/.config/git/config.machine`, `~/.ssh/config.local`) are already seeded on this machine (Plan 2), so this task is idempotent about them.

**Interfaces:**

- Produces: the cutover, with signing preserved and nothing clobbered.

- [ ] **Step 1: Ensure per-machine overrides exist (idempotent, backup-guarded)**

```bash
mkdir -p ~/.config/git
if [ ! -f ~/.config/git/config.machine ]; then
  # only on a machine that signs via 1Password; seed with THIS machine's values
  cat > ~/.config/git/config.machine <<'EOF'
[gpg "ssh"]
 program = "/Applications/1Password.app/Contents/MacOS/op-ssh-sign"
[user]
 signingkey = <your 1Password ssh signing public key>
EOF
  echo "seeded config.machine (edit signingkey to your 1Password pubkey)"
else
  echo "config.machine already present; leaving it untouched"
fi
```

(On this machine it already exists and holds the real values, so this is a no-op. Do NOT hardcode the real key in the committed plan.)

- [ ] **Step 2: Confirm signing works BEFORE deleting anything**

Run: `git config --global --includes --get user.signingkey` and `... --get gpg.ssh.program` resolve to the 1Password values; and `dotctl verify` local checks are green. If not, stop and fix `config.machine` (restore from the migration backup) before proceeding.

- [ ] **Step 3: Retire the old scripts (locate tracked vs untracked)**

```bash
git rm --ignore-unmatch install.sh preflight.sh    # removes whatever is tracked
rm -f preflight.sh                                  # remove if it was untracked on disk
```

Then update any README/docs references to point at `dotctl` + the new `bootstrap.sh`.

- [ ] **Step 4: Final verify and commit (explicit paths, no `git add -A`)**

Run `dotctl verify` (local green) and, after the key is registered, `dotctl verify --remote`.

```bash
git add -- install.sh preflight.sh README.md   # only the intended paths (git handles the deletions)
git commit -m "chore(bootstrap): cut over to dotctl; retire install.sh and preflight.sh"
```

Rollback: if anything regresses, `git revert` this commit restores the old scripts, and the migration backup restores `config.machine`.

---

## Self-Review

**1. Spec coverage:** ensure_dotctl fetch + sha256-over-TLS + PATH + version-recheck: Task 5. ensure_pkg_mgr (brew shellenv, non-fatal yay, apt no-op): Task 6. rc-accumulating flow + gated ssh key + safe shell + stow conflicts: Task 7. goreleaser + release: Task 8. CI + gitignore + merge + release + pinned checksums + real fetch smoke: Task 9. Container e2e (pinned Go, root-aware install, verify --skip): Task 10. Idempotent, backup-guarded migration: Task 11. Supporting dotctl changes the bootstrap/CI need: `version` (Task 1), `verify --skip` (Task 2), root-aware install (Task 3).

**2. Placeholder scan:** No TBD/TODO. `config.machine` signingkey is an intentional `<placeholder>` (do not commit the real key; it is a public key but kept out of the doc). The `FETCH_OVERRIDE` hook (Task 5) is a real, documented test seam.

**3. Consistency:** `$DOTCTL`/`$BIN_DIR`/`$DOTFILES_ROOT`/`asset_name`/`USER_NAME` used consistently. The `version` subcommand (Task 1) is what `ensure_dotctl` (Task 5) compares against `dotctl/VERSION`. `verify --skip` (Task 2) is what the e2e (Task 10) calls. Root-aware `installCmd` (Task 3) is what lets the root container skip sudo. Sequencing: dotctl code changes (1-3) land before the bash that depends on them; e2e (10) before the cutover (11); release cut from main (9) before the shim is relied on.

**Adversarial review:** 4 reviewers, all reject on the prior draft; every confirmed finding folded in (see Revision note). One false positive (undefined `assert` in e2e.sh) was an artifact of the review prompt, not the plan. Accepted deviations unchanged: sha256-over-TLS (not signing); `pacman -Syu` blast radius on Arch.
