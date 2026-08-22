package main

import "testing"

type fakeRunner struct {
	present map[string]bool
	calls   [][]string
}

func (f *fakeRunner) Run(name string, args ...string) error {
	f.calls = append(f.calls, append([]string{name}, args...))
	return nil
}
func (f *fakeRunner) Look(name string) bool { return f.present[name] }

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
