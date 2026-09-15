# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""LIVE RECEIPTS — the hook's record beats the transcript inference.

``receipts`` can only infer a load status from whatever context records a
transcript happens to carry, and says ``unknown`` when it carries none.  The
``InstructionsLoaded`` hook knows.  So live wins — and every override is
counted, because silently preferring one evidence source over another is the
failure this whole tool exists to make visible.
"""

from __future__ import annotations

import json
import os

import pytest
from precedent.live import live_rows, merge_live_receipts
from precedent.state import StateDir
from receipts.scan import scan as receipts_scan

SESSION = "99999999-9999-4999-8999-999999999999"   # not one of the mining home's


@pytest.fixture
def home(mining_home, tmp_path):
    state = StateDir.open(str(tmp_path / "state"), mining_home.home, create=True)
    state.tree = mining_home
    return state


def write_live(state, session, rows):
    path = state.session_receipt_path(session)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(dict(row, session=session),
                                ensure_ascii=False) + "\n")


def test_live_receipt_overrides_the_inferred_status(home):
    session = home.tree.sessions["1"]
    protected = home.tree.protected
    result = receipts_scan(home.claude_home)
    before = {a.path: a.status for sr in result.session_receipts
              if sr.session_id == session for a in sr.artifacts}
    assert protected in before

    write_live(home, session, [
        {"event": "session_start", "ts": "2026-09-01T09:00:00Z"},
        {"event": "instructions_loaded", "ts": "2026-09-01T09:00:01Z",
         "path": protected, "status": "loaded_complete", "coverage": 1.0},
    ])
    result = receipts_scan(home.claude_home)
    summary = merge_live_receipts(result, home)
    assert summary["sessions"] == 1 and summary["overrides"] == 1
    assert summary["sessionStarts"] == 1
    sr = [s for s in result.session_receipts if s.session_id == session][0]
    art = [a for a in sr.artifacts if a.path == protected][0]
    assert art.status == "loaded_complete"
    assert art.evidence_kind == "live:InstructionsLoaded"
    assert any("live receipt" in n for n in art.notes)
    if before[protected] != "loaded_complete":
        assert summary["changed"][0]["was"] == before[protected]
        assert summary["changed"][0]["now"] == "loaded_complete"


def test_the_newest_live_row_for_a_path_wins(home):
    session = home.tree.sessions["1"]
    write_live(home, session, [
        {"event": "instructions_loaded", "ts": "2026-09-01T09:00:00Z",
         "path": home.tree.protected, "status": "loaded_complete"},
        {"event": "instructions_loaded", "ts": "2026-09-01T10:00:00Z",
         "path": home.tree.protected, "status": "loaded_truncated"},
    ])
    result = receipts_scan(home.claude_home)
    merge_live_receipts(result, home)
    sr = [s for s in result.session_receipts if s.session_id == session][0]
    art = [a for a in sr.artifacts if a.path == home.tree.protected][0]
    assert art.status == "loaded_truncated"


def test_a_live_only_artifact_is_added_and_a_live_only_session_is_named(home):
    session = home.tree.sessions["1"]
    stranger = os.path.join(home.tree.memory, "never-scanned.md")
    write_live(home, session, [
        {"event": "instructions_loaded", "ts": "2026-09-01T09:00:00Z",
         "path": stranger, "status": "loaded_complete"}])
    write_live(home, SESSION, [
        {"event": "instructions_loaded", "ts": "2026-09-01T09:00:00Z",
         "path": stranger, "status": "not_loaded"}])
    result = receipts_scan(home.claude_home)
    summary = merge_live_receipts(result, home)
    assert summary["added"] == 1
    assert summary["unmatchedSessions"] == [SESSION]
    sr = [s for s in result.session_receipts if s.session_id == session][0]
    art = [a for a in sr.artifacts if a.path == stranger][0]
    assert art.status == "loaded_complete"
    assert "live receipt only" in art.notes[0]


def test_no_live_receipts_changes_nothing(home):
    result = receipts_scan(home.claude_home)
    before = [(a.path, a.status) for sr in result.session_receipts
              for a in sr.artifacts]
    summary = merge_live_receipts(result, home)
    assert summary == {"sessions": 0, "rows": 0, "overrides": 0, "added": 0,
                       "unmatchedSessions": [], "changed": [],
                       "sessionStarts": 0, "humanTurns": 0, "stops": 0}
    after = [(a.path, a.status) for sr in result.session_receipts
             for a in sr.artifacts]
    assert after == before


def test_live_rows_survive_a_torn_line(home):
    path = home.session_receipt_path(SESSION)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"event": "session_start", "session": "%s"}\n' % SESSION)
        fh.write('{"event": "instructions_loa\n')          # killed mid-write
    rows = live_rows(home)
    assert list(rows) == [SESSION] and len(rows[SESSION]) == 1
