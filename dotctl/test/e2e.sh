#!/usr/bin/env bash
set -uo pipefail
fail() { echo "E2E FAIL: $*" >&2; exit 1; }
cd /repo/dotctl || fail "repo not at /repo"
go build -o /usr/local/bin/dotctl . || fail "go build"
dotctl lint -file /repo/packages.csv          || fail lint
dotctl check                                  || fail check
dotctl install -file /repo/packages.csv || fail "install"
for b in git jq tmux nvim; do command -v "$b" >/dev/null 2>&1 || fail "missing: $b"; done
dotctl verify -file /repo/packages.csv --skip=stow,shell || fail "verify"
echo "E2E OK on $(. /etc/os-release; echo "$ID")"
