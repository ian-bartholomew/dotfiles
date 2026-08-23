package main

import ("bytes"; "strings"; "testing")

func TestDispatchVersion(t *testing.T) {
	var out bytes.Buffer
	if code := dispatch([]string{"version"}, &out, &out); code != 0 {
		t.Fatalf("exit=%d, want 0", code)
	}
	if strings.TrimSpace(out.String()) == "" { t.Fatal("version printed nothing") }
}
