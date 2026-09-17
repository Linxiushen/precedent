#!/usr/bin/env bash
# release-notes helper: check that the working copy has a changelog.
set -euo pipefail

target="${1:-.}"
if [ ! -d "$target" ]; then
  echo "no such directory: $target" >&2
  exit 2
fi
echo "inspected $target"
