package main

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"runtime"
	"strings"
)

type Platform string

const (
	PlatformMacOS  Platform = "macos"
	PlatformArch   Platform = "arch"
	PlatformUbuntu Platform = "ubuntu"
)

func DetectPlatform(goos string, osRelease io.Reader) (Platform, error) {
	switch goos {
	case "darwin":
		return PlatformMacOS, nil
	case "linux":
		id, idLike := parseOSRelease(osRelease)
		blob := id + " " + idLike
		switch {
		case containsAny(blob, "arch", "manjaro", "endeavouros"):
			return PlatformArch, nil
		case containsAny(blob, "ubuntu", "debian", "pop", "linuxmint"):
			return PlatformUbuntu, nil
		default:
			return "", fmt.Errorf("unsupported linux distro: ID=%q ID_LIKE=%q", id, idLike)
		}
	default:
		return "", fmt.Errorf("unsupported OS: %s", goos)
	}
}

func parseOSRelease(r io.Reader) (id, idLike string) {
	sc := bufio.NewScanner(r)
	for sc.Scan() {
		line := sc.Text()
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		v = strings.Trim(strings.TrimSpace(v), `"`)
		switch strings.TrimSpace(k) {
		case "ID":
			id = v
		case "ID_LIKE":
			idLike = v
		}
	}
	return id, idLike
}

func containsAny(haystack string, needles ...string) bool {
	for _, n := range needles {
		for _, tok := range strings.Fields(haystack) {
			if tok == n {
				return true
			}
		}
	}
	return false
}

func Detect() (Platform, error) {
	if runtime.GOOS != "linux" {
		return DetectPlatform(runtime.GOOS, nil)
	}
	f, err := os.Open("/etc/os-release")
	if err != nil {
		return "", fmt.Errorf("cannot read /etc/os-release: %w", err)
	}
	defer f.Close()
	return DetectPlatform(runtime.GOOS, f)
}
