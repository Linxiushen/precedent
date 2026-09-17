# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""One session, several deliveries of the same file — the status must follow
the worst of them.

This is a regression test for a correction we did not find ourselves.
DanceNitra reported it on win32 in anthropics/claude-code#82056 (2026-09-17):
the ``instructions`` record is written per prompt build, not per session, so
one session can hold many records for the same file.  Every compaction
re-reads it, a resume repeats the previous delivery byte for byte, and a file
that grows mid-session is delivered at several sizes — they found one session
holding eleven records carrying three different cuts.  Their sentence is the
one that matters: *"a per-session card reports whichever record it read."*

It reproduced here.  Across 510 transcripts on the author's machine, two
sessions carry ``MEMORY.md`` at two different lengths inside the one session
(252 and 400 characters; 103 and 137).

Our code never picked arbitrarily — it took the delivery with the highest
coverage.  That is worse than arbitrary for this particular tool: it is the
one rule that systematically hides truncation, which is the thing the module
exists to find.  A session whose index was cut for most of its life reads
``loaded_complete`` because one late delivery happened to be whole.
"""

import json
import os

from receipts.scan import scan

SLUG = "-tmp-ws"
SESSION = "11111111-2222-3333-4444-555555555555"
LINES = [f"- [Note {i}](n{i}.md) — line {i}" for i in range(1, 13)]


def _rec(ts, cwd, attachment, rendered):
    return {
        "type": "attachment", "timestamp": ts, "sessionId": SESSION, "cwd": cwd,
        "gitBranch": "main", "version": "2.1.268", "isSidechain": False,
        "uuid": f"a-{ts}", "attachment": attachment,
        "rendered": [{"content": rendered}],
    }


def _delivery(ts, cwd, path, n_lines, reason, changed):
    """An ``instructions`` record carrying the first ``n_lines`` of the index."""
    body = "\n".join(LINES[:n_lines]) + "\n"
    return _rec(ts, cwd,
                {"type": "instructions", "reason": reason, "changed": changed,
                 "files": [{"path": path, "type": "AutoMem", "content": body}]},
                "<system-reminder>\n" + body + "\n</system-reminder>")


def _home(tmp_path, deliveries):
    home = tmp_path / "claude"
    ws = tmp_path / "ws"
    ws.mkdir()
    mem = home / "projects" / SLUG / "memory"
    mem.mkdir(parents=True)
    index = mem / "MEMORY.md"
    index.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    for i in range(1, 13):
        (mem / f"n{i}.md").write_text(f"note {i}\n", encoding="utf-8")

    recs = [_delivery(ts, str(ws), str(index), n, reason, changed)
            for ts, n, reason, changed in deliveries]
    p = home / "projects" / SLUG / f"{SESSION}.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    return home


def _index_receipt(home):
    r = scan(str(home))
    sess = next(s for s in r.session_receipts if s.session_id == SESSION)
    return next(a for a in sess.artifacts if a.artifact_id.startswith("memory_index:"))


def test_one_whole_delivery_does_not_launder_two_truncated_ones(tmp_path):
    """4 of 12, then 8 of 12, then all 12 — the session is NOT `complete`."""
    art = _index_receipt(_home(tmp_path, [
        ("2026-09-17T09:00:00.000Z", 4, "session_start", True),
        ("2026-09-17T09:30:00.000Z", 8, "compaction", True),
        ("2026-09-17T10:00:00.000Z", 12, "compaction", True),
    ]))
    assert art.status == "loaded_truncated", (
        "a session holding two cut deliveries and one whole one reported "
        f"{art.status!r} — the best delivery laundered the other two")
    assert any("3 deliveries" in n and "WORST" in n for n in art.notes), art.notes
    # the citation still points at the most complete record, which is the one
    # a reader would want to open.
    assert art.coverage.units_found == 12


def test_the_note_counts_the_incomplete_deliveries(tmp_path):
    art = _index_receipt(_home(tmp_path, [
        ("2026-09-17T09:00:00.000Z", 5, "session_start", True),
        ("2026-09-17T09:30:00.000Z", 12, "compaction", True),
        ("2026-09-17T09:45:00.000Z", 12, "resume", False),
        ("2026-09-17T10:00:00.000Z", 6, "compaction", True),
    ]))
    assert art.status == "loaded_truncated"
    note = next(n for n in art.notes if "deliveries" in n)
    assert "4 deliveries" in note and "2 of them" in note, note


def test_every_delivery_whole_is_still_complete(tmp_path):
    """The correction must not turn every multi-delivery session into an alarm."""
    art = _index_receipt(_home(tmp_path, [
        ("2026-09-17T09:00:00.000Z", 12, "session_start", True),
        ("2026-09-17T09:30:00.000Z", 12, "compaction", True),
        ("2026-09-17T10:00:00.000Z", 12, "resume", False),
    ]))
    assert art.status == "loaded_complete"
    assert not any("deliveries" in n for n in art.notes), art.notes


def test_a_single_delivery_is_unchanged(tmp_path):
    """The common case keeps its old answer and its old note-free receipt."""
    art = _index_receipt(_home(tmp_path, [
        ("2026-09-17T09:00:00.000Z", 7, "session_start", True),
    ]))
    assert art.status == "loaded_truncated"
    assert art.coverage.units_found == 7
    assert not any("deliveries" in n for n in art.notes), art.notes
