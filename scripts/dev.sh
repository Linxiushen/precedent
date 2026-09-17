#!/usr/bin/env bash
# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
#
# dev.sh — create the three uv-managed virtualenvs and run all three suites.
#
#   ./scripts/dev.sh             venvs (reused if present) + all three suites
#   ./scripts/dev.sh --venvs     set up the venvs only
#   ./scripts/dev.sh --tests     run the suites only (venvs must exist)
#   ./scripts/dev.sh --recreate  throw the venvs away and build them again
#   ./scripts/dev.sh --clean     remove the venvs and the caches
#   ./scripts/dev.sh --python 3.11      pin a different interpreter
#   ./scripts/dev.sh --zipapp    build dist/precedent.pyz and prove it runs on
#                                a Python that has none of the packages -- and
#                                that a hostile PYTHONPATH cannot shadow it
#   ./scripts/dev.sh --demo      build the fixture Claude home and print the
#                                `audit --share` card that is in the README
#
# Each package gets its own .venv.  precedent's venv additionally has receipts
# and acceptor installed EDITABLE, so an edit in one is visible to the other's
# tests immediately.  The system python on macOS is 3.9 and is never used.
#
# This script is read-only on your Claude home.  It never runs precedent
# against ~/.claude, and the test suites build synthetic homes under tmp_path.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKGS="$ROOT/packages"
PYVER="${PRECEDENT_PYTHON:-3.12}"

DO_VENVS=1
DO_TESTS=1
DO_CLEAN=0
RECREATE=0
DO_ZIPAPP=0
DO_DEMO=0

while [ $# -gt 0 ]; do
  case "$1" in
    --venvs)     DO_TESTS=0 ;;
    --tests)     DO_VENVS=0 ;;
    --recreate)  RECREATE=1 ;;
    --clean)     DO_CLEAN=1; DO_VENVS=0; DO_TESTS=0 ;;
    --zipapp)    DO_ZIPAPP=1; DO_VENVS=0; DO_TESTS=0 ;;
    --demo)      DO_DEMO=1; DO_VENVS=0; DO_TESTS=0 ;;
    --python) shift; PYVER="${1:?--python needs a version}" ;;
    -h|--help)
      sed -n '8,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "dev.sh: unknown option $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------
if [ "$DO_CLEAN" = 1 ]; then
  say "removing venvs and caches"
  for p in receipts acceptor precedent; do
    rm -rf "$PKGS/$p/.venv" "$PKGS/$p/.pytest_cache" "$PKGS/$p"/*.egg-info \
           "$PKGS/$p/src"/*.egg-info "$PKGS/$p/dist" "$PKGS/$p/build"
  done
  find "$PKGS" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
  echo "clean."
  exit 0
fi

# ---------------------------------------------------------------------------
# zipapp: one file, and a Python that has nothing installed to prove it on
# ---------------------------------------------------------------------------
if [ "$DO_ZIPAPP" = 1 ]; then
  PYZ="$ROOT/dist/precedent.pyz"
  say "building $PYZ"
  python3 "$ROOT/scripts/build_zipapp.py" --out "$PYZ"
  python3 "$ROOT/scripts/build_zipapp.py" --check --quiet
  echo "  reproducible: two builds of this tree are byte-identical"

  command -v uv >/dev/null 2>&1 || fail "uv is required for the bare-venv check"
  BARE="$(mktemp -d)"
  FIX="$BARE/claude-home"
  trap 'rm -rf "$BARE"' EXIT
  say "running it on a Python with NONE of the packages installed"
  uv venv --python "$PYVER" "$BARE/venv" >/dev/null
  "$BARE/venv/bin/python" - <<'PY'
import importlib.util, sys
missing = [m for m in ("receipts", "acceptor", "precedent")
           if importlib.util.find_spec(m) is None]
if len(missing) != 3:
    sys.exit("this venv already has %s installed; the check would prove nothing"
             % ", ".join(set(("receipts", "acceptor", "precedent")) - set(missing)))
print("  site-packages has none of receipts / acceptor / precedent")
PY
  python3 "$ROOT/scripts/make_fixture_home.py" "$FIX" --quiet
  "$BARE/venv/bin/python" "$PYZ" --version
  "$BARE/venv/bin/python" "$PYZ" init --state-dir "$BARE/state" \
      --claude-home "$FIX" >/dev/null
  "$BARE/venv/bin/python" "$PYZ" audit --share --state-dir "$BARE/state" \
      --claude-home "$FIX"

  # ...and the other half of "self-contained": the packages are PRESENT and
  # WRONG.  A decoy on PYTHONPATH that kills the process if it is ever imported
  # is the only way to tell "the archive won the import" from "nothing else was
  # there to win it".
  say "and again with a hostile PYTHONPATH in front of it"
  DECOY="$BARE/decoy"
  mkdir -p "$DECOY/receipts" "$DECOY/acceptor" "$DECOY/precedent"
  for n in receipts acceptor precedent; do
    cat > "$DECOY/$n/__init__.py" <<EOF
import os
open(os.environ["DECOY_MARKER"], "a").write("$n imported\n")
raise SystemExit("DECOY $n was imported instead of the vendored copy")
EOF
  done
  cp "$DECOY/receipts/__init__.py" "$DECOY/receipts.py"
  echo "  decoy: $DECOY  (hostile receipts.py + three hostile packages)"
  DECOY_MARKER="$BARE/decoy-marker" PYTHONPATH="$DECOY" \
    "$BARE/venv/bin/python" "$PYZ" audit --share --state-dir "$BARE/state2" \
    --claude-home "$FIX" | tail -3
  if [ -e "$BARE/decoy-marker" ]; then
    fail "the decoy on PYTHONPATH was imported: the zipapp is NOT self-contained"
  fi
  echo "  the decoy was never imported; every module came out of the archive"
  DECOY_MARKER="$BARE/decoy-marker" PYTHONPATH="$DECOY" \
    "$BARE/venv/bin/python" -c "import sys; sys.path.insert(0, '$PYZ');
import receipts, acceptor, precedent
for m in (receipts, acceptor, precedent):
    assert m.__file__.startswith('$PYZ'), m.__file__
    print('  %-10s <- %s' % (m.__name__, m.__file__))"

  echo
  echo "precedent.pyz runs standalone."
  exit 0
fi

# ---------------------------------------------------------------------------
# demo: the fixture home the README's numbers come from
# ---------------------------------------------------------------------------
if [ "$DO_DEMO" = 1 ]; then
  DEMO="${PRECEDENT_DEMO_DIR:-$(mktemp -d)}"
  python3 "$ROOT/scripts/make_fixture_home.py" "$DEMO/claude-home" --force --quiet
  PY="$PKGS/precedent/.venv/bin/python"
  [ -x "$PY" ] || PY="python3"
  say "precedent audit --share  (English)"
  "$PY" -m precedent audit --share --lang en \
      --claude-home "$DEMO/claude-home" --state-dir "$DEMO/state"
  say "precedent audit --share --lang zh"
  "$PY" -m precedent audit --share --lang zh \
      --claude-home "$DEMO/claude-home" --state-dir "$DEMO/state"
  echo
  echo "fixture home: $DEMO/claude-home"
  exit 0
fi

# ---------------------------------------------------------------------------
# venvs
# ---------------------------------------------------------------------------
if [ "$DO_VENVS" = 1 ]; then
  command -v uv >/dev/null 2>&1 || fail \
    "uv is required: https://docs.astral.sh/uv/  (brew install uv / curl -LsSf https://astral.sh/uv/install.sh | sh)"

  if [ "$RECREATE" = 1 ]; then
    say "recreating venvs on Python $PYVER"
    VENV_FLAG="--clear"
  else
    say "creating venvs on Python $PYVER (existing ones are reused)"
    VENV_FLAG="--allow-existing"
  fi
  for p in receipts acceptor precedent; do
    uv venv --python "$PYVER" $VENV_FLAG "$PKGS/$p/.venv"
  done

  # Standalone libraries first, then the CLI with both of them editable.
  say "installing receipts"
  uv pip install --python "$PKGS/receipts/.venv/bin/python" \
     -e "$PKGS/receipts" pytest

  say "installing acceptor"
  uv pip install --python "$PKGS/acceptor/.venv/bin/python" \
     -e "$PKGS/acceptor" pytest

  say "installing precedent (+ receipts, acceptor editable)"
  uv pip install --python "$PKGS/precedent/.venv/bin/python" \
     -e "$PKGS/receipts" -e "$PKGS/acceptor" -e "$PKGS/precedent" pytest

  for p in receipts acceptor precedent; do
    "$PKGS/$p/.venv/bin/python" -c "import sys; print('  $p  ->  python', '.'.join(map(str, sys.version_info[:3])))"
  done
fi

# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
if [ "$DO_TESTS" = 1 ]; then
  failed=0
  declare -a SUMMARY=()

  for p in receipts acceptor precedent; do
    py="$PKGS/$p/.venv/bin/python"
    [ -x "$py" ] || fail "$p has no .venv — run ./scripts/dev.sh --venvs first"
    say "pytest: $p"
    # -o addopts="" so the packages' own -q does not swallow the summary line
    if (cd "$PKGS/$p" && "$py" -m pytest -o addopts="" -q); then
      SUMMARY+=("  PASS  $p")
    else
      SUMMARY+=("  FAIL  $p")
      failed=1
    fi
  done

  say "summary"
  printf '%s\n' "${SUMMARY[@]}"
  [ "$failed" = 0 ] || fail "at least one suite failed"
  echo
  echo "all three suites passed."
fi
