package main

import (
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

func keyBody(pubkey string) string {
	f := strings.Fields(strings.TrimSpace(pubkey))
	if len(f) >= 2 {
		return f[0] + " " + f[1] // type + base64 body, comment dropped
	}
	return strings.TrimSpace(pubkey)
}

func AllowedSignerLine(email, pubkey string) string {
	return email + " " + strings.TrimSpace(pubkey)
}

func AddAllowedSigner(existing []string, email, pubkey string) ([]string, bool) {
	want := email + " " + keyBody(pubkey)
	for _, ln := range existing {
		f := strings.Fields(ln)
		if len(f) >= 3 && f[0]+" "+f[1]+" "+f[2] == want {
			return existing, false
		}
	}
	return append(existing, AllowedSignerLine(email, pubkey)), true
}

func runAllowedSigners(args []string, stdout, stderr io.Writer) int {
	if len(args) < 1 || args[0] != "add" {
		fmt.Fprintln(stderr, "usage: dotctl allowed-signers add -file <f> -email <e> -pubkey <path>")
		return 2
	}
	fs := flag.NewFlagSet("allowed-signers add", flag.ContinueOnError)
	fs.SetOutput(stderr)
	file := fs.String("file", "", "allowed_signers path")
	email := fs.String("email", "", "committing email")
	pubPath := fs.String("pubkey", "", "path to the .pub file")
	if err := fs.Parse(args[1:]); err != nil {
		return 2
	}
	if *file == "" || *email == "" || *pubPath == "" {
		fmt.Fprintln(stderr, "dotctl allowed-signers add: -file, -email, -pubkey are required")
		return 2
	}
	pub, err := os.ReadFile(*pubPath)
	if err != nil {
		fmt.Fprintf(stderr, "dotctl allowed-signers add: %v\n", err)
		return 1
	}
	var existing []string
	if data, err := os.ReadFile(*file); err == nil {
		for _, ln := range strings.Split(string(data), "\n") {
			if strings.TrimSpace(ln) != "" {
				existing = append(existing, ln)
			}
		}
	}
	updated, changed := AddAllowedSigner(existing, *email, string(pub))
	if !changed {
		fmt.Fprintln(stdout, "allowed-signers: already present")
		return 0
	}
	if err := os.MkdirAll(filepath.Dir(*file), 0o755); err != nil {
		fmt.Fprintf(stderr, "dotctl allowed-signers add: %v\n", err)
		return 1
	}
	if err := os.WriteFile(*file, []byte(strings.Join(updated, "\n")+"\n"), 0o644); err != nil {
		fmt.Fprintf(stderr, "dotctl allowed-signers add: %v\n", err)
		return 1
	}
	fmt.Fprintf(stdout, "allowed-signers: added key for %s\n", *email)
	return 0
}
