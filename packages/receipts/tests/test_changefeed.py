# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Change feed: attribution, stated reason, Bash heuristics, out-of-band."""

import os

from receipts.changefeed import bash_write_ops, _candidate_tokens
from receipts.scan import scan


def _feed(result):
    return {(m.tool, m.artifact_id): m for m in result.mutations}


def test_write_edit_and_bash_heredoc_are_all_attributed(fake_home):
    r = scan(fake_home.home)
    feed = _feed(r)
    assert ("Write", f"memory:{fake_home.slug}/kept.md") in feed
    assert ("Bash", f"memory:{fake_home.slug}/orphan.md") in feed
    claude_md = os.path.realpath(os.path.join(fake_home.workspace, "CLAUDE.md"))
    assert ("Edit", f"claude_md:{claude_md}") in feed


def test_mutations_are_chronological_and_carry_session_and_locator(fake_home):
    r = scan(fake_home.home)
    assert r.mutations
    assert [m.ts for m in r.mutations] == sorted(m.ts for m in r.mutations)
    for m in r.mutations:
        assert m.session_id == fake_home.session_1
        assert m.locator().endswith(f":{m.line_no}")
        assert os.path.isfile(m.transcript)


def test_write_records_before_and_after(fake_home):
    r = scan(fake_home.home)
    m = _feed(r)[("Write", f"memory:{fake_home.slug}/kept.md")]
    assert m.change_type == "update"
    assert m.before_excerpt and m.before_excerpt.startswith("---")
    assert m.after_excerpt and m.after_excerpt.startswith("---")
    # excerpts are capped at 400 chars; the full write is kept for the disk check
    assert "Never delete templates" in m.after_full
    assert "Delete every local copy" not in m.after_full
    assert m.corroborated, "file-history-delta should corroborate this write"


def test_edit_records_old_and_new_strings(fake_home):
    r = scan(fake_home.home)
    claude_md = os.path.realpath(os.path.join(fake_home.workspace, "CLAUDE.md"))
    m = _feed(r)[("Edit", f"claude_md:{claude_md}")]
    assert m.change_type == "update"
    assert "default branch" in (m.before_excerpt or "")
    assert "default branch" in (m.after_excerpt or "")


def test_stated_reason_is_the_nearest_preceding_assistant_text(fake_home):
    r = scan(fake_home.home)
    m = _feed(r)[("Write", f"memory:{fake_home.slug}/kept.md")]
    assert m.reason.startswith("Recording the publish-to-doc-tool preference")
    assert len(m.reason) <= 301
    assert m.reason_line_no and m.reason_line_no < m.line_no
    bash = _feed(r)[("Bash", f"memory:{fake_home.slug}/orphan.md")]
    assert "heredoc" in bash.reason


def test_bash_mutation_is_medium_confidence_and_keeps_the_command(fake_home):
    r = scan(fake_home.home)
    m = _feed(r)[("Bash", f"memory:{fake_home.slug}/orphan.md")]
    assert m.op == "bash:heredoc+python"
    assert m.confidence == "medium"
    assert "python3" in (m.detail or "")


def test_read_only_bash_is_not_a_mutation(fake_home):
    r = scan(fake_home.home)
    # `cat <memory file> && ls <memory dir>` must not appear as a write
    cats = [m for m in r.mutations if m.detail and m.detail.startswith("cat ")]
    assert cats == []


def test_bash_write_ops_requires_the_path_inside_a_write_construct():
    cmd = "find /repo -maxdepth 3 -name CLAUDE.md"
    assert bash_write_ops(cmd, _candidate_tokens(cmd)) == {}
    cmd2 = "sed -i '' 's/a/b/' /home/.claude/skills/x/SKILL.md"
    assert bash_write_ops(cmd2, _candidate_tokens(cmd2)) == {
        "/home/.claude/skills/x/SKILL.md": "sed -i"}
    cmd3 = "cat > /x/memory/a.md <<'EOF'\nhi\nEOF"
    assert bash_write_ops(cmd3, _candidate_tokens(cmd3))["/x/memory/a.md"] == "redirect"


def test_bash_write_ops_ignores_paths_quoted_inside_prose():
    cmd = ("python3 - <<'EOF'\n"
           "s = s.replace(\"see /home/.claude/skills/lovart/SKILL.md for details\", \"x\")\n"
           "open('README.md', 'w').write(s)\nEOF")
    ops = bash_write_ops(cmd, _candidate_tokens(cmd))
    assert "/home/.claude/skills/lovart/SKILL.md" not in ops


def test_out_of_band_flags_the_hand_edited_file(fake_home):
    r = scan(fake_home.home)
    high = {o.artifact_id: o for o in r.out_of_band if o.severity == "high"}
    drifted = high[f"memory:{fake_home.slug}/drifted.md"]
    assert drifted.reason == "content_mismatch"
    assert drifted.last_write_session == fake_home.session_1
    assert drifted.last_write_locator


def test_frontmatter_stamping_is_info_not_out_of_band(fake_home, tmp_path):
    r = scan(fake_home.home)
    ids = {o.artifact_id for o in r.out_of_band if o.severity == "high"}
    # kept.md was written verbatim, so it must not be flagged at all
    assert f"memory:{fake_home.slug}/kept.md" not in ids


def test_unwritten_artifacts_are_reported_as_unattributed(fake_home):
    r = scan(fake_home.home)
    info = {o.artifact_id: o for o in r.out_of_band if o.severity != "high"}
    assert info["skill:beta"].reason == "no_attributed_write"
    assert "skill:gamma" not in info  # gamma has an attributed sed -i


def test_file_history_events_are_collected(fake_home):
    r = scan(fake_home.home)
    assert len(r.history_events) == 1
    assert r.history_events[0]["artifactId"] == f"memory:{fake_home.slug}/kept.md"


def test_mtime_only_change_is_flagged_out_of_band(fake_home):
    r = scan(fake_home.home)
    high = {o.artifact_id: o for o in r.out_of_band if o.severity == "high"}
    touched = high[f"memory:{fake_home.slug}/touched.md"]
    assert touched.reason == "mtime_after_last_write"
    assert touched.last_write_session == fake_home.session_1
    assert "later than the last attributed write" in touched.detail


def test_subagent_writes_fold_into_the_parent_session(fake_home):
    r = scan(fake_home.home)
    subs = [m for m in r.mutations if m.is_sidechain]
    assert len(subs) == 1
    m = subs[0]
    assert m.artifact_id == "skill:gamma"
    assert m.op == "bash:sed -i" and m.confidence == "high"
    assert m.session_id == fake_home.session_1
    assert "subagents" in m.transcript and m.agent_file == m.transcript
    assert m.reason.startswith("Bumping the gamma skill timeout")


def test_no_subagents_flag_drops_subagent_mutations(fake_home):
    r = scan(fake_home.home, include_subagents=False)
    assert [m for m in r.mutations if m.is_sidechain] == []


def test_paths_only_inside_inline_code_are_low_confidence():
    from receipts.changefeed import _only_inside_inline_code
    tok = "/home/.claude/skills/x/SKILL.md"
    assert _only_inside_inline_code(f'python3 -c "open(\'{tok}\', \'w\')"', tok)
    assert not _only_inside_inline_code(f"sed -i '' s/a/b/ {tok}", tok)
    assert not _only_inside_inline_code(
        f"python3 -c 'print(1)' && sed -i '' s/a/b/ {tok}", tok)


def test_paths_outside_every_known_cwd_are_out_of_scope(fake_home):
    from receipts.paths import PathClassifier
    pc = PathClassifier(fake_home.home, {fake_home.slug: fake_home.workspace})
    assert pc.classify("/somewhere/else/.claude/skills/x/SKILL.md") is None
    assert pc.classify("/somewhere/else/CLAUDE.md") is None
    # inside a known cwd it does classify
    assert pc.classify(f"{fake_home.workspace}/CLAUDE.md").kind == "claude_md"
    # and the user's own skills dir is always in scope
    assert pc.classify(f"{fake_home.home}/skills/alpha/SKILL.md").artifact_id == "skill:alpha"
