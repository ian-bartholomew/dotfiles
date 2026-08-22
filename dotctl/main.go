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
		fmt.Fprintln(stderr, "usage: dotctl <check|lint|migrate|install|gitconfig|allowed-signers|verify> [flags]")
		return 2
	}
	switch args[0] {
	case "check":
		return runCheck(args[1:], stdout, stderr)
	case "lint":
		return runLint(args[1:], stdout, stderr)
	case "migrate":
		return runMigrate(args[1:], stdout, stderr)
	case "install":
		return runInstall(args[1:], stdout, stderr)
	case "gitconfig":
		return runGitconfig(args[1:], stdout, stderr)
	case "allowed-signers":
		return runAllowedSigners(args[1:], stdout, stderr)
	case "verify":
		return runVerify(args[1:], stdout, stderr)
	default:
		fmt.Fprintf(stderr, "dotctl: unknown command %q\n", args[0])
		fmt.Fprintln(stderr, "usage: dotctl <check|lint|migrate|install|gitconfig|allowed-signers|verify> [flags]")
		return 2
	}
}
