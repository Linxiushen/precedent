# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""receipts — read-only receipts for Claude Code learned state.

Answers three questions practitioners actually file:

1. "Did my memory / CLAUDE.md / skill actually load — whole, truncated, or not at all?"
   (claude-code#82056, #92998)
2. "What changed in my agent's learned state since yesterday, from which session, why?"
   (hermes-agent#12238, prime-agent#1208, codex#34668)
3. "Which learned artifacts are never used?"  (hermes#96704: 182 skills, many use_count=0)

The package NEVER writes anything under the Claude home directory it reads.
"""

__version__ = "0.1.0"
SCHEMA_VERSION = 1

__all__ = ["__version__", "SCHEMA_VERSION"]
