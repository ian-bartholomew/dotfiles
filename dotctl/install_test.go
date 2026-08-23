package main

import (
	"reflect"
	"strings"
	"testing"
)

func TestInstallCmd(t *testing.T) {
	cases := []struct {
		plat Platform
		r    Resolved
		want [][]string
	}{
		{PlatformMacOS, Resolved{Name: "bat", Kind: KindNormal}, [][]string{{"brew", "install", "bat"}}},
		{PlatformMacOS, Resolved{Name: "ghostty", Kind: KindCask}, [][]string{{"brew", "install", "--cask", "ghostty"}}},
		{PlatformArch, Resolved{Name: "bat", Kind: KindNormal}, [][]string{{"sudo", "pacman", "-S", "--needed", "--noconfirm", "bat"}}},
		{PlatformArch, Resolved{Name: "eza", Kind: KindAUR}, [][]string{{"yay", "-S", "--needed", "--noconfirm", "eza"}}},
		{PlatformUbuntu, Resolved{Name: "bat", Kind: KindNormal}, [][]string{{"sudo", "apt-get", "install", "-y", "bat"}}},
	}
	for _, c := range cases {
		got := installCmd(c.plat, c.r, false)
		if !reflect.DeepEqual(got, c.want) {
			t.Fatalf("installCmd(%s,%+v)=%v want %v", c.plat, c.r, got, c.want)
		}
	}
}

func TestInstallCmdRootDropsSudo(t *testing.T) {
	got := installCmd(PlatformUbuntu, Resolved{Name: "bat", Kind: KindNormal}, true)
	if !reflect.DeepEqual(got, [][]string{{"apt-get", "install", "-y", "bat"}}) {
		t.Fatalf("root ubuntu = %v (want no sudo)", got)
	}
	got = installCmd(PlatformArch, Resolved{Name: "bat", Kind: KindNormal}, false)
	if !reflect.DeepEqual(got, [][]string{{"sudo", "pacman", "-S", "--needed", "--noconfirm", "bat"}}) {
		t.Fatalf("non-root arch = %v (want sudo)", got)
	}
}

func TestInstallRunsEachPackage(t *testing.T) {
	r := &fakeRunner{present: map[string]bool{}}
	plan := []Resolved{{Name: "bat", Kind: KindNormal}, {Name: "jq", Kind: KindNormal}}
	if err := Install(PlatformUbuntu, plan, r, false); err != nil {
		t.Fatalf("Install error: %v", err)
	}
	if len(r.calls) != 2 {
		t.Fatalf("made %d calls, want 2: %v", len(r.calls), r.calls)
	}
	if !reflect.DeepEqual(r.calls[0], []string{"sudo", "apt-get", "install", "-y", "bat"}) {
		t.Fatalf("call0 = %v", r.calls[0])
	}
}

func TestInstallCollectsErrors(t *testing.T) {
	r := &fakeRunner{present: map[string]bool{}, failCmds: map[string]bool{"sudo": true}}
	plan := []Resolved{{Name: "bat", Kind: KindNormal}, {Name: "jq", Kind: KindNormal}}
	err := Install(PlatformUbuntu, plan, r, false)
	if err == nil {
		t.Fatalf("Install should return error when runner fails")
	}
	if len(r.calls) != 2 {
		t.Fatalf("made %d calls, want 2 (no fail-fast): %v", len(r.calls), r.calls)
	}
	errMsg := err.Error()
	if !strings.Contains(errMsg, "bat") {
		t.Fatalf("error should mention failing package 'bat': %s", errMsg)
	}
}
