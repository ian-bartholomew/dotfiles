package main

import (
	"flag"
	"fmt"
	"io"
	"os"
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

// loginShell reports the user's configured login shell (not $SHELL, which
// reflects the running shell and may already be zsh from a temporary sub-shell).
func loginShell(plat Platform, r Runner) (string, error) {
	user := os.Getenv("USER")
	if user == "" {
		return "", fmt.Errorf("cannot determine current user: $USER is empty")
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

// verifyRemote runs the GitHub-dependent checks gated behind -remote. The ssh
// check never fails fast on a non-zero exit: `ssh -T git@github.com` always
// exits 1 on a successful auth, so success is read from the banner text
// (which lands on stderr, hence RunOut using CombinedOutput). The signing
// check verifies HEAD's existing signature read-only; it must never create a
// new signature, so it uses `git verify-commit`, never `git commit -S`.
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

	if _, err := r.RunOut("git", "rev-parse", "--verify", "HEAD"); err != nil {
		fmt.Fprintln(stdout, "verify: no signed HEAD to verify (no commits or not a git repository)")
	} else if out, err := r.RunOut("git", "verify-commit", "HEAD"); err != nil {
		// git verify-commit checks that HEAD is signed by a trusted key; it
		// does not confirm this machine can itself produce a new signature.
		msg := strings.TrimSpace(out)
		if msg == "" {
			msg = err.Error()
		}
		errs = append(errs, fmt.Errorf("signature verify failed for HEAD: %s", msg))
	}

	return errs
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
// preconditions, e.g. loginShell itself) rather than just their assertions,
// since loginShell errors when $USER/user lookup is empty on bare containers.
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
