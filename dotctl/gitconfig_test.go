package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRenderConfigLocalMac(t *testing.T) {
	got := RenderConfigLocal("me@example.com", PlatformMacOS)
	for _, want := range []string{
		"[user]", "email = me@example.com",
		"helper = osxkeychain",
		`[credential "https://github.com"]`,
		"helper = !gh auth git-credential",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("output missing %q:\n%s", want, got)
		}
	}
	if strings.Contains(got, "op-ssh-sign") {
		t.Fatalf("config.local must not contain 1Password lines:\n%s", got)
	}
}

func TestRenderConfigLocalLinuxHelper(t *testing.T) {
	got := RenderConfigLocal("me@example.com", PlatformArch)
	if !strings.Contains(got, "helper = cache") {
		t.Fatalf("linux helper should be cache:\n%s", got)
	}
}

func TestWriteConfigLocalNoClobber(t *testing.T) {
	dir := t.TempDir()
	// pre-existing machine file must never be touched
	machine := filepath.Join(dir, "config.machine")
	os.WriteFile(machine, []byte("[gpg \"ssh\"]\n\tprogram = op-ssh-sign\n"), 0o644)

	if _, err := writeConfigLocal(dir, "a@x.com", PlatformMacOS, false); err != nil {
		t.Fatal(err)
	}
	// second call without force must not overwrite
	if _, err := writeConfigLocal(dir, "b@y.com", PlatformMacOS, false); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(filepath.Join(dir, "config.local"))
	if !strings.Contains(string(got), "a@x.com") {
		t.Fatalf("no-force overwrote config.local: %s", got)
	}
	// force overwrites config.local only
	if _, err := writeConfigLocal(dir, "b@y.com", PlatformMacOS, true); err != nil {
		t.Fatal(err)
	}
	got, _ = os.ReadFile(filepath.Join(dir, "config.local"))
	if !strings.Contains(string(got), "b@y.com") {
		t.Fatalf("force did not re-render: %s", got)
	}
	// config.machine untouched throughout
	m, _ := os.ReadFile(machine)
	if !strings.Contains(string(m), "op-ssh-sign") {
		t.Fatalf("config.machine was modified: %s", m)
	}
}
