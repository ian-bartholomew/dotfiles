package main

import "testing"

// RunOutStdout must exclude stderr: config reads parse the value, and a git
// warning on stderr (exit 0) would otherwise be folded into it.
func TestExecRunnerRunOutStdoutExcludesStderr(t *testing.T) {
	out, err := ExecRunner{}.RunOutStdout("sh", "-c", "echo value; echo warning >&2")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if out != "value\n" {
		t.Fatalf("RunOutStdout = %q, want only stdout %q", out, "value\n")
	}
}

// RunOut keeps combining stderr (the ssh -T banner depends on it).
func TestExecRunnerRunOutIncludesStderr(t *testing.T) {
	out, err := ExecRunner{}.RunOut("sh", "-c", "echo warning >&2")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if out != "warning\n" {
		t.Fatalf("RunOut = %q, want the stderr text %q", out, "warning\n")
	}
}
