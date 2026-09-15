# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Entry point for ``python -m receipts``."""

from receipts.cli import main

if __name__ == "__main__":  # pragma: no cover - trivial
    raise SystemExit(main())
