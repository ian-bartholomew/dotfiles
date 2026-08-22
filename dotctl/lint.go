package main

import (
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
)

func Lint(pkgs []Pkg) []error {
	var errs []error
	for _, p := range pkgs {
		if strings.HasPrefix(p.Brew, "AUR:") {
			errs = append(errs, fmt.Errorf("line %d: AUR: prefix not allowed in brew column", p.Line))
		}
		if strings.HasPrefix(p.Apt, "AUR:") {
			errs = append(errs, fmt.Errorf("line %d: AUR: prefix not allowed in apt column", p.Line))
		}
		if strings.HasPrefix(p.Pacman, "CASK:") {
			errs = append(errs, fmt.Errorf("line %d: CASK: prefix not allowed in pacman column", p.Line))
		}
		if strings.HasPrefix(p.Apt, "CASK:") {
			errs = append(errs, fmt.Errorf("line %d: CASK: prefix not allowed in apt column", p.Line))
		}
		if p.Brew == "-" && p.Pacman == "-" && p.Apt == "-" {
			errs = append(errs, fmt.Errorf("line %d: all platform columns are '-'", p.Line))
		}
	}
	return errs
}

func runLint(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("lint", flag.ContinueOnError)
	fs.SetOutput(stderr)
	file := fs.String("file", "packages.csv", "path to packages.csv")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	f, err := os.Open(*file)
	if err != nil {
		fmt.Fprintf(stderr, "dotctl lint: %v\n", err)
		return 1
	}
	defer f.Close()
	pkgs, err := ParsePackages(f)
	if err != nil {
		fmt.Fprintf(stderr, "dotctl lint: %v\n", err)
		return 1
	}
	errs := Lint(pkgs)
	for _, e := range errs {
		fmt.Fprintln(stderr, e)
	}
	if len(errs) > 0 {
		return 1
	}
	fmt.Fprintf(stdout, "lint: %d packages OK\n", len(pkgs))
	return 0
}
