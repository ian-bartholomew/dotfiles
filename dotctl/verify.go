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

func checkRequiredInstalled(r Runner, plan []Resolved) []error {
	var errs []error
	for _, p := range plan {
		if !r.Look(p.Name) {
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
		fmt.Fprintln(stdout, "verify: no commits yet, skipping signature check")
	} else if out, err := r.RunOut("git", "verify-commit", "HEAD"); err != nil {
		msg := strings.TrimSpace(out)
		if msg == "" {
			msg = err.Error()
		}
		errs = append(errs, fmt.Errorf("signature verify failed for HEAD: %s", msg))
	}

	return errs
}

func runVerify(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("verify", flag.ContinueOnError)
	fs.SetOutput(stderr)
	remote := fs.Bool("remote", false, "also run GitHub auth and signing checks")
	file := fs.String("file", "packages.csv", "path to packages.csv")
	if err := fs.Parse(args); err != nil {
		return 2
	}

	plat, err := Detect()
	if err != nil {
		fmt.Fprintf(stderr, "dotctl verify: %v\n", err)
		return 1
	}
	r := ExecRunner{}
	var errs []error

	errs = append(errs, checkTools(r, []string{"git", "curl", "stow"})...)

	if sh, err := loginShell(plat, r); err != nil {
		errs = append(errs, fmt.Errorf("login shell lookup failed: %w", err))
	} else if err := checkDefaultShell(sh); err != nil {
		errs = append(errs, err)
	}

	if home, err := os.UserHomeDir(); err != nil {
		errs = append(errs, fmt.Errorf("cannot determine home dir: %w", err))
	} else {
		errs = append(errs, checkSymlinks(isSymlink, []string{
			filepath.Join(home, ".gitconfig"),
		})...)
	}

	if f, err := os.Open(*file); err != nil {
		errs = append(errs, fmt.Errorf("cannot read %s: %w", *file, err))
	} else {
		defer f.Close()
		if pkgs, err := ParsePackages(f); err != nil {
			errs = append(errs, fmt.Errorf("cannot parse %s: %w", *file, err))
		} else {
			plan := BuildPlan(pkgs, plat, Selection{})
			errs = append(errs, checkRequiredInstalled(r, plan)...)
		}
	}

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
