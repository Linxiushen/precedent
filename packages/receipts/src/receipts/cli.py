# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""``python -m receipts`` — read-only receipts for Claude Code learned state."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .report import render_markdown
from .scan import scan

__all__ = ["main"]

DEFAULT_CLAUDE_HOME = "~/.claude"


class ReadOnlyViolation(RuntimeError):
    """Raised if an output path would land inside the Claude home being read."""


def _guard_output(path: str | None, claude_home: str) -> str | None:
    """Refuse to write anything under the Claude home directory."""
    if not path:
        return None
    target = os.path.realpath(os.path.expanduser(path))
    home = os.path.realpath(os.path.expanduser(claude_home))
    if target == home or target.startswith(home + os.sep):
        raise ReadOnlyViolation(
            f"refusing to write {target!r}: it is inside the Claude home {home!r}, "
            "which this tool only ever reads")
    return target


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="receipts",
        description="Read-only load receipts, change feed and activation funnel for "
                    "Claude Code learned state (memory, CLAUDE.md, rules, skills).")
    p.add_argument("--version", action="version", version=f"receipts {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="scan transcripts and emit receipts")
    s.add_argument("--claude-home", default=DEFAULT_CLAUDE_HOME,
                   help=f"Claude home directory to read (default: {DEFAULT_CLAUDE_HOME})")
    s.add_argument("--project", default=None, metavar="SLUG_SUBSTRING",
                   help="only scan projects whose slug contains this substring")
    s.add_argument("--json", dest="json_out", default=None, metavar="PATH",
                   help="write the full result as JSON (schemaVersion 1)")
    s.add_argument("--md", dest="md_out", default=None, metavar="PATH",
                   help="write the Markdown report (default: stdout)")
    s.add_argument("--last", dest="last_n", type=int, default=None, metavar="N",
                   help="only scan the N most recent sessions")
    s.add_argument("--no-subagents", action="store_true",
                   help="ignore <session-id>/subagents/**.jsonl transcripts")
    s.add_argument("--quiet", action="store_true",
                   help="do not print the Markdown report to stdout")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "scan":  # pragma: no cover - argparse enforces
        return 2

    home = os.path.realpath(os.path.expanduser(args.claude_home))
    if not os.path.isdir(home):
        print(f"receipts: no such Claude home: {home}", file=sys.stderr)
        return 2
    try:
        json_out = _guard_output(args.json_out, home)
        md_out = _guard_output(args.md_out, home)
    except ReadOnlyViolation as exc:
        print(f"receipts: {exc}", file=sys.stderr)
        return 2

    result = scan(home, project_filter=args.project, last_n=args.last_n,
                  include_subagents=not args.no_subagents)
    markdown = render_markdown(result, last_n=args.last_n)

    if json_out:
        os.makedirs(os.path.dirname(json_out) or ".", exist_ok=True)
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, ensure_ascii=False, indent=2)
        print(f"receipts: wrote {json_out}", file=sys.stderr)
    if md_out:
        os.makedirs(os.path.dirname(md_out) or ".", exist_ok=True)
        with open(md_out, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        print(f"receipts: wrote {md_out}", file=sys.stderr)
    if not args.quiet and not md_out:
        sys.stdout.write(markdown)
    elif not args.quiet and md_out:
        sys.stdout.write(markdown)
    return 0
