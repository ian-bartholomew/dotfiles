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
