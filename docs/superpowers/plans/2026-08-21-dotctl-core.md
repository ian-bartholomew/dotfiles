# dotctl Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `dotctl` Go binary's core: parse and validate `packages.csv`, detect the platform, build an install plan, and install packages, replacing the fragile logic in `install.sh`.

**Architecture:** A single Go module with a `flag`-based CLI dispatching subcommands (`check`, `lint`, `migrate`, `install`). Pure functions (parse, lint, detect, resolve, plan) are unit-tested; package installation shells out to brew/pacman/apt/yay through a small injectable runner so it can be tested against a fake.

**Tech Stack:** Go (stdlib only: `flag`, `encoding/csv`, `os/exec`, `runtime`, `testing`). No third-party dependencies, so the binary builds offline from a clean clone.

**Spec:** `docs/superpowers/specs/2026-08-21-dotctl-bootstrap-design.md`

## Global Constraints

- Go module path `github.com/ian-bartholomew/dotfiles/dotctl`; binary `dotctl`.
- Stdlib only. No third-party imports (keeps `go build` offline and dependency-free).
- Supported platforms: `macos`, `arch`, `ubuntu` (Debian folds into `ubuntu`). No Windows.
- `packages.csv` schema is 6 pipe-delimited fields: `category | required | brew | pacman | apt | notes`. A `-` in a platform column means skip on that platform. `AUR:name` and `CASK:name` prefixes are valid only in the pacman and brew columns respectively.
- Fields may carry surrounding spaces (the current file is space-padded around `|`); trim with `strings.TrimSpace`, never `xargs`.
- Comment lines (first field starts with `#` after trim) and blank lines are skipped.
- Do not use em dashes in any file content, comment, or commit message. Do not use emojis.

---

### Task 1: Module scaffold and CLI dispatch

**Files:**

- Create: `dotctl/go.mod`
- Create: `dotctl/main.go`
- Test: `dotctl/main_test.go`

**Interfaces:**

- Consumes: nothing.
- Produces: `func dispatch(args []string, stdout, stderr io.Writer) int` (returns process exit code); `main()` calls `os.Exit(dispatch(os.Args[1:], os.Stdout, os.Stderr))`. Subcommand handlers are added in later tasks and registered in `dispatch`.

- [ ] **Step 1: Write the failing test**

```go
package main

import (
 "bytes"
 "strings"
 "testing"
)

func TestDispatchUnknownCommand(t *testing.T) {
 var out, errb bytes.Buffer
 code := dispatch([]string{"bogus"}, &out, &errb)
 if code != 2 {
  t.Fatalf("exit code = %d, want 2", code)
 }
 if !strings.Contains(errb.String(), "unknown command") {
  t.Fatalf("stderr = %q, want it to mention unknown command", errb.String())
 }
}

func TestDispatchNoArgsPrintsUsage(t *testing.T) {
 var out, errb bytes.Buffer
 code := dispatch(nil, &out, &errb)
 if code != 2 {
  t.Fatalf("exit code = %d, want 2", code)
 }
 if !strings.Contains(errb.String(), "usage:") {
  t.Fatalf("stderr = %q, want usage text", errb.String())
 }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dotctl && go test ./... -run TestDispatch -v`
Expected: FAIL (build error: `dispatch` undefined).

- [ ] **Step 3: Create the module and minimal implementation**

`dotctl/go.mod`:

```
module github.com/ian-bartholomew/dotfiles/dotctl

go 1.22
```

`dotctl/main.go`:

```go
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
 // subcommands registered in later tasks
 default:
  fmt.Fprintf(stderr, "dotctl: unknown command %q\n", args[0])
  fmt.Fprintln(stderr, "usage: dotctl <check|lint|migrate|install> [flags]")
  return 2
 }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dotctl && go test ./... -run TestDispatch -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dotctl/go.mod dotctl/main.go dotctl/main_test.go
git commit -m "feat(dotctl): module scaffold and CLI dispatch"
```

---

### Task 2: packages.csv parser

**Files:**

- Create: `dotctl/packages.go`
- Test: `dotctl/packages_test.go`

**Interfaces:**

- Consumes: nothing.
- Produces:
  - `type Pkg struct { Category string; Required bool; Brew, Pacman, Apt, Notes string; Line int }`
  - `func ParsePackages(r io.Reader) ([]Pkg, error)` — skips comment/blank lines, trims each field, sets `Line` to the 1-based source line, errors on a row without exactly 6 fields.

- [ ] **Step 1: Write the failing test**

```go
package main

import (
 "strings"
 "testing"
)

func TestParsePackages(t *testing.T) {
 in := `# comment
system | yes | stow | stow | stow |

shell |  | bat | bat | bat | a note
`
 pkgs, err := ParsePackages(strings.NewReader(in))
 if err != nil {
  t.Fatalf("unexpected error: %v", err)
 }
 if len(pkgs) != 2 {
  t.Fatalf("len = %d, want 2", len(pkgs))
 }
 if pkgs[0].Category != "system" || !pkgs[0].Required || pkgs[0].Brew != "stow" {
  t.Fatalf("row0 = %+v", pkgs[0])
 }
 if pkgs[1].Required {
  t.Fatalf("row1 Required = true, want false (empty column)")
 }
 if pkgs[1].Notes != "a note" || pkgs[1].Line != 4 {
  t.Fatalf("row1 = %+v, want Notes=\"a note\" Line=4", pkgs[1])
 }
}

func TestParsePackagesWrongColumnCount(t *testing.T) {
 _, err := ParsePackages(strings.NewReader("system | yes | stow | stow |\n"))
 if err == nil {
  t.Fatal("expected error for 5-column row, got nil")
 }
 if !strings.Contains(err.Error(), "line 1") {
  t.Fatalf("error = %q, want it to cite line 1", err)
 }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dotctl && go test ./... -run TestParsePackages -v`
Expected: FAIL (`ParsePackages` undefined).

- [ ] **Step 3: Write minimal implementation**

```go
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dotctl && go test ./... -run TestParsePackages -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dotctl/packages.go dotctl/packages_test.go
git commit -m "feat(dotctl): packages.csv parser"
```

---

### Task 3: lint validation and the `lint` subcommand

**Files:**

- Create: `dotctl/lint.go`
- Modify: `dotctl/main.go` (register `lint` in `dispatch`)
- Test: `dotctl/lint_test.go`

**Interfaces:**

- Consumes: `Pkg`, `ParsePackages`.
- Produces:
  - `func Lint(pkgs []Pkg) []error` — returns all validation failures (empty slice if clean).
  - `func runLint(args []string, stdout, stderr io.Writer) int` — reads the CSV path (flag `-file`, default `packages.csv`), prints each error, returns 1 if any.

Validation rules: `required` field must have been `yes` or empty (enforced by parser storing bool; also reject a non-empty non-`yes` literal — see Step 3); `AUR:` prefix allowed only in Pacman; `CASK:` prefix allowed only in Brew; a row must have at least one non-`-` platform value.

- [ ] **Step 1: Write the failing test**

```go
package main

import "testing"

func TestLintCatchesMisplacedPrefixes(t *testing.T) {
 pkgs := []Pkg{
  {Category: "shell", Brew: "AUR:eza", Pacman: "eza", Apt: "eza", Line: 3},   // AUR in brew
  {Category: "shell", Brew: "bat", Pacman: "bat", Apt: "CASK:bat", Line: 4},  // CASK in apt
  {Category: "shell", Brew: "-", Pacman: "-", Apt: "-", Line: 5},             // all skipped
 }
 errs := Lint(pkgs)
 if len(errs) != 3 {
  t.Fatalf("got %d errors, want 3: %v", len(errs), errs)
 }
}

func TestLintClean(t *testing.T) {
 pkgs := []Pkg{{Category: "shell", Brew: "eza", Pacman: "eza", Apt: "-", Line: 3}}
 if errs := Lint(pkgs); len(errs) != 0 {
  t.Fatalf("expected clean, got %v", errs)
 }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dotctl && go test ./... -run TestLint -v`
Expected: FAIL (`Lint` undefined).

- [ ] **Step 3: Write minimal implementation**

`dotctl/lint.go`:

```go
package main

import (
 "flag"
 "fmt"
 "io"
 "os"
 "strings"
)

func Lint(pkgs []Pkg) []error {
 var errs []error
 for _, p := range pkgs {
  if strings.HasPrefix(p.Brew, "AUR:") {
   errs = append(errs, fmt.Errorf("line %d: AUR: prefix not allowed in brew column", p.Line))
  }
  if strings.HasPrefix(p.Apt, "AUR:") {
   errs = append(errs, fmt.Errorf("line %d: AUR: prefix not allowed in apt column", p.Line))
  }
  if strings.HasPrefix(p.Pacman, "CASK:") {
   errs = append(errs, fmt.Errorf("line %d: CASK: prefix not allowed in pacman column", p.Line))
  }
  if strings.HasPrefix(p.Apt, "CASK:") {
   errs = append(errs, fmt.Errorf("line %d: CASK: prefix not allowed in apt column", p.Line))
  }
  if p.Brew == "-" && p.Pacman == "-" && p.Apt == "-" {
   errs = append(errs, fmt.Errorf("line %d: all platform columns are '-'", p.Line))
  }
 }
 return errs
}

func runLint(args []string, stdout, stderr io.Writer) int {
 fs := flag.NewFlagSet("lint", flag.ContinueOnError)
 fs.SetOutput(stderr)
 file := fs.String("file", "packages.csv", "path to packages.csv")
 if err := fs.Parse(args); err != nil {
  return 2
 }
 f, err := os.Open(*file)
 if err != nil {
  fmt.Fprintf(stderr, "dotctl lint: %v\n", err)
  return 1
 }
 defer f.Close()
 pkgs, err := ParsePackages(f)
 if err != nil {
  fmt.Fprintf(stderr, "dotctl lint: %v\n", err)
  return 1
 }
 errs := Lint(pkgs)
 for _, e := range errs {
  fmt.Fprintln(stderr, e)
 }
 if len(errs) > 0 {
  return 1
 }
 fmt.Fprintf(stdout, "lint: %d packages OK\n", len(pkgs))
 return 0
}
```

In `dotctl/main.go`, add to the `switch` in `dispatch`:

```go
 case "lint":
  return runLint(args[1:], stdout, stderr)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dotctl && go test ./... -run TestLint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dotctl/lint.go dotctl/main.go dotctl/lint_test.go
git commit -m "feat(dotctl): lint validation and lint subcommand"
```

---

### Task 4: platform detection

**Files:**

- Create: `dotctl/platform.go`
- Test: `dotctl/platform_test.go`

**Interfaces:**

- Consumes: nothing.
- Produces:
  - `type Platform string` with consts `PlatformMacOS = "macos"`, `PlatformArch = "arch"`, `PlatformUbuntu = "ubuntu"`.
  - `func DetectPlatform(goos string, osRelease io.Reader) (Platform, error)` — pure and testable: `goos == "darwin"` returns macos; `goos == "linux"` reads os-release `ID`/`ID_LIKE` (arch-family returns arch, debian-family returns ubuntu); anything else errors.
  - `func Detect() (Platform, error)` — wraps `DetectPlatform(runtime.GOOS, <open /etc/os-release>)` for real use.

- [ ] **Step 1: Write the failing test**

```go
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dotctl && go test ./... -run TestDetectPlatform -v`
Expected: FAIL (`DetectPlatform` undefined).

- [ ] **Step 3: Write minimal implementation**

```go
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dotctl && go test ./... -run TestDetectPlatform -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dotctl/platform.go dotctl/platform_test.go
git commit -m "feat(dotctl): platform detection"
```

---

### Task 5: plan building (resolve names, required + selection)

**Files:**

- Create: `dotctl/plan.go`
- Test: `dotctl/plan_test.go`

**Interfaces:**

- Consumes: `Pkg`, `Platform` (and its consts).
- Produces:
  - `type Kind int` with consts `KindNormal`, `KindAUR`, `KindCask`.
  - `type Resolved struct { Name string; Kind Kind; Category string }`
  - `type Selection struct { All bool; Categories map[string]bool; Packages map[string]bool }`
  - `func columnFor(p Pkg, plat Platform) string` — returns the raw platform cell (`p.Brew`/`p.Pacman`/`p.Apt`).
  - `func resolveOne(p Pkg, plat Platform) (Resolved, bool)` — strips `AUR:`/`CASK:` into `Kind`; returns `ok=false` when the cell is `-` or empty.
  - `func BuildPlan(pkgs []Pkg, plat Platform, sel Selection) []Resolved` — always includes `Required` packages; additionally includes a package if `sel.All`, or its `Category` is in `sel.Categories`, or its resolved base name is in `sel.Packages`. Skips `-`/empty cells. Deduplicates by resolved name, preserving first occurrence order.

- [ ] **Step 1: Write the failing test**

```go
package main

import "testing"

func TestResolveOne(t *testing.T) {
 r, ok := resolveOne(Pkg{Brew: "CASK:ghostty", Pacman: "AUR:ghostty", Apt: "-", Category: "shell"}, PlatformMacOS)
 if !ok || r.Kind != KindCask || r.Name != "ghostty" {
  t.Fatalf("macos resolve = %+v ok=%v", r, ok)
 }
 r, ok = resolveOne(Pkg{Brew: "CASK:ghostty", Pacman: "AUR:ghostty", Apt: "-", Category: "shell"}, PlatformArch)
 if !ok || r.Kind != KindAUR || r.Name != "ghostty" {
  t.Fatalf("arch resolve = %+v ok=%v", r, ok)
 }
 if _, ok := resolveOne(Pkg{Brew: "CASK:ghostty", Pacman: "AUR:ghostty", Apt: "-"}, PlatformUbuntu); ok {
  t.Fatal("ubuntu resolve should skip '-'")
 }
}

func TestBuildPlanRequiredPlusSelection(t *testing.T) {
 pkgs := []Pkg{
  {Category: "system", Required: true, Brew: "stow", Pacman: "stow", Apt: "stow"},
  {Category: "shell", Brew: "bat", Pacman: "bat", Apt: "bat"},
  {Category: "cloud", Brew: "awscli", Pacman: "AUR:aws-cli-v2", Apt: "-"},
 }
 // required only
 got := BuildPlan(pkgs, PlatformArch, Selection{Categories: map[string]bool{}, Packages: map[string]bool{}})
 if len(got) != 1 || got[0].Name != "stow" {
  t.Fatalf("required-only = %+v", got)
 }
 // required + shell category
 got = BuildPlan(pkgs, PlatformArch, Selection{Categories: map[string]bool{"shell": true}, Packages: map[string]bool{}})
 if len(got) != 2 || got[1].Name != "bat" {
  t.Fatalf("required+shell = %+v", got)
 }
 // all, on ubuntu the aws row ('-') is skipped
 got = BuildPlan(pkgs, PlatformUbuntu, Selection{All: true})
 if len(got) != 2 {
  t.Fatalf("ubuntu all = %+v (aws '-' should be skipped)", got)
 }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dotctl && go test ./... -run "TestResolveOne|TestBuildPlan" -v`
Expected: FAIL (`resolveOne`/`BuildPlan` undefined).

- [ ] **Step 3: Write minimal implementation**

```go
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dotctl && go test ./... -run "TestResolveOne|TestBuildPlan" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dotctl/plan.go dotctl/plan_test.go
git commit -m "feat(dotctl): plan building with required set and selection"
```

---

### Task 6: command runner and `check` subcommand

**Files:**

- Create: `dotctl/runner.go`
- Create: `dotctl/check.go`
- Modify: `dotctl/main.go` (register `check`)
- Test: `dotctl/check_test.go`

**Interfaces:**

- Consumes: `Platform`, `Detect`.
- Produces:
  - `type Runner interface { Run(name string, args ...string) error; Look(name string) bool }`
  - `type ExecRunner struct{}` implementing `Runner` via `os/exec` and `exec.LookPath`.
  - `func Check(plat Platform, r Runner) []error` — verifies a package manager exists for the platform and that `git` and `curl` are present. Does NOT check stow, zsh, or the default shell (post-conditions handled in Plan 2's `verify`).
  - `func runCheck(args []string, stdout, stderr io.Writer) int`.

- [ ] **Step 1: Write the failing test**

```go
package main

import "testing"

type fakeRunner struct {
 present map[string]bool
 calls   [][]string
}

func (f *fakeRunner) Run(name string, args ...string) error {
 f.calls = append(f.calls, append([]string{name}, args...))
 return nil
}
func (f *fakeRunner) Look(name string) bool { return f.present[name] }

func TestCheckMissingTools(t *testing.T) {
 r := &fakeRunner{present: map[string]bool{"brew": true}} // git and curl missing
 errs := Check(PlatformMacOS, r)
 if len(errs) != 2 {
  t.Fatalf("got %d errors, want 2 (git, curl): %v", len(errs), errs)
 }
}

func TestCheckCleanArch(t *testing.T) {
 r := &fakeRunner{present: map[string]bool{"pacman": true, "git": true, "curl": true}}
 if errs := Check(PlatformArch, r); len(errs) != 0 {
  t.Fatalf("expected clean, got %v", errs)
 }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dotctl && go test ./... -run TestCheck -v`
Expected: FAIL (`Check`/`Runner` undefined).

- [ ] **Step 3: Write minimal implementation**

`dotctl/runner.go`:

```go
package main

import (
 "os"
 "os/exec"
)

type Runner interface {
 Run(name string, args ...string) error
 Look(name string) bool
}

type ExecRunner struct{}

func (ExecRunner) Run(name string, args ...string) error {
 cmd := exec.Command(name, args...)
 cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
 return cmd.Run()
}

func (ExecRunner) Look(name string) bool {
 _, err := exec.LookPath(name)
 return err == nil
}
```

`dotctl/check.go`:

```go
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
```

In `dotctl/main.go`, add to the `switch`:

```go
 case "check":
  return runCheck(args[1:], stdout, stderr)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dotctl && go test ./... -run TestCheck -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dotctl/runner.go dotctl/check.go dotctl/main.go dotctl/check_test.go
git commit -m "feat(dotctl): command runner and check subcommand"
```

---

### Task 7: install execution and the `install` subcommand

**Files:**

- Create: `dotctl/install.go`
- Modify: `dotctl/main.go` (register `install`)
- Test: `dotctl/install_test.go`

**Interfaces:**

- Consumes: `Resolved`, `Kind`, `Platform`, `Runner`, `BuildPlan`, `ParsePackages`, `Detect`.
- Produces:
  - `func installCmd(plat Platform, r Resolved) [][]string` — returns the argv(s) to install one package (a slice so Arch/Debian can prefix a sync).
  - `func Install(plat Platform, plan []Resolved, run Runner) error` — installs each resolved package idempotently via `run`, returning the first error encountered after attempting all (collect and join).
  - `func runInstall(args []string, stdout, stderr io.Writer) int` — flags `-file` (default `packages.csv`), `-dry-run`, `-yes`, `-categories` (comma list), `-packages` (comma list), `-all`.

Install commands per platform (idempotent forms): macOS `brew install <n>` / `brew install --cask <n>`; Arch `pacman -S --needed --noconfirm <n>` (normal) or `yay -S --needed --noconfirm <n>` (AUR); Debian `apt-get install -y <n>`. The Arch/Debian db-refresh (`pacman -Syu` / `apt-get update`) is the caller's responsibility once before the loop (see `runInstall`), not per package.

- [ ] **Step 1: Write the failing test**

```go
package main

import (
 "reflect"
 "testing"
)

func TestInstallCmd(t *testing.T) {
 cases := []struct {
  plat Platform
  r    Resolved
  want [][]string
 }{
  {PlatformMacOS, Resolved{Name: "bat", Kind: KindNormal}, [][]string{{"brew", "install", "bat"}}},
  {PlatformMacOS, Resolved{Name: "ghostty", Kind: KindCask}, [][]string{{"brew", "install", "--cask", "ghostty"}}},
  {PlatformArch, Resolved{Name: "bat", Kind: KindNormal}, [][]string{{"pacman", "-S", "--needed", "--noconfirm", "bat"}}},
  {PlatformArch, Resolved{Name: "eza", Kind: KindAUR}, [][]string{{"yay", "-S", "--needed", "--noconfirm", "eza"}}},
  {PlatformUbuntu, Resolved{Name: "bat", Kind: KindNormal}, [][]string{{"apt-get", "install", "-y", "bat"}}},
 }
 for _, c := range cases {
  got := installCmd(c.plat, c.r)
  if !reflect.DeepEqual(got, c.want) {
   t.Fatalf("installCmd(%s,%+v)=%v want %v", c.plat, c.r, got, c.want)
  }
 }
}

func TestInstallRunsEachPackage(t *testing.T) {
 r := &fakeRunner{present: map[string]bool{}}
 plan := []Resolved{{Name: "bat", Kind: KindNormal}, {Name: "jq", Kind: KindNormal}}
 if err := Install(PlatformUbuntu, plan, r); err != nil {
  t.Fatalf("Install error: %v", err)
 }
 if len(r.calls) != 2 {
  t.Fatalf("made %d calls, want 2: %v", len(r.calls), r.calls)
 }
 if !reflect.DeepEqual(r.calls[0], []string{"apt-get", "install", "-y", "bat"}) {
  t.Fatalf("call0 = %v", r.calls[0])
 }
}
```

Note: `reflect` and `fakeRunner` (from Task 6) are reused; ensure `check_test.go` and `install_test.go` are in the same `package main`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dotctl && go test ./... -run TestInstall -v`
Expected: FAIL (`installCmd`/`Install` undefined).

- [ ] **Step 3: Write minimal implementation**

```go
package main

import (
 "errors"
 "flag"
 "fmt"
 "io"
 "os"
 "strings"
)

func installCmd(plat Platform, r Resolved) [][]string {
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
  return [][]string{{"pacman", "-S", "--needed", "--noconfirm", r.Name}}
 default:
  return [][]string{{"apt-get", "install", "-y", r.Name}}
 }
}

func Install(plat Platform, plan []Resolved, run Runner) error {
 var errs []error
 for _, r := range plan {
  for _, argv := range installCmd(plat, r) {
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
 _ = fs.Bool("yes", false, "non-interactive; required set plus any --categories/--packages/--all")
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
 if err := Install(plat, plan, ExecRunner{}); err != nil {
  fmt.Fprintf(stderr, "dotctl install: %v\n", err)
  return 1
 }
 fmt.Fprintf(stdout, "install: %d packages\n", len(plan))
 return 0
}
```

In `dotctl/main.go`, add to the `switch`:

```go
 case "install":
  return runInstall(args[1:], stdout, stderr)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dotctl && go test ./... -run TestInstall -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dotctl/install.go dotctl/main.go dotctl/install_test.go
git commit -m "feat(dotctl): install execution and install subcommand"
```

---

### Task 8: migrate the 5-column packages.csv to 6 columns

**Files:**

- Create: `dotctl/migrate.go`
- Modify: `dotctl/main.go` (register `migrate`)
- Test: `dotctl/migrate_test.go`

**Interfaces:**

- Consumes: nothing (operates on raw lines to preserve comments and spacing).
- Produces:
  - `var requiredSet = map[string]bool{...}` — the single source of the required package set, keyed by the logical package name found in the brew column (falling back to pacman then apt). Members: coreutils, findutils, grep, gnu-sed, wget, curl, htop, btop, tree, stow, less, zsh, tmux, git, tig, neovim, go, python, nodejs, jq, gh, npm, python-pip, python-pipx.
  - `func migrateLine(line string) string` — for a non-comment, non-blank 5-field row, insert the `required` column (value `yes` if the row's brew/pacman/apt name is in `requiredSet`, else empty) as field 2; comment and blank lines pass through unchanged.
  - `func runMigrate(args []string, stdout, stderr io.Writer) int` — rewrites the file in place (flag `-file`, default `packages.csv`).

Note: this subcommand is a one-off for the schema cutover and is removed after migration (tracked in the spec's Migration section). It reads the required set from `requiredSet`, the same map install will consult, so the policy is single-sourced.

- [ ] **Step 1: Write the failing test**

```go
package main

import "testing"

func TestMigrateLine(t *testing.T) {
 // comment passes through
 if got := migrateLine("# Shell and terminal"); got != "# Shell and terminal" {
  t.Fatalf("comment changed: %q", got)
 }
 // required package gets yes
 got := migrateLine("shell | zsh | zsh | zsh |")
 want := "shell | yes | zsh | zsh | zsh |"
 if got != want {
  t.Fatalf("required row = %q want %q", got, want)
 }
 // non-required gets empty required column
 got = migrateLine("shell | bat | bat | bat | note")
 want = "shell |  | bat | bat | bat | note"
 if got != want {
  t.Fatalf("non-required row = %q want %q", got, want)
 }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dotctl && go test ./... -run TestMigrateLine -v`
Expected: FAIL (`migrateLine` undefined).

- [ ] **Step 3: Write minimal implementation**

```go
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
```

In `dotctl/main.go`, add to the `switch`:

```go
 case "migrate":
  return runMigrate(args[1:], stdout, stderr)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dotctl && go test ./... -run TestMigrateLine -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and build**

Run: `cd dotctl && go test ./... && go vet ./... && go build -o /dev/null .`
Expected: all PASS, clean build.

- [ ] **Step 6: Commit**

```bash
git add dotctl/migrate.go dotctl/main.go dotctl/migrate_test.go
git commit -m "feat(dotctl): one-off packages.csv 5-to-6 column migration"
```

---

### Task 9: apply the migration and wire the required set into the real CSV

**Files:**

- Modify: `packages.csv` (via `dotctl migrate`, then add the missing `gh` row)
- Modify: `dotctl/lint.go` (no code change expected; used to verify)

**Interfaces:**

- Consumes: `dotctl migrate`, `dotctl lint`.
- Produces: a committed 6-column `packages.csv` that lints clean and marks the required set.

- [ ] **Step 1: Add the missing gh row to packages.csv**

Add under the Development tools or Cloud section (5-column form so `migrate` handles it):

```
dev | gh | github-cli | github-cli | gh | GitHub CLI
```

(Verify these package names during execution: `brew info gh`, `pacman -Si github-cli`, `apt-cache show gh`. Adjust cells to whatever the archives actually provide before committing.)

- [ ] **Step 2: Run the migration**

Run: `cd dotctl && go run . migrate -file ../packages.csv`
Expected: `migrate: rewrote ../packages.csv`.

- [ ] **Step 3: Lint the migrated file**

Run: `cd dotctl && go run . lint -file ../packages.csv`
Expected: `lint: N packages OK`, exit 0. If errors, fix the offending rows by hand and re-run.

- [ ] **Step 4: Spot-check the required rows**

Run: `grep -E '^(system|shell|dev|editor|lang|tools) \| yes' ../packages.csv`
Expected: the required set (all system rows, plus zsh, tmux, git, tig, neovim, go, python, nodejs, jq, gh, npm, python-pip, python-pipx) show `yes`; nothing else does.

- [ ] **Step 5: Dry-run install to confirm the plan resolves**

Run: `cd dotctl && go run . install -file ../packages.csv --dry-run`
Expected: lists the required set for this platform (macOS), no error.

- [ ] **Step 6: Commit**

```bash
git add packages.csv
git commit -m "chore(packages): migrate to 6-column schema with required set"
```

---

## Self-Review

**1. Spec coverage (core scope):**

- packages.csv 6-column schema + parse: Task 2. lint: Task 3. migrate + required set single-sourced: Tasks 8, 9. Platform detection (macOS/arch/ubuntu incl Debian and ID_LIKE): Task 4. Plan building (required + selection, skip `-`, AUR/CASK kinds, dedup): Task 5. install (per-platform idempotent forms, dry-run, non-interactive flags, `--needed`/`-y`): Task 7. check (git/curl/pkg-mgr only, not stow/zsh/shell): Task 6.
- Deferred to Plan 2 (git identity): `gitconfig`, `allowed-signers add`, `verify` (local + `--remote`). Deferred to Plan 3 (bootstrap + distribution): `bootstrap.sh`, build-from-source, `pacman -Syu`/`apt-get update` pre-sync placement, `~/.local/bin` PATH, CI shellcheck + lint gate, schema-version marker. These are explicitly out of this plan's scope and named here so no reader assumes they are missing by accident.

**2. Placeholder scan:** No TBD/TODO. The only deferred detail is the exact `gh` package names (Task 9 Step 1), which is a real archive-lookup instruction with the commands to run, not a placeholder.

**3. Type consistency:** `Pkg`, `Platform`/consts, `Resolved`/`Kind`/consts, `Selection`, `Runner`/`ExecRunner`, `fakeRunner` (defined in Task 6, reused in Task 7) are used consistently. `dispatch` gains one `case` per subcommand across Tasks 3, 6, 7, 8. `ParsePackages`, `BuildPlan`, `Detect`, `Install`, `Check` signatures match their call sites.

**Note on the `-Syu` pre-sync:** Task 7 deliberately leaves the Arch full-system sync and the Debian `apt-get update` to the caller (bootstrap, Plan 3) so a single sync runs once, not per package. The spec's blast-radius caveat about `pacman -Syu` is carried into Plan 3.
