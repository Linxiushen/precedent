# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""OWNERSHIP — governed trees, snapshots, the owner decision, `precedent own`.

The unit under test is :mod:`precedent._hooklib`, which is the *same file* that
gets copied into ``<state>/hooks/_plib.py`` — so what is asserted here is what
Claude Code runs.
"""

from __future__ import annotations

import json
import os

import pytest
from conftest import tree_fingerprint
from precedent import _hooklib as H
from precedent.cli import main
from precedent.ownership import (GUARD_RULE_ID, build_agent_created,
                                 guard_active, own, read_owners,
                                 read_write_candidates, set_guard,
                                 summarise_candidates)
from precedent.state import StateDir


@pytest.fixture
def lib(tmp_path):
    """A configured hook library over a synthetic state dir + Claude home."""
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    os.makedirs(state.claude_home, exist_ok=True)
    H.configure(state.root, state.claude_home)
    H._SLOW.clear()
    return state


def _mem(state, slug="-tmp-proj"):
    d = os.path.join(state.claude_home, "projects", slug, "memory")
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# the governed trees
# --------------------------------------------------------------------------

def test_classify_path_covers_exactly_the_governed_trees(lib):
    home = lib.claude_home
    governed = {
        f"{home}/projects/-a-b/memory/notes.md": "memory",
        f"{home}/skills/foo/SKILL.md": "skills",
        f"{home}/CLAUDE.md": "claude_md",
        "/repo/CLAUDE.md": "claude_md",
        "/repo/CLAUDE.local.md": "claude_md",
        "/repo/.claude/rules/style.md": "rules",
        "/repo/.claude/skills/deps/SKILL.md": "skills",
    }
    for path, kind in governed.items():
        assert H.classify_path(path) == kind, path
    for path in ("/repo/src/main.py", f"{home}/settings.json", "/repo/README.md",
                 f"{home}/projects/-a-b/session.jsonl", "/repo/.claude/foo.json"):
        assert H.classify_path(path) is None, path


def test_bash_write_targets_reads_the_write_construct_not_the_mention(lib):
    mem = _mem(lib)
    target = os.path.join(mem, "project.md")
    for cmd, how in (
        (f"echo hi > {target}", "redirect"),
        (f"cat x | tee -a {target}", "tee"),
        (f"sed -i '' 's/a/b/' {target}", "sed -i"),
        (f"cp /tmp/x {target}", "verb"),
        (f"M={target} && python3 -c \"open('x','w')\" > out", "var"),
    ):
        got = H.bash_write_targets(cmd)
        assert got and got[0][0] == target and got[0][1] == how, (cmd, got)
    # a mention is not a write
    assert H.bash_write_targets(f"grep pip {target}") == []
    assert H.bash_write_targets(f"cat {target}") == []
    # a heredoc body that *contains* the path is not a write to it
    assert H.bash_write_targets(
        "python3 - <<'EOF'\nprint('%s')\nEOF" % target) == []
    # a non-governed path is never even considered
    assert H.bash_write_targets("echo x > /repo/src/main.py") == []


# --------------------------------------------------------------------------
# snapshot + diff
# --------------------------------------------------------------------------

def test_snapshot_is_content_addressed_and_taken_before_the_write(lib):
    mem = _mem(lib)
    path = os.path.join(mem, "notes.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("one\ntwo\n")
    snap = H.snapshot_file(path)
    assert snap["existed"] and snap["bytes"] == 8
    assert os.path.isfile(snap["blob"])
    assert open(snap["blob"], encoding="utf-8").read() == "one\ntwo\n"
    assert snap["blob"].endswith(snap["sha256"])
    # a second identical snapshot deduplicates onto the same blob
    again = H.snapshot_file(path)
    assert again["blob"] == snap["blob"]
    assert H.snapshot_file(os.path.join(mem, "nope.md"))["existed"] is False


def test_diff_summary_counts_lines_for_every_write_tool(lib):
    mem = _mem(lib)
    path = os.path.join(mem, "notes.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("a\nb\nc\n")
    d = H.diff_summary(path, "Write", {"file_path": path, "content": "a\nB\nc\nd\n"})
    assert d["addedLines"] == 2 and d["removedLines"] == 1
    assert d["summary"] == "+2 −1 lines"
    d = H.diff_summary(path, "Edit", {"file_path": path, "old_string": "b",
                                      "new_string": "bbb"})
    assert d["addedLines"] == 1 and d["removedLines"] == 1
    d = H.diff_summary(path, "MultiEdit", {"file_path": path, "edits": [
        {"old_string": "a", "new_string": "A"},
        {"old_string": "c", "new_string": "C"}]})
    assert d["addedLines"] == 2
    d = H.diff_summary(path, "Bash", {"command": "echo x >> " + path})
    assert d["summary"].startswith("bash: echo x")


# --------------------------------------------------------------------------
# who owns it
# --------------------------------------------------------------------------

def test_owner_of_order_is_declaration_then_change_feed_then_existence(lib):
    mem = _mem(lib)
    mine_ = os.path.join(mem, "hand-written.md")
    theirs = os.path.join(mem, "agent-made.md")
    for p in (mine_, theirs):
        open(p, "w").write("x")
    # nothing declared, nothing in the change feed: an existing file is yours
    assert H.owner_of(mine_)[0] == "user"
    # a file an agent created is the agent's
    lib.write_json(lib.agent_created_path, {"paths": {theirs: {"n": 1}}})
    assert H.owner_of(theirs)[0] == "agent"
    # an explicit declaration beats the change feed
    own(lib, theirs, "user")
    assert H.owner_of(theirs)[0] == "user"
    # a glob declaration applies to the tree
    own(lib, os.path.join(mem, "*.md"), "agent")
    assert H.owner_of(os.path.join(mem, "brand-new.md"))[0] == "agent"
    # a file that does not exist yet is being created by this very call
    assert H.owner_of(os.path.join(lib.claude_home, "skills", "x", "SKILL.md"))[0] \
        == "agent"


def test_build_agent_created_reads_the_hook_candidates(lib):
    mem = _mem(lib)
    created = os.path.join(mem, "made-by-agent.md")
    H.append_jsonl(lib.write_candidates_path, {
        "id": "w-1", "path": created, "ts": "2026-09-10T00:00:00Z",
        "tool": "Write", "session": "s1", "snapshot": {"existed": False}})
    H.append_jsonl(lib.write_candidates_path, {
        "id": "w-2", "path": os.path.join(mem, "edited.md"),
        "ts": "2026-09-11T00:00:00Z", "tool": "Edit", "session": "s1",
        "snapshot": {"existed": True}})
    doc = build_agent_created(lib)
    assert created in doc["paths"]
    assert os.path.join(mem, "edited.md") not in doc["paths"]
    assert json.load(open(lib.agent_created_path, encoding="utf-8"))["paths"]


# --------------------------------------------------------------------------
# the decision
# --------------------------------------------------------------------------

def _payload(path, agent=None, tool="Write"):
    p = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": "sess-1",
         "cwd": "/repo",
         "tool_input": {"file_path": path, "content": "new body\n"}}
    if agent:
        p["agent_id"] = agent
    return p


def test_every_governed_write_is_recorded_even_when_it_is_allowed(lib):
    mem = _mem(lib)
    path = os.path.join(mem, "notes.md")
    open(path, "w").write("old\n")
    out, recs = H.ownership_pass(_payload(path))
    assert out is None                               # foreground: allow
    assert len(recs) == 1
    rec = recs[0]
    assert rec["agent"] == "foreground" and rec["governed"] == "memory"
    assert rec["decision"] == "allow" and rec["owner"] == "user"
    assert rec["snapshot"]["existed"] and rec["diff"]["summary"] == "+1 −1 lines"
    stored = read_write_candidates(lib)
    assert [r["id"] for r in stored] == [rec["id"]]
    assert summarise_candidates(stored)["byGoverned"] == {"memory": 1}


def test_subagent_write_to_a_user_file_asks_only_with_the_confirmed_guard(lib):
    mem = _mem(lib)
    path = os.path.join(mem, "notes.md")
    open(path, "w").write("old\n")
    # no guard rule yet: observe and record, never interrupt
    out, recs = H.ownership_pass(_payload(path, agent="agent-7"))
    assert out is None and recs[0]["decision"] == "allow"
    assert recs[0]["agent"] == "subagent" and recs[0]["agentId"] == "agent-7"

    set_guard(lib, True)
    out, recs = H.ownership_pass(_payload(path, agent="agent-7"))
    assert out["permissionDecision"] == "ask"
    assert GUARD_RULE_ID in out["permissionDecisionReason"]
    assert path in out["permissionDecisionReason"]
    assert recs[0]["decision"] == "ask"

    # same guard, same file, but the foreground agent: allow
    out, _ = H.ownership_pass(_payload(path))
    assert out is None
    # same guard, subagent, but a file the agent created: allow
    theirs = os.path.join(mem, "agent.md")
    open(theirs, "w").write("x")
    lib.write_json(lib.agent_created_path, {"paths": {theirs: {"n": 1}}})
    out, recs = H.ownership_pass(_payload(theirs, agent="agent-7"))
    assert out is None and recs[0]["owner"] == "agent"


def test_ungoverned_writes_are_not_snapshotted_or_recorded(lib):
    out, recs = H.ownership_pass(_payload("/repo/src/main.py"))
    assert out is None and recs == []
    assert not os.path.exists(lib.write_candidates_path)


# --------------------------------------------------------------------------
# `precedent own`
# --------------------------------------------------------------------------

def test_own_cli_declares_and_arms_the_guard(tmp_path, capsys, real_home_canary):
    home = str(tmp_path / "claude")
    os.makedirs(os.path.join(home, "skills", "foo"))
    target = os.path.join(home, "skills", "foo", "SKILL.md")
    open(target, "w").write("---\nname: foo\n---\n")
    state_dir = str(tmp_path / "state")
    before = tree_fingerprint(home)

    rc = main(["own", target, "--user", "--claude-home", home,
               "--state-dir", state_dir])
    assert rc == 0
    out = capsys.readouterr().out
    assert "owner=user" in out and GUARD_RULE_ID in out

    state = StateDir.open(state_dir, home)
    assert read_owners(state)["paths"][target]["owner"] == "user"
    assert guard_active(state)
    guard = [r for r in state.precedents() if r["id"] == GUARD_RULE_ID][0]
    assert guard["status"] == "active" and guard["action"] == "ask"
    assert guard["kind"] == "ownership-guard"

    # listing
    assert main(["own", "--claude-home", home, "--state-dir", state_dir]) == 0
    assert target in capsys.readouterr().out
    # and off again
    assert main(["own", "--guard", "off", "--claude-home", home,
                 "--state-dir", state_dir]) == 0
    assert not guard_active(StateDir.open(state_dir, home))

    assert tree_fingerprint(home) == before          # read-only on the home
    real_home_canary()


def test_own_warns_when_the_path_is_not_governed(tmp_path, capsys):
    home = str(tmp_path / "claude")
    os.makedirs(home)
    rc = main(["own", str(tmp_path / "src" / "main.py"), "--user",
               "--claude-home", home, "--state-dir", str(tmp_path / "state")])
    assert rc == 0
    assert "不在治理树里" in capsys.readouterr().out
