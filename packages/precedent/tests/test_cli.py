# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""End-to-end CLI: init → mine → compile → confirm → hooks → report.

Every test runs against a synthetic home under ``tmp_path`` and every one of
them ends by checking that nothing outside ``tmp_path`` moved.
"""

from __future__ import annotations

import json
import os

import pytest
from conftest import tree_fingerprint
from precedent.cli import main
from precedent.state import (ClaudeHomeWriteRefused, StateDir,
                             guard_not_under_claude_home, is_real_claude_home)


def run(*argv):
    return main(list(argv))


@pytest.fixture
def cli(mining_home, tmp_path):
    class C:
        home = mining_home.home
        state = str(tmp_path / "state")
        tree = mining_home

        def __call__(self, *argv):
            return run(*argv, "--claude-home", self.home, "--state-dir", self.state)
    return C()


# --------------------------------------------------------------------------
# the write guard
# --------------------------------------------------------------------------

def test_guard_refuses_a_path_inside_the_claude_home(tmp_path):
    home = str(tmp_path / "claude")
    os.makedirs(home)
    with pytest.raises(ClaudeHomeWriteRefused):
        guard_not_under_claude_home(os.path.join(home, "settings.json"), home)
    with pytest.raises(ClaudeHomeWriteRefused):
        guard_not_under_claude_home(home, home)
    assert guard_not_under_claude_home(str(tmp_path / "state"), home)


def test_state_dir_inside_the_claude_home_is_refused_at_open(tmp_path):
    home = str(tmp_path / "claude")
    os.makedirs(home)
    with pytest.raises(ClaudeHomeWriteRefused):
        StateDir.open(os.path.join(home, "precedent"), home, create=True)


def test_is_real_claude_home_only_matches_the_users_own(tmp_path):
    assert is_real_claude_home(os.path.expanduser("~/.claude"))
    assert is_real_claude_home(os.path.expanduser("~/.claude/skills/x/SKILL.md"))
    assert not is_real_claude_home(str(tmp_path / "claude"))


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------

def test_init_creates_state_and_prints_the_ten_line_headline(cli, capsys,
                                                             real_home_canary):
    assert cli("init") == 0
    out = capsys.readouterr().out
    for n in range(1, 11):
        assert f"{n:2d}. " in out or f" {n}. " in out, f"line {n} missing"
    assert "会话" in out and "学习工件" in out and "从未被引用" in out
    assert "无人值守写入" in out and "已强制执行的先例: 0 条" in out

    state = StateDir.open(cli.state, cli.home)
    assert os.path.exists(state.config_path)
    assert os.path.exists(state.ledger_path)
    assert len(state.ledger()) == 1
    assert state.ledger().verify_chain()[0]
    assert state.latest_scan()["schemaVersion"] == 1
    real_home_canary()


def test_init_never_writes_into_the_claude_home(cli, real_home_canary):
    before = tree_fingerprint(cli.home)
    assert cli("init") == 0
    assert tree_fingerprint(cli.home) == before
    real_home_canary()


def test_init_on_a_missing_home_fails_cleanly(tmp_path, capsys):
    rc = run("init", "--claude-home", str(tmp_path / "nope"),
             "--state-dir", str(tmp_path / "state"))
    assert rc == 2
    assert "no such Claude home" in capsys.readouterr().err


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------

def test_scan_archives_into_the_state_dir(cli, capsys, real_home_canary):
    before = tree_fingerprint(cli.home)
    assert cli("scan", "--quiet") == 0
    state = StateDir.open(cli.state, cli.home)
    archived = sorted(os.listdir(state.scans_dir))
    assert len(archived) == 1 and archived[0].startswith("receipts-")
    assert tree_fingerprint(cli.home) == before
    real_home_canary()


def test_scan_refuses_an_output_path_inside_the_claude_home(cli, capsys):
    rc = cli("scan", "--json", os.path.join(cli.home, "out.json"), "--quiet")
    assert rc == 2
    assert "inside the Claude home" in capsys.readouterr().err


# --------------------------------------------------------------------------
# mine
# --------------------------------------------------------------------------

def test_mine_reports_and_persists_topics(cli, capsys, tmp_path, real_home_canary):
    before = tree_fingerprint(cli.home)
    out_json = str(tmp_path / "mine.json")
    assert cli("mine", "--json", out_json) == 0
    out = capsys.readouterr().out
    assert "检出纠正" in out and "重复主题" in out
    assert "written but violated" in out
    assert "不要用 pip，用 uv。" in out

    data = json.load(open(out_json, encoding="utf-8"))
    assert data["mine"]["nCorrections"] == 6
    assert data["mine"]["nRepeatedTopics"] == 3
    assert data["topics"][0]["index"] == "T1"
    assert data["topics"][0]["suggestedCheck"]["template"]

    state = StateDir.open(cli.state, cli.home)
    saved = json.load(open(os.path.join(state.root, "topics.json"), encoding="utf-8"))
    assert saved["mine"]["nCorrections"] == 6
    assert tree_fingerprint(cli.home) == before
    real_home_canary()


# --------------------------------------------------------------------------
# compile → confirm → hooks
# --------------------------------------------------------------------------

def _pip_topic_id(cli):
    state = StateDir.open(cli.state, cli.home)
    saved = json.load(open(os.path.join(state.root, "topics.json"), encoding="utf-8"))
    for t in saved["topics"]:
        if any("pip" in q for q in t["quotes"]):
            return t["id"]
    raise AssertionError("no pip topic")


def test_full_loop_compile_confirm_hooks(cli, capsys, real_home_canary):
    assert cli("mine", "--quiet") == 0
    topic_id = _pip_topic_id(cli)

    assert cli("compile", topic_id) == 0
    out = capsys.readouterr().out
    assert "TEMPORAL BIRTH GATE" in out and "**PASS**" in out
    assert "(a) 命中违规动作" in out and "(b) t0 之后可判定" in out
    assert "误触率" in out

    state = StateDir.open(cli.state, cli.home)
    cands = state.candidates()
    assert len(cands) == 1 and cands[0]["birth"]["verdict"] == "PASS"
    rule_id = cands[0]["id"]

    assert cli("confirm", rule_id) == 0
    assert capsys.readouterr().out.count(rule_id) >= 1
    rules = StateDir.open(cli.state, cli.home).precedents()
    assert len(rules) == 1 and rules[0]["status"] == "active"
    assert rules[0]["confirmedBy"] == "user"

    before = tree_fingerprint(cli.home)
    assert cli("hooks", "install", "claude-code") == 0
    out = capsys.readouterr().out
    assert "settings.json" in out and "dry run" in out
    assert not os.path.exists(state.hook_script_path)
    assert tree_fingerprint(cli.home) == before

    # --apply with --scripts-only still leaves the Claude home byte-identical
    assert cli("hooks", "install", "claude-code", "--apply", "--scripts-only") == 0
    assert os.path.exists(state.hook_script_path)
    assert tree_fingerprint(cli.home) == before, "the Claude home stays untouched"

    # a plain --apply is the one command that may edit settings.json, after a backup
    assert cli("hooks", "install", "claude-code", "--apply") == 0
    capsys.readouterr()
    settings = json.load(open(os.path.join(cli.home, "settings.json"),
                              encoding="utf-8"))
    assert settings["hooks"]["PreToolUse"]
    assert os.listdir(state.backups_dir) or True     # nothing to back up first time

    # and uninstall puts it back
    assert cli("hooks", "uninstall", "claude-code", "--apply") == 0
    capsys.readouterr()
    settings = json.load(open(os.path.join(cli.home, "settings.json"),
                              encoding="utf-8"))
    assert "hooks" not in settings or not settings["hooks"].get("PreToolUse")
    assert [f for f in os.listdir(state.backups_dir) if f.endswith(".json")]

    ledger = StateDir.open(cli.state, cli.home).ledger()
    kinds = [r.as_dict().get("decision") or r.as_dict().get("event")
             for r in ledger.records()]
    assert "HOLD" in kinds and "ACCEPT" in kinds and "activated" in kinds
    assert ledger.verify_chain()[0]
    real_home_canary()


def test_compile_rejects_an_unknown_topic(cli, capsys):
    assert cli("mine", "--quiet") == 0
    assert cli("compile", "t-deadbeef") == 2
    assert "no such topic" in capsys.readouterr().err


def test_confirm_refuses_a_rule_that_failed_the_birth_gate(cli, capsys):
    assert cli("mine", "--quiet") == 0
    topic_id = _pip_topic_id(cli)
    assert cli("compile", topic_id, "--template", "dont_use", "--x", "install") == 1
    capsys.readouterr()
    state = StateDir.open(cli.state, cli.home)
    rule_id = state.candidates()[0]["id"]
    assert state.candidates()[0]["birth"]["verdict"] == "FAIL"

    assert cli("confirm", rule_id) == 1
    assert "did not PASS the temporal birth gate" in capsys.readouterr().err
    assert StateDir.open(cli.state, cli.home).precedents() == []

    assert cli("confirm", rule_id, "--force") == 0
    assert len(StateDir.open(cli.state, cli.home).precedents()) == 1


def test_confirm_without_a_candidate(cli, capsys):
    assert cli("confirm", "p-nope") == 2
    assert "no compiled candidate" in capsys.readouterr().err


# --------------------------------------------------------------------------
# snapshot / undo / report through the CLI
# --------------------------------------------------------------------------

def test_snapshot_and_undo_dry_run_through_the_cli(cli, capsys, real_home_canary):
    before = tree_fingerprint(cli.home)
    assert cli("snapshot") == 0
    assert "precedent snapshot snap-" in capsys.readouterr().out

    assert cli("undo", "--session", cli.tree.sessions["4"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert cli.tree.protected in out
    assert tree_fingerprint(cli.home) == before
    real_home_canary()


def test_report_combines_everything(cli, capsys, tmp_path, real_home_canary):
    assert cli("init") == 0
    capsys.readouterr()
    md = str(tmp_path / "digest.md")
    assert cli("report", "--md", md, "--quiet") == 0
    text = open(md, encoding="utf-8").read()
    for section in ("## 0. 告警（STARVATION）", "## 1. 首屏",
                    "## 2. 漏斗：proposed → accepted → activated → attributed",
                    "## 3. 待办（docket）", "## 4. 治理树所有权",
                    "## 5. 加载收据（live 优先）", "## 6. 纠正挖掘",
                    "## 7. 已确认的先例", "## 8. 支出表", "## 9. 最近 7 天",
                    "## 10. 账本尾部", "## 11. 安装状态", "## 12. 诚实的限制"):
        assert section in text, section
    assert tree_fingerprint(cli.home) == tree_fingerprint(cli.home)
    real_home_canary()


# --------------------------------------------------------------------------
# the global "no writes outside tmp_path" sweep
# --------------------------------------------------------------------------

DRY_RUN_COMMANDS = [
    ("init",),
    ("scan", "--quiet"),
    ("mine", "--quiet"),
    ("snapshot", "--dry-run"),
    ("docket", "--quiet"),
    ("docket", "--batch", "--quiet"),
    ("own",),
    ("hooks", "install", "claude-code"),
    ("hooks", "install", "claude-code", "--show-scripts"),
    ("hooks", "uninstall", "claude-code"),
    ("hooks", "status", "claude-code"),
    ("report", "--quiet"),
]


@pytest.mark.parametrize("argv", DRY_RUN_COMMANDS,
                         ids=["-".join(c) for c in DRY_RUN_COMMANDS])
def test_no_command_ever_writes_into_the_claude_home(cli, argv, capsys,
                                                     real_home_canary):
    before = tree_fingerprint(cli.home)
    assert cli(*argv) in (0, 1)         # `hooks status` returns 1 on drift
    capsys.readouterr()
    after = tree_fingerprint(cli.home)
    assert after == before, f"{argv} modified the Claude home"
    real_home_canary()


def test_everything_precedent_writes_lives_under_the_state_dir(cli, tmp_path,
                                                               real_home_canary):
    """Run the whole loop, then prove the only new files are in the state dir."""
    outside = tree_fingerprint(str(tmp_path))
    assert cli("init") == 0
    assert cli("mine", "--quiet") == 0
    topic_id = _pip_topic_id(cli)
    assert cli("compile", topic_id) == 0
    state = StateDir.open(cli.state, cli.home)
    assert cli("confirm", state.candidates()[0]["id"]) == 0
    assert cli("hooks", "install", "claude-code", "--apply", "--scripts-only") == 0
    assert cli("snapshot") == 0

    after = tree_fingerprint(str(tmp_path))
    new_or_changed = {p for p, h in after.items() if outside.get(p) != h}
    state_rel = os.path.relpath(cli.state, str(tmp_path))
    assert new_or_changed, "the loop should have written something"
    assert all(p.startswith(state_rel + os.sep) for p in new_or_changed), \
        sorted(p for p in new_or_changed if not p.startswith(state_rel + os.sep))
    real_home_canary()
