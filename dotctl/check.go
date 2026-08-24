package main

import (
	"fmt"
	"io"
)

func pkgMgrFor(plat Platform) string {
	switch plat {
	case PlatformMacOS:
		return "brew"
	case PlatformArch:
		return "pacman"
	default:
		return "apt-get"
	}
}

func Check(plat Platform, r Runner) []error {
	var errs []error
	if mgr := pkgMgrFor(plat); !r.Look(mgr) {
		errs = append(errs, fmt.Errorf("package manager %q not found", mgr))
	}
	for _, tool := range []string{"git", "curl"} {
		if !r.Look(tool) {
			errs = append(errs, fmt.Errorf("required tool %q not found", tool))
		}
	}
	return errs
}

func runCheck(args []string, stdout, stderr io.Writer) int {
	plat, err := Detect()
	if err != nil {
		fmt.Fprintf(stderr, "dotctl check: %v\n", err)
		return 1
	}
	errs := Check(plat, ExecRunner{})
	for _, e := range errs {
		fmt.Fprintln(stderr, e)
	}
	if len(errs) > 0 {
		return 1
	}
	fmt.Fprintf(stdout, "check: %s prerequisites OK\n", plat)
	return 0
}
