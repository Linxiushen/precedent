#!/bin/bash
# Grade one acceptor decision.
#
# Shell only, and no interpreter is installed in the image on purpose: the
# verifier must not be able to run anything the bundle carries.
#
# The three paths default to Harbor's own (`/app`, `/tests`, `/logs`), so inside
# Harbor this behaves exactly as the contract says.  They are overridable so the
# test suite can execute *these bytes* without a container and without writing
# to absolute paths on a developer's machine.
set -uo pipefail

APP_DIR="${APP_DIR:-/app}"
TESTS_DIR="${TESTS_DIR:-/tests}"
LOGS_DIR="${LOGS_DIR:-/logs}"

mkdir -p "$LOGS_DIR/verifier"

decision_of() {  # decision_of <file> -> the value of "decision", or nothing
  tr -d ' \n\t' < "$1" 2>/dev/null \
    | sed -n 's/.*"decision":"\([a-z]*\)".*/\1/p'
}

expected="$(decision_of "$TESTS_DIR/expected.json")"
got=""
if [ -f "$APP_DIR/verdict.json" ]; then
  got="$(decision_of "$APP_DIR/verdict.json")"
fi

echo "expected=${expected} got=${got:-<none>}" >&2
if [ -n "$expected" ] && [ "$got" = "$expected" ]; then
  printf '{"reward": 1.0}\n' > "$LOGS_DIR/verifier/reward.json"
else
  printf '{"reward": 0.0}\n' > "$LOGS_DIR/verifier/reward.json"
fi
