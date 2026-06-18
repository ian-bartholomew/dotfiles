#!/usr/bin/env bash
count=0
printf 'a\nb\nc\n' | while read -r line; do
  count=$((count + 1))
done
echo "lines: $count"
