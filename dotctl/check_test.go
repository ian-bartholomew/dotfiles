package main

import (
	"fmt"
	"strings"
	"testing"
)

type fakeRunner struct {
	present  map[string]bool
	calls    [][]string
	failCmds map[string]bool
	outputs  map[string]string
	// failArgs injects a RunOut error keyed by the full command line
	// ("name arg1 arg2 ..."), for callers that invoke the same command name
	// with different args expecting different results (e.g. per-package
	// install checks).
	failArgs map[string]bool
}

func (f *fakeRunner) Run(name string, args ...string) error {
	f.calls = append(f.calls, append([]string{name}, args...))
	if f.failCmds != nil && f.failCmds[name] {
		return fmt.Errorf("mock failure: %s", name)
	}
	return nil
}
func (f *fakeRunner) Look(name string) bool { return f.present[name] }

func (f *fakeRunner) RunOut(name string, args ...string) (string, error) {
	f.calls = append(f.calls, append([]string{name}, args...))
	if f.failCmds != nil && f.failCmds[name] {
		return "", fmt.Errorf("mock failure: %s", name)
	}
	if f.failArgs != nil && f.failArgs[strings.Join(append([]string{name}, args...), " ")] {
		return "", fmt.Errorf("mock failure: %s %s", name, strings.Join(args, " "))
	}
	return f.outputs[name], nil
}

func TestCheckMissingTools(t *testing.T) {
	r := &fakeRunner{present: map[string]bool{"brew": true}} // git and curl missing
	errs := Check(PlatformMacOS, r)
	if len(errs) != 2 {
		t.Fatalf("got %d errors, want 2 (git, curl): %v", len(errs), errs)
	}
}

func TestCheckCleanArch(t *testing.T) {
	r := &fakeRunner{present: map[string]bool{"pacman": true, "git": true, "curl": true}}
	if errs := Check(PlatformArch, r); len(errs) != 0 {
		t.Fatalf("expected clean, got %v", errs)
	}
}
