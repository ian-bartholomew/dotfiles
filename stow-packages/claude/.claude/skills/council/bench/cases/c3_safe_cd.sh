#!/usr/bin/env bash
build_dir="$1"
cd "$build_dir" || exit 1
rm -f ./*.o
