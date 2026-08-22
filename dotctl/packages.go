package main

import (
	"bufio"
	"fmt"
	"io"
	"strings"
)

type Pkg struct {
	Category         string
	Required         bool
	Brew, Pacman, Apt string
	Notes            string
	Line             int
}

func ParsePackages(r io.Reader) ([]Pkg, error) {
	var out []Pkg
	sc := bufio.NewScanner(r)
	n := 0
	for sc.Scan() {
		n++
		raw := sc.Text()
		trimmed := strings.TrimSpace(raw)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		fields := strings.Split(raw, "|")
		if len(fields) != 6 {
			return nil, fmt.Errorf("line %d: expected 6 fields, got %d", n, len(fields))
		}
		for i := range fields {
			fields[i] = strings.TrimSpace(fields[i])
		}
		out = append(out, Pkg{
			Category: fields[0],
			Required: fields[1] == "yes",
			Brew:     fields[2],
			Pacman:   fields[3],
			Apt:      fields[4],
			Notes:    fields[5],
			Line:     n,
		})
	}
	return out, sc.Err()
}
