package main

import (
	"strings"
	"testing"
)

func TestCheckDefaultShell(t *testing.T) {
	if err := checkDefaultShell("/bin/zsh"); err != nil {
		t.Fatalf("/bin/zsh should pass: %v", err)
	}
	if err := checkDefaultShell("/opt/homebrew/bin/zsh"); err != nil {
		t.Fatalf("homebrew zsh should pass: %v", err)
	}
	if err := checkDefaultShell("/bin/bash"); err == nil {
		t.Fatal("/bin/bash should fail")
	}
}

func TestCheckTools(t *testing.T) {
	r := &fakeRunner{present: map[string]bool{"git": true}} // curl missing
	errs := checkTools(r, []string{"git", "curl"})
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "curl") {
		t.Fatalf("errs = %v, want one about curl", errs)
	}
}

func TestCheckRequiredInstalled(t *testing.T) {
	r := &fakeRunner{present: map[string]bool{"git": true}} // jq missing
	plan := []Resolved{{Name: "git"}, {Name: "jq"}}
	errs := checkRequiredInstalled(r, plan)
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "jq") {
		t.Fatalf("errs = %v, want one about jq", errs)
	}
}

func TestCheckSymlinks(t *testing.T) {
	isLink := func(p string) bool { return p == "/home/x/.gitconfig" }
	errs := checkSymlinks(isLink, []string{"/home/x/.gitconfig", "/home/x/.zshrc"})
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), ".zshrc") {
		t.Fatalf("errs = %v, want one about .zshrc", errs)
	}
}

func TestLoginShellParseLinux(t *testing.T) {
	r := &fakeRunner{outputs: map[string]string{
		"getent": "user:x:1000:1000::/home/user:/usr/bin/zsh\n",
	}}
	got, err := loginShell(PlatformUbuntu, r)
	if err != nil {
		t.Fatalf("loginShell error: %v", err)
	}
	if got != "/usr/bin/zsh" {
		t.Fatalf("loginShell = %q, want /usr/bin/zsh", got)
	}
}

func TestLoginShellParseMacOS(t *testing.T) {
	r := &fakeRunner{outputs: map[string]string{
		"dscl": "UserShell: /bin/zsh\n",
	}}
	got, err := loginShell(PlatformMacOS, r)
	if err != nil {
		t.Fatalf("loginShell error: %v", err)
	}
	if got != "/bin/zsh" {
		t.Fatalf("loginShell = %q, want /bin/zsh", got)
	}
}

func TestLoginShellPropagatesRunError(t *testing.T) {
	r := &fakeRunner{failCmds: map[string]bool{"getent": true}}
	if _, err := loginShell(PlatformArch, r); err == nil {
		t.Fatal("expected error when getent fails")
	}
}
