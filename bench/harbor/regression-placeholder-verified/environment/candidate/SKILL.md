---
name: chrome-devtools
description: Open a page in a debuggable Chrome and read the console
scope: project
---

# Drive Chrome through the DevTools protocol

## When to use

Use this when a page has to be loaded and its console or network log inspected, and a screenshot alone will not answer the question.

## Procedure

1. Start a debuggable Chrome on a free port and note the tab id it prints.
2. Confirm with the user before you attach to a browser the user is already working in; do not proceed without an explicit yes.
3. Run `scripts/devtools.sh` to check that the working copy has a profile directory.
4. Close only the tab this run opened; leave every other window alone.

## Rules

- Always use `chrome-devtools-cli` for driving the browser.
- Never use `killall`: it takes down windows the user opened by hand.

## Evidence

Verified NNNN-NN-NN by the reviewer; see TODO for the transcript.
