# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""THE SIX HOOKS, end to end.

Every one of these writes the real generated script to disk and feeds it a real
payload on stdin through a subprocess, because that is the only thing Claude
Code will actually run.  The invariant under test in all of them is the same:
**exit 0, never wedge the agent**, and write the evidence to the state dir.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest
from conftest import tree_fingerprint
from precedent.hooks import HOOK_EVENTS, MARKER, hooks_block, install
from precedent.hooks import status as hooks_status
from precedent.ownership import set_guard
from precedent.state import StateDir

SESSION = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def suite(tmp_path):
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    mem = os.path.join(state.claude_home, "projects", "-repo", "memory")
    os.makedirs(mem, exist_ok=True)
    with open(os.path.join(mem, "project.md"), "w", encoding="utf-8") as fh:
        fh.write("# project\n\nuse uv, never pip\n")
    install(state, [])
    state.memory = mem
    return state


def run_hook(state, script, payload, env=None, timeout=30):
    e = dict(os.environ, PRECEDENT_STATE_DIR=state.root,
             PRECEDENT_CLAUDE_HOME=state.claude_home, PRECEDENT_HOOK_DEBUG="1")
    e.update(env or {})
    proc = subprocess.run(
        [sys.executable, state.hook_script(script), MARKER, "X"],
        input=json.dumps(payload) if isinstance(payload, dict) else payload,
        capture_output=True, text=True, env=e, timeout=timeout)
    assert proc.returncode == 0, proc.stderr
    return proc


def rows(path):
    if not os.path.exists(path):
        return []
    return [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]


# --------------------------------------------------------------------------
# PostToolUse — the change feed
# --------------------------------------------------------------------------

def test_post_tool_use_captures_governed_writes_only(suite):
    target = os.path.join(suite.memory, "project.md")
    run_hook(suite, "post_tool_use.py", {
        "hook_event_name": "PostToolUse", "tool_name": "Write",
        "session_id": SESSION, "cwd": "/repo",
        "tool_input": {"file_path": target, "content": "x"},
        "tool_response": {"filePath": target}})
    run_hook(suite, "post_tool_use.py", {
        "hook_event_name": "PostToolUse", "tool_name": "Write",
        "session_id": SESSION, "tool_input": {"file_path": "/repo/src/main.py"}})
    feed = rows(suite.changes_path)
    assert len(feed) == 1
    row = feed[0]
    assert row["path"] == target and row["governed"] == "memory"
    assert row["tool"] == "Write" and row["agent"] == "foreground"
    assert row["ok"] is True and row["exists"] is True


def test_post_tool_use_records_a_bash_bypass_of_the_memory_tool(suite):
    target = os.path.join(suite.memory, "project.md")
    run_hook(suite, "post_tool_use.py", {
        "hook_event_name": "PostToolUse", "tool_name": "Bash",
        "session_id": SESSION, "agent_id": "night-fork",
        "tool_input": {"command": f"echo more >> {target}"},
        "tool_response": {"stdout": ""}})
    row = rows(suite.changes_path)[0]
    assert row["how"] == "redirect" and row["agent"] == "subagent"
    assert row["agentId"] == "night-fork"


# --------------------------------------------------------------------------
# UserPromptSubmit — the trusted origin
# --------------------------------------------------------------------------

def test_user_prompt_submit_records_the_human_turn(suite):
    run_hook(suite, "user_prompt_submit.py", {
        "hook_event_name": "UserPromptSubmit", "session_id": SESSION,
        "cwd": "/repo", "prompt": "不要用 pip，用 uv。" + "x" * 500})
    row = rows(suite.origins_path)[0]
    assert row["origin"] == "human" and row["trusted"] is True
    assert row["chars"] == len("不要用 pip，用 uv。") + 500
    assert len(row["preview"]) == 120            # a preview, not the transcript
    assert len(row["sha256"]) == 64
    assert rows(suite.session_receipt_path(SESSION))[0]["event"] == "human_turn"


# --------------------------------------------------------------------------
# SessionStart + InstructionsLoaded — the live receipts
# --------------------------------------------------------------------------

def test_session_start_writes_a_live_receipt(suite):
    run_hook(suite, "session_start.py", {
        "hook_event_name": "SessionStart", "session_id": SESSION,
        "cwd": "/repo", "source": "startup",
        "transcript_path": "/t/x.jsonl"})
    row = rows(suite.session_receipt_path(SESSION))[0]
    assert row["event"] == "session_start" and row["source"] == "startup"
    assert row["claudeHome"] == suite.claude_home and row["activeRules"] == 0


def test_instructions_loaded_proves_complete_truncated_and_missing(suite):
    whole = os.path.join(suite.memory, "project.md")
    body = open(whole, encoding="utf-8").read()
    big = os.path.join(suite.memory, "big.md")
    with open(big, "w", encoding="utf-8") as fh:
        fh.write("para one\n\npara two\n\npara three\n")
    run_hook(suite, "instructions_loaded.py", {
        "hook_event_name": "InstructionsLoaded", "session_id": SESSION,
        "files": [
            {"path": whole, "content": "SYSTEM\n" + body, "type": "memory"},
            {"path": big, "content": "para one\n\npara two\n"},
            {"path": os.path.join(suite.memory, "gone.md"), "content": "x"},
            {"path": os.path.join(suite.memory, "project.md"),
             "content": "nothing like it"},
        ]})
    got = {r["path"]: r for r in rows(suite.session_receipt_path(SESSION))
           if r.get("event") == "instructions_loaded"}
    assert got[big]["status"] == "loaded_truncated"
    assert 0 < got[big]["coverage"] < 1
    assert got[os.path.join(suite.memory, "gone.md")]["status"] == "missing_on_disk"
    # the last row for project.md wins (a session can reload a file)
    assert got[whole]["status"] == "not_loaded"
    assert got[whole]["source"] == "live:InstructionsLoaded"


def test_instructions_loaded_says_declared_when_it_has_no_content(suite):
    whole = os.path.join(suite.memory, "project.md")
    run_hook(suite, "instructions_loaded.py", {
        "hook_event_name": "InstructionsLoaded", "session_id": SESSION,
        "instructions": [whole]})
    row = [r for r in rows(suite.session_receipt_path(SESSION))
           if r.get("event") == "instructions_loaded"][0]
    assert row["status"] == "loaded_declared"    # listed, bytes unproven


# --------------------------------------------------------------------------
# Stop — the funnel counters
# --------------------------------------------------------------------------

def test_stop_counts_the_session_from_its_own_receipt_file(suite):
    target = os.path.join(suite.memory, "project.md")
    run_hook(suite, "session_start.py",
             {"session_id": SESSION, "hook_event_name": "SessionStart"})
    run_hook(suite, "user_prompt_submit.py",
             {"session_id": SESSION, "prompt": "go"})
    run_hook(suite, "pre_tool_use.py", {
        "hook_event_name": "PreToolUse", "tool_name": "Write",
        "session_id": SESSION,
        "tool_input": {"file_path": target, "content": "changed\n"}})
    run_hook(suite, "post_tool_use.py", {
        "hook_event_name": "PostToolUse", "tool_name": "Write",
        "session_id": SESSION, "tool_input": {"file_path": target}})
    run_hook(suite, "stop.py", {"hook_event_name": "Stop",
                                "session_id": SESSION, "cwd": "/repo"})
    row = rows(suite.funnel_path)[-1]
    assert row["event"] == "session_stop" and row["session"] == SESSION
    c = row["counts"]
    assert c["humanTurns"] == 1 and c["candidates"] == 1 and c["changes"] == 1
    assert c["asks"] == 0


# --------------------------------------------------------------------------
# PreToolUse ownership, through the real script
# --------------------------------------------------------------------------

def test_subagent_write_to_a_user_file_asks_through_the_real_script(suite):
    target = os.path.join(suite.memory, "project.md")
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
               "session_id": SESSION, "agent_id": "night-fork", "cwd": "/repo",
               "tool_input": {"file_path": target, "old_string": "uv",
                              "new_string": "pip"}}
    out = json.loads(run_hook(suite, "pre_tool_use.py", payload).stdout or "{}")
    assert out == {}                                  # no guard: observe only
    cand = rows(suite.write_candidates_path)[-1]
    assert cand["decision"] == "allow" and cand["owner"] == "user"
    assert cand["snapshot"]["existed"] and cand["diff"]["summary"] == "+1 −1 lines"

    set_guard(suite, True)
    out = json.loads(run_hook(suite, "pre_tool_use.py", payload).stdout or "{}")
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "ask"
    assert "p-own-guard" in hso["permissionDecisionReason"]
    assert target in hso["permissionDecisionReason"]
    assert "docket confirm" in hso["permissionDecisionReason"]
    # the pre-image is on disk, content-addressed, before the edit happened
    cand = rows(suite.write_candidates_path)[-1]
    assert open(cand["snapshot"]["blob"], encoding="utf-8").read() == \
        open(target, encoding="utf-8").read()


def test_the_claude_home_is_never_written_by_any_hook(suite):
    target = os.path.join(suite.memory, "project.md")
    before = tree_fingerprint(suite.claude_home)
    set_guard(suite, True)
    for script, payload in (
        ("pre_tool_use.py", {"tool_name": "Write", "session_id": SESSION,
                             "agent_id": "a", "hook_event_name": "PreToolUse",
                             "tool_input": {"file_path": target, "content": "x"}}),
        ("post_tool_use.py", {"tool_name": "Write", "session_id": SESSION,
                              "tool_input": {"file_path": target}}),
        ("user_prompt_submit.py", {"session_id": SESSION, "prompt": "hi"}),
        ("session_start.py", {"session_id": SESSION}),
        ("instructions_loaded.py", {"session_id": SESSION,
                                    "files": [{"path": target, "content": "x"}]}),
        ("stop.py", {"session_id": SESSION}),
    ):
        run_hook(suite, script, payload)
    assert tree_fingerprint(suite.claude_home) == before


# --------------------------------------------------------------------------
# the contract: never break Claude Code
# --------------------------------------------------------------------------

@pytest.mark.parametrize("script", [e[1] for e in HOOK_EVENTS])
def test_every_hook_is_fail_open_on_torn_stdin(suite, script):
    proc = subprocess.run([sys.executable, suite.hook_script(script)],
                          input="not json at all", capture_output=True, text=True,
                          env=dict(os.environ, PRECEDENT_STATE_DIR=suite.root))
    assert proc.returncode == 0 and proc.stdout.strip() == ""
    log = open(suite.hooklog_path, encoding="utf-8").read()
    assert '"event": "error"' in log and "JSONDecodeError" in log


@pytest.mark.parametrize("script", [e[1] for e in HOOK_EVENTS])
def test_every_hook_survives_a_missing_state_dir(suite, script, tmp_path):
    proc = subprocess.run([sys.executable, suite.hook_script(script)],
                          input='{"session_id":"s","tool_name":"Write",'
                                '"tool_input":{"file_path":"/x/CLAUDE.md"}}',
                          capture_output=True, text=True,
                          env=dict(os.environ,
                                   PRECEDENT_STATE_DIR=str(tmp_path / "gone")))
    assert proc.returncode == 0
    assert proc.stdout.strip() in ("", "{}")


@pytest.mark.parametrize("script", [e[1] for e in HOOK_EVENTS])
def test_every_hook_is_fast_enough(suite, script):
    """The budget is 300 ms including interpreter start."""
    payload = {"session_id": SESSION, "hook_event_name": "X",
               "tool_name": "Write", "cwd": "/repo",
               "tool_input": {"file_path": os.path.join(suite.memory,
                                                        "project.md"),
                              "content": "x" * 2000},
               "files": [{"path": os.path.join(suite.memory, "project.md"),
                          "content": "x"}]}
    t0 = time.monotonic()
    for _ in range(3):
        run_hook(suite, script, payload)
    per_call = (time.monotonic() - t0) / 3 * 1000
    assert per_call < 300, f"{script}: {per_call:.0f} ms per call"


# --------------------------------------------------------------------------
# install receipt, markers, status / drift
# --------------------------------------------------------------------------

def test_install_receipt_records_every_script_hash(suite):
    receipt = json.load(open(suite.install_receipt_path, encoding="utf-8"))
    assert set(receipt["scripts"]) == {"_plib.py"} | {e[1] for e in HOOK_EVENTS}
    for name, meta in receipt["scripts"].items():
        on_disk = open(suite.hook_script(name), encoding="utf-8").read()
        import hashlib
        assert meta["sha256"] == hashlib.sha256(on_disk.encode()).hexdigest()
    assert receipt["block"]["PreToolUse"][0]["hooks"][0]["timeout"] == 5


def test_status_is_clean_after_install_and_reports_every_drift(suite, tmp_path):
    settings = os.path.join(suite.claude_home, "settings.json")
    install(suite, [], settings_path=settings, write_settings_file=True)
    st = hooks_status(suite, [], settings)
    assert st["installed"] and st["drift"] == []
    assert st["entriesFound"] == st["entriesExpected"] == len(HOOK_EVENTS)

    # (1) a hand-edited script
    with open(suite.hook_script("stop.py"), "a", encoding="utf-8") as fh:
        fh.write("\n# tampered\n")
    # (2) one of our entries deleted from settings.json
    doc = json.load(open(settings, encoding="utf-8"))
    doc["hooks"].pop("Stop")
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    # (3) a rule confirmed after the install names a tool the matcher misses
    rule = {"id": "p-x", "tool": "Agent", "action": "deny", "status": "active",
            "hook": "PreToolUse", "message": "m"}
    st = hooks_status(suite, [rule], settings)
    codes = " | ".join(st["drift"])
    assert "stop.py" in codes and "edited by hand" in codes
    assert "is missing" in codes and "Stop" in codes
    # the PreToolUse entry is now stale too: its matcher predates the new rule
    assert "stale precedent" in codes and "PreToolUse" in codes
    assert "p-x:Agent" in codes
    assert st["matcher"] != st["expectedMatcher"]


def test_uninstall_removes_exactly_our_entries_including_the_new_events(suite):
    settings = os.path.join(suite.claude_home, "settings.json")
    theirs = {"hooks": {"Stop": [{"hooks": [{"type": "command",
                                             "command": "theirs.sh"}]}]}}
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump(theirs, fh)
    install(suite, [], settings_path=settings, write_settings_file=True)
    from precedent.hooks import uninstall
    out = uninstall(suite, settings)
    assert out["removed"] == len(HOOK_EVENTS)
    assert json.load(open(settings, encoding="utf-8")) == theirs


def test_every_entry_we_write_carries_the_marker():
    block = hooks_block([], "/s/hooks")
    for event, entries in block.items():
        for e in entries:
            cmd = e["hooks"][0]["command"]
            assert MARKER in cmd and "/s/hooks/" in cmd
