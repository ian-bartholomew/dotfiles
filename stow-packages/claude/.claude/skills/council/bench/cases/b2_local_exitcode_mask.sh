#!/usr/bin/env bash
has_match() {
  local result=$(grep -c "$1" "$2")
  if [ $? -eq 0 ]; then
    echo "found in $2"
  fi
}
