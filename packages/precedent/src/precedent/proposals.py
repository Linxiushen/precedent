# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""PROPOSALS — the third kind of docket entry.

``candidates.json`` holds compiled *rule* candidates and ``candidates.jsonl``
holds governed-write candidates.  A **proposal** is what the nightly improver
drafts and what the examiner certifies: a bounded edit to one surface, with a
hypothesis, an expected effect, the paths it declares it will touch, and the
verdict of whatever gate was competent to judge it.

A proposal is **never applied by precedent**.  Confirming one in the docket
records the human decision and prints the command that would apply it; the
bytes are only ever written by the user.  That is the whole point of ⑤: the
loop proposes, the gate decides, and the human is the only writer.

``<state>/proposals.jsonl`` is append-only; the newest record for an id wins,
so a proposal can be re-certified (examined, then examined again with more
cassettes) without losing its history.
"""

from __future__ import annotations

import hashlib
import json
import os

from . import SCHEMA_VERSION

__all__ = ["PROPOSAL_SURFACES", "append_proposal", "proposal_id",
           "read_proposals", "read_proposal_history"]

#: The surfaces a proposal may touch.  ``precedent`` drafts go to the temporal
#: birth gate; everything else goes to the examiner (or HOLD "no evidence").
PROPOSAL_SURFACES = ("claude_md", "skill", "precedent")


def proposal_id(payload: dict) -> str:
    """Content-addressed id: the same edit proposed twice is the same id."""
    body = json.dumps({k: payload.get(k) for k in
                       ("surface", "declaredPaths", "content", "hypothesis")},
                      ensure_ascii=False, sort_keys=True)
    return "d-" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]


def append_proposal(state, row: dict) -> str:
    """Append one proposal record; returns its id."""
    row = dict(row)
    row.setdefault("schemaVersion", SCHEMA_VERSION)
    row.setdefault("id", proposal_id(row))
    path = state.path("proposals.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row["id"]


def read_proposal_history(state) -> list[dict]:
    """Every proposal record ever appended, oldest first."""
    path = state.path("proposals.jsonl")
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
                if isinstance(row, dict) and row.get("id"):
                    rows.append(row)
    except OSError:
        return []
    return rows


def read_proposals(state) -> list[dict]:
    """The newest record per proposal id, newest first."""
    latest: dict[str, dict] = {}
    for row in read_proposal_history(state):
        latest[row["id"]] = row
    out = list(latest.values())
    out.sort(key=lambda r: (r.get("ts") or ""), reverse=True)
    return out
