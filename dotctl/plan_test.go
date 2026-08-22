package main

import "testing"

func TestResolveOne(t *testing.T) {
	r, ok := resolveOne(Pkg{Brew: "CASK:ghostty", Pacman: "AUR:ghostty", Apt: "-", Category: "shell"}, PlatformMacOS)
	if !ok || r.Kind != KindCask || r.Name != "ghostty" {
		t.Fatalf("macos resolve = %+v ok=%v", r, ok)
	}
	r, ok = resolveOne(Pkg{Brew: "CASK:ghostty", Pacman: "AUR:ghostty", Apt: "-", Category: "shell"}, PlatformArch)
	if !ok || r.Kind != KindAUR || r.Name != "ghostty" {
		t.Fatalf("arch resolve = %+v ok=%v", r, ok)
	}
	if _, ok := resolveOne(Pkg{Brew: "CASK:ghostty", Pacman: "AUR:ghostty", Apt: "-"}, PlatformUbuntu); ok {
		t.Fatal("ubuntu resolve should skip '-'")
	}
}

func TestBuildPlanRequiredPlusSelection(t *testing.T) {
	pkgs := []Pkg{
		{Category: "system", Required: true, Brew: "stow", Pacman: "stow", Apt: "stow"},
		{Category: "shell", Brew: "bat", Pacman: "bat", Apt: "bat"},
		{Category: "cloud", Brew: "awscli", Pacman: "AUR:aws-cli-v2", Apt: "-"},
	}
	// required only
	got := BuildPlan(pkgs, PlatformArch, Selection{Categories: map[string]bool{}, Packages: map[string]bool{}})
	if len(got) != 1 || got[0].Name != "stow" {
		t.Fatalf("required-only = %+v", got)
	}
	// required + shell category
	got = BuildPlan(pkgs, PlatformArch, Selection{Categories: map[string]bool{"shell": true}, Packages: map[string]bool{}})
	if len(got) != 2 || got[1].Name != "bat" {
		t.Fatalf("required+shell = %+v", got)
	}
	// all, on ubuntu the aws row ('-') is skipped
	got = BuildPlan(pkgs, PlatformUbuntu, Selection{All: true})
	if len(got) != 2 {
		t.Fatalf("ubuntu all = %+v (aws '-' should be skipped)", got)
	}
}
