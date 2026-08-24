# dotctl Bootstrap and Distribution Implementation Plan (final)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `bootstrap.sh` as the slim orchestration entry point that drives `dotctl` plus the pure-CLI steps (fetch binary, package-manager bootstrap, stow, ssh-keygen, chsh), add the small dotctl changes the bootstrap and CI depend on (`version`, `verify --skip`, root-aware install), set up distribution (goreleaser + GitHub Actions) and CI, prove cross-distro parity in containers **before** cutting a release, then cut over from the old scripts.

**Architecture:** `bootstrap.sh` is pure bash orchestration, run from a cloned repo (not `curl | bash`): it fetches a pinned `dotctl` release binary (sha256-verified over TLS against a repo-committed checksum), ensures a package manager, then calls `dotctl check/install/gitconfig/allowed-signers add/verify` in a fail-fast, rc-accumulating flow with the bash-native steps interleaved. The script exits non-zero if any step fails. goreleaser cross-compiles static binaries; a tag-triggered workflow publishes them from `main`; a push/PR workflow runs Go tests, `dotctl lint`, shellcheck, and a container e2e matrix that must be green before the release is cut.

**Tech Stack:** bash, GNU stow, ssh-keygen, chsh; Go (small dotctl additions); goreleaser v2 (local >= 2.4) + GitHub Actions; shellcheck; Podman (podman reads the Dockerfile as-is; pre-installed on GitHub Actions ubuntu-latest). `dotctl` from Plans 1 and 2 (merged on this branch).

**Spec:** `docs/superpowers/specs/2026-08-21-dotctl-bootstrap-design.md`

**Depends on:** Plans 1 and 2 (merged): all `dotctl` subcommands exist; the layered base gitconfig and this machine's `~/.config/git/config.machine` (1Password signing) are already committed/seeded.

## Review history

Two adversarial rounds. Round 1 (4 reviewers) rejected the first draft; all findings folded in. Round 2 (4 reviewers) returned accept-with-changes; those residuals are folded in here: `root bool` threaded through `Install` (not read from ambient euid, which broke Plan-1 tests under root); e2e proven green **before** merge/release; `VERSION` committed in the same post-release commit as the checksums (no broken window); `fetch-depth: 0` on the release checkout; bootstrap obtains the git email (prompt or `DOTCTL_EMAIL`) since `dotctl gitconfig` requires `-email`; `verify --skip=shell` guards the whole `loginShell()` call (`$USER` is empty in containers); real stow conflict backup + corrected `check-ignore` path; Task 11 locates scripts and runs from `$DOTFILES_ROOT`; e2e drops the `go` false-positive assertion; Arch e2e uses `pacman -Syu`; `parseSkips` dropped for `splitList`; both usage strings updated; `ensure_dotctl` cleans up its tempdir; DoD + bad-release rollback added. `gh` is verified present in Debian bookworm main and Ubuntu noble universe (madison), so the required set resolves on the e2e images.

## Global Constraints

- `bootstrap.sh` is run from a cloned repo (`git clone https://github.com/ian-bartholomew/dotfiles && cd dotfiles && ./stow-packages/bootstrap.sh`), NOT `curl | bash` (`DOTFILES_ROOT` derives from `$0`).
- `set -uo pipefail` (not `-e`): survive `read` EOF and expected non-zero (`|| true`), but `main` accumulates an rc and `exit`s non-zero if any real step failed. Never report success on a failed step.
- Bare-machine prerequisites installed by hand: `git` plus `curl` or `wget`.
- Binary lands at `~/.local/bin/dotctl`; that dir is on PATH for the run and persisted for future shells via the stowed zsh config (which must export `~/.local/bin` on PATH).
- Integrity: verify the release asset sha256 against a checksum committed in the repo, over TLS (signing deferred, the spec's alternative).
- Release discipline: the release is cut from `main`; `dotctl/VERSION` and `dotctl/checksums/<version>.txt` are committed together in one post-release commit, so `main` never advertises a version whose release/checksums do not yet exist.
- `chsh`/`/etc/shells` steps are non-fatal, skipped when non-interactive, no passwordless sudo, or already correct.
- No em dashes or emojis in file content, comments, or commit messages. No hardcoded secrets; use placeholders (public keys are not secret but stay out of docs).

## Definition of Done

All CI jobs green on the branch (go: gofmt+vet+test+lint; shell: shellcheck; e2e: ubuntu/debian/arch); branch merged to `main`; `v0.1.0` released from `main` with `dotctl/VERSION` + `dotctl/checksums/v0.1.0.txt` committed; a real (non-mocked) `ensure_dotctl` fetch verified against the release; `dotctl verify` local-green on this machine and `--remote`-green after key registration; `install.sh`/`preflight.sh` retired; this machine's signing still works.

---

### Task 1: dotctl `version` subcommand

**Files:** Modify `dotctl/main.go`; Test `dotctl/version_test.go`.

**Interfaces:** package var `var version = "dev"` overridable via `-ldflags "-X main.version=..."`; `case "version"` prints it. Update BOTH usage strings in `dispatch` (the no-args and unknown-command messages) to include `version`.

- [ ] **Step 1: Failing test**

```go
package main

import ("bytes"; "strings"; "testing")

func TestDispatchVersion(t *testing.T) {
 var out bytes.Buffer
 if code := dispatch([]string{"version"}, &out, &out); code != 0 {
  t.Fatalf("exit=%d, want 0", code)
 }
 if strings.TrimSpace(out.String()) == "" { t.Fatal("version printed nothing") }
}
```

- [ ] **Step 2: Run** -> FAIL (unknown command).
- [ ] **Step 3: Implement** package-level `var version = "dev"`; add `case "version": fmt.Fprintln(stdout, version); return 0`; add `version` to BOTH usage strings.
- [ ] **Step 4: Run** the test and `go test ./...` -> PASS.
- [ ] **Step 5: Commit** `git add dotctl/main.go dotctl/version_test.go && git commit -m "feat(dotctl): version subcommand for the release pin"`

---

### Task 2: dotctl `verify --skip`

**Files:** Modify `dotctl/verify.go`, `dotctl/verify_test.go`.

**Interfaces:** `runVerify` accepts `-skip <csv>` naming local checks to skip: `stow`, `shell`, `packages`. Parse with the EXISTING `splitList` (do not add a `parseSkips`). `--skip=shell` guards the ENTIRE shell block (the `loginShell(...)` call AND `checkDefaultShell`), since `loginShell` errors when `$USER` is empty (bare containers). Each skipped check prints `verify: skipped <name>` and contributes no error.

- [ ] **Step 1: Failing test** asserting `-skip=stow,shell` on a `fakeRunner` yields no stow/shell errors and prints the skip lines, and that with no skip the shell check still runs. (Reuse `splitList` in the assertion.)
- [ ] **Step 2: Run** -> FAIL.
- [ ] **Step 3: Implement**: add `skip := splitList(*skipFlag)`; wrap the shell block `if skip["shell"] { print skipped } else { sh,err := loginShell(...); ...checkDefaultShell... }`; same pattern for the stow-symlink check and the required-packages check.
- [ ] **Step 4: Run** the new test + `go test ./...` -> PASS (existing verify tests unaffected).
- [ ] **Step 5: Commit** `git add dotctl/verify.go dotctl/verify_test.go && git commit -m "feat(dotctl): verify --skip for bare-container checks"`

---

### Task 3: root-aware install (thread `root` through `Install`)

**Files:** Modify `dotctl/install.go`, `dotctl/install_test.go`.

**Interfaces:** `installCmd(plat Platform, r Resolved, root bool)` omits `sudo` on pacman/apt-get when `root` (yay/brew never had it). `Install(plat Platform, plan []Resolved, run Runner, root bool)` takes `root` explicitly (do NOT read `os.Geteuid()` inside `Install`). `runInstall` computes `root := os.Geteuid() == 0` and passes it to `Install`. Update ALL callers and tests: `TestInstallCmd`, `TestInstallRunsEachPackage`, `TestInstallCollectsErrors` pass `false` explicitly so they are deterministic under any ambient euid.

- [ ] **Step 1: Failing test**

```go
func TestInstallCmdRootDropsSudo(t *testing.T) {
 got := installCmd(PlatformUbuntu, Resolved{Name: "bat", Kind: KindNormal}, true)
 if !reflect.DeepEqual(got, [][]string{{"apt-get", "install", "-y", "bat"}}) {
  t.Fatalf("root ubuntu = %v (want no sudo)", got)
 }
 got = installCmd(PlatformArch, Resolved{Name: "bat", Kind: KindNormal}, false)
 if !reflect.DeepEqual(got, [][]string{{"sudo", "pacman", "-S", "--needed", "--noconfirm", "bat"}}) {
  t.Fatalf("non-root arch = %v (want sudo)", got)
 }
}
```

- [ ] **Step 2: Run** -> FAIL (arity). Update `TestInstallCmd` (pass `false`), `TestInstallRunsEachPackage` and `TestInstallCollectsErrors` (call `Install(..., false)`).
- [ ] **Step 3: Implement** the `root bool` param on `installCmd` (guard `sudo` on pacman+apt-get with `if !root`) and on `Install`; `runInstall` computes euid and passes it.
- [ ] **Step 4: Run** `go test ./...` -> all PASS.
- [ ] **Step 5: Commit** `git add dotctl/install.go dotctl/install_test.go && git commit -m "fix(dotctl): omit sudo for installs when running as root"`

---

### Task 4: bootstrap.sh safe scaffold

**Files:** Modify the repo bootstrap script (locate first). Test: `bash -n` + `shellcheck`.

- [ ] **Step 1: Locate** `git ls-files | grep -n bootstrap.sh` (expected `stow-packages/bootstrap.sh`); use as `$BOOTSTRAP`.
- [ ] **Step 2: Scaffold** (`log`, `have`, `fetch`, `USER_NAME=${USER:-$(id -un)}`, `DOTFILES_ROOT` from `$0`, `set -uo pipefail`, `LOG` under `${XDG_STATE_HOME:-$HOME/.local/state}/dotctl/`). `fetch` supports an optional `FETCH_OVERRIDE` (if set, `cp "$FETCH_OVERRIDE" "$2"`) documented as a test-only seam.
- [ ] **Step 3: Gate** `bash -n "$BOOTSTRAP" && shellcheck "$BOOTSTRAP"` (avoid `cat|` SC2002; quote per SC2086).
- [ ] **Step 4: Commit** `git add "$BOOTSTRAP" && git commit -m "refactor(bootstrap): slim scaffold with logging and curl/wget fetch"`

---

### Task 5: ensure_dotctl (fetch + verify + PATH + version recheck)

**Files:** Modify `$BOOTSTRAP`. (`dotctl/VERSION` is created later, in Task 10's post-release commit, so `main` never advertises an unreleased version.)

**Interfaces:** `ensure_dotctl()` reads the pin from `$DOTFILES_ROOT/dotctl/VERSION`; if the installed `dotctl version` equals the pin, returns; else downloads the asset, verifies sha256 against `dotctl/checksums/<version>.txt` with `awk -v a="$asset" '$2==a{print $1}'`, strips quarantine, installs, prepends `~/.local/bin` to PATH. Uses a `trap 'rm -rf "$tmp"' RETURN` (or explicit cleanup) so the tempdir does not leak.

- [ ] **Step 1: Write** `asset_name()` (`local os="" arch=""`) and `ensure_dotctl()` per the interface (version-recheck short-circuit; tempdir cleanup; robust awk parse; PATH prepend). Guard the checksum-file read: if `dotctl/VERSION` or the checksums file is absent, `log "FATAL: no pinned release yet"` and return 1.
- [ ] **Step 2: Gate + mocked mismatch** via `FETCH_OVERRIDE` pointing at a local file with a bogus committed checksum; confirm non-zero on mismatch and that `$tmp` is removed.
- [ ] **Step 3: Commit** `git add "$BOOTSTRAP" && git commit -m "feat(bootstrap): ensure_dotctl fetch, pinned sha256 verify, version recheck"`

---

### Task 6: ensure_pkg_mgr

**Files:** Modify `$BOOTSTRAP`. Test: `bash -n` + `shellcheck`.

**Interfaces:** macOS: `have brew && return 0`; else fetch the installer to a tempfile (rc-checked), `NONINTERACTIVE=1 /bin/bash <installer>`, then `eval "$(/opt/homebrew/bin/brew shellenv)"` (fallback `/usr/local/bin/brew`), then `have brew || FATAL`. Linux: warn (not fail) if `pacman` present and `yay` absent. Debian/Ubuntu: no-op.

- [ ] **Step 1: Write** `ensure_pkg_mgr()` per the interface.
- [ ] **Step 2: Gate** `bash -n` + `shellcheck`.
- [ ] **Step 3: Commit** `git add "$BOOTSTRAP" && git commit -m "feat(bootstrap): ensure_pkg_mgr with brew shellenv and non-fatal yay"`

---

### Task 7: main flow, shell, key, stow

**Files:** Modify `$BOOTSTRAP`. Test: `bash -n` + `shellcheck` + mocked dry-run.

**Interfaces / behaviors:**

- `prompt_email()`: if `$DOTCTL_EMAIL` is set, echo it; else if a TTY, `read -r -p "git email: " e; echo "$e"`; else echo empty (caller logs and skips gitconfig rather than hanging). `main` passes `-email "$(prompt_email)"` to `dotctl gitconfig` only when non-empty.
- `uses_1password_signing()`: `grep -q op-ssh-sign ~/.config/git/config.machine 2>/dev/null`.
- `setup_ssh_key()`: `mkdir -p ~/.ssh; chmod 700 ~/.ssh`; if `uses_1password_signing`, log and skip on-disk keygen/registration; else `ssh-keygen` if absent, `dotctl allowed-signers add ...`, `print_key_instructions`.
- `stow_all()`: subshell `cd "$DOTFILES_ROOT/stow-packages"`; for each package dir `p`, skip if `git -C "$DOTFILES_ROOT" check-ignore "stow-packages/$p" >/dev/null 2>&1`; run `stow`; on conflict, back up the colliding target (`mv "$HOME/<file>" "$HOME/<file>.pre-dotctl.<ts>"`) then re-stow, and only set `rc=1` if it still fails. Capture and return the rc.
- `set_default_shell()`: uses `$USER_NAME`; adds zsh to `/etc/shells` via `-w` or `sudo -n` only; `chsh || warn`; fully non-fatal.
- `main()`: `rc=0`; `ensure_dotctl || exit 1`; `ensure_pkg_mgr || exit 1`; `dotctl check || exit 1`; then `dotctl install -file csv ${DOTCTL_YES:+--yes} || rc=1`; `stow_all || rc=1`; gitconfig (guarded on non-empty email) `|| rc=1`; `setup_ssh_key || rc=1`; `set_default_shell || rc=1`; `dotctl verify || rc=1`; log the post-registration remote-switch instructions; `exit "$rc"`.

- [ ] **Step 1: Write** the helpers + `main` per the interface.
- [ ] **Step 2: Gate + mocked dry-run**: stub `dotctl`/`stow`/`ssh-keygen`/`chsh`/`git` on PATH; run in a temp HOME with `config.machine` present (assert keygen skipped) and absent; assert ordering, non-interactive no-hang, `exit 0` on all-success and non-zero when a stub fails, and that a pre-existing `~/.zshrc` gets backed up rather than aborting.
- [ ] **Step 3: Commit** `git add "$BOOTSTRAP" && git commit -m "feat(bootstrap): rc flow, email prompt, gated ssh key, stow conflict backup, safe shell"`

---

### Task 8: goreleaser + release workflow

**Files:** Create `dotctl/.goreleaser.yaml`, `.github/workflows/release.yml`. Test: `goreleaser check` + `goreleaser release --snapshot --clean` (needs local goreleaser >= 2.4).

- [ ] **Step 1: `.goreleaser.yaml`**

```yaml
version: 2
project_name: dotctl
builds:
  - main: .
    binary: dotctl
    env: [CGO_ENABLED=0]
    goos: [darwin, linux]
    goarch: [amd64, arm64]
    ldflags: ["-s -w -X main.version={{ .Tag }}"]
    hooks:
      post:
        - cmd: sh -c 'case "{{ .Os }}" in darwin) codesign -s - "{{ .Path }}" ;; esac'
archives:
  - formats: [binary]
    name_template: "dotctl_{{ .Os }}_{{ .Arch }}"
checksum:
  name_template: "checksums.txt"
```

(The codesign runs as a build post-hook, before checksums are computed on the resulting artifacts, so the pinned sha matches the signed binary.)

- [ ] **Step 2: `.github/workflows/release.yml`** (fetch full history for goreleaser)

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
        with: { fetch-depth: 0 }
      - uses: actions/setup-go@v5
        with: { go-version: "1.22" }
      - uses: goreleaser/goreleaser-action@v6
        with: { version: "~> v2", args: release --clean, workdir: dotctl }
        env: { GITHUB_TOKEN: "${{ secrets.GITHUB_TOKEN }}" }
```

- [ ] **Step 3: Validate** `cd dotctl && goreleaser check && goreleaser release --snapshot --clean`; confirm four `dotctl_<os>_<arch>` binaries + `checksums.txt` in `dotctl/dist/`, and a built binary reports the injected version.
- [ ] **Step 4: Commit** `git add dotctl/.goreleaser.yaml .github/workflows/release.yml && git commit -m "ci(dotctl): goreleaser config and tag-triggered release"`

---

### Task 9: container e2e (must pass before release)

**Files:** Create `dotctl/test/Dockerfile`, `dotctl/test/e2e.sh`, `dotctl/test/Makefile`; create `dotctl/.gitignore`; add the `e2e` job to `.github/workflows/ci.yml`.

**Interfaces:** builds `dotctl` from source (Go via a pinned go.dev tarball, arch-mapped; Debian 12 ships Go 1.19), runs `dotctl install --yes` as root (root-aware, no sudo), asserts `dotctl verify --skip=stow,shell` passes. Linux only.

- [ ] **Step 1: `dotctl/.gitignore`** = `/dotctl` and `/dist/`.
- [ ] **Step 2: `e2e.sh`** (note: `go` comes from the tarball, so it is NOT in the required-binary assertion):

```bash
#!/usr/bin/env bash
set -uo pipefail
fail() { echo "E2E FAIL: $*" >&2; exit 1; }
cd /repo/dotctl || fail "repo not at /repo"
go build -o /usr/local/bin/dotctl . || fail "go build"
dotctl lint -file /repo/packages.csv          || fail lint
dotctl check                                  || fail check
dotctl install -file /repo/packages.csv --yes || fail "install"
for b in git jq tmux nvim; do command -v "$b" >/dev/null 2>&1 || fail "missing: $b"; done
dotctl verify -file /repo/packages.csv --skip=stow,shell || fail "verify"
echo "E2E OK on $(. /etc/os-release; echo "$ID")"
```

- [ ] **Step 3: `Dockerfile`** (Arch uses `-Syu`; Go via pinned tarball):

```dockerfile
ARG BASE=ubuntu:24.04
FROM ${BASE}
ARG GO_VERSION=1.22.12
RUN if command -v apt-get >/dev/null; then apt-get update && apt-get install -y git curl ca-certificates; \
    elif command -v pacman >/dev/null; then pacman -Syu --noconfirm git curl; fi
RUN set -eux; case "$(uname -m)" in x86_64) g=amd64;; aarch64|arm64) g=arm64;; esac; \
    curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${g}.tar.gz" | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:${PATH}"
COPY . /repo
RUN chmod +x /repo/dotctl/test/e2e.sh
CMD ["/repo/dotctl/test/e2e.sh"]
```

- [ ] **Step 4: `Makefile`** targets e2e-ubuntu/debian/arch (`podman build --build-arg BASE=... -f Dockerfile <repo>` then `podman run --rm ...`).
- [ ] **Step 5: Run locally** (needs Podman) for all three bases; each prints `E2E OK`. Fix any real package-name resolution failure here (this is the gate for wrong apt/pacman names).
- [ ] **Step 6: Add CI `e2e` job** (matrix ubuntu:24.04/debian:12/archlinux:latest, `fail-fast: false`, `podman build` + `podman run` -- podman is pre-installed on ubuntu-latest, no setup step needed) and commit `git add dotctl/test dotctl/.gitignore .github/workflows/ci.yml && git commit -m "test(dotctl): containerized cross-distro end-to-end harness"`

---

### Task 10: CI, merge, cut the first release

**Files:** Create `.github/workflows/ci.yml` (go + shell jobs; the e2e job was added in Task 9); create `dotctl/VERSION` + `dotctl/checksums/v0.1.0.txt` in one post-release commit.

- [ ] **Step 1: `ci.yml`** go job (`gofmt -l` gate, `go vet`, `go test`, `go run . lint -file ../packages.csv`) and shell job (`shellcheck stow-packages/bootstrap.sh`).
- [ ] **Step 2: Get the branch green** (go + shell + e2e matrix all pass) and open the PR. **The e2e matrix must be green before proceeding** (this is the cross-distro gate).
- [ ] **Step 3: Merge to `main`** (this is an outward action; only with explicit go-ahead).
- [ ] **Step 4: Cut the release from `main`, then commit VERSION + checksums together** (one commit, no broken window; outward, explicit go-ahead):

```bash
git checkout main && git pull
git tag v0.1.0 && git push origin v0.1.0
# after the release workflow publishes:
mkdir -p dotctl/checksums
gh release download v0.1.0 -p checksums.txt -O dotctl/checksums/v0.1.0.txt
printf 'v0.1.0\n' > dotctl/VERSION
git add dotctl/VERSION dotctl/checksums/v0.1.0.txt
git commit -m "chore(dotctl): pin v0.1.0 (VERSION + checksums)" && git push
```

- [ ] **Step 5: Real fetch smoke** on a scratch checkout: run `ensure_dotctl` for real (no override), confirm download + checksum verify + `dotctl version` == `v0.1.0`.

---

### Task 11: migration cutover (locate, backup-guarded, idempotent)

**Files:** delete `install.sh` (tracked) and `preflight.sh` (untracked); update README refs. Per-machine files already seeded on this machine (Plan 2), so this task is idempotent about them.

- [ ] **Step 1: Ensure per-machine overrides (idempotent, no clobber)**: `mkdir -p ~/.config/git`; if `~/.config/git/config.machine` is absent, seed it (1Password `program` + `<your 1Password ssh signing public key>` placeholder) and tell the user to fill the key; else leave it untouched. (On this machine it already exists, so this is a no-op.)
- [ ] **Step 2: Verify signing BEFORE deleting anything**: `git config --global --includes --get user.signingkey` and `--get gpg.ssh.program` resolve to the 1Password values, and `dotctl verify` local checks are green. If not, restore from the migration backup and stop.
- [ ] **Step 3: Locate + retire the old scripts** (run from `$DOTFILES_ROOT`):

```bash
cd "$(git rev-parse --show-toplevel)"
git ls-files | grep -E '^(install|preflight)\.sh$'   # confirm what is tracked
git rm --ignore-unmatch install.sh preflight.sh      # remove tracked ones
rm -f preflight.sh                                    # remove if it was untracked on disk
git diff --cached --name-only | grep -q . || { echo "nothing staged to remove"; }
```

Update README/docs references to `dotctl` + the new `bootstrap.sh`.

- [ ] **Step 4: Final verify + commit (explicit paths)**: `dotctl verify` (local green), and after key registration `dotctl verify --remote`. Then `git add -- install.sh preflight.sh README.md && git commit -m "chore(bootstrap): cut over to dotctl; retire install.sh and preflight.sh"`.
- [ ] **Rollback**: `git revert` this commit restores the scripts; the migration backup restores `config.machine`. Bad *binary* release: delete the tag/release, revert the VERSION/checksums pin commit, re-cut.

---

## Self-Review

**1. Spec coverage:** ensure_dotctl (Task 5); ensure_pkg_mgr (Task 6); rc flow + email prompt + gated key + stow backup + safe shell (Task 7); goreleaser + release (Task 8); container e2e before release (Task 9); CI + merge + release + VERSION/checksums-together + real fetch smoke (Task 10); idempotent backup-guarded migration (Task 11); supporting dotctl changes version/verify-skip/root-aware-install (Tasks 1-3).

**2. Placeholder scan:** No TBD/TODO. `config.machine` signingkey is an intentional placeholder. `FETCH_OVERRIDE` is a documented test seam.

**3. Consistency / round-2 fixes:** `Install` takes an explicit `root` (Task 3); e2e (Task 9) precedes merge/release (Task 10); `VERSION` + checksums committed together post-release; `fetch-depth: 0` set; email obtained before gitconfig; `--skip=shell` guards `loginShell`; stow backs up conflicts and uses `stow-packages/$p` for check-ignore; Task 11 locates scripts from repo root; e2e drops the `go` assertion; Arch e2e uses `-Syu`; `splitList` reused (no `parseSkips`); both usage strings updated; tempdir cleaned. Accepted deviations unchanged: sha256-over-TLS; `pacman -Syu` blast radius.
