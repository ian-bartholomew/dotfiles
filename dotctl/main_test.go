package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestDispatchUnknownCommand(t *testing.T) {
	var out, errb bytes.Buffer
	code := dispatch([]string{"bogus"}, &out, &errb)
	if code != 2 {
		t.Fatalf("exit code = %d, want 2", code)
	}
	if !strings.Contains(errb.String(), "unknown command") {
		t.Fatalf("stderr = %q, want it to mention unknown command", errb.String())
	}
}

func TestDispatchNoArgsPrintsUsage(t *testing.T) {
	var out, errb bytes.Buffer
	code := dispatch(nil, &out, &errb)
	if code != 2 {
		t.Fatalf("exit code = %d, want 2", code)
	}
	if !strings.Contains(errb.String(), "usage:") {
		t.Fatalf("stderr = %q, want usage text", errb.String())
	}
}
