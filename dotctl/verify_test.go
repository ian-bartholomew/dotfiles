package main

import (
	"bytes"
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
	r := &fakeRunner{failArgs: map[string]bool{
		"brew list --versions jq": true, // jq reported missing by brew
	}}
	plan := []Resolved{{Name: "git"}, {Name: "jq"}}
	errs := checkRequiredInstalled(PlatformMacOS, r, plan)
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "jq") {
		t.Fatalf("errs = %v, want one about jq", errs)
	}
}

func TestInstalledCheckCmd(t *testing.T) {
	cases := []struct {
		name string
		plat Platform
		pkg  Resolved
		want []string
	}{
		{"macos formula", PlatformMacOS, Resolved{Name: "jq"}, []string{"brew", "list", "--versions", "jq"}},
		{"macos cask", PlatformMacOS, Resolved{Name: "docker", Kind: KindCask}, []string{"brew", "list", "--cask", "docker"}},
		{"arch", PlatformArch, Resolved{Name: "jq"}, []string{"pacman", "-Q", "jq"}},
		{"debian", PlatformUbuntu, Resolved{Name: "jq"}, []string{"dpkg", "-s", "jq"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := installedCheckCmd(c.plat, c.pkg)
			if strings.Join(got, " ") != strings.Join(c.want, " ") {
				t.Fatalf("installedCheckCmd(%v, %v) = %v, want %v", c.plat, c.pkg, got, c.want)
			}
		})
	}
}

func TestLoadRequiredPlanMissingFileDoesNotFail(t *testing.T) {
	var errb bytes.Buffer
	plan := loadRequiredPlan(PlatformMacOS, "/nonexistent/dir/packages.csv", &errb)
	if plan != nil {
		t.Fatalf("plan = %v, want nil for missing file", plan)
	}
	if !strings.Contains(errb.String(), "warning") {
		t.Fatalf("stderr = %q, want a warning about the missing file", errb.String())
	}

	errs := checkRequiredInstalled(PlatformMacOS, &fakeRunner{}, plan)
	if len(errs) != 0 {
		t.Fatalf("errs = %v, want none when the required-package check is skipped", errs)
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

// Regression: $USER is unset in containers/cron/non-login su. loginShell must
// fall back to the passwd DB via uid instead of erroring. Before the fix this
// returned "cannot determine current user".
func TestLoginShellFallsBackWhenUSERUnset(t *testing.T) {
	t.Setenv("USER", "")
	t.Setenv("LOGNAME", "")
	r := &fakeRunner{outputs: map[string]string{
		"getent": "root:x:0:0::/root:/usr/bin/zsh\n",
	}}
	got, err := loginShell(PlatformUbuntu, r)
	if err != nil {
		t.Fatalf("loginShell should fall back to os/user.Current() when $USER is empty, got error: %v", err)
	}
	if got != "/usr/bin/zsh" {
		t.Fatalf("loginShell = %q, want /usr/bin/zsh", got)
	}
}

func TestVerifyLocalSkipStowAndShell(t *testing.T) {
	r := &fakeRunner{present: map[string]bool{"git": true, "curl": true, "stow": true}}
	var stdout, stderr bytes.Buffer

	errs := verifyLocal(PlatformMacOS, r, "/nonexistent/dir/packages.csv", splitList("stow,shell"), &stdout, &stderr)

	for _, e := range errs {
		if strings.Contains(e.Error(), "shell") || strings.Contains(e.Error(), "symlink") {
			t.Fatalf("errs = %v, want no shell/stow errors when both are skipped", errs)
		}
	}
	out := stdout.String()
	if !strings.Contains(out, "verify: skipped stow") {
		t.Fatalf("stdout = %q, want a skipped-stow line", out)
	}
	if !strings.Contains(out, "verify: skipped shell") {
		t.Fatalf("stdout = %q, want a skipped-shell line", out)
	}
}

func TestVerifyLocalNoSkipStillRunsShellCheck(t *testing.T) {
	r := &fakeRunner{present: map[string]bool{"git": true, "curl": true, "stow": true}}
	var stdout, stderr bytes.Buffer

	errs := verifyLocal(PlatformUbuntu, r, "/nonexistent/dir/packages.csv", splitList(""), &stdout, &stderr)

	found := false
	for _, e := range errs {
		if strings.Contains(e.Error(), "login shell lookup failed") {
			found = true
		}
	}
	if !found {
		t.Fatalf("errs = %v, want a login shell lookup failure since it is not skipped and the fake getent output is unparsable", errs)
	}
}
