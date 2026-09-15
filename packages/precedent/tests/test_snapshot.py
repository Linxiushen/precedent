# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Content-addressed snapshots and the ``undo --session`` round trip."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from conftest import KEPT_FILE, KEPT_FILE_BEFORE, RECEIPTS_SESSION_1
from precedent.snapshot import (UndoRefused, apply_undo, learned_state_roots,
                                list_snapshots, newest_before, plan_undo,
                                take_snapshot)
from precedent.state import StateDir
from receipts.scan import scan


def _state(tmp_path, home):
    return StateDir.open(str(tmp_path / "state"), home, create=True)


def _utc(day, hour=0):
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# roots + snapshot
# --------------------------------------------------------------------------

def test_learned_state_roots_cover_both_scopes(fake_home):
    roots = learned_state_roots(fake_home.home, cwds=[fake_home.workspace],
                                slugs=[fake_home.slug])
    assert os.path.join(fake_home.home, "skills") in roots
    assert os.path.join(fake_home.home, "projects", fake_home.slug, "memory") in roots
    assert os.path.join(fake_home.workspace, "CLAUDE.md") in roots
    assert os.path.join(fake_home.workspace, ".claude", "rules") in roots
    assert os.path.join(fake_home.workspace, ".claude", "skills") in roots


def test_snapshot_stores_blobs_and_a_manifest(tmp_path, fake_home):
    state = _state(tmp_path, fake_home.home)
    man = take_snapshot(state, cwds=[fake_home.workspace], slugs=[fake_home.slug])
    paths = {f["path"] for f in man.files}
    assert os.path.join(fake_home.memory, "kept.md") in paths
    assert os.path.join(fake_home.workspace, "CLAUDE.md") in paths
    assert man.n_bytes > 0
    for f in man.files:
        blob = os.path.join(state.blobs_dir, f["sha256"])
        assert os.path.exists(blob)
        assert os.path.getsize(blob) == f["size"]
    assert os.path.exists(os.path.join(state.snapshots_dir, man.id + ".json"))
    assert man.id.startswith("snap-")


def test_snapshot_is_content_addressed_and_deduplicates(tmp_path, fake_home):
    state = _state(tmp_path, fake_home.home)
    a = take_snapshot(state, cwds=[fake_home.workspace], slugs=[fake_home.slug],
                      now=_utc(10))
    n_blobs = len(os.listdir(state.blobs_dir))
    b = take_snapshot(state, cwds=[fake_home.workspace], slugs=[fake_home.slug],
                      now=_utc(11))
    assert len(os.listdir(state.blobs_dir)) == n_blobs, "unchanged tree, no new blobs"
    assert a.id.split("-")[-1] == b.id.split("-")[-1], "same tree hash"
    assert a.id != b.id, "different timestamps"
    assert len(list_snapshots(state)) == 2


def test_snapshot_dry_run_writes_nothing(tmp_path, fake_home):
    state = _state(tmp_path, fake_home.home)
    man = take_snapshot(state, cwds=[fake_home.workspace], slugs=[fake_home.slug],
                        dry_run=True)
    assert man.files
    assert os.listdir(state.blobs_dir) == []
    assert os.listdir(state.snapshots_dir) == []


def test_newest_before_picks_the_right_baseline(tmp_path, fake_home):
    state = _state(tmp_path, fake_home.home)
    old = take_snapshot(state, cwds=[fake_home.workspace], slugs=[fake_home.slug],
                        now=_utc(10))
    take_snapshot(state, cwds=[fake_home.workspace], slugs=[fake_home.slug],
                  now=_utc(14))
    assert newest_before(state, "2026-09-12T09:00:00Z").id == old.id
    assert newest_before(state, "2026-09-01T00:00:00Z") is None


# --------------------------------------------------------------------------
# undo
# --------------------------------------------------------------------------

@pytest.fixture
def undoable(tmp_path, fake_home):
    """Rewind the tree to its pre-session state, snapshot it, then replay.

    Session 1 of the receipts fixture updated ``kept.md`` and *created*
    ``drifted.md``; this sets the disk back to how it looked before that
    session, takes the baseline snapshot, and then puts the post-session state
    back — which is exactly the situation ``undo --session`` exists for.
    """
    kept = os.path.join(fake_home.memory, "kept.md")
    drifted = os.path.join(fake_home.memory, "drifted.md")
    with open(kept, "w", encoding="utf-8") as fh:
        fh.write(KEPT_FILE_BEFORE)
    drifted_after = open(drifted, encoding="utf-8").read()
    os.remove(drifted)

    state = _state(tmp_path, fake_home.home)
    baseline = take_snapshot(state, cwds=[fake_home.workspace],
                             slugs=[fake_home.slug], now=_utc(11))

    with open(kept, "w", encoding="utf-8") as fh:
        fh.write(KEPT_FILE)
    with open(drifted, "w", encoding="utf-8") as fh:
        fh.write(drifted_after)

    result = scan(fake_home.home)
    return state, baseline, result, kept, drifted


def test_undo_plan_names_restore_and_delete(undoable):
    state, baseline, result, kept, drifted = undoable
    plan = plan_undo(state, RECEIPTS_SESSION_1, result)
    assert plan.snapshot_id == baseline.id
    by_path = {a["path"]: a for a in plan.actions}
    assert by_path[kept]["action"] == "restore"
    assert by_path[drifted]["action"] == "delete"
    assert plan.changes


def test_undo_round_trip(undoable):
    state, _baseline, result, kept, drifted = undoable
    plan = plan_undo(state, RECEIPTS_SESSION_1, result)
    done = apply_undo(state, plan)
    assert open(kept, encoding="utf-8").read() == KEPT_FILE_BEFORE
    assert not os.path.exists(drifted)
    assert {d["result"] for d in done} == {"restored", "deleted"}

    # re-planning is now a no-op: the tree matches the snapshot again
    again = plan_undo(state, RECEIPTS_SESSION_1, scan(state.claude_home))
    assert {a["action"] for a in again.actions} <= {"unchanged", "noop"}
    assert not again.changes


def test_undo_is_dry_run_unless_applied(undoable):
    state, _b, result, kept, _d = undoable
    plan_undo(state, RECEIPTS_SESSION_1, result)      # planning alone changes nothing
    assert open(kept, encoding="utf-8").read() == KEPT_FILE


def test_undo_without_a_baseline_snapshot_explains_itself(tmp_path, fake_home):
    state = _state(tmp_path, fake_home.home)
    plan = plan_undo(state, RECEIPTS_SESSION_1, scan(fake_home.home))
    assert plan.snapshot_id is None
    assert any("no snapshot older" in n for n in plan.notes)
    assert all(a["action"] in ("delete", "noop") for a in plan.actions)


def test_undo_flags_later_sessions_as_conflicts(undoable, fake_home):
    state, _b, result, kept, _d = undoable
    # pretend a later session also wrote kept.md
    from receipts.changefeed import Mutation
    result.mutations.append(Mutation(
        ts="2026-09-13T08:00:05.000Z", session_id=fake_home.session_2,
        project_slug=fake_home.slug, transcript="x", line_no=1, tool="Write",
        op="write", path=kept, artifact_id="memory:x/kept.md",
        artifact_kind="memory", change_type="update"))
    plan = plan_undo(state, RECEIPTS_SESSION_1, result)
    assert any(n.startswith("CONFLICT:") for n in plan.notes)


def test_undo_refuses_a_real_claude_home_without_i_know(tmp_path, fake_home,
                                                        monkeypatch):
    """The guard is on the *target path*, not on a flag we set ourselves."""
    state = _state(tmp_path, fake_home.home)
    plan = plan_undo(state, RECEIPTS_SESSION_1, scan(fake_home.home))
    plan.actions = [{"path": os.path.expanduser("~/.claude/CLAUDE.md"),
                     "action": "restore", "reason": "test",
                     "currentSha256": "a", "snapshotSha256": "b"}]
    with pytest.raises(UndoRefused) as exc:
        apply_undo(state, plan, i_know=False)
    assert "--i-know" in str(exc.value)
    assert not os.path.exists(os.path.join(state.blobs_dir, "b"))


def test_snapshot_never_writes_outside_the_state_dir(tmp_path, fake_home,
                                                     real_home_canary):
    from conftest import tree_fingerprint
    state = _state(tmp_path, fake_home.home)
    before = tree_fingerprint(fake_home.home)
    take_snapshot(state, cwds=[fake_home.workspace], slugs=[fake_home.slug])
    assert tree_fingerprint(fake_home.home) == before
    real_home_canary()
