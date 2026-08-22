package main

import (
	"os"
	"os/exec"
	"path/filepath"
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
