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
	// outArgs supplies RunOut output keyed by the full command line, for
	// callers that invoke one command name with different args expecting
	// different results (e.g. several `git config --get <key>` reads).
	outArgs map[string]string
	// failPrefix injects a RunOut error for any command line starting with
	// the key, for callers whose args include a value the test cannot predict
	// (e.g. a path under a fresh temp dir).
	failPrefix map[string]bool
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
	line := strings.Join(append([]string{name}, args...), " ")
	if f.failArgs != nil && f.failArgs[line] {
		return "", fmt.Errorf("mock failure: %s %s", name, strings.Join(args, " "))
	}
	for prefix := range f.failPrefix {
		if strings.HasPrefix(line, prefix) {
			return "", fmt.Errorf("mock failure: %s", line)
		}
	}
	if out, ok := f.outArgs[line]; ok {
		return out, nil
	}
	return f.outputs[name], nil
}

// RunOutStdout mirrors RunOut for the fake: the combined-vs-stdout distinction
// is an ExecRunner concern (see runner_test.go), irrelevant to the maps here.
func (f *fakeRunner) RunOutStdout(name string, args ...string) (string, error) {
	return f.RunOut(name, args...)
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
