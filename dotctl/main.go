package main

import (
	"fmt"
	"io"
	"os"
)

func main() {
	os.Exit(dispatch(os.Args[1:], os.Stdout, os.Stderr))
}

func dispatch(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, "usage: dotctl <check|lint|migrate|install> [flags]")
		return 2
	}
	switch args[0] {
	case "lint":
		return runLint(args[1:], stdout, stderr)
	default:
		fmt.Fprintf(stderr, "dotctl: unknown command %q\n", args[0])
		fmt.Fprintln(stderr, "usage: dotctl <check|lint|migrate|install> [flags]")
		return 2
	}
}
