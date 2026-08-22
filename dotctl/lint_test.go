package main

import "testing"

func TestLintCatchesMisplacedPrefixes(t *testing.T) {
	pkgs := []Pkg{
		{Category: "shell", Brew: "AUR:eza", Pacman: "eza", Apt: "eza", Line: 3},  // AUR in brew
		{Category: "shell", Brew: "bat", Pacman: "bat", Apt: "CASK:bat", Line: 4}, // CASK in apt
		{Category: "shell", Brew: "-", Pacman: "-", Apt: "-", Line: 5},            // all skipped
	}
	errs := Lint(pkgs)
	if len(errs) != 3 {
		t.Fatalf("got %d errors, want 3: %v", len(errs), errs)
	}
}

func TestLintClean(t *testing.T) {
	pkgs := []Pkg{{Category: "shell", Brew: "eza", Pacman: "eza", Apt: "-", Line: 3}}
	if errs := Lint(pkgs); len(errs) != 0 {
		t.Fatalf("expected clean, got %v", errs)
	}
}
