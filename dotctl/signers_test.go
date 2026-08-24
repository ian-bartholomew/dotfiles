package main

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestAddAllowedSignerDedup(t *testing.T) {
	key := "ssh-ed25519 AAAAKEYBODY comment-a"
	lines, changed := AddAllowedSigner(nil, "me@x.com", key)
	if !changed || len(lines) != 1 {
		t.Fatalf("first add: changed=%v lines=%v", changed, lines)
	}
	// same email + same key body but different comment -> already present
	lines2, changed2 := AddAllowedSigner(lines, "me@x.com", "ssh-ed25519 AAAAKEYBODY comment-b")
	if changed2 || len(lines2) != 1 {
		t.Fatalf("dedup failed: changed=%v lines=%v", changed2, lines2)
	}
	// different email -> new entry
	_, changed3 := AddAllowedSigner(lines, "work@x.com", key)
	if !changed3 {
		t.Fatal("different email should add a new entry")
	}
}

func TestRunAllowedSignersCreatesMissingParentDir(t *testing.T) {
	dir := t.TempDir()
	pubPath := filepath.Join(dir, "id_ed25519.pub")
	key := "ssh-ed25519 AAAAKEYBODY comment-a"
	if err := os.WriteFile(pubPath, []byte(key), 0o644); err != nil {
		t.Fatalf("write pubkey: %v", err)
	}

	// parent dir of file does not exist yet
	file := filepath.Join(dir, "git", ".config", "git", "allowed_signers")

	var stdout, stderr bytes.Buffer
	rc := runAllowedSigners([]string{"add", "-file", file, "-email", "me@x.com", "-pubkey", pubPath}, &stdout, &stderr)
	if rc != 0 {
		t.Fatalf("rc = %d, want 0; stderr = %s", rc, stderr.String())
	}

	data, err := os.ReadFile(file)
	if err != nil {
		t.Fatalf("allowed_signers file not created: %v", err)
	}
	want := AllowedSignerLine("me@x.com", key)
	if !bytes.Contains(data, []byte(want)) {
		t.Fatalf("allowed_signers content = %q, want it to contain %q", data, want)
	}
}
