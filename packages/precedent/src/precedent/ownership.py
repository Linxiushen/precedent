# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""OWNERSHIP — write permission enforced by **path**, not by tool.

The worst incident in the whole research corpus is not a bad rule, it is a
2:17am background fork rewriting an artifact the user had verified (and doing
it with a Bash heredoc, straight past the memory tool's approval gate).  So:

* the governed trees are project memory, ``CLAUDE.md``, ``.claude/rules``,
  ``.claude/skills`` and ``~/.claude/skills`` — classified by
  :func:`precedent._hooklib.classify_path`, the same function the hook runs;
* **every** agent write into them is snapshotted before it happens and recorded
  as a candidate in ``<state>/candidates.jsonl`` (foreground or subagent, path,
  diff summary, owner, and why we think so);
* a **subagent** write to a **user-owned** artifact answers ``ask``.

"User-owned" is decided in this order: an explicit ``precedent own <path>
--user`` (or a glob), then the change feed — a file no agent has ever been
recorded creating, which exists, is yours.

The ``ask`` is gated on a confirmed rule, ``p-own-guard``, exactly like every
other decision the hook can make: ``precedent own <path> --user`` writes it,
``precedent own --guard off`` removes it.  Until then the hook observes and
records but never interrupts.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from . import SCHEMA_VERSION, __version__
from ._hooklib import GOVERNED_KINDS, classify_path
from .state import now_iso

__all__ = [
    "GOVERNED_KINDS",
    "GUARD_RULE_ID",
    "build_agent_created",
    "classify_path",
    "guard_active",
    "guard_rule",
    "own",
    "read_owners",
    "read_write_candidates",
    "set_guard",
    "summarise_candidates",
    "write_owners",
]

GUARD_RULE_ID = "p-own-guard"

GUARD_MESSAGE = ("子代理不得改写用户拥有的工件（precedent own）—— 先给你看 diff 和快照，"
                 "由你决定")


def _norm(path: str) -> str:
    return os.path.normpath(os.path.expanduser(str(path)))


# --------------------------------------------------------------------------
# owners.json
# --------------------------------------------------------------------------

def read_owners(state) -> dict:
    doc = state.read_json(state.owners_path, default=None)
    if not isinstance(doc, dict):
        doc = {}
    doc.setdefault("schemaVersion", SCHEMA_VERSION)
    if not isinstance(doc.get("paths"), dict):
        doc["paths"] = {}
    if not isinstance(doc.get("policy"), dict):
        doc["policy"] = {}
    return doc


def write_owners(state, doc: dict, now: datetime | None = None) -> str:
    doc["schemaVersion"] = SCHEMA_VERSION
    doc["updatedAt"] = now_iso(now)
    return state.write_json(state.owners_path, doc)


def own(state, path: str, owner: str, now: datetime | None = None,
        note: str | None = None) -> dict:
    """``precedent own <path> --user|--agent``.

    A path ending in ``*`` is stored as a glob, so a whole tree can be claimed
    in one line (``precedent own '~/.claude/skills/**' --user``).
    """
    if owner not in ("user", "agent"):
        raise ValueError("owner must be 'user' or 'agent'")
    key = str(path) if any(c in str(path) for c in "*?") else _norm(path)
    doc = read_owners(state)
    rec = {"owner": owner, "at": now_iso(now), "by": "user",
           "governed": classify_path(key, state.claude_home),
           "exists": os.path.exists(key) if "*" not in key else None}
    if note:
        rec["note"] = note
    doc["paths"][key] = rec
    write_owners(state, doc, now=now)
    return {"path": key, **rec}


# --------------------------------------------------------------------------
# the guard rule — the confirmed precedent that lets the hook say "ask"
# --------------------------------------------------------------------------

def guard_rule(now: datetime | None = None) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": GUARD_RULE_ID,
        "kind": "ownership-guard",
        "hook": "PreToolUse",
        "tool": "Bash|Edit|MultiEdit|NotebookEdit|Write",
        "action": "ask",
        "scope": "global",
        "status": "active",
        "message": GUARD_MESSAGE,
        "confirmedBy": "user",
        "confirmedAt": now_iso(now),
        "origin": "precedent own",
        "tool_version": __version__,
    }


def guard_active(state) -> bool:
    return any(r.get("id") == GUARD_RULE_ID and r.get("status") == "active"
               for r in state.precedents())


def set_guard(state, on: bool, now: datetime | None = None) -> bool:
    """Add or remove ``p-own-guard``.  Returns whether anything changed."""
    rules = state.precedents()
    existing = [r for r in rules if r.get("id") == GUARD_RULE_ID]
    active = any(r.get("status") == "active" for r in existing)
    if on and active:
        return False
    if not on and not existing:
        return False
    rest = [r for r in rules if r.get("id") != GUARD_RULE_ID]
    if on:
        rest.append(guard_rule(now))
    state.write_precedents(rest, now=now)
    return True


# --------------------------------------------------------------------------
# the agent-created index (what the hook consults for "is this yours?")
# --------------------------------------------------------------------------

def build_agent_created(state, scan_result=None, now: datetime | None = None) -> dict:
    """Index every governed path an agent has been recorded **creating**.

    Two sources, both of them records of something that already happened: the
    transcript change feed (``changeType == "create"``) and this tool's own
    pre-write snapshots (a candidate whose snapshot says the file did not exist
    is a creation).  A path that is in neither, and exists, is the user's.
    """
    paths: dict[str, dict] = {}

    def add(path, source, ts, session, tool):
        p = _norm(path)
        row = paths.setdefault(p, {"firstSeen": ts, "sources": [], "n": 0})
        row["n"] += 1
        if ts and (not row.get("firstSeen") or ts < row["firstSeen"]):
            row["firstSeen"] = ts
        tag = f"{source}:{tool}"
        if tag not in row["sources"]:
            row["sources"].append(tag)
        if session and not row.get("session"):
            row["session"] = session

    if scan_result is not None:
        for m in getattr(scan_result, "mutations", []):
            if m.change_type == "create" and classify_path(m.path, state.claude_home):
                add(m.path, "changefeed", m.ts, m.session_id, m.tool)

    for rec in read_write_candidates(state, apply_decisions=False):
        snap = rec.get("snapshot") or {}
        if snap.get("existed") is False:
            add(rec.get("path", ""), "hook", rec.get("ts"), rec.get("session"),
                rec.get("tool", "?"))

    doc = {"schemaVersion": SCHEMA_VERSION, "updatedAt": now_iso(now),
           "tool": {"name": "precedent", "version": __version__},
           "paths": paths}
    state.write_json(state.agent_created_path, doc)
    return doc


# --------------------------------------------------------------------------
# the candidate stream written by the hook
# --------------------------------------------------------------------------

def read_write_candidates(state, limit: int | None = None,
                          apply_decisions: bool = True) -> list[dict]:
    """Every governed-write candidate the hook recorded, newest last."""
    rows: list[dict] = []
    try:
        with open(state.write_candidates_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    if apply_decisions:
        decisions = state.read_json(state.docket_path, default=None) or {}
        dec = decisions.get("decisions") if isinstance(decisions, dict) else None
        dec = dec if isinstance(dec, dict) else {}
        for row in rows:
            d = dec.get(row.get("id"))
            if isinstance(d, dict):
                row["status"] = d.get("status", row.get("status"))
                row["decision_at"] = d.get("at")
                row["decision_reason"] = d.get("reason")
                row["snoozedUntil"] = d.get("until")
    if limit:
        rows = rows[-limit:]
    return rows


def summarise_candidates(rows: list[dict]) -> dict:
    """Counts for the docket header and the report funnel."""
    out = {"total": len(rows), "byAgent": {}, "byGoverned": {}, "byDecision": {},
           "byStatus": {}, "paths": {}, "asks": 0, "subagentWrites": 0}
    for r in rows:
        a = r.get("agent") or "?"
        out["byAgent"][a] = out["byAgent"].get(a, 0) + 1
        g = r.get("governed") or "?"
        out["byGoverned"][g] = out["byGoverned"].get(g, 0) + 1
        d = r.get("decision") or "?"
        out["byDecision"][d] = out["byDecision"].get(d, 0) + 1
        s = r.get("status") or "pending"
        out["byStatus"][s] = out["byStatus"].get(s, 0) + 1
        p = r.get("path") or "?"
        out["paths"][p] = out["paths"].get(p, 0) + 1
        if d == "ask":
            out["asks"] += 1
        if a == "subagent":
            out["subagentWrites"] += 1
    return out
