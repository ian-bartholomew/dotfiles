package main

import (
	"strings"
	"testing"
)

func TestParsePackages(t *testing.T) {
	in := `# comment
system | yes | stow | stow | stow |

shell |  | bat | bat | bat | a note
`
	pkgs, err := ParsePackages(strings.NewReader(in))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(pkgs) != 2 {
		t.Fatalf("len = %d, want 2", len(pkgs))
	}
	if pkgs[0].Category != "system" || !pkgs[0].Required || pkgs[0].Brew != "stow" {
		t.Fatalf("row0 = %+v", pkgs[0])
	}
	if pkgs[1].Required {
		t.Fatalf("row1 Required = true, want false (empty column)")
	}
	if pkgs[1].Notes != "a note" || pkgs[1].Line != 4 {
		t.Fatalf("row1 = %+v, want Notes=\"a note\" Line=4", pkgs[1])
	}
}

func TestParsePackagesWrongColumnCount(t *testing.T) {
	_, err := ParsePackages(strings.NewReader("system | yes | stow | stow |\n"))
	if err == nil {
		t.Fatal("expected error for 5-column row, got nil")
	}
	if !strings.Contains(err.Error(), "line 1") {
		t.Fatalf("error = %q, want it to cite line 1", err)
	}
}

func TestParsePackagesInvalidRequired(t *testing.T) {
	_, err := ParsePackages(strings.NewReader("system | maybe | stow | stow | stow |\n"))
	if err == nil {
		t.Fatal("expected error for invalid required value, got nil")
	}
	if !strings.Contains(err.Error(), "line 1") {
		t.Fatalf("error = %q, want it to cite line 1", err)
	}
}
