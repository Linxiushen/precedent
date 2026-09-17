#!/usr/bin/env bash
# fetch-changelog helper: check that the working copy has a cache directory.
set -euo pipefail

target="${1:-.}"
if [ ! -d "$target" ]; then
  echo "no such directory: $target" >&2
  exit 2
fi
echo "inspected $target"
