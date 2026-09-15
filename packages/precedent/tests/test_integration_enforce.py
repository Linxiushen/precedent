# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""B-ENFORCE, end to end on a synthetic home.

install --apply → a session runs through all six hooks (a subagent tries to
rewrite a file the user owns) → docket → report.  Everything here happens
inside ``tmp_path``; the ``real_home_canary`` fixture asserts the user's own
``~/.claude`` was never touched, which is the point of the whole exercise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from conftest import tree_fingerprint
from precedent.cli import main
from precedent.hooks import HOOK_EVENTS, is_precedent_entry
from precedent.state import StateDir

SESSION = "abcdabcd-1234-4321-8888-abcdabcdabcd"


def run_script(state, script, payload):
    env = dict(os.environ, PRECEDENT_STATE_DIR=state.root,
               PRECEDENT_CLAUDE_HOME=state.claude_home)
    proc = subprocess.run([sys.executable, state.hook_script(script)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout or "{}")


def test_install_apply_then_a_whole_session_then_the_report(mining_home, tmp_path,
                                                            capsys,
                                                            real_home_canary):
    home = mining_home.home
    state_dir = str(tmp_path / "state")
    args = ["--claude-home", home, "--state-dir", state_dir]
    settings = os.path.join(home, "settings.json")
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({"model": "opus",
                   "hooks": {"Stop": [{"hooks": [{"type": "command",
                                                  "command": "theirs.sh"}]}]}},
                  fh, indent=2)

    # ---- the user declares a file theirs, which arms the guard --------------
    assert main(["own", mining_home.protected, "--user"] + args) == 0
    capsys.readouterr()

    # ---- dry run writes nothing -------------------------------------------
    before = tree_fingerprint(home)
    assert main(["hooks", "install", "claude-code"] + args) == 0
    plan = capsys.readouterr().out
    assert "--dry-run" in plan and "p-own-guard" in plan
    for event, script, timeout, _what in HOOK_EVENTS:
        assert f"| {event} |" in plan and f"| {timeout}s |" in plan
    assert tree_fingerprint(home) == before

    # ---- --apply: backup, merge, receipt ------------------------------------
    assert main(["hooks", "install", "claude-code", "--apply"] + args) == 0
    out = capsys.readouterr().out
    assert "backup" in out and "install receipt" in out
    state = StateDir.open(state_dir, home)
    backups = os.listdir(state.backups_dir)
    assert any(b.startswith("settings-") and b.endswith(".json") for b in backups)
    doc = json.load(open(settings, encoding="utf-8"))
    assert doc["model"] == "opus"                                  # theirs kept
    assert doc["hooks"]["Stop"][0]["hooks"][0]["command"] == "theirs.sh"
    assert any(is_precedent_entry(e, state.hooks_dir)
               for e in doc["hooks"]["Stop"])
    assert set(doc["hooks"]) >= {e[0] for e in HOOK_EVENTS}

    # a second --apply changes nothing and writes no second backup
    assert main(["hooks", "install", "claude-code", "--apply"] + args) == 0
    assert "idempotent" in capsys.readouterr().out
    assert os.listdir(state.backups_dir) == backups

    # ---- a session, through the real hook scripts ---------------------------
    run_script(state, "session_start.py",
               {"hook_event_name": "SessionStart", "session_id": SESSION,
                "cwd": mining_home.workspace, "source": "startup"})
    run_script(state, "instructions_loaded.py",
               {"hook_event_name": "InstructionsLoaded", "session_id": SESSION,
                "files": [{"path": mining_home.protected, "type": "memory",
                           "content": open(mining_home.protected,
                                           encoding="utf-8").read()}]})
    run_script(state, "user_prompt_submit.py",
               {"hook_event_name": "UserPromptSubmit", "session_id": SESSION,
                "prompt": "整理一下发布说明"})
    ask = run_script(state, "pre_tool_use.py", {
        "hook_event_name": "PreToolUse", "tool_name": "Write",
        "session_id": SESSION, "agent_id": "night-fork",
        "cwd": mining_home.workspace,
        "tool_input": {"file_path": mining_home.protected,
                       "content": "rewritten by a background fork\n"}})
    assert ask["hookSpecificOutput"]["permissionDecision"] == "ask"
    run_script(state, "post_tool_use.py", {
        "hook_event_name": "PostToolUse", "tool_name": "Write",
        "session_id": SESSION, "tool_input": {"file_path": mining_home.notes}})
    run_script(state, "stop.py", {"hook_event_name": "Stop",
                                  "session_id": SESSION})

    # the protected file itself is untouched — PreToolUse ran *before* the write
    assert "rewritten" not in open(mining_home.protected, encoding="utf-8").read()

    # ---- the docket now carries that write, with its evidence ---------------
    assert main(["docket", "--batch"] + args) == 0
    docket = capsys.readouterr().out
    assert "night-fork" in docket or "subagent" in docket
    assert "protected.md" in docket
    entry_id = [line.split("`")[1] for line in docket.splitlines()
                if line.startswith("| `w-")][0]
    assert main(["docket", "reject", entry_id, "--reason", "夜里改我的文件"] + args) == 0
    rejected = capsys.readouterr().out
    assert "快照" in rejected or "snapshot" in rejected.lower()

    # ---- the report: funnel, alarms, live receipts, spend -------------------
    md = str(tmp_path / "digest.md")
    assert main(["report", "--md", md, "--quiet"] + args) == 0
    text = open(md, encoding="utf-8").read()
    assert "## 2. 漏斗" in text
    assert "① proposed" in text and "④ attributed" in text
    assert "实时收据" in text and "InstructionsLoaded" in text
    assert "p-own-guard" in text
    assert "所有权守卫：开" in text
    assert "## 8. 支出表" in text and "claude -p" in text

    # ---- status is clean, then uninstall leaves the user's own hook alone ----
    assert main(["hooks", "status", "claude-code"] + args) == 0
    status_out = capsys.readouterr().out
    assert "drift              : none" in status_out
    assert main(["hooks", "uninstall", "claude-code", "--apply"] + args) == 0
    capsys.readouterr()
    doc = json.load(open(settings, encoding="utf-8"))
    assert doc["hooks"] == {"Stop": [{"hooks": [{"type": "command",
                                                 "command": "theirs.sh"}]}]}
    assert doc["model"] == "opus"
    real_home_canary()
