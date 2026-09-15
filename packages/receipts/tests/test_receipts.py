# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""(session x artifact) load receipts against the synthetic tree."""

from receipts.scan import scan


def _by_session(result):
    return {s.session_id: s for s in result.session_receipts}


def _art(session_receipt, artifact_id):
    for a in session_receipt.artifacts:
        if a.artifact_id == artifact_id:
            return a
    raise AssertionError(f"{artifact_id} not in receipt "
                         f"({[a.artifact_id for a in session_receipt.artifacts]})")


def test_complete_load_of_a_recalled_memory(fake_home):
    r = scan(fake_home.home)
    s1 = _by_session(r)[fake_home.session_1]
    kept = _art(s1, f"memory:{fake_home.slug}/kept.md")
    assert kept.status == "loaded_complete"
    assert kept.evidence_kind == "prompt_snapshot"
    assert kept.evidence_line_no and kept.evidence_ts


def test_claude_md_and_rule_load_status(fake_home):
    r = scan(fake_home.home)
    s1 = _by_session(r)[fake_home.session_1]
    md = next(a for a in s1.artifacts if a.kind == "claude_md")
    assert md.status == "loaded_complete"
    rule = next(a for a in s1.artifacts if a.kind == "rule")
    # the rule file is not in the snapshot -> honestly reported as not loaded
    assert rule.status == "not_loaded"


def test_truncated_index_names_the_dropped_lines(fake_home):
    r = scan(fake_home.home)
    s1 = _by_session(r)[fake_home.session_1]
    idx = _art(s1, f"memory_index:{fake_home.slug}")
    assert idx.status == "loaded_truncated"
    assert idx.coverage.method == "lines"
    assert idx.coverage.units_total == 211 and idx.coverage.units_found == 200
    missing = [n for n, _ in idx.missing_index_lines]
    assert missing == list(range(201, 212))
    assert any("cap" in n for n in idx.notes)
    assert idx.evidence_kind == "instructions"


def test_skill_listed_is_complete_and_unlisted_is_not_loaded(fake_home):
    r = scan(fake_home.home)
    s1 = _by_session(r)[fake_home.session_1]
    assert _art(s1, "skill:alpha").status == "loaded_complete"
    gamma = _art(s1, "skill:gamma")
    assert gamma.status == "not_loaded"
    assert any("not among" in n for n in gamma.notes)


def test_stale_index_entry_is_missing_on_disk(fake_home):
    r = scan(fake_home.home)
    s1 = _by_session(r)[fake_home.session_1]
    ghost = _art(s1, f"memory:{fake_home.slug}/ghost.md")
    assert ghost.status == "missing_on_disk"
    assert any("stale" in n for n in ghost.notes)


def test_orphan_is_flagged_but_still_receipted(fake_home):
    r = scan(fake_home.home)
    s1 = _by_session(r)[fake_home.session_1]
    orphan = _art(s1, f"memory:{fake_home.slug}/orphan.md")
    assert orphan.status == "not_loaded"
    assert any("orphan" in n for n in orphan.notes)


def test_session_without_context_records_is_unknown_not_not_loaded(fake_home):
    r = scan(fake_home.home)
    s2 = _by_session(r)[fake_home.session_2]
    assert not s2.has_prompt_snapshot and not s2.has_skill_listing
    assert {a.status for a in s2.artifacts} == {"unknown", "missing_on_disk"}
    assert any("prompt_snapshot" in n for a in s2.artifacts for n in a.notes)


def test_cc_memory_citation_is_captured(fake_home):
    r = scan(fake_home.home)
    s1 = _by_session(r)[fake_home.session_1]
    cites = [c for c in s1.cited_artifacts if c["via"] == "cc-memory tag"]
    assert len(cites) == 1
    assert cites[0]["name"] == "kept.md" and cites[0]["resolved"]
    assert cites[0]["lineNo"] and cites[0]["ts"]


def test_skill_invocation_is_captured(fake_home):
    r = scan(fake_home.home)
    s1 = _by_session(r)[fake_home.session_1]
    skills = [c for c in s1.cited_artifacts if c["kind"] == "skill"]
    assert [c["name"] for c in skills] == ["alpha"]


def test_session_summary_carries_model_and_usage(fake_home):
    r = scan(fake_home.home)
    s1 = _by_session(r)[fake_home.session_1]
    assert s1.models == ["claude-opus-5"]
    assert s1.usage["output_tokens"] > 0
    assert s1.n_human_prompts == 1
    assert s1.cwd == fake_home.workspace and s1.git_branch == "main"
    assert s1.written_artifacts  # this session wrote learned state


def test_every_receipt_carries_a_grep_locator(fake_home):
    r = scan(fake_home.home)
    s1 = _by_session(r)[fake_home.session_1]
    for a in s1.artifacts:
        if a.status in ("loaded_complete", "loaded_truncated"):
            assert a.locator(), f"{a.artifact_id} has no evidence locator"
            assert ":" in a.locator()
