package main

import (
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
