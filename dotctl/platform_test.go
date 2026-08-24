package main

import (
	"strings"
	"testing"
)

func TestDetectPlatform(t *testing.T) {
	cases := []struct {
		goos, osRelease string
		want            Platform
		wantErr         bool
	}{
		{"darwin", "", PlatformMacOS, false},
		{"linux", "ID=arch\n", PlatformArch, false},
		{"linux", "ID=debian\nID_LIKE=\n", PlatformUbuntu, false},
		{"linux", "ID=ubuntu\n", PlatformUbuntu, false},
		{"linux", "ID=manjaro\nID_LIKE=arch\n", PlatformArch, false},
		{"linux", "ID=pop\nID_LIKE=\"ubuntu debian\"\n", PlatformUbuntu, false},
		{"linux", "ID=void\n", "", true},
		{"plan9", "", "", true},
	}
	for _, c := range cases {
		got, err := DetectPlatform(c.goos, strings.NewReader(c.osRelease))
		if (err != nil) != c.wantErr {
			t.Fatalf("DetectPlatform(%q,%q) err=%v wantErr=%v", c.goos, c.osRelease, err, c.wantErr)
		}
		if got != c.want {
			t.Fatalf("DetectPlatform(%q,%q)=%q want %q", c.goos, c.osRelease, got, c.want)
		}
	}
}
