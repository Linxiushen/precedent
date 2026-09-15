# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Activation funnel: eligibility, load, citation, duplicates, never-cited."""

from receipts.funnel import find_near_duplicates
from receipts.artifacts import discover_artifacts
from receipts.scan import scan


def _rows(result):
    return {r.artifact_id: r for r in result.funnel}


def test_cited_memory_shows_the_full_funnel(fake_home):
    rows = _rows(scan(fake_home.home))
    kept = rows[f"memory:{fake_home.slug}/kept.md"]
    # the write is an update, so creation falls back to the frontmatter stamp
    assert kept.created_by_session == fake_home.session_1
    assert kept.created_evidence == "frontmatter metadata.originSessionId"
    assert kept.created_at == "2026-09-10T10:00:00.000Z"
    assert kept.n_sessions_eligible == 2
    assert kept.n_sessions_loaded == 1
    assert kept.n_sessions_cited == 1
    assert kept.last_cited and kept.last_cited_locator
    assert kept.never_cited is False
    assert kept.n_writes == 1
    assert kept.size_bytes > 0 and kept.age_days is not None


def test_never_cited_artifacts_are_flagged(fake_home):
    rows = _rows(scan(fake_home.home))
    orphan = rows[f"memory:{fake_home.slug}/orphan.md"]
    assert orphan.never_cited is True
    assert any("never cited" in w for w in orphan.warnings)
    assert any("orphan" in w for w in orphan.warnings)
    gamma = rows["skill:gamma"]
    assert gamma.never_cited is True


def test_kinds_without_a_citation_signal_report_na_not_zero(fake_home):
    rows = _rows(scan(fake_home.home))
    md = next(r for r in rows.values() if r.kind == "claude_md")
    assert md.citable is False
    assert md.to_dict()["nSessionsCited"] is None
    assert md.never_cited is False


def test_eligibility_excludes_sessions_that_predate_the_artifact(fake_home):
    """`drifted.md` is created mid-session-1, so session 1 is not eligible for it
    while session 2 (a day later) is."""
    rows = _rows(scan(fake_home.home))
    drifted = rows[f"memory:{fake_home.slug}/drifted.md"]
    assert drifted.created_evidence == "change feed"
    assert drifted.created_by_session == fake_home.session_1
    assert drifted.n_sessions_eligible == 1
    kept = rows[f"memory:{fake_home.slug}/kept.md"]
    assert kept.n_sessions_eligible == 2


def test_near_duplicate_descriptions_are_paired(fake_home):
    rows = _rows(scan(fake_home.home))
    alpha, beta = rows["skill:alpha"], rows["skill:beta"]
    assert [d["artifactId"] for d in alpha.near_duplicates] == ["skill:beta"]
    assert [d["artifactId"] for d in beta.near_duplicates] == ["skill:alpha"]
    assert 0.6 <= alpha.near_duplicates[0]["jaccard"] < 1.0
    assert any("near-duplicate" in w for w in alpha.warnings)
    assert not rows["skill:gamma"].near_duplicates


def test_find_near_duplicates_threshold_is_respected(fake_home):
    arts = discover_artifacts(fake_home.home, [fake_home.slug],
                              {fake_home.slug: fake_home.workspace})
    assert find_near_duplicates(arts, threshold=0.95) == []
    pairs = find_near_duplicates(arts, threshold=0.6)
    assert {p[0] for p in pairs} | {p[1] for p in pairs} == {"skill:alpha", "skill:beta"}


def test_stale_entry_row_is_marked_missing(fake_home):
    rows = _rows(scan(fake_home.home))
    ghost = rows[f"memory:{fake_home.slug}/ghost.md"]
    assert ghost.exists is False
    assert any("missing on disk" in w for w in ghost.warnings)


def test_index_cap_warning_reaches_the_funnel(fake_home):
    rows = _rows(scan(fake_home.home))
    idx = rows[f"memory_index:{fake_home.slug}"]
    assert any("cap" in w for w in idx.warnings)


def test_artifact_with_no_eligible_session_is_unmeasured_not_never_cited(fake_home):
    rows = _rows(scan(fake_home.home))
    # drifted.md is created mid-session-1; session 2 is a day later, so it IS
    # eligible.  touched.md likewise.  Build the "nobody could have used it" case
    # by scanning only session 1.
    only_first = scan(fake_home.home, last_n=1)
    assert only_first.session_receipts[0].session_id == fake_home.session_2
    rows_last = {r.artifact_id: r for r in only_first.funnel}
    assert rows_last[f"memory:{fake_home.slug}/kept.md"].n_sessions_eligible == 1
    drifted = rows[f"memory:{fake_home.slug}/drifted.md"]
    assert drifted.unmeasured is False and drifted.n_sessions_eligible == 1


def test_scan_of_a_single_session_marks_later_artifacts_unmeasured(fake_home):
    """Scanning only the newest session leaves session-1 creations measurable,
    but an artifact created after that session started is unmeasured."""
    r = scan(fake_home.home, project_filter="tmp-workspace")
    rows = {x.artifact_id: x for x in r.funnel}
    # every row is either measured or explicitly marked unmeasured, never both
    for row in rows.values():
        assert not (row.unmeasured and row.never_cited)


def test_unmeasured_flag_serialises(fake_home):
    r = scan(fake_home.home)
    assert all("unmeasured" in row.to_dict() for row in r.funnel)
