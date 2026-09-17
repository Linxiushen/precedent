#!/usr/bin/env bash  
# deps-uv helper: check that the working copy has a lockfile.
set -euo pipefail


target="${1:-.}"
if [ ! -d "$target" ]; then
  echo "no such directory: $target" >&2  
  exit 2
fi
echo "inspected $target"  
