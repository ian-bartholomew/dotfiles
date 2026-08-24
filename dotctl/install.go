package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
)

func installCmd(plat Platform, r Resolved, root bool) [][]string {
	switch plat {
	case PlatformMacOS:
		if r.Kind == KindCask {
			return [][]string{{"brew", "install", "--cask", r.Name}}
		}
		return [][]string{{"brew", "install", r.Name}}
	case PlatformArch:
		if r.Kind == KindAUR {
			return [][]string{{"yay", "-S", "--needed", "--noconfirm", r.Name}}
		}
		if root {
			return [][]string{{"pacman", "-S", "--needed", "--noconfirm", r.Name}}
		}
		return [][]string{{"sudo", "pacman", "-S", "--needed", "--noconfirm", r.Name}}
	default:
		if root {
			return [][]string{{"apt-get", "install", "-y", r.Name}}
		}
		return [][]string{{"sudo", "apt-get", "install", "-y", r.Name}}
	}
}

func Install(plat Platform, plan []Resolved, run Runner, root bool) error {
	var errs []error
	for _, r := range plan {
		for _, argv := range installCmd(plat, r, root) {
			if err := run.Run(argv[0], argv[1:]...); err != nil {
				errs = append(errs, fmt.Errorf("%s: %w", r.Name, err))
			}
		}
	}
	return errors.Join(errs...)
}

func splitList(s string) map[string]bool {
	m := map[string]bool{}
	for _, part := range strings.Split(s, ",") {
		part = strings.TrimSpace(part)
		if part != "" {
			m[part] = true
		}
	}
	return m
}

func runInstall(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("install", flag.ContinueOnError)
	fs.SetOutput(stderr)
	file := fs.String("file", "packages.csv", "path to packages.csv")
	dryRun := fs.Bool("dry-run", false, "print the plan without installing")
	all := fs.Bool("all", false, "install every category")
	cats := fs.String("categories", "", "comma-separated categories to add")
	pkgsFlag := fs.String("packages", "", "comma-separated individual packages to add")
	if err := fs.Parse(args); err != nil {
		return 2
	}

	plat, err := Detect()
	if err != nil {
		fmt.Fprintf(stderr, "dotctl install: %v\n", err)
		return 1
	}
	f, err := os.Open(*file)
	if err != nil {
		fmt.Fprintf(stderr, "dotctl install: %v\n", err)
		return 1
	}
	defer f.Close()
	pkgs, err := ParsePackages(f)
	if err != nil {
		fmt.Fprintf(stderr, "dotctl install: %v\n", err)
		return 1
	}
	sel := Selection{All: *all, Categories: splitList(*cats), Packages: splitList(*pkgsFlag)}
	plan := BuildPlan(pkgs, plat, sel)

	if *dryRun {
		for _, r := range plan {
			fmt.Fprintf(stdout, "would install: %s (%s)\n", r.Name, r.Category)
		}
		fmt.Fprintf(stdout, "install (dry-run): %d packages\n", len(plan))
		return 0
	}
	root := os.Geteuid() == 0
	if err := Install(plat, plan, ExecRunner{}, root); err != nil {
		fmt.Fprintf(stderr, "dotctl install: %v\n", err)
		return 1
	}
	fmt.Fprintf(stdout, "install: %d packages\n", len(plan))
	return 0
}
