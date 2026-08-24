package main

import (
	"bytes"
	"os"
	"slices"
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

// sshSigningConfig is the git config a machine using on-disk ssh signing
// reports; tests override individual keys to model misconfiguration.
func sshSigningConfig() map[string]string {
	return map[string]string{
		"git config --get gpg.format":                 "ssh\n",
		"git config --get user.signingkey":            "~/.ssh/id_ed25519.pub\n",
		"git config --get gpg.ssh.allowedSignersFile": "~/.config/git/allowed_signers\n",
	}
}

// An explicit non-ssh format (a machine that opted into gpg/openpgp signing)
// is out of scope for this check and skips cleanly.
func TestCheckSigningSkipsExplicitNonSSHFormat(t *testing.T) {
	r := &fakeRunner{outArgs: map[string]string{"git config --get gpg.format": "openpgp\n"}}
	var out bytes.Buffer
	if errs := checkSigning(r, &out); len(errs) != 0 {
		t.Fatalf("openpgp machine should skip, got %v", errs)
	}
	if !strings.Contains(out.String(), "skipped signing") {
		t.Fatalf("stdout = %q, want a skip note", out.String())
	}
}

// Unset gpg.format is the fresh-machine failure this check exists to catch
// (the committed .gitconfig always sets ssh, so empty means it is not linked),
// so it must fail, not skip.
func TestCheckSigningFailsWhenFormatUnset(t *testing.T) {
	r := &fakeRunner{}
	var out bytes.Buffer
	errs := checkSigning(r, &out)
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "gpg.format is unset") {
		t.Fatalf("errs = %v, want one about gpg.format being unset", errs)
	}
	if strings.Contains(out.String(), "skipped") {
		t.Fatalf("unset format must not print a skip note; stdout = %q", out.String())
	}
}

// A git config read that fails for a reason other than "unset" (git absent,
// dubious ownership) must surface as an error, never collapse into a pass.
func TestCheckSigningFailsWhenGitConfigErrors(t *testing.T) {
	r := &fakeRunner{failCmds: map[string]bool{"git": true}}
	errs := checkSigning(r, &bytes.Buffer{})
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "reading git config") {
		t.Fatalf("errs = %v, want one about reading git config failing", errs)
	}
}

func TestCheckSigningPasses(t *testing.T) {
	r := &fakeRunner{outArgs: sshSigningConfig()}
	if errs := checkSigning(r, &bytes.Buffer{}); len(errs) != 0 {
		t.Fatalf("expected clean, got %v", errs)
	}
}

// The failing check must be the machine's own signing key, never HEAD: every
// commit on a squash-merged repo is signed by GitHub's GPG key.
func TestCheckSigningNeverInspectsHEAD(t *testing.T) {
	r := &fakeRunner{outArgs: sshSigningConfig()}
	checkSigning(r, &bytes.Buffer{})
	for _, call := range r.calls {
		if call[0] == "git" && len(call) > 1 && call[1] == "verify-commit" {
			t.Fatalf("checkSigning ran %v; it must not verify HEAD", call)
		}
	}
}

func TestCheckSigningExpandsHomeAndSignsWithConfiguredKey(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	r := &fakeRunner{outArgs: sshSigningConfig()}
	checkSigning(r, &bytes.Buffer{})

	var signCall []string
	for _, call := range r.calls {
		if call[0] == "ssh-keygen" && len(call) > 2 && call[2] == "sign" {
			signCall = call
		}
	}
	if signCall == nil {
		t.Fatal("no ssh-keygen -Y sign call")
	}
	want := home + "/.ssh/id_ed25519.pub"
	if !slices.Contains(signCall, want) {
		t.Fatalf("sign call = %v, want it to reference %q", signCall, want)
	}
}

func TestCheckSigningUsesGpgSSHProgramWhenSet(t *testing.T) {
	cfg := sshSigningConfig()
	cfg["git config --get gpg.ssh.program"] = "op-ssh-sign\n"
	r := &fakeRunner{outArgs: cfg}
	checkSigning(r, &bytes.Buffer{})

	for _, call := range r.calls {
		if len(call) > 2 && call[1] == "-Y" && call[2] == "sign" {
			if call[0] != "op-ssh-sign" {
				t.Fatalf("signed with %q, want op-ssh-sign", call[0])
			}
			return
		}
	}
	t.Fatal("no sign call")
}

func TestCheckSigningFailsWhenKeyCannotSign(t *testing.T) {
	r := &fakeRunner{outArgs: sshSigningConfig(), failCmds: map[string]bool{"ssh-keygen": true}}
	errs := checkSigning(r, &bytes.Buffer{})
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "cannot sign") {
		t.Fatalf("errs = %v, want one about signing failing", errs)
	}
}

func TestCheckSigningFailsWhenKeyNotInAllowedSigners(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	r := &fakeRunner{outArgs: sshSigningConfig()}
	// only find-principals fails: the key signs fine but is untrusted
	r.failPrefix = map[string]bool{"ssh-keygen -Y find-principals": true}
	errs := checkSigning(r, &bytes.Buffer{})
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "not trusted") {
		t.Fatalf("errs = %v, want one about the key being untrusted", errs)
	}
}

func TestCheckSigningFailsWhenSigningKeyUnset(t *testing.T) {
	cfg := sshSigningConfig()
	delete(cfg, "git config --get user.signingkey")
	r := &fakeRunner{outArgs: cfg}
	errs := checkSigning(r, &bytes.Buffer{})
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "user.signingkey") {
		t.Fatalf("errs = %v, want one about user.signingkey", errs)
	}
}

func TestCheckSigningFailsWhenAllowedSignersUnset(t *testing.T) {
	cfg := sshSigningConfig()
	delete(cfg, "git config --get gpg.ssh.allowedSignersFile")
	r := &fakeRunner{outArgs: cfg}
	errs := checkSigning(r, &bytes.Buffer{})
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "allowedSignersFile") {
		t.Fatalf("errs = %v, want one about allowedSignersFile", errs)
	}
}

func signCallOf(calls [][]string) []string {
	for _, call := range calls {
		if len(call) > 2 && call[1] == "-Y" && call[2] == "sign" {
			return call
		}
	}
	return nil
}

// An inline user.signingkey (1Password's documented form) must be materialized
// to a real file before it reaches `-f`, not passed as a literal path.
func TestCheckSigningMaterializesInlineKey(t *testing.T) {
	const inline = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKS5NsNhO9laxuHLOTqucIfjuefLiVgb0i3arbAUWQVv"
	cfg := sshSigningConfig()
	cfg["git config --get user.signingkey"] = inline + "\n"
	r := &fakeRunner{outArgs: cfg}
	if errs := checkSigning(r, &bytes.Buffer{}); len(errs) != 0 {
		t.Fatalf("expected clean with inline key, got %v", errs)
	}
	sc := signCallOf(r.calls)
	if sc == nil {
		t.Fatal("no sign call")
	}
	keyArg := sc[len(sc)-2] // -f <key> -n git <probe> => key is 4th from end... resolve by scanning
	for i, a := range sc {
		if a == "-f" && i+1 < len(sc) {
			keyArg = sc[i+1]
		}
	}
	if keyArg == inline {
		t.Fatal("inline key was passed to -f verbatim; want a materialized file path")
	}
	if !strings.HasSuffix(keyArg, "signingkey.pub") {
		t.Fatalf("-f arg = %q, want a materialized signingkey.pub path", keyArg)
	}
}

// The "key::" prefix explicitly marks literal key material and is stripped
// before the key is written out.
func TestResolveSigningKeyStripsKeyPrefix(t *testing.T) {
	dir := t.TempDir()
	const material = "ssh-ed25519 AAAAB3keymaterial"
	path, err := resolveSigningKey("key::"+material, dir)
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(string(data)) != material {
		t.Fatalf("wrote %q, want %q (key:: prefix stripped)", data, material)
	}
}

func TestResolveSigningKeyPassesThroughPath(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	got, err := resolveSigningKey("~/.ssh/id_ed25519.pub", t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if got != home+"/.ssh/id_ed25519.pub" {
		t.Fatalf("resolveSigningKey path = %q, want the expanded path", got)
	}
}

// The key can be present in allowed_signers under a different email than the
// one this machine commits under; find-principals still exits 0, but real
// commits would not verify. The check must catch that.
func TestCheckSigningFailsWhenKeyTrustedForWrongEmail(t *testing.T) {
	cfg := sshSigningConfig()
	cfg["git config --get user.email"] = "home@example.com\n"
	r := &fakeRunner{
		outArgs: cfg,
		// find-principals (and the ignored sign call) return a different email
		outputs: map[string]string{"ssh-keygen": "work@example.com\n"},
	}
	errs := checkSigning(r, &bytes.Buffer{})
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "committing identity") {
		t.Fatalf("errs = %v, want one about the committing identity", errs)
	}
}

func TestCheckSigningPassesWhenKeyTrustedForCommittingEmail(t *testing.T) {
	cfg := sshSigningConfig()
	cfg["git config --get user.email"] = "home@example.com\n"
	r := &fakeRunner{
		outArgs: cfg,
		outputs: map[string]string{"ssh-keygen": "home@example.com\n"},
	}
	if errs := checkSigning(r, &bytes.Buffer{}); len(errs) != 0 {
		t.Fatalf("expected clean when email matches a principal, got %v", errs)
	}
}

func TestExpandHome(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	if got := expandHome("~/.ssh/id_ed25519.pub"); got != home+"/.ssh/id_ed25519.pub" {
		t.Fatalf("expandHome = %q", got)
	}
	if got := expandHome("/abs/path"); got != "/abs/path" {
		t.Fatalf("absolute path should pass through, got %q", got)
	}
	if got := expandHome("~notme/key"); got != "~notme/key" {
		t.Fatalf("~user should pass through untouched, got %q", got)
	}
}
