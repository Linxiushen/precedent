---
name: repo-cleanup
description: Remove generated files from a worktree without touching sources
scope: project
---

# Clean build output out of a worktree

## When to use

Use this when a worktree has accumulated build output and a clean build is needed.

## Procedure

1. List the generated paths the build system declares.
2. Confirm with the user before you remove anything the build system did not declare; do not proceed without an explicit yes.
3. Run `scripts/cleanup.sh` to check that the working copy has a build directory.
4. Remove only the paths the build declares; leave every untracked source file where it is.

## Rules

- Always use `build clean` for clearing build output.
- Never use `git clean -xfd`: it deletes untracked files the user has not committed yet.

## Evidence

Verified by running `scripts/cleanup.sh --check` against the fixture in this bundle.
