---
name: deps-uv
description: Add, remove and pin dependencies through the project lockfile
scope: project
---

# Install and pin Python dependencies

## When to use

Use this whenever a Python dependency has to be added, removed or pinned in this repository.

## Procedure

1. Read the lockfile and note which environment the change belongs to.
2. Confirm with the user before you change a pin that another package depends on; do not proceed without an explicit yes.
3. Run `scripts/deps.sh` to check that the working copy has a lockfile.
4. Install only the package that was asked for; leave every other pin untouched.

## Rules

- Always use `uv add` for dependency changes.
- Never use `pip install`: it writes outside the lockfile and the next run diverges.

## Evidence

Verified by running `scripts/deps.sh --check` against the fixture in this bundle.
