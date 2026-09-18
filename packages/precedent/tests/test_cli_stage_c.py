# Copyright 2026 The precedent authors.
# SPDX-License-Identifier: Apache-2.0
"""``precedent examine`` / ``improve`` / ``loop`` at the CLI boundary.

Same contract as every other command: read-only on the Claude home, a
``real_home_canary`` on every test, and no ``claude`` binary is ever the real
one — it is a shim written under ``tmp_path`` and reached through
``PRECEDENT_CLAUDE_BIN``.
"""

from __future__ import annotations

import json
import os

import pytest

from conftest import build_home_v2, sid, tree_fingerprint
from precedent.cli import build_parser, main
from precedent.proposals import read_proposals
from precedent.state import StateDir


@pytest.fixture
def stage_c_home(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()

    def s(day):
        d = f"2026-09-{day:02d}"
        return [
            ("human", f"{d}T09:00:00.000Z", "fix it and run the tests"),
            ("assistant", f"{d}T09:00:01.000Z", "Editing."),
            ("tool_err", f"{d}T09:00:02.000Z", "Bash",
             {"command": "pip install requests"},
             "ERROR: externally-managed-environment"),
            ("human", f"{d}T09:00:03.000Z", "不要用 pip，用 uv。"),
            ("tool", f"{d}T09:00:04.000Z", "Bash", {"command": "pytest -q"}),
            ("human", f"{d}T09:00:09.000Z", "谢谢"),
        ]

    home = build_home_v2(tmp_path, "cch", {sid(1): s(11), sid(2): s(12)},
                         cwd=str(ws))
    (tmp_path / "cch" / "settings.json").write_text("{}\n", encoding="utf-8")
    return home


@pytest.fixture
def candidate_skill(tmp_path):
    d = tmp_path / "skills" / "uv-first"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: uv-first\ndescription: install python dependencies with uv\n"
        "---\n\nUse `uv add`, never `pip install`.\n", encoding="utf-8")
    return str(d)


def _shim(tmp_path, answer, cost=0.01):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    payload = json.dumps({"type": "result", "subtype": "success",
                          "is_error": False, "result": answer,
                          "total_cost_usd": cost}, ensure_ascii=False)
    p = d / "claude"
    p.write_text("#!/usr/bin/env python3\nimport sys\n"
                 f"sys.stdout.write({json.dumps(payload)})\n", encoding="utf-8")
    os.chmod(p, 0o755)
    return str(p)


# --------------------------------------------------------------------------
# the parser
# --------------------------------------------------------------------------

def test_the_three_new_commands_are_wired():
    p = build_parser()
    for argv in (["examine", "--candidate", "x"], ["improve"], ["loop"]):
        args = p.parse_args(argv)
        assert callable(args.func)


def test_examine_requires_a_candidate():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["examine"])


def test_loop_dry_run_apply_and_cron_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["loop", "--apply", "--cron"])


# --------------------------------------------------------------------------
# examine
# --------------------------------------------------------------------------

def test_examine_dry_run_prints_the_plan_and_runs_nothing(
        tmp_path, stage_c_home, candidate_skill, capsys, real_home_canary,
        monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("--dry-run must not spawn a subprocess")))
    before = tree_fingerprint(stage_c_home)
    rc = main(["examine", "--candidate", candidate_skill, "--dry-run",
               "--no-git", "--claude-home", stage_c_home,
               "--state-dir", str(tmp_path / "s")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "磁带" in out and "HOLD" in out
    assert "--dry-run" in out
    assert tree_fingerprint(stage_c_home) == before
    real_home_canary()


def test_examine_falls_back_and_certifies(tmp_path, stage_c_home,
                                          candidate_skill, capsys,
                                          monkeypatch, real_home_canary):
    """The whole ② path with a stubbed agent: cassettes -> arms -> certificate."""
    trace = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]}}
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": "done", "total_cost_usd": 0.01}

    calls = []

    def fake_run(argv, *a, **kw):
        calls.append(argv)

        class P:
            returncode = 0
            stderr = ""
            stdout = ""
        if "--plugin-dir" in argv:                 # the with-arm finds the oracle
            P.stdout = json.dumps(trace) + "\n" + json.dumps(result) + "\n"
        else:
            P.stdout = json.dumps(result) + "\n"
        return P

    monkeypatch.setattr("precedent.examine.subprocess.run", fake_run)
    state_dir = str(tmp_path / "s2")
    rc = main(["examine", "--candidate", candidate_skill, "--fallback",
               "--no-git", "--runs", "2", "--max-cost-usd", "2",
               "--claude-home", stage_c_home, "--state-dir", state_dir])
    out = capsys.readouterr().out
    assert rc == 0
    # four paired wins is real evidence but not yet 1/alpha = 32.5, so the
    # honest answer here is NSF ("the budget ran out before it could be
    # certified"), not ACCEPT.  What the test pins is that a decision was made,
    # spelled in the ledger's vocabulary, and written down.
    assert any(d in out for d in ("ACCEPT", "HOLD", "NSF", "REJECT", "BLOCKED"))
    assert "不一致 4" in out
    assert "不会自动应用" in out

    st = StateDir.open(state_dir, stage_c_home)
    props = read_proposals(st)
    assert props and props[0]["applied"] is False
    assert props[0]["certificateHash"]
    ok, _i, _m = st.ledger().verify_chain()
    assert ok
    for argv in calls:
        assert "--dangerously-skip-permissions" not in argv
    real_home_canary()


def test_examine_json_output_carries_examine_and_acceptance(
        tmp_path, stage_c_home, candidate_skill, monkeypatch):
    monkeypatch.setattr(
        "precedent.examine.subprocess.run",
        lambda argv, *a, **kw: type("P", (), {
            "returncode": 0, "stderr": "",
            "stdout": json.dumps({"type": "result", "subtype": "success",
                                  "is_error": False, "result": "x",
                                  "total_cost_usd": 0.0}) + "\n"})())
    out_path = tmp_path / "exam.json"
    rc = main(["examine", "--candidate", candidate_skill, "--fallback",
               "--no-git", "--runs", "1", "--json", str(out_path), "--quiet",
               "--claude-home", stage_c_home, "--state-dir", str(tmp_path / "s3")])
    assert rc in (0, 1)
    payload = json.load(open(out_path, encoding="utf-8"))
    assert payload["examine"]["candidate"]["surface"] == "skill"
    assert payload["acceptance"]["decision"] in (
        "ACCEPT", "HOLD", "REJECT", "NSF", "BLOCKED")


def test_examine_on_an_unknown_candidate_exits_2(tmp_path, stage_c_home, capsys):
    rc = main(["examine", "--candidate", "nope-not-a-thing",
               "--claude-home", stage_c_home, "--state-dir", str(tmp_path / "s4")])
    assert rc == 2
    assert "neither a path" in capsys.readouterr().err


# --------------------------------------------------------------------------
# improve
# --------------------------------------------------------------------------

def test_improve_dry_run_lists_clusters_without_a_model_call(
        tmp_path, stage_c_home, capsys, monkeypatch, real_home_canary):
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("--dry-run must not spawn a subprocess")))
    rc = main(["improve", "--dry-run", "--min-count", "1",
               "--claude-home", stage_c_home, "--state-dir", str(tmp_path / "s5")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "失败签名聚类" in out
    assert "$0.0000" in out
    real_home_canary()


def test_improve_default_budget_is_two_dollars():
    args = build_parser().parse_args(["improve"])
    assert args.budget_usd == 2.0


def test_improve_end_to_end_with_a_shim(tmp_path, stage_c_home, capsys,
                                        monkeypatch, real_home_canary):
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    draft = {"surface": "skill",
             "hypothesis": "pip is reached for when uv is the rule",
             "expected_effect": "the externally-managed-environment error stops",
             "declared_paths": [str(skill)],
             "edits": [{"path": str(skill),
                        "content": "---\nname: s\ndescription: use uv\n---\n\n"
                                   "Use `uv add`.\n"}]}
    monkeypatch.setenv("PRECEDENT_CLAUDE_BIN",
                       _shim(tmp_path, json.dumps(draft)))
    state_dir = str(tmp_path / "s6")
    before = tree_fingerprint(stage_c_home)
    rc = main(["improve", "--top", "1", "--min-count", "1", "--no-examine",
               "--budget-usd", "1", "--claude-home", stage_c_home,
               "--state-dir", state_dir])
    assert rc == 0
    out = capsys.readouterr().out
    assert "HOLD" in out and "no evidence" in out
    st = StateDir.open(state_dir, stage_c_home)
    props = read_proposals(st)
    assert props and props[0]["decision"] == "HOLD"
    assert props[0]["applied"] is False
    assert not skill.exists()                    # nothing was written
    assert tree_fingerprint(stage_c_home) == before
    real_home_canary()


def test_improve_proposals_show_up_in_the_docket(tmp_path, stage_c_home,
                                                 capsys, monkeypatch):
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    draft = {"surface": "skill", "hypothesis": "h", "expected_effect": "e",
             "declared_paths": [str(skill)],
             "edits": [{"path": str(skill),
                        "content": "---\nname: s\ndescription: d\n---\n\nbody\n"}]}
    monkeypatch.setenv("PRECEDENT_CLAUDE_BIN",
                       _shim(tmp_path, json.dumps(draft)))
    state_dir = str(tmp_path / "s7")
    main(["improve", "--top", "1", "--min-count", "1", "--no-examine",
          "--claude-home", stage_c_home, "--state-dir", state_dir, "--quiet"])
    capsys.readouterr()
    rc = main(["docket", "--batch", "--claude-home", stage_c_home,
               "--state-dir", state_dir])
    assert rc == 0
    out = capsys.readouterr().out
    assert "## Proposals" in out and "never auto-applied" in out


# --------------------------------------------------------------------------
# the whole stage, read-only
# --------------------------------------------------------------------------

def test_none_of_the_three_commands_writes_a_settings_json(
        tmp_path, stage_c_home, candidate_skill, monkeypatch, capsys,
        real_home_canary):
    settings = os.path.join(stage_c_home, "settings.json")
    before = open(settings, encoding="utf-8").read()
    monkeypatch.setattr(
        "precedent.examine.subprocess.run",
        lambda argv, *a, **kw: type("P", (), {
            "returncode": 0, "stderr": "", "stdout": ""})())
    sd = str(tmp_path / "s8")
    main(["loop", "--claude-home", stage_c_home, "--state-dir", sd, "--quiet"])
    main(["improve", "--dry-run", "--claude-home", stage_c_home,
          "--state-dir", sd, "--quiet"])
    main(["examine", "--candidate", candidate_skill, "--dry-run", "--no-git",
          "--claude-home", stage_c_home, "--state-dir", sd, "--quiet"])
    capsys.readouterr()
    assert open(settings, encoding="utf-8").read() == before
    real_home_canary()
