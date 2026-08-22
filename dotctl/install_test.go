package main

import (
	"reflect"
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
		{PlatformArch, Resolved{Name: "bat", Kind: KindNormal}, [][]string{{"pacman", "-S", "--needed", "--noconfirm", "bat"}}},
		{PlatformArch, Resolved{Name: "eza", Kind: KindAUR}, [][]string{{"yay", "-S", "--needed", "--noconfirm", "eza"}}},
		{PlatformUbuntu, Resolved{Name: "bat", Kind: KindNormal}, [][]string{{"apt-get", "install", "-y", "bat"}}},
	}
	for _, c := range cases {
		got := installCmd(c.plat, c.r)
		if !reflect.DeepEqual(got, c.want) {
			t.Fatalf("installCmd(%s,%+v)=%v want %v", c.plat, c.r, got, c.want)
		}
	}
}

func TestInstallRunsEachPackage(t *testing.T) {
	r := &fakeRunner{present: map[string]bool{}}
	plan := []Resolved{{Name: "bat", Kind: KindNormal}, {Name: "jq", Kind: KindNormal}}
	if err := Install(PlatformUbuntu, plan, r); err != nil {
		t.Fatalf("Install error: %v", err)
	}
	if len(r.calls) != 2 {
		t.Fatalf("made %d calls, want 2: %v", len(r.calls), r.calls)
	}
	if !reflect.DeepEqual(r.calls[0], []string{"apt-get", "install", "-y", "bat"}) {
		t.Fatalf("call0 = %v", r.calls[0])
	}
}
