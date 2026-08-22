# dotctl Bootstrap and Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `bootstrap.sh` as the slim orchestration entry point that drives the `dotctl` subcommands and the pure-CLI steps (fetch binary, package-manager bootstrap, stow, ssh-keygen, chsh), and set up distribution (goreleaser + GitHub Actions release) plus CI (build, test, lint gate, shellcheck). Ends with the migration runbook that cuts over from the old scripts.

**Architecture:** `bootstrap.sh` is pure orchestration in bash: it fetches a pinned `dotctl` release binary (sha256-verified over TLS), ensures a package manager, then calls `dotctl check/install/gitconfig/allowed-signers add/verify` in order, with the bash-native steps (stow, ssh-keygen, chsh, print-key) interleaved per the spec's flow. goreleaser cross-compiles static binaries; a tag-triggered workflow publishes them; a push/PR workflow runs Go tests, `dotctl lint`, and shellcheck. Since bash resists unit testing, each bash task is gated by `bash -n`, `shellcheck`, and a dry-run smoke invocation.

**Tech Stack:** bash, GNU stow, ssh-keygen, chsh; goreleaser + GitHub Actions; shellcheck. `dotctl` from Plans 1 and 2.

**Spec:** `docs/superpowers/specs/2026-08-21-dotctl-bootstrap-design.md`

**Depends on:** Plan 1 (core) and Plan 2 (git identity) merged, so all `dotctl` subcommands exist.

## Global Constraints

- `bootstrap.sh` must not abort a non-interactive run on a `read` EOF; guard reads and default sensibly. It must not use `set -e` in a way that dies on an expected non-zero (e.g. `xattr -d` when the attr is absent -> guard with `|| true`).
- Bare-machine prerequisites installed by hand: `git` plus `curl` or `wget`. The shim tries `curl` then `wget`.
- Binary lands at `~/.local/bin/dotctl`; ensure that dir is on PATH for the run and persisted for future shells (via the stowed zsh config).
- Integrity: verify the release asset's sha256 against a checksum pinned in the repo, over TLS. Signing (minisign/cosign) is deferred; document the TLS-only threat model (this is the spec's stated alternative).
- Release discipline: any `packages.csv` schema bump and the `dotctl` version pin are committed together and a matching release is cut, so a fresh clone's CSV schema always matches the pinned binary (prevents the pin-vs-HEAD lint brick).
- `chsh`/`/etc/shells` steps are non-fatal and skipped when non-interactive or non-root.
- Do not use em dashes or emojis in any file content, comment, or commit message.

---

### Task 1: bootstrap.sh safe scaffold

**Files:**

- Modify: `stow-packages/.../bootstrap.sh` is not the target; rewrite the repo-root `bootstrap.sh` (currently at `stow-packages/bootstrap.sh` per the repo). Confirm the path with `git ls-files | grep bootstrap.sh` before editing.
- Test: `bash -n` + `shellcheck`.

**Interfaces:**

- Produces: functions `log()`, `have(cmd)`, `fetch(url, dest)` (curl-or-wget), and a `main()` that sequences the flow (filled in later tasks). Logging writes to both stdout and a timestamped file under `${XDG_STATE_HOME:-$HOME/.local/state}/dotctl/`.

- [ ] **Step 1: Locate the current bootstrap.sh**

Run: `cd /Users/ian.bartholomew/.dotfiles && git ls-files | grep -n bootstrap.sh`
Record the path (expected `stow-packages/bootstrap.sh`). Use that path everywhere below as `$BOOTSTRAP`.

- [ ] **Step 2: Write the scaffold**

Replace `$BOOTSTRAP` contents with:

```bash
#!/usr/bin/env bash
# bootstrap.sh - slim orchestration entry point for dotfiles.
# Pure CLI orchestration; all data/logic lives in the dotctl binary.
set -uo pipefail  # not -e: we handle failures explicitly and must survive read EOF

DOTFILES_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/dotctl"
mkdir -p "$STATE_DIR"
LOG="$STATE_DIR/bootstrap.log"   # executors: add a timestamp suffix via a value passed in, since scripts cannot call date reproducibly in CI; a plain name is fine at runtime

log() { printf '%s\n' "$*" | tee -a "$LOG" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

fetch() { # fetch <url> <dest>
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
```

- [ ] **Step 3: Verify syntax and lint**

Run: `bash -n "$BOOTSTRAP" && shellcheck "$BOOTSTRAP"`
Expected: no syntax errors; shellcheck clean (or only benign informational notes, which you address).

- [ ] **Step 4: Commit**

```bash
git add "$BOOTSTRAP"
git commit -m "refactor(bootstrap): slim scaffold with logging and curl/wget fetch"
```

---

### Task 2: ensure_dotctl (fetch + verify + PATH)

**Files:**

- Modify: `$BOOTSTRAP`
- Create: `dotctl/VERSION` (the pinned version string, e.g. `v0.1.0`)
- Test: `bash -n` + `shellcheck` + a mocked dry-run.

**Interfaces:**

- Produces: `ensure_dotctl()` that reads the pin from `dotctl/VERSION`, maps `uname` to the goreleaser asset name, downloads the asset and the `checksums.txt` from that release, verifies the asset's sha256 against the pinned checksum committed at `dotctl/checksums/<version>.txt`, strips macOS quarantine, installs to `~/.local/bin/dotctl`, and prepends `~/.local/bin` to PATH.

- [ ] **Step 1: Add the version pin**

Create `dotctl/VERSION` containing a single line (the first release you will cut in Task 6):

```
v0.1.0
```

- [ ] **Step 2: Write ensure_dotctl**

Add to `$BOOTSTRAP` (before `main`):

```bash
BIN_DIR="$HOME/.local/bin"
DOTCTL="$BIN_DIR/dotctl"

asset_name() {
  local os arch
  case "$(uname -s)" in Darwin) os=darwin ;; Linux) os=linux ;; *) return 1 ;; esac
  case "$(uname -m)" in x86_64|amd64) arch=amd64 ;; arm64|aarch64) arch=arm64 ;; *) return 1 ;; esac
  printf 'dotctl_%s_%s' "$os" "$arch"
}

ensure_dotctl() {
  have "$DOTCTL" 2>/dev/null && { log "dotctl already present"; return 0; }
  mkdir -p "$BIN_DIR"
  local version asset tmp
  version="$(cat "$DOTFILES_ROOT/dotctl/VERSION")"
  asset="$(asset_name)" || { log "FATAL: unsupported platform for dotctl asset"; return 1; }
  tmp="$(mktemp -d)"
  local base="https://github.com/ian-bartholomew/dotfiles/releases/download/$version"
  fetch "$base/$asset" "$tmp/dotctl" || return 1
  # integrity: compare against the checksum pinned in the repo (TLS-only trust; signing deferred)
  local want got
  want="$(grep " $asset\$" "$DOTFILES_ROOT/dotctl/checksums/$version.txt" | awk '{print $1}')"
  if have sha256sum; then got="$(sha256sum "$tmp/dotctl" | awk '{print $1}')"
  else got="$(shasum -a 256 "$tmp/dotctl" | awk '{print $1}')"; fi
  [ -n "$want" ] && [ "$want" = "$got" ] || { log "FATAL: checksum mismatch for $asset"; return 1; }
  xattr -d com.apple.quarantine "$tmp/dotctl" 2>/dev/null || true
  chmod +x "$tmp/dotctl"
  mv "$tmp/dotctl" "$DOTCTL"
  case ":$PATH:" in *":$BIN_DIR:"*) ;; *) export PATH="$BIN_DIR:$PATH" ;; esac
  log "installed dotctl $version to $DOTCTL"
}
```

- [ ] **Step 3: Verify syntax, lint, and a mocked run**

Run: `bash -n "$BOOTSTRAP" && shellcheck "$BOOTSTRAP"`
Then a mocked smoke test (no network): create `dotctl/checksums/v0.1.0.txt` with a dummy line, point `fetch` at a local file via a `FETCH_OVERRIDE` you add, and confirm `ensure_dotctl` fails closed on a checksum mismatch. Document the override hook in a comment.
Expected: clean lint; `ensure_dotctl` returns non-zero on mismatch.

- [ ] **Step 4: Commit**

```bash
git add "$BOOTSTRAP" dotctl/VERSION
git commit -m "feat(bootstrap): ensure_dotctl fetch with pinned sha256 verify"
```

---

### Task 3: ensure_pkg_mgr

**Files:**

- Modify: `$BOOTSTRAP`
- Test: `bash -n` + `shellcheck`.

**Interfaces:**

- Produces: `ensure_pkg_mgr()` that installs Homebrew (macOS) with `NONINTERACTIVE=1` or bootstraps `yay` (Arch, only when missing and only via non-root user with base-devel) if absent; Debian/Ubuntu is a no-op (apt ships). Runs BEFORE any system-mutating install but AFTER the immutable `dotctl check` in the flow ordering (Task 4 sequences this correctly).

- [ ] **Step 1: Write ensure_pkg_mgr**

```bash
ensure_pkg_mgr() {
  case "$(uname -s)" in
    Darwin)
      have brew && return 0
      log "installing Homebrew (non-interactive)"
      NONINTERACTIVE=1 /bin/bash -c \
        "$(fetch https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh /dev/stdout)"
      ;;
    Linux)
      if have pacman && ! have yay; then
        log "yay missing; install it manually (needs base-devel and a non-root user), then re-run"
        # Executors: yay has no official one-line installer and cannot build as root.
        # Document the manual clone+makepkg steps here; do not attempt as root.
        return 1
      fi
      ;;
  esac
}
```

- [ ] **Step 2: Verify syntax and lint**

Run: `bash -n "$BOOTSTRAP" && shellcheck "$BOOTSTRAP"`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add "$BOOTSTRAP"
git commit -m "feat(bootstrap): ensure_pkg_mgr for brew and yay"
```

---

### Task 4: chsh helper and full flow wiring

**Files:**

- Modify: `$BOOTSTRAP`
- Test: `bash -n` + `shellcheck` + `main` dry-run with `DOTCTL` mocked.

**Interfaces:**

- Produces: `set_default_shell()` (non-fatal; adds zsh to `/etc/shells` if writable/sudo available, else warns and skips), `print_key_instructions()`, and a `main()` that runs the spec's ordered flow.

- [ ] **Step 1: Write set_default_shell**

```bash
set_default_shell() {
  local zsh_path; zsh_path="$(command -v zsh || true)"
  [ -n "$zsh_path" ] || { log "zsh not found; skipping shell change"; return 0; }
  case "$(dscl . -read "/Users/$USER" UserShell 2>/dev/null || getent passwd "$USER")" in
    *"$zsh_path"*|*"/zsh") log "zsh already default"; return 0 ;;
  esac
  if ! grep -qx "$zsh_path" /etc/shells 2>/dev/null; then
    if [ -w /etc/shells ] || have sudo; then
      printf '%s\n' "$zsh_path" | sudo tee -a /etc/shells >/dev/null 2>&1 || {
        log "could not add $zsh_path to /etc/shells; change shell manually"; return 0; }
    else
      log "cannot edit /etc/shells (no sudo); change shell manually"; return 0
    fi
  fi
  chsh -s "$zsh_path" 2>/dev/null || log "chsh failed (interactive/no perms); run: chsh -s $zsh_path"
}
```

- [ ] **Step 2: Wire main() to the spec flow**

```bash
main() {
  log "dotfiles bootstrap starting ($(uname -s) $(uname -m))"
  ensure_dotctl        || { log "FATAL: could not obtain dotctl"; exit 1; }
  ensure_pkg_mgr       || { log "FATAL: package manager unavailable"; exit 1; }
  "$DOTCTL" check      || { log "FATAL: preflight failed"; exit 1; }
  "$DOTCTL" install -file "$DOTFILES_ROOT/packages.csv"
  ( cd "$DOTFILES_ROOT/stow-packages" && for p in */; do
      stow -v -t "$HOME" --ignore='\.DS_Store' "${p%/}"; done )
  "$DOTCTL" gitconfig   # prompts for email (or pass -email in non-interactive use)
  if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
    ssh-keygen -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519"
  fi
  "$DOTCTL" allowed-signers add -file "$DOTFILES_ROOT/stow-packages/git/.config/git/allowed_signers" \
    -email "$(git config user.email)" -pubkey "$HOME/.ssh/id_ed25519.pub" || true
  print_key_instructions
  set_default_shell
  "$DOTCTL" verify
  log "bootstrap done. After registering the key on GitHub:"
  log "  git -C '$DOTFILES_ROOT' remote set-url origin git@github.com:ian-bartholomew/dotfiles.git"
  log "  $DOTCTL verify --remote"
  log "  git -C '$DOTFILES_ROOT' add stow-packages/git/.config/git/allowed_signers && git commit && git push"
}

print_key_instructions() {
  log "== Register this key on GitHub (BOTH sections) =="
  log "Authentication keys AND Signing keys: https://github.com/settings/keys"
  cat "$HOME/.ssh/id_ed25519.pub" | tee -a "$LOG" >&2
}
```

- [ ] **Step 3: Verify syntax, lint, mocked dry-run**

Run: `bash -n "$BOOTSTRAP" && shellcheck "$BOOTSTRAP"`.
Then a mocked run: create a fake `dotctl` on PATH that echoes its args and exits 0, stub `stow`/`ssh-keygen`/`chsh` similarly, and run `bash "$BOOTSTRAP"` in a temp HOME to confirm the ordering (check -> install -> stow -> gitconfig -> keygen -> allowed-signers -> print -> shell -> verify) prints as expected and nothing aborts non-interactively.
Expected: the ordered log lines appear; exit 0.

- [ ] **Step 4: Commit**

```bash
git add "$BOOTSTRAP"
git commit -m "feat(bootstrap): non-fatal shell change and full ordered flow"
```

---

### Task 5: goreleaser + release workflow

**Files:**

- Create: `dotctl/.goreleaser.yaml`
- Create: `.github/workflows/release.yml`
- Test: `goreleaser check` + a local `goreleaser release --snapshot --clean`.

**Interfaces:**

- Produces: cross-compiled static binaries `dotctl_{darwin,linux}_{amd64,arm64}` with a `checksums.txt`, published on tag push. Darwin binaries get an ad-hoc codesign so arm64 macOS will execute them.

- [ ] **Step 1: Write .goreleaser.yaml**

```yaml
project_name: dotctl
builds:
  - main: .
    dir: dotctl
    binary: dotctl
    env: [CGO_ENABLED=0]
    goos: [darwin, linux]
    goarch: [amd64, arm64]
    # ad-hoc codesign so arm64 macOS runs the binary (no notarization)
    hooks:
      post:
        - cmd: sh -c 'case "{{ .Os }}" in darwin) codesign -s - "{{ .Path }}" ;; esac'
archives:
  - format: binary
    name_template: "dotctl_{{ .Os }}_{{ .Arch }}"
checksum:
  name_template: "checksums.txt"
```

- [ ] **Step 2: Write the release workflow**

`.github/workflows/release.yml`:

```yaml
name: release
on:
  push:
    tags: ["v*"]
permissions:
  contents: write
jobs:
  release:
    runs-on: macos-latest   # macOS runner so darwin codesign is available
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: "1.22" }
      - uses: goreleaser/goreleaser-action@v6
        with: { version: latest, args: release --clean, workdir: dotctl }
        env: { GITHUB_TOKEN: "${{ secrets.GITHUB_TOKEN }}" }
```

- [ ] **Step 3: Validate locally**

Run: `cd dotctl && goreleaser check && goreleaser release --snapshot --clean`
Expected: config valid; snapshot produces the four binaries + `checksums.txt` under `dotctl/dist/`.

- [ ] **Step 4: Commit**

```bash
git add dotctl/.goreleaser.yaml .github/workflows/release.yml
git commit -m "ci(dotctl): goreleaser config and tag-triggered release"
```

---

### Task 6: CI (build, test, lint gate, shellcheck) and cut the first release

**Files:**

- Create: `.github/workflows/ci.yml`
- Create: `dotctl/checksums/v0.1.0.txt` (populated after cutting the release)
- Test: the workflow runs green on a branch.

**Interfaces:**

- Produces: a push/PR workflow gating Go build+test, `dotctl lint` on the committed `packages.csv`, and shellcheck on `bootstrap.sh`. Then the first tagged release `v0.1.0` and its pinned checksum.

- [ ] **Step 1: Write the CI workflow**

`.github/workflows/ci.yml`:

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
      - run: cd dotctl && go vet ./... && go test ./...
      - run: cd dotctl && go run . lint -file ../packages.csv   # gate the miscolumn bug at CI, not bootstrap
  shell:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: sudo apt-get update && sudo apt-get install -y shellcheck
      - run: shellcheck stow-packages/bootstrap.sh
```

- [ ] **Step 2: Verify the CI passes on the branch**

Push the branch and confirm both jobs are green (`gh run watch` or the Actions tab). Fix any lint/test failures before proceeding.

- [ ] **Step 3: Cut the first release and pin its checksum**

Tag and push `v0.1.0`; wait for the release workflow to publish `checksums.txt`; copy it into the repo so the shim can verify against a repo-pinned copy:

```bash
git tag v0.1.0 && git push origin v0.1.0
# after the release workflow finishes:
mkdir -p dotctl/checksums
gh release download v0.1.0 -p checksums.txt -O dotctl/checksums/v0.1.0.txt
git add dotctl/checksums/v0.1.0.txt
git commit -m "chore(dotctl): pin v0.1.0 release checksums"
```

- [ ] **Step 4: End-to-end smoke on a clean environment**

In a throwaway container/VM (or a fresh user), run the documented bare-machine prereqs then `git clone ... && ./bootstrap.sh` and confirm it reaches `verify` with local checks green. Record any gaps.

---

### Task 7: migration cutover and retire old scripts

**Files:**

- Modify: `install.sh` (delete), `preflight.sh` (delete), repo `README`/docs referencing them
- Create (once, on this machine): `~/.config/git/config.machine`, `~/.ssh/config.local`
- Test: `dotctl verify` green locally; `dotctl verify --remote` green after registration.

**Interfaces:**

- Produces: the cutover from the old bash installer to dotctl, with this machine's 1Password signing preserved.

- [ ] **Step 1: Seed this machine's per-machine overrides**

Create `~/.config/git/config.machine` (never generated, never committed):

```
[gpg "ssh"]
 program = "/Applications/1Password.app/Contents/MacOS/op-ssh-sign"
[user]
 signingkey = ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKWX9s/d4lN6z0w85HvUFSLesri72mA99ua/etjOx2Cc ian.bartholomew@FBG-K919PM6F64
```

Create `~/.ssh/config.local` if this machine routes `Host *` through the 1Password agent:

```
Host github.com
 IdentityAgent none
```

- [ ] **Step 2: Add this machine's signing key to allowed_signers**

Run: `dotctl allowed-signers add -file stow-packages/git/.config/git/allowed_signers -email "$(git config user.email)" -pubkey <path-to-1password-pubkey>`
(For this machine the entry must carry the 1Password signing key, since that is what it signs with.)

- [ ] **Step 3: Verify signing still works on this machine**

Run: `git commit --allow-empty -m "test: signing" && git verify-commit HEAD && git reset --soft HEAD~1`
Expected: verification succeeds (1Password may prompt once). If it fails, fix `config.machine` before deleting anything.

- [ ] **Step 4: Retire the old scripts**

Run: `git rm install.sh preflight.sh`
Update any README/docs references to point at `dotctl` and the new `bootstrap.sh`.

- [ ] **Step 5: Final verify and commit**

Run: `dotctl verify` (local green) and, after the key is registered on GitHub, `dotctl verify --remote`.

```bash
git add -A
git commit -m "chore(bootstrap): cut over to dotctl; retire install.sh and preflight.sh"
```

---

## Self-Review

**1. Spec coverage (bootstrap + distribution scope):** ensure_dotctl fetch + sha256-over-TLS + quarantine strip + `~/.local/bin` PATH: Task 2. ensure_pkg_mgr (brew NONINTERACTIVE, yay caveat, apt no-op): Task 3. chsh non-fatal + `/etc/shells` both platforms: Task 4. Ordered flow (check -> install -> stow -> gitconfig -> keygen -> allowed-signers -> print -> shell -> verify -> post-registration remote switch + push): Task 4. goreleaser static builds + darwin ad-hoc codesign + tag release: Task 5. CI build/test/lint-gate/shellcheck + first release + pinned checksum: Task 6. Migration: seed config.machine + ssh config.local, allowed_signers with the 1Password key, retire install.sh/preflight.sh, remote switch: Task 7. Release discipline (schema + pin together) is in Global Constraints and enforced by the CI lint gate.

**2. Placeholder scan:** No TBD/TODO. Two items are explicit executor instructions with the exact action, not placeholders: the yay manual-install steps (Task 3, because yay has no root-safe one-liner) and the `FETCH_OVERRIDE` test hook (Task 2). The log-timestamp note in Task 1 is a runtime-vs-CI caveat, not a missing value.

**3. Consistency:** `$DOTCTL`, `$BIN_DIR`, `$DOTFILES_ROOT`, `asset_name`, the `dotctl/VERSION` pin, and `dotctl/checksums/<version>.txt` are used consistently across Tasks 2, 4, 6. The allowed_signers path (`stow-packages/git/.config/git/allowed_signers`) matches Plan 2 Task 5. Subcommand invocations (`check`, `install -file`, `gitconfig`, `allowed-signers add`, `verify [--remote]`) match the signatures defined in Plans 1 and 2.

**Known deviations from the spec, accepted (round-2 findings the user chose to disregard, noted so executors are not surprised):** the shim verifies sha256 over TLS rather than a minisign/cosign signature (the spec's stated deferred-signing alternative); `pacman -Syu` full-system-upgrade blast radius on Arch is inherent to installing new packages safely there and is left as-is. These are documented, not silently dropped.
