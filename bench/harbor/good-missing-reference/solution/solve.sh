#!/bin/bash
# Oracle solution: write the sealed verdict for this case.  APP_DIR
# defaults to Harbor's /app and is overridable for the same reason
# tests/test.sh overrides its paths.
set -euo pipefail
APP_DIR="${APP_DIR:-/app}"
mkdir -p "$APP_DIR"
cat > "$APP_DIR/verdict.json" <<'EOF'
{"decision": "commit", "reason": "good/missing-reference: restores scripts/migrate.sh, which SKILL.md references and the incumbent does not carry"}
EOF
