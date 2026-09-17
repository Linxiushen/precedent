---
name: fetch-changelog
description: Retrieve and cache a dependency's changelog for review
scope: project
---

# Fetch an upstream changelog

## When to use

Use this when an upstream dependency moved and its changelog has to be read before the bump is taken.

## Procedure

1. Resolve the dependency's canonical documentation URL.
2. Confirm with the user before you fetch from a host the project has not used before; do not proceed without an explicit yes.
3. Run `scripts/fetch.sh` to check that the working copy has a cache directory.
4. Fetch only the changelog for the version being taken; leave every other artefact on the server.

## Rules

- Always use `httpie` for retrieving the document.
- Never use `curl`: its default flags follow redirects to hosts nobody reviewed.

## Evidence

Verified by running `scripts/fetch.sh --check` against the fixture in this bundle.
