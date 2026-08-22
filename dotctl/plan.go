package main

import "strings"

type Kind int

const (
	KindNormal Kind = iota
	KindAUR
	KindCask
)

type Resolved struct {
	Name     string
	Kind     Kind
	Category string
}

type Selection struct {
	All        bool
	Categories map[string]bool
	Packages   map[string]bool
}

func columnFor(p Pkg, plat Platform) string {
	switch plat {
	case PlatformMacOS:
		return p.Brew
	case PlatformArch:
		return p.Pacman
	default:
		return p.Apt
	}
}

func resolveOne(p Pkg, plat Platform) (Resolved, bool) {
	cell := columnFor(p, plat)
	if cell == "" || cell == "-" {
		return Resolved{}, false
	}
	r := Resolved{Kind: KindNormal, Category: p.Category, Name: cell}
	switch {
	case strings.HasPrefix(cell, "AUR:"):
		r.Kind, r.Name = KindAUR, strings.TrimPrefix(cell, "AUR:")
	case strings.HasPrefix(cell, "CASK:"):
		r.Kind, r.Name = KindCask, strings.TrimPrefix(cell, "CASK:")
	}
	return r, true
}

func BuildPlan(pkgs []Pkg, plat Platform, sel Selection) []Resolved {
	var out []Resolved
	seen := map[string]bool{}
	for _, p := range pkgs {
		r, ok := resolveOne(p, plat)
		if !ok {
			continue
		}
		include := p.Required || sel.All || sel.Categories[p.Category] || sel.Packages[r.Name]
		if !include || seen[r.Name] {
			continue
		}
		seen[r.Name] = true
		out = append(out, r)
	}
	return out
}
