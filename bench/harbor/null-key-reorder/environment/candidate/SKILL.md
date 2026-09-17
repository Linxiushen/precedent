---
scope: project
description: Turn merged pull requests into a reviewed release note
name: release-notes
---

# Assemble release notes from the changelog

## When to use

Use this when a release is being cut and the notes have to be assembled from merged work.

## Procedure

1. Collect the merged pull requests since the previous tag.
2. Confirm with the user before you publish notes that name a customer; do not proceed without an explicit yes.
3. Run `scripts/notes.sh` to check that the working copy has a changelog.
4. Publish only the section for the release being cut; leave every earlier section as it stands.

## Rules

- Always use `changelog-cli render` for rewriting the changelog.
- Never use `sed -i`: an in-place edit loses the section a reviewer already signed off.

## Evidence

Verified by running `scripts/notes.sh --check` against the fixture in this bundle.
