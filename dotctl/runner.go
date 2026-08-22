package main

import (
	"os"
	"os/exec"
)

type Runner interface {
	Run(name string, args ...string) error
	Look(name string) bool
	RunOut(name string, args ...string) (string, error)
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

// RunOut uses CombinedOutput rather than Output: some callers (e.g. the
// GitHub ssh -T banner) need text that lands on stderr, not stdout.
func (ExecRunner) RunOut(name string, args ...string) (string, error) {
	out, err := exec.Command(name, args...).CombinedOutput()
	return string(out), err
}
