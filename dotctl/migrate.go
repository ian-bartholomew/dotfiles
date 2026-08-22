package main

import (
	"fmt"
	"io"
	"os"
	"strings"
)

var requiredSet = map[string]bool{
	"coreutils": true, "findutils": true, "grep": true, "gnu-sed": true,
	"wget": true, "curl": true, "htop": true, "btop": true, "tree": true,
	"stow": true, "less": true, "zsh": true, "tmux": true, "git": true,
	"tig": true, "neovim": true, "go": true, "python": true, "nodejs": true,
	"jq": true, "gh": true, "npm": true, "python-pip": true, "python-pipx": true,
}

func migrateLine(line string) string {
	trimmed := strings.TrimSpace(line)
	if trimmed == "" || strings.HasPrefix(trimmed, "#") {
		return line
	}
	fields := strings.Split(line, "|")
	if len(fields) != 5 {
		return line // already migrated or malformed; lint will catch it
	}
	name := func(i int) string { return strings.TrimSpace(fields[i]) }
	req := ""
	for _, i := range []int{1, 2, 3} { // brew, pacman, apt
		n := strings.TrimPrefix(strings.TrimPrefix(name(i), "AUR:"), "CASK:")
		if requiredSet[n] {
			req = "yes"
			break
		}
	}
	// splice: category | <required> | brew | pacman | apt | notes
	return fmt.Sprintf("%s| %s |%s|%s|%s|%s", fields[0], req, fields[1], fields[2], fields[3], fields[4])
}

func runMigrate(args []string, stdout, stderr io.Writer) int {
	file := "packages.csv"
	if len(args) == 2 && args[0] == "-file" {
		file = args[1]
	}
	data, err := os.ReadFile(file)
	if err != nil {
		fmt.Fprintf(stderr, "dotctl migrate: %v\n", err)
		return 1
	}
	lines := strings.Split(string(data), "\n")
	for i, l := range lines {
		lines[i] = migrateLine(l)
	}
	if err := os.WriteFile(file, []byte(strings.Join(lines, "\n")), 0o644); err != nil {
		fmt.Fprintf(stderr, "dotctl migrate: %v\n", err)
		return 1
	}
	fmt.Fprintf(stdout, "migrate: rewrote %s\n", file)
	return 0
}
