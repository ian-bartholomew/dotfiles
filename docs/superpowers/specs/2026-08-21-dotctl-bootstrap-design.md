# dotctl: dotfiles bootstrap tool (design)

- Author: Ian Bartholomew
- Date: 2026-08-21
- Status: DRAFT (pending review)
- Repo: github.com/ian-bartholomew/dotfiles (public)

## Summary

Replace the fragile `install.sh` bash installer with a small self contained Go
binary (`dotctl`) plus a slimmed bash wrapper. Go owns the data and logic
heavy work (parse, validate, resolve, render, verify); bash owns pure
orchestration of existing CLIs (fetch binary, stow, ssh-keygen, chsh). The
tool also standardizes cross machine git setup: on-disk SSH keys used for both
GitHub auth and SSH commit signing, a layered gitconfig that never clobbers a
machine's local overrides, changing the default shell to zsh, and a single end
to end verification at the end of bootstrap.

## Motivation

An adversarial review of `install.sh` surfaced real defects rooted in bash:
`sudo pacman -Sy` partial-upgrade risk, `set -e` plus `read` aborting any non
interactive run, macOS system bash 3.2 plus `set -u` empty-array failures, no
CSV validation (the class of bug that put `AUR:` values in the apt column), and
hand rolled CSV parsing via `xargs` trimming. The logic is mostly data work
(parse CSV, resolve per-platform names, branch on OS), which is exactly where
bash is the wrong tool. A compiled binary erases that whole class and is
testable.

Separately, keeping git commit signing working across work and home machines
has been a recurring pain (key id mismatches after machine changes, hardcoded
macOS paths in the checked-in gitconfig). This project fixes that by making the
config path-based and layered.

## Goals

- Single testable Go binary for the fragile parse/validate/resolve/render/verify logic.
- Non-interactive capable (flags), so bootstrap can be scripted.
- Cross-distro (macOS, Arch, Debian/Ubuntu) with one package catalog.
- On-disk per-machine SSH key for auth and signing; portable, no 1Password dependency for new machines.
- Layered gitconfig that never clobbers a machine's local overrides (this machine keeps 1Password signing).
- One end to end verification that the machine is correctly set up.

## Non-goals

- Not a config management framework (no daemon, no inventory).
- Does not replace the package managers; it orchestrates brew/pacman/apt/yay.
- No Windows support.
- Not adopting nix/home-manager (evaluated and declined as too heavy for this need).

## Architecture

Two artifacts, split by the "bash only for pure CLI orchestration" heuristic.

### dotctl (Go), data and logic, unit tested

- `check`: preflight of the prerequisites needed to run the bootstrap itself. Verify platform is supported, a package manager is present, and `git` and `curl` exist. It does not require `stow` or zsh (dotctl installs both) and does not assert the default shell (those are post-conditions checked by `verify`).
- `lint`: validate `packages.csv` (column count, known category, valid `required` value, valid `AUR:`/`CASK:` prefixes only in the columns where they are allowed). Also check the CSV schema version against the version embedded in the binary and fail loudly if the data is ahead of the binary.
- `migrate`: one-off, programmatic conversion of `packages.csv` from the 5-column to 6-column schema (adds `required`). Done in code so hand-editing ~100 rows cannot reintroduce the miscolumn bug the tool exists to prevent.
- `install`: resolve the plan from `packages.csv` for the detected platform, install the required set unconditionally, prompt for additional categories/packages, install those. On Arch use `pacman -Syu`/`--needed` (never a bare `-Sy` followed by single-package installs, which is the partial-upgrade trap); on Debian/Ubuntu run `apt-get update` first. Supports `--dry-run`, `--yes`, `--categories`, `--packages`, `--all`.
- `gitconfig`: prompt for the git email and render the gitignored `~/.config/git/config.local` (email, credential.helper). `--force` re-renders `config.local` only; it never generates or touches `~/.config/git/config.machine`, so hand-maintained per-machine overrides (this machine's 1Password lines, seeded once during migration) always survive.
- `allowed-signers add`: append an SSH public key to the repo's committed `allowed_signers` file (deduped), keyed by the committing email passed from the rendered `config.local`. It adds the key that machine actually signs with: the on-disk key on new machines, the 1Password signing key on this machine (see SSH keys and signing).
- `verify`: the end to end test (see below).

### bootstrap.sh (bash), pure CLI orchestration

- `ensure_dotctl`: download the matching `dotctl` release asset (detect os/arch), verify its checksum and signature (see Distribution), strip the macOS quarantine attribute (`xattr -d com.apple.quarantine`), chmod, install to `~/.local/bin`, and ensure that directory is on PATH for the rest of the run. This is the distribution-B fetch shim.
- `ensure_pkg_mgr`: install Homebrew (macOS) or bootstrap yay (Arch) via the official installers if absent. Debian/Ubuntu needs no bootstrap (apt is present by default).
- stow linking loop (existing logic, kept).
- `ssh-keygen` (ed25519) only if no key exists.
- print the public key and GitHub registration instructions.
- `chsh` to zsh.
- call the `dotctl` subcommands in order.

## Components

### packages.csv schema

Add a `required` column as the second field:

```
# category | required | brew | pacman | apt | notes
system | yes | stow | stow | stow |
shell  | yes | zsh  | zsh  | zsh  |
shell  |     | bat  | bat  | bat  |
```

Empty `required` means not required. `required` is orthogonal to category
(required packages live across several categories), which is why it is a
column and not a pseudo-category or a separate file. `dotctl lint` validates
the schema.

### Required set

Baseline installed unconditionally (platform filtered; a `-` in a platform
column is skipped even when required):

- All of `system`: coreutils, findutils, grep, gnu-sed, wget, curl, htop, btop, tree, stow, less
- zsh, tmux, git, tig, neovim, go, python, nodejs, jq
- gh (used by the git credential helper; a `gh` row must be added to `packages.csv`, which currently lacks one)
- Linux companions of the above (separate packages on pacman/apt, bundled by brew on macOS): npm, python-pip, python-pipx

Note: on macOS the companion rows have `-` in the brew column, so marking them
required is a no-op there and only affects Arch/Ubuntu.

### Package selection model

One time bootstrap; no persisted selection.

1. Install the required set (platform filtered, idempotent skip if already present).
2. Prompt: which additional categories to install, and optionally any individual packages by name.
3. Install those.

Re-running just re-prompts; already-installed packages are skipped, so it is
safe. Non-interactive (`--yes` or no TTY): install required only, unless
`--categories`, `--packages`, or `--all` add more. Explicit rather than
surprising.

### gitconfig (base plus local include)

Layered so machine-specific bits never get clobbered and template improvements
still propagate on `git pull`.

Committed static base `.gitconfig` (stowed to `~/.gitconfig`), all uniform:

- aliases, color, core (`editor = nvim`, `excludesfile = ~/.gitignore`), apply, help, push
- `commit.gpgsign = true`, `tag.gpgSign = true`, `gpg.format = ssh`
- `user.signingkey = ~/.ssh/id_ed25519.pub` (path based, identical on every machine, never goes stale)
- `gpg.ssh.allowedSignersFile = ~/.config/git/allowed_signers`
- `user.name = Ian Bartholomew`
- nvimdiff mergetool (exact block below)
- `pull.rebase = false`
- two includes as the LAST entries (so local wins): `[include] path = ~/.config/git/config.local` (dotctl-generated) then `[include] path = ~/.config/git/config.machine` (never generated; hand-maintained per-machine overrides)

nvimdiff block (verbatim):

```
[merge]
    tool = nvimdiff
[mergetool]
    prompt = true
[mergetool "nvimdiff"]
    cmd = "nvim -d \"$LOCAL\" \"$MERGED\" \"$BASE\" \"$REMOTE\" -c 'wincmd w' -c 'wincmd J'"
```

Generated, gitignored `~/.config/git/config.local`, only the dotctl-managed variable bits:

- `user.email` (prompted per machine: work vs home)
- `credential.helper` (per platform: `osxkeychain` on macOS, `cache` on Linux; plus the github-specific `!gh auth git-credential` resolved from PATH, not a hardcoded path)

Separately, gitignored `~/.config/git/config.machine` holds hand-maintained
per-machine overrides that dotctl never generates or touches, in particular this
machine's 1Password signing override (`[gpg "ssh"] program = .../op-ssh-sign`
and its signingkey), seeded once during migration. Keeping it out of the
generated file is what lets `gitconfig --force` re-render `config.local` (to fix
a wrong email) without destroying the 1Password setup.

Removed from the current config: `merge.tool = kdiff3`, `init.templateDir`
(hardcoded and a username typo, currently broken), `http.sslVerify = false`
(global TLS bypass, security risk; removed outright, no corporate-proxy dependency), the hardcoded `/opt/homebrew/bin/gh`
credential path (replaced by PATH-based `gh`), and the hardcoded 1Password
program path (moved into the machine-local override).

Include mechanics (verified): git follows `include.path` automatically during
real operations (full-stack reads default `--includes` on); a missing include
file is silently ignored; an include placed after the base's own values
overrides them. `git config --global`/`--file` default `--includes` off, so
those are a testing gotcha only.

Stow: add `.gitconfig.template`/local artifacts to `.stow-local-ignore` as
needed; `config.local` is generated in place and gitignored, not stowed.

### SSH keys and signing

- On-disk per-machine ed25519 key at `~/.ssh/id_ed25519`, used for both GitHub auth and SSH commit signing.
- The key is generated without a passphrase (decision: accepted). This is a deliberate security tradeoff: it is weaker than the 1Password agent's hardware/biometric gating, since anyone with read access to the file can auth as this machine and forge signed commits. Accepted for convenience on trusted personal machines. Revocation path if a key leaks: remove it from the GitHub Authentication and Signing sections and from `allowed_signers`, then regenerate. This machine keeps 1Password signing and is unaffected.
- bootstrap.sh runs `ssh-keygen` only if the key is absent.
- The public key is printed with instructions to register it on GitHub in BOTH the Authentication keys and Signing keys sections (they are distinct).
- `~/.ssh/config` uses the same base-plus-local layering as gitconfig, because a single stowed file cannot express per-machine differences. A committed base config carries the github host block that forces the on-disk key (`IdentityFile ~/.ssh/id_ed25519`, `IdentitiesOnly yes`) and `Include ~/.ssh/config.local` at the top. On a machine whose `Host *` routes through the 1Password agent, the gitignored `~/.ssh/config.local` adds `Host github.com` / `IdentityAgent none` so the on-disk key is used and GitHub's five-failed-key-attempt cutoff is avoided, scoped to github.com only so other SSH/agent-forwarding workflows are untouched. Without this, auth can silently fail.
- `allowed_signers` (public keys only, not secret) is committed to the repo and stowed to `~/.config/git/allowed_signers`. `dotctl allowed-signers add` appends the key that machine actually signs with (deduped): the on-disk key on new machines, and on this machine the existing 1Password signing key (so verification of this machine's own commits matches what it signs with). Every other machine trusts it on the next pull, so local verification works across machines.
- The `allowed_signers` principal is the committing email, which differs work vs home, so `allowed-signers add` takes the email from the rendered `config.local`. A commit signed under a given email verifies only if that email plus the signing key is present in the file, so entries carry the email(s) a machine commits under.
- Sequencing: appending the key locally needs nothing, but committing and pushing `allowed_signers` back to the repo needs push access, which needs the key registered on GitHub first AND the origin remote switched from the HTTPS clone URL to SSH (`git@github.com:...`). Both happen in the post-registration follow-up (see Bootstrap flow step 12), not in the unattended run.

### Default shell

bootstrap.sh changes the login shell to the installed zsh. On both Linux and
macOS the zsh path (including the Homebrew path, which is not there by default)
must be present in `/etc/shells` before `chsh -s` will accept it, so bootstrap
adds it if missing. `chsh` prompts for the user password, so this step is
interactive by nature and runs near the end, after packages are installed. It
is a no-op if zsh is already the default. Because `$SHELL` is fixed at login
and does not change in the running process after `chsh`, `dotctl verify` reads
the login shell from the passwd database (`getent passwd`/`dscl`), not
`$SHELL`.

### verify (end to end)

`dotctl verify` runs after bootstrap and reports pass/fail per check. Local
checks run always; the GitHub-dependent checks require the key to be registered
first, so they run only under `--remote` and, when not run, are reported as
pending rather than failed.

Local checks:

- platform supported and package manager present
- `git`, `curl`, `stow` present
- zsh installed and it is the default shell (read from the passwd database, not `$SHELL`)
- stow symlinks are in place
- the required packages are installed

Remote checks (`--remote`, after key registration):

- SSH auth to GitHub succeeds (`ssh -T git@github.com`, interpret the result)
- commit signing verifies (sign an ephemeral object and verify it against `allowed_signers` via `ssh-keygen -Y verify`, or a throwaway signed commit)

Exit non-zero if any check that actually ran fails. Pending remote checks do
not fail the run.

## Bootstrap flow (ordered)

Prerequisites on a bare machine (the only things installed by hand): `git` (to
clone) and `curl` or `wget` (for the shim and the Homebrew installer).
Everything else is installed by the flow. The shim tries `curl`, then falls
back to `wget`.

1. `git clone https://github.com/ian-bartholomew/dotfiles` (public, HTTPS, no key needed), then `./bootstrap.sh`.
2. bash `ensure_dotctl`: download and verify the `dotctl` binary from GitHub Releases.
3. bash `ensure_pkg_mgr`: install Homebrew or bootstrap yay if absent.
4. `dotctl check`: preflight; fail early with clear guidance if prerequisites are missing.
5. `dotctl install`: required set plus prompted extras (installs zsh among the required set).
6. bash: stow linking (the base `.gitconfig` lands at `~/.gitconfig`).
7. `dotctl gitconfig`: prompt email, render `~/.config/git/config.local`; seed 1Password lines on migration.
8. bash: `ssh-keygen` if absent; then `dotctl allowed-signers add` (appends locally only; the push waits for step 12).
9. bash: print the public key and GitHub registration instructions (auth and signing sections).
10. bash: `chsh` to zsh.
11. `dotctl verify`: local checks (deps, default shell via passwd, stow symlinks, required packages). GitHub auth and signing are reported as pending, not failed, until the key is registered.
12. After registering the key on GitHub: switch origin to SSH (`git remote set-url origin git@github.com:ian-bartholomew/dotfiles.git`) since the repo was cloned over HTTPS, run `dotctl verify --remote` to confirm SSH auth and signing, then commit and push `allowed_signers`.

Each step is idempotent and safe to re-run. bootstrap writes a timestamped log
(for example under `~/.local/state/dotctl/`) so a partial or remote failure is
debuggable, and a failed step leaves the machine in a re-runnable state rather
than an undefined one.

## Distribution (option B)

- goreleaser builds static binaries (`CGO_ENABLED=0`) for darwin/{amd64,arm64} and linux/{amd64,arm64}.
- A GitHub Actions workflow releases on tag push, attaching the binaries and a checksums file, using the default `GITHUB_TOKEN`.
- The bootstrap shim detects `uname -s`/`uname -m`, maps to the asset name, downloads a pinned release version (the version is recorded in the repo; latest is opt-in so one bad release cannot brick every fresh bootstrap), verifies the sha256 against the checksums file, chmods, and installs to `~/.local/bin/dotctl`.
- The checksums file is signed (minisign or cosign) and the shim verifies that signature with a public key committed to the repo. Without this the checksums ship from the same release as the binary and provide no integrity against a compromised release; the alternative, if signing is deferred, is to explicitly document a TLS-only threat model.
- The binary embeds the expected `packages.csv` schema version; `dotctl lint` fails if the cloned data is ahead of the pinned binary, preventing silent breakage from binary/data skew.
- Only pure-orchestration bash remains at the bootstrap entry point, which is where bash is appropriate.
- The first tagged release must be cut before the shim can download anything; cutting the initial release is part of rollout.

## Testing

Go table-driven unit tests for the pure functions: platform detection, CSV
parse and lint, per-platform name resolution, plan building (required plus
selection), and `config.local` rendering. `verify` logic is tested against
mocked command execution. No frameworks beyond the standard `testing` package.

## Migration

- Run `dotctl migrate` to add the `required` column to `packages.csv` programmatically and populate the required set above; do not hand-edit the ~100 rows. Add a `gh` row at the same time.
- Convert `stow-packages/git/.gitconfig` into the committed static base described here, with all hardcoded/machine-specific values removed and the `[include]` added.
- On this machine, seed `~/.config/git/config.machine` (and `~/.ssh/config.local`) with the current 1Password `gpg.ssh.program` and signingkey so signing does not break. It is never regenerated, so `gitconfig --force` cannot wipe it.
- Add the committed `allowed_signers` file with this machine's 1Password signing key.
- On re-provisioning, handle stow conflicts with pre-existing `~/.gitconfig` / `~/.ssh/config`: back up the existing file to a timestamped copy, then stow, rather than failing.
- Slim `bootstrap.sh` to the orchestration responsibilities above; retire `install.sh` once `dotctl install` reaches parity, where parity is an explicit cutover checklist (every current package/category installs on each platform; gitconfig, ssh config, and default shell are set up; `dotctl verify` is green) plus a passing end-to-end run.
- Keep `preflight.sh` behavior as `dotctl check` and retire the standalone script.

## Open decisions and assumptions

- Selection non-interactive default is required-only (confirmed).
- Companions (npm, python-pip, python-pipx) are required (confirmed).
- SSH key is passphraseless: accepted security tradeoff vs 1Password (confirmed; documented in SSH keys and signing).
- `http.sslVerify` removed outright: no corporate-proxy dependency (confirmed).
- The shim pins a dotctl version by default (per Distribution); latest is opt-in.
- Binary install path is `~/.local/bin/dotctl` (change if you prefer a repo-local `bin/`).

## Out of scope / future

- Named profiles (desktop/server/work) with shared committed definitions; add later if the same category set is repeatedly re-picked across machines of one role.
- `dotctl stow` wrapping stow in Go (kept in bash for now).
- Automated GitHub key registration (currently a manual, one-time-per-machine step by design).
- Rollback of `chsh`/gitconfig changes (acceptable for a one-time bootstrap; changes are small and reversible by hand).
