# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
import os

from receipts.artifacts import (INDEX_MAX_LINES, discover_artifacts, parse_frontmatter,
                                parse_index)


def test_parse_frontmatter_nested_and_folded():
    fm = parse_frontmatter(
        "---\n"
        "name: demo\n"
        "description: >-\n"
        "  folded line one\n"
        "  folded line two\n"
        "metadata:\n"
        "  type: project\n"
        "  originSessionId: abc\n"
        "tags: [a, b]\n"
        "---\nbody\n")
    assert fm["name"] == "demo"
    assert fm["description"] == "folded line one folded line two"
    assert fm["metadata"] == {"type": "project", "originSessionId": "abc"}
    assert fm["tags"] == ["a", "b"]


def test_parse_frontmatter_on_plain_file_is_empty():
    assert parse_frontmatter("# just markdown") == {}


def test_parse_index_flags_cap_and_stale(tmp_path):
    (tmp_path / "there.md").write_text("x", encoding="utf-8")
    lines = ["- [There](there.md) — present", "- [Gone](gone.md) — absent"]
    lines += [f"- [F{i}](f{i}.md) — filler" for i in range(2, 205)]
    entries = parse_index("\n".join(lines), str(tmp_path))
    assert entries[0].target == "there.md" and entries[0].exists
    assert entries[1].target == "gone.md" and not entries[1].exists
    beyond = [e for e in entries if e.beyond_cap]
    assert beyond and beyond[0].line_no == INDEX_MAX_LINES + 1


def test_discover_finds_index_entries_orphans_and_stale(fake_home):
    arts = discover_artifacts(fake_home.home, [fake_home.slug],
                              {fake_home.slug: fake_home.workspace})
    by_id = {a.id: a for a in arts}

    idx = by_id[f"memory_index:{fake_home.slug}"]
    assert idx.exists and any("200-line cap" in n or "lines (>" in n for n in idx.notes)

    orphan = by_id[f"memory:{fake_home.slug}/orphan.md"]
    assert any("orphan" in n for n in orphan.notes)

    ghost = by_id[f"memory:{fake_home.slug}/ghost.md"]
    assert not ghost.exists and any("stale" in n for n in ghost.notes)

    assert by_id["skill:alpha"].description.startswith("Publish a document")
    assert f"claude_md:{os.path.realpath(os.path.join(fake_home.workspace, 'CLAUDE.md'))}" in by_id
    assert any(a.kind == "rule" and a.name == "security.md" for a in arts)


def test_user_skills_are_not_double_counted_when_cwd_is_home_parent(fake_home):
    # cwd whose .claude/skills resolves to the user skills dir must not duplicate
    arts = discover_artifacts(fake_home.home, [fake_home.slug],
                              {fake_home.slug: os.path.dirname(fake_home.home)})
    ids = [a.id for a in arts if a.kind == "skill"]
    assert sorted(ids) == ["skill:alpha", "skill:beta", "skill:gamma"]
