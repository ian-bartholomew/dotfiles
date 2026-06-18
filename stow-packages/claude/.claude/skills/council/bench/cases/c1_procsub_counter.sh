#!/usr/bin/env bash
count=0
while read -r line; do
  count=$((count + 1))
done < <(printf 'a\nb\nc\n')
echo "lines: $count"
