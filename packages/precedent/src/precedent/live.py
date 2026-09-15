# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""LIVE RECEIPTS — what the SessionStart / InstructionsLoaded hooks saw.

``receipts`` infers a load receipt *after the fact*, by looking for the
artifact's text inside whatever context records the transcript happens to
carry; when a session carries none, the honest answer is ``unknown``.  The
``InstructionsLoaded`` hook removes the inference: Claude Code hands us the
files it loaded, at the moment it loads them.

So the merge rule is simply **live wins**.  A live row replaces the inferred
status for that ``(session, path)`` and says so in its evidence
(``live:InstructionsLoaded``); where there is no live row, nothing changes.
Every override is counted and reported — a silent overwrite of one evidence
source by another is exactly the kind of thing this tool exists to catch.
"""

from __future__ import annotations

import json
import os

from receipts.receipts import ArtifactReceipt

__all__ = ["live_rows", "merge_live_receipts", "read_session_events",
           "session_files"]

_LOAD_EVENT = "instructions_loaded"


def session_files(state) -> list[str]:
    try:
        names = sorted(n for n in os.listdir(state.receipts_dir)
                       if n.endswith(".jsonl"))
    except OSError:
        return []
    return [os.path.join(state.receipts_dir, n) for n in names]


def read_session_events(path: str) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
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
    return rows


def live_rows(state) -> dict[str, list[dict]]:
    """``{session_id: [event rows]}`` for every live receipt file on disk."""
    out: dict[str, list[dict]] = {}
    for path in session_files(state):
        rows = read_session_events(path)
        if not rows:
            continue
        session = (rows[0].get("session")
                   or os.path.basename(path)[: -len(".jsonl")])
        out.setdefault(session, []).extend(rows)
    return out


def _latest_loads(rows: list[dict]) -> dict[str, dict]:
    """The newest ``instructions_loaded`` row per path (a session can reload)."""
    best: dict[str, dict] = {}
    for r in rows:
        if r.get("event") != _LOAD_EVENT:
            continue
        path = r.get("path")
        if not path:
            continue
        prev = best.get(path)
        if prev is None or str(r.get("ts") or "") >= str(prev.get("ts") or ""):
            best[path] = r
    return best


def merge_live_receipts(scan_result, state) -> dict:
    """Fold live receipts into a :class:`receipts.scan.ScanResult`.  Live wins.

    Returns a summary ``{sessions, rows, overrides, added, unmatchedSessions,
    changed: [...]}`` — ``changed`` names every artifact whose status the live
    record disagreed with, which is the interesting part.
    """
    by_session = live_rows(state)
    summary = {"sessions": 0, "rows": 0, "overrides": 0, "added": 0,
               "unmatchedSessions": [], "changed": [], "sessionStarts": 0,
               "humanTurns": 0, "stops": 0}
    if not by_session:
        return summary
    index = {sr.session_id: sr for sr in scan_result.session_receipts}
    for session, rows in by_session.items():
        summary["rows"] += len(rows)
        summary["sessionStarts"] += len([r for r in rows
                                         if r.get("event") == "session_start"])
        summary["humanTurns"] += len([r for r in rows
                                      if r.get("event") == "human_turn"])
        summary["stops"] += len([r for r in rows
                                 if r.get("event") == "session_stop"])
        loads = _latest_loads(rows)
        if not loads:
            continue
        summary["sessions"] += 1
        sr = index.get(session)
        if sr is None:
            summary["unmatchedSessions"].append(session)
            continue
        by_path = {a.path: a for a in sr.artifacts}
        for path, row in loads.items():
            status = row.get("status") or "unknown"
            note = (f"live receipt (InstructionsLoaded at {row.get('ts')}) — "
                    f"overrides the transcript-inferred status")
            art = by_path.get(path)
            if art is None:
                art = ArtifactReceipt(
                    session_id=session, ts=row.get("ts"),
                    artifact_id=path, kind=row.get("type") or "instruction",
                    name=os.path.basename(path), path=path, status=status,
                    evidence_kind="live:InstructionsLoaded",
                    evidence_ts=row.get("ts"),
                    evidence_source=state.session_receipt_path(session),
                    notes=["live receipt only — this artifact was not in the "
                           "transcript-derived scan"])
                sr.artifacts.append(art)
                summary["added"] += 1
                continue
            if art.status != status:
                summary["changed"].append({"session": session, "path": path,
                                           "was": art.status, "now": status})
            art.status = status
            art.evidence_kind = "live:InstructionsLoaded"
            art.evidence_ts = row.get("ts")
            art.evidence_source = state.session_receipt_path(session)
            art.notes = list(art.notes) + [note]
            summary["overrides"] += 1
    return summary
