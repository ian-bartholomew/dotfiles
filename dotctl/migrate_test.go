package main

import "testing"

func TestMigrateLine(t *testing.T) {
	// comment passes through
	if got := migrateLine("# Shell and terminal"); got != "# Shell and terminal" {
		t.Fatalf("comment changed: %q", got)
	}
	// required package gets yes
	got := migrateLine("shell | zsh | zsh | zsh |")
	want := "shell | yes | zsh | zsh | zsh |"
	if got != want {
		t.Fatalf("required row = %q want %q", got, want)
	}
	// non-required gets empty required column
	got = migrateLine("shell | bat | bat | bat | note")
	want = "shell |  | bat | bat | bat | note"
	if got != want {
		t.Fatalf("non-required row = %q want %q", got, want)
	}
}
