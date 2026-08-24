package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	osuser "os/user"
	"path/filepath"
	"strings"
)

func checkDefaultShell(loginShell string) error {
	if filepath.Base(strings.TrimSpace(loginShell)) != "zsh" {
		return fmt.Errorf("default shell is %q, want zsh", loginShell)
	}
	return nil
}

func checkTools(r Runner, tools []string) []error {
	var errs []error
	for _, t := range tools {
		if !r.Look(t) {
			errs = append(errs, fmt.Errorf("tool %q not found", t))
		}
	}
	return errs
}

// installedCheckCmd returns the package-manager invocation that reports
// whether p is installed. Resolved.Name is the package-manager name, not
// necessarily an executable on PATH (e.g. neovim's binary is nvim, coreutils
// has no eponymous binary), so this must query the manager, not exec.LookPath.
func installedCheckCmd(plat Platform, p Resolved) []string {
	switch plat {
	case PlatformMacOS:
		if p.Kind == KindCask {
			return []string{"brew", "list", "--cask", p.Name}
		}
		return []string{"brew", "list", "--versions", p.Name}
	case PlatformArch:
		return []string{"pacman", "-Q", p.Name}
	default:
		return []string{"dpkg", "-s", p.Name}
	}
}

func checkRequiredInstalled(plat Platform, r Runner, plan []Resolved) []error {
	var errs []error
	for _, p := range plan {
		argv := installedCheckCmd(plat, p)
		if _, err := r.RunOut(argv[0], argv[1:]...); err != nil {
			errs = append(errs, fmt.Errorf("required package %q not installed", p.Name))
		}
	}
	return errs
}

func checkSymlinks(isSymlink func(string) bool, paths []string) []error {
	var errs []error
	for _, p := range paths {
		if !isSymlink(p) {
			errs = append(errs, fmt.Errorf("%q is not a symlink (stow not applied?)", p))
		}
	}
	return errs
}

func isSymlink(path string) bool {
	fi, err := os.Lstat(path)
	return err == nil && fi.Mode()&os.ModeSymlink != 0
}

// currentUsername resolves the login user without trusting $USER alone, which
// is unset in containers, cron, and non-login su. Mirrors bootstrap.sh's
// ${USER:-$(id -un)}: env first (so tests can pin it), then the passwd DB via uid.
func currentUsername() string {
	if u := os.Getenv("USER"); u != "" {
		return u
	}
	if u := os.Getenv("LOGNAME"); u != "" {
		return u
	}
	if u, err := osuser.Current(); err == nil {
		return u.Username
	}
	return ""
}

// loginShell reports the user's configured login shell (not $SHELL, which
// reflects the running shell and may already be zsh from a temporary sub-shell).
func loginShell(plat Platform, r Runner) (string, error) {
	user := currentUsername()
	if user == "" {
		return "", fmt.Errorf("cannot determine current user")
	}

	if plat == PlatformMacOS {
		out, err := r.RunOut("dscl", ".", "-read", "/Users/"+user, "UserShell")
		if err != nil {
			return "", fmt.Errorf("dscl lookup failed: %w", err)
		}
		return parseDsclUserShell(out)
	}

	out, err := r.RunOut("getent", "passwd", user)
	if err != nil {
		return "", fmt.Errorf("getent lookup failed: %w", err)
	}
	return parseGetentShell(out)
}

func parseGetentShell(out string) (string, error) {
	line := strings.TrimSpace(strings.SplitN(out, "\n", 2)[0])
	fields := strings.Split(line, ":")
	if len(fields) < 7 {
		return "", fmt.Errorf("unexpected getent passwd output: %q", out)
	}
	return strings.TrimSpace(fields[6]), nil
}

func parseDsclUserShell(out string) (string, error) {
	line := strings.TrimSpace(out)
	_, shell, ok := strings.Cut(line, ":")
	if !ok {
		return "", fmt.Errorf("unexpected dscl UserShell output: %q", out)
	}
	return strings.TrimSpace(shell), nil
}

// verifyRemote runs the checks gated behind -remote: GitHub ssh auth and
// commit signing. The ssh check never fails fast on a non-zero exit:
// `ssh -T git@github.com` always exits 1 on a successful auth, so success is
// read from the banner text (which lands on stderr, hence RunOut using
// CombinedOutput).
func verifyRemote(r Runner, stdout io.Writer) []error {
	var errs []error

	out, runErr := r.RunOut("ssh", "-T", "git@github.com")
	if !strings.Contains(out, "successfully authenticated") {
		msg := strings.TrimSpace(out)
		if msg == "" && runErr != nil {
			msg = runErr.Error()
		}
		errs = append(errs, fmt.Errorf("github ssh auth check failed: %s", msg))
	}

	return append(errs, checkSigning(r, stdout)...)
}

// gitConfigValue reads a single git config key. An unset key (git config
// --get exits 1) returns ("", nil); any other failure (git absent, dubious
// ownership, unparseable config) returns a non-nil error so a broken machine
// cannot masquerade as one that simply has the key unset. It reads stdout
// only, so a git warning on stderr never contaminates the value.
func gitConfigValue(r Runner, key string) (string, error) {
	out, err := r.RunOutStdout("git", "config", "--get", key)
	if err == nil {
		return strings.TrimSpace(out), nil
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) && ee.ExitCode() == 1 {
		return "", nil // key unset
	}
	return "", fmt.Errorf("reading git config %s: %w", key, err)
}

// expandHome resolves a leading ~ the way git does for path-valued config.
// Values reach us verbatim from `git config --get`, so ~/.ssh/id_ed25519.pub
// would otherwise be passed to ssh-keygen as a literal relative path.
func expandHome(p string) string {
	if p != "~" && !strings.HasPrefix(p, "~/") {
		return p
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return p
	}
	return filepath.Join(home, strings.TrimPrefix(p, "~"))
}

// isInlineKey reports whether a user.signingkey value is literal key material
// rather than a path. Git accepts both; 1Password's documented setup uses the
// inline form.
func isInlineKey(s string) bool {
	for _, p := range []string{"ssh-", "ecdsa-", "sk-ssh-", "sk-ecdsa-"} {
		if strings.HasPrefix(s, p) {
			return true
		}
	}
	return false
}

// resolveSigningKey turns a user.signingkey value into a filesystem path for
// `ssh-keygen -f`. A path is expanded; inline key material (a bare "ssh-ed25519
// AAAA..." or an explicit "key::"-prefixed value, as 1Password configures) is
// written to a file inside dir, mirroring what git itself does before invoking
// the signer.
func resolveSigningKey(raw, dir string) (string, error) {
	lit, hadPrefix := strings.CutPrefix(raw, "key::")
	if !hadPrefix && !isInlineKey(raw) {
		return expandHome(raw), nil
	}
	path := filepath.Join(dir, "signingkey.pub")
	if err := os.WriteFile(path, []byte(strings.TrimSpace(lit)+"\n"), 0o600); err != nil {
		return "", err
	}
	return path, nil
}

// principalTrusted reports whether email appears as a principal in the output
// of `ssh-keygen -Y find-principals` (one principal per line).
func principalTrusted(findPrincipalsOut, email string) bool {
	for _, line := range strings.Split(findPrincipalsOut, "\n") {
		if strings.TrimSpace(line) == email {
			return true
		}
	}
	return false
}

// checkSigning confirms this machine can produce a git signature with its
// configured key, and that the resulting signature is trusted by the
// configured allowed_signers file for the machine's committing identity.
//
// It deliberately does not inspect HEAD. On a repo whose commits land via
// GitHub squash-merge, HEAD is committed by GitHub and signed with GitHub's
// own GPG key, so `git verify-commit HEAD` only asks whether GitHub's public
// key sits in the local GPG keyring: unrelated to this machine's signing
// setup, and false on every freshly bootstrapped box.
//
// Signing happens over a throwaway file in a fresh temp dir. Git history is
// never touched and no commit is ever created. The temp dir must be fresh:
// `ssh-keygen -Y sign` prompts interactively rather than clobbering an
// existing <file>.sig, which would hang a non-interactive bootstrap.
func checkSigning(r Runner, stdout io.Writer) []error {
	format, err := gitConfigValue(r, "gpg.format")
	if err != nil {
		return []error{fmt.Errorf("signing check failed: %w", err)}
	}
	switch format {
	case "ssh":
		// proceed
	case "":
		// The committed .gitconfig always sets gpg.format=ssh, so an empty
		// value means it is not in effect (e.g. stow has not linked
		// ~/.gitconfig yet) rather than a machine that opted out of signing.
		return []error{fmt.Errorf("signing check failed: gpg.format is unset (expected ssh); is ~/.gitconfig linked?")}
	default:
		fmt.Fprintf(stdout, "verify: skipped signing (gpg.format=%s; this check covers ssh signing only)\n", format)
		return nil
	}

	rawKey, err := gitConfigValue(r, "user.signingkey")
	if err != nil {
		return []error{fmt.Errorf("signing check failed: %w", err)}
	}
	if rawKey == "" {
		return []error{fmt.Errorf("signing check failed: user.signingkey is unset")}
	}
	rawSigners, err := gitConfigValue(r, "gpg.ssh.allowedSignersFile")
	if err != nil {
		return []error{fmt.Errorf("signing check failed: %w", err)}
	}
	if rawSigners == "" {
		return []error{fmt.Errorf("signing check failed: gpg.ssh.allowedSignersFile is unset")}
	}
	signers := expandHome(rawSigners)
	email, err := gitConfigValue(r, "user.email")
	if err != nil {
		return []error{fmt.Errorf("signing check failed: %w", err)}
	}

	// gpg.ssh.program is the configured signer when set (1Password's
	// op-ssh-sign is a drop-in for `ssh-keygen -Y sign`, and on those machines
	// no private key exists on disk for ssh-keygen to find). Verification is
	// always ssh-keygen: op-ssh-sign only signs.
	signer, err := gitConfigValue(r, "gpg.ssh.program")
	if err != nil {
		return []error{fmt.Errorf("signing check failed: %w", err)}
	}
	if signer == "" {
		signer = "ssh-keygen"
	}

	dir, err := os.MkdirTemp("", "dotctl-signing-")
	if err != nil {
		return []error{fmt.Errorf("signing check failed: %w", err)}
	}
	defer os.RemoveAll(dir)

	key, err := resolveSigningKey(rawKey, dir)
	if err != nil {
		return []error{fmt.Errorf("signing check failed: %w", err)}
	}

	probe := filepath.Join(dir, "probe")
	if err := os.WriteFile(probe, []byte("dotctl signing probe\n"), 0o600); err != nil {
		return []error{fmt.Errorf("signing check failed: %w", err)}
	}

	if out, err := r.RunOut(signer, "-Y", "sign", "-f", key, "-n", "git", probe); err != nil {
		return []error{fmt.Errorf("cannot sign with %s via %s: %s", rawKey, signer, runMsg(out, err))}
	}
	// find-principals resolves the signature's public key against
	// allowed_signers. Paired with the sign step above it establishes both
	// properties bootstrap needs, and unlike `-Y verify` it takes the signed
	// data as a file rather than on stdin, which Runner does not plumb.
	out, err := r.RunOut("ssh-keygen", "-Y", "find-principals", "-s", probe+".sig", "-f", signers)
	if err != nil {
		return []error{fmt.Errorf("signing key %s is not trusted by %s: %s", rawKey, signers, runMsg(out, err))}
	}
	// find-principals matching the key is not enough: the key must be trusted
	// for the email this machine commits under, or real commits verify as
	// "No principal matched" despite the key being present under another email.
	if email != "" && !principalTrusted(out, email) {
		return []error{fmt.Errorf("signing key %s is in %s but not for committing identity %s; add an entry for that email", rawKey, signers, email)}
	}

	return nil
}

// runMsg prefers a failed command's own output over Go's generic exit error.
func runMsg(out string, err error) string {
	if msg := strings.TrimSpace(out); msg != "" {
		return msg
	}
	return err.Error()
}

// loadRequiredPlan reads and parses file to build the required-package plan.
// If the file cannot be read or parsed (e.g. verify is run from outside the
// dotfiles repo with the default cwd-relative path), it prints a warning to
// stderr and returns a nil plan rather than failing verify.
func loadRequiredPlan(plat Platform, file string, stderr io.Writer) []Resolved {
	f, err := os.Open(file)
	if err != nil {
		fmt.Fprintf(stderr, "verify: warning: cannot read %s, skipping required-package check: %v\n", file, err)
		return nil
	}
	defer f.Close()
	pkgs, err := ParsePackages(f)
	if err != nil {
		fmt.Fprintf(stderr, "verify: warning: cannot parse %s, skipping required-package check: %v\n", file, err)
		return nil
	}
	return BuildPlan(pkgs, plat, Selection{})
}

// verifyLocal runs the local (non-remote) verify checks: tool presence, the
// default shell, stow symlinks, and required-package installation. skip
// gates the shell, stow, and packages checks off entirely (including their
// preconditions) rather than just their assertions, so a caller can verify a
// subset (e.g. the CI e2e skips the shell/stow/packages it does not set up).
func verifyLocal(plat Platform, r Runner, file string, skip map[string]bool, stdout, stderr io.Writer) []error {
	var errs []error

	errs = append(errs, checkTools(r, []string{"git", "curl", "stow"})...)

	if skip["shell"] {
		fmt.Fprintln(stdout, "verify: skipped shell")
	} else if sh, err := loginShell(plat, r); err != nil {
		errs = append(errs, fmt.Errorf("login shell lookup failed: %w", err))
	} else if err := checkDefaultShell(sh); err != nil {
		errs = append(errs, err)
	}

	if skip["stow"] {
		fmt.Fprintln(stdout, "verify: skipped stow")
	} else if home, err := os.UserHomeDir(); err != nil {
		errs = append(errs, fmt.Errorf("cannot determine home dir: %w", err))
	} else {
		errs = append(errs, checkSymlinks(isSymlink, []string{
			filepath.Join(home, ".gitconfig"),
		})...)
	}

	if skip["packages"] {
		fmt.Fprintln(stdout, "verify: skipped packages")
	} else {
		plan := loadRequiredPlan(plat, file, stderr)
		errs = append(errs, checkRequiredInstalled(plat, r, plan)...)
	}

	return errs
}

func runVerify(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("verify", flag.ContinueOnError)
	fs.SetOutput(stderr)
	remote := fs.Bool("remote", false, "also run GitHub auth and signing checks")
	file := fs.String("file", "packages.csv", "path to packages.csv")
	skipFlag := fs.String("skip", "", "comma-separated local checks to skip: stow,shell,packages")
	if err := fs.Parse(args); err != nil {
		return 2
	}

	plat, err := Detect()
	if err != nil {
		fmt.Fprintf(stderr, "dotctl verify: %v\n", err)
		return 1
	}
	r := ExecRunner{}
	skip := splitList(*skipFlag)

	errs := verifyLocal(plat, r, *file, skip, stdout, stderr)

	if *remote {
		errs = append(errs, verifyRemote(r, stdout)...)
	} else {
		fmt.Fprintln(stdout, "verify: GitHub auth/signing pending (run with --remote after registering the key)")
	}

	for _, e := range errs {
		fmt.Fprintln(stderr, e)
	}
	if len(errs) > 0 {
		fmt.Fprintf(stderr, "verify: %d issue(s) found\n", len(errs))
		return 1
	}
	fmt.Fprintln(stdout, "verify: OK")
	return 0
}
