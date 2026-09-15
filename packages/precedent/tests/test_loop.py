# Copyright 2026 The precedent authors.
# SPDX-License-Identifier: Apache-2.0
"""``precedent loop`` — one nightly cycle end to end, and its schedule.

``--dry-run`` is the default and the contract is strict: **zero model calls,
zero bytes written into the Claude home**.  ``--cron`` prints a launchd plist
and a crontab line and installs neither, for the same reason ``hooks install``
defaults to a dry run.
"""

from __future__ import annotations

import json
import os
import plistlib

import pytest

from conftest import build_home_v2, sid, tree_fingerprint
from precedent.cli import main
from precedent.loop import cron_snippet, launchd_plist, render_cycle, run_cycle
from precedent.state import StateDir


@pytest.fixture
def loop_home(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()

    def s(day):
        d = f"2026-09-{day:02d}"
        return [
            ("human", f"{d}T09:00:00.000Z", "装一下依赖然后跑测试"),
            ("assistant", f"{d}T09:00:01.000Z", "Installing."),
            ("tool_err", f"{d}T09:00:02.000Z", "Bash",
             {"command": "pip install requests"},
             "ERROR: externally-managed-environment"),
            ("human", f"{d}T09:00:03.000Z", "不要用 pip，用 uv。"),
            ("tool", f"{d}T09:00:04.000Z", "Bash", {"command": "uv add requests"}),
            ("tool", f"{d}T09:00:05.000Z", "Bash", {"command": "pytest -q"}),
            ("human", f"{d}T09:00:09.000Z", "谢谢"),
        ]

    home = build_home_v2(tmp_path, "loophome", {sid(1): s(11), sid(2): s(12)},
                         cwd=str(ws))
    (tmp_path / "loophome" / "settings.json").write_text("{}\n", encoding="utf-8")
    return home


def _state(tmp_path, home):
    return StateDir.open(str(tmp_path / "state"), home, create=True)


# --------------------------------------------------------------------------
# the dry cycle
# --------------------------------------------------------------------------

def test_the_dry_cycle_walks_all_five_steps(tmp_path, loop_home,
                                            real_home_canary):
    st = _state(tmp_path, loop_home)
    res = run_cycle(st, dry_run=True)
    assert [s.n for s in res.steps] == ["①", "①", "②", "③", "④", "⑤"]
    assert all(not s.error for s in res.steps), [s.error for s in res.steps]
    assert res.spend_usd == 0.0
    md = render_cycle(res, st)
    for needle in ("① propose · correction miner", "① propose · nightly improver",
                   "② accept · examiner", "③ enforce", "④ audit", "⑤ re-evolve"):
        assert needle in md
    real_home_canary()


def test_the_dry_cycle_writes_nothing_into_the_claude_home(tmp_path, loop_home,
                                                           real_home_canary):
    st = _state(tmp_path, loop_home)
    before = tree_fingerprint(loop_home)
    run_cycle(st, dry_run=True)
    assert tree_fingerprint(loop_home) == before
    real_home_canary()


def test_the_dry_cycle_makes_no_model_call(tmp_path, loop_home, monkeypatch):
    st = _state(tmp_path, loop_home)

    def boom(*a, **kw):                        # pragma: no cover
        raise AssertionError("--dry-run must not spawn a subprocess")

    monkeypatch.setattr("subprocess.run", boom)
    res = run_cycle(st, dry_run=True)
    assert all(not s.error for s in res.steps), [s.error for s in res.steps]
    assert not os.path.exists(st.path("spend.jsonl"))


def test_the_cycle_reports_the_miner_the_clusters_and_the_cassettes(tmp_path,
                                                                    loop_home):
    st = _state(tmp_path, loop_home)
    res = run_cycle(st, dry_run=True)
    mine_step, improve_step, exam_step = res.steps[0], res.steps[1], res.steps[2]
    assert any("纠正" in l for l in mine_step.lines)
    assert any("失败签名聚类" in l for l in improve_step.lines)
    assert any("合格磁带" in l for l in exam_step.lines)
    # the fixture's sessions end in `pytest -q`, so they are cassettes
    assert any("pytest" in l for l in exam_step.lines)


def test_no_cassette_says_hold_no_evidence_rather_than_nothing(tmp_path):
    ws = tmp_path / "p"
    ws.mkdir()
    home = build_home_v2(tmp_path, "bare", {sid(1): [
        ("human", "2026-09-01T00:00:00.000Z", "hello"),
        ("assistant", "2026-09-01T00:00:01.000Z", "hi")]}, cwd=str(ws))
    st = _state(tmp_path, home)
    res = run_cycle(st, dry_run=True)
    exam = res.steps[2]
    assert any("HOLD 'no evidence'" in l for l in exam.lines)


def test_a_failing_step_does_not_abort_the_cycle(tmp_path, loop_home,
                                                 monkeypatch):
    st = _state(tmp_path, loop_home)
    import precedent.loop as loopmod
    monkeypatch.setattr("precedent.audit.hook_health",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("nope")))
    res = run_cycle(st, dry_run=True)
    audit_step = [s for s in res.steps if s.n == "④"][0]
    assert audit_step.error and "nope" in audit_step.error
    assert [s.n for s in res.steps] == ["①", "①", "②", "③", "④", "⑤"]


# --------------------------------------------------------------------------
# --cron
# --------------------------------------------------------------------------

def test_the_launchd_plist_parses_and_names_the_dry_safe_command(tmp_path,
                                                                 loop_home):
    st = _state(tmp_path, loop_home)
    xml = launchd_plist(st, hour=4, minute=5, budget_usd=1.5)
    plist = plistlib.loads(xml.encode("utf-8"))
    assert plist["Label"] == "dev.precedent.nightly"
    assert plist["StartCalendarInterval"] == {"Hour": 4, "Minute": 5}
    argv = plist["ProgramArguments"]
    assert argv[1:5] == ["-m", "precedent", "loop", "--budget-usd"]
    assert argv[5] == "1.5"
    assert "--state-dir" in argv and st.root in argv
    assert plist["RunAtLoad"] is False


def test_the_cron_snippet_installs_nothing_and_says_so(tmp_path, loop_home):
    st = _state(tmp_path, loop_home)
    md = cron_snippet(st, hour=3, minute=17, budget_usd=2)
    assert "precedent 不会替你安装定时任务" in md
    assert "17 3 * * *" in md
    assert "launchctl load" in md
    assert "--dry-run" in md
    assert not os.path.exists(os.path.expanduser(
        "~/Library/LaunchAgents/dev.precedent.nightly.plist"))


def test_cron_writes_nothing_anywhere(tmp_path, loop_home, real_home_canary):
    st = _state(tmp_path, loop_home)
    before_home = tree_fingerprint(loop_home)
    before_state = tree_fingerprint(st.root)
    cron_snippet(st)
    assert tree_fingerprint(loop_home) == before_home
    assert tree_fingerprint(st.root) == before_state
    real_home_canary()


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------

def test_cli_loop_dry_run_is_the_default(tmp_path, loop_home, capsys,
                                         real_home_canary):
    rc = main(["loop", "--claude-home", loop_home,
               "--state-dir", str(tmp_path / "s1")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "--dry-run" in out
    assert "① propose · correction miner" in out
    real_home_canary()


def test_cli_loop_cron_prints_both_snippets(tmp_path, loop_home, capsys):
    rc = main(["loop", "--cron", "--claude-home", loop_home,
               "--state-dir", str(tmp_path / "s2"), "--budget-usd", "2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "launchd" in out and "crontab" in out


def test_cli_loop_json_is_machine_readable(tmp_path, loop_home, capsys):
    out_path = tmp_path / "cycle.json"
    rc = main(["loop", "--claude-home", loop_home,
               "--state-dir", str(tmp_path / "s3"), "--json", str(out_path),
               "--quiet"])
    assert rc == 0
    payload = json.load(open(out_path, encoding="utf-8"))
    assert payload["dryRun"] is True
    assert [s["step"] for s in payload["steps"]] == ["①", "①", "②", "③", "④", "⑤"]
    assert payload["spendUsd"] == 0.0


def test_the_crontab_line_is_shell_quoted(tmp_path, loop_home):
    """A `.venv` under "AI coding open/自进化Agent Harness" has spaces in it;
    an unquoted crontab line then runs something else, at 03:17, unattended."""
    import shlex
    spacey = tmp_path / "a dir with spaces"
    spacey.mkdir()
    st = StateDir.open(str(spacey / "state"), loop_home, create=True)
    md = cron_snippet(st, hour=3, minute=17, budget_usd=2,
                      python="/opt/py 3.12/bin/python")
    line = [l for l in md.splitlines() if l.startswith("17 3 * * *")][0]
    body = line.split("*  ", 1)[1].split(" >>", 1)[0]
    argv = shlex.split(body)
    assert argv[0] == "/opt/py 3.12/bin/python"
    assert argv[argv.index("--state-dir") + 1] == st.root
    assert " " in st.root                     # the fixture really is spacey


def test_the_plist_escapes_xml_in_paths(tmp_path, loop_home):
    amp = tmp_path / "r&d <lab>"
    amp.mkdir()
    st = StateDir.open(str(amp / "state"), loop_home, create=True)
    xml = launchd_plist(st, python="/usr/bin/python3")
    plist = plistlib.loads(xml.encode("utf-8"))   # raises if the XML is broken
    assert st.root in plist["ProgramArguments"]
