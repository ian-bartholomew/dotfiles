package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Confirms the base include ordering: a value in config.local overrides the base.
// This is the mechanic the layered gitconfig relies on (local/machine win over base).
func TestBaseGitconfigIncludeOrder(t *testing.T) {
	home := t.TempDir()
	gitcfg := filepath.Join(home, ".gitconfig")
	if err := os.WriteFile(gitcfg, []byte("[user]\n\temail = base@none\n[include]\n\tpath = "+home+"/.config/git/config.local\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(home, ".config", "git"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, ".config", "git", "config.local"), []byte("[user]\n\temail = local@wins\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	repo := t.TempDir()
	run := func(args ...string) string {
		c := exec.Command("git", args...)
		c.Dir = repo
		c.Env = append(os.Environ(), "HOME="+home, "GIT_CONFIG_NOSYSTEM=1")
		out, err := c.Output()
		if err != nil {
			t.Fatalf("git %v: %v", args, err)
		}
		return string(out)
	}
	run("init")
	if got := run("config", "user.email"); got != "local@wins\n" {
		t.Fatalf("include order wrong: user.email = %q, want local@wins", got)
	}
}

// TestRealGitconfigIncludeChain reads the actual committed base gitconfig
// (stow-packages/git/.gitconfig) and lays it out with real config.local and
// config.machine includes the way dotctl does on a real machine. It proves
// the full chain: base values resolve, config.local overrides the base, and
// config.machine (the last include) overrides both the base and config.local
// -- the mechanic 1Password SSH signing in production depends on.
func TestRealGitconfigIncludeChain(t *testing.T) {
	baseContent, err := os.ReadFile("../stow-packages/git/.gitconfig")
	if err != nil {
		t.Fatalf("reading real base gitconfig: %v", err)
	}

	home := t.TempDir()
	if err := os.WriteFile(filepath.Join(home, ".gitconfig"), baseContent, 0o644); err != nil {
		t.Fatal(err)
	}

	gitDir := filepath.Join(home, ".config", "git")
	if err := os.MkdirAll(gitDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(gitDir, "config.local"), []byte("[user]\n\temail = local@example.com\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(gitDir, "config.machine"), []byte("[user]\n\tsigningkey = MACHINE-OVERRIDE-KEY\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	repo := t.TempDir()
	run := func(args ...string) string {
		c := exec.Command("git", args...)
		c.Dir = repo
		c.Env = append(os.Environ(), "HOME="+home, "GIT_CONFIG_NOSYSTEM=1")
		out, err := c.Output()
		if err != nil {
			t.Fatalf("git %v: %v", args, err)
		}
		return strings.TrimRight(string(out), "\n")
	}
	run("init")

	if got, want := run("config", "user.signingkey"), "MACHINE-OVERRIDE-KEY"; got != want {
		t.Errorf("user.signingkey = %q, want %q (config.machine should override base)", got, want)
	}
	if got, want := run("config", "user.email"), "local@example.com"; got != want {
		t.Errorf("user.email = %q, want %q (config.local include should apply)", got, want)
	}
	if got, want := run("config", "user.name"), "Ian Bartholomew"; got != want {
		t.Errorf("user.name = %q, want %q (base value should still resolve)", got, want)
	}
	if got, want := run("config", "merge.tool"), "nvimdiff"; got != want {
		t.Errorf("merge.tool = %q, want %q (base value should still resolve)", got, want)
	}
}
