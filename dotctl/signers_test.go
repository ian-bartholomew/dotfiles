package main

import "testing"

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
