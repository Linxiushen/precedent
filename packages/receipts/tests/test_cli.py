# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""CLI surface, JSON schema, Markdown rendering and the read-only guard."""

import json
import os
import subprocess
import sys

import pytest

from receipts import SCHEMA_VERSION
from receipts.cli import main
from receipts.report import render_markdown
from receipts.scan import scan


def test_scan_writes_json_and_md(fake_home, tmp_path, capsys):
    out_json = tmp_path / "out" / "receipts.json"
    out_md = tmp_path / "out" / "receipts.md"
    rc = main(["scan", "--claude-home", fake_home.home,
               "--json", str(out_json), "--md", str(out_md), "--quiet"])
    assert rc == 0
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    assert doc["schemaVersion"] == SCHEMA_VERSION
    assert doc["scan"]["readOnly"] is True
    assert doc["scan"]["sessionsScanned"] == 2
    assert {"sessions", "changeFeed", "outOfBand", "artifacts", "limitations"} <= set(doc)
    md = out_md.read_text(encoding="utf-8")
    for heading in ("# Learned-state receipts", "## Receipts", "## Change feed",
                    "## Artifacts: activation funnel + warnings", "## Limitations"):
        assert heading in md


def test_json_rows_carry_session_and_timestamp(fake_home, tmp_path):
    out_json = tmp_path / "r.json"
    main(["scan", "--claude-home", fake_home.home, "--json", str(out_json), "--quiet"])
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    for m in doc["changeFeed"]:
        assert m["sessionId"] and m["ts"] and m["lineNo"] and m["transcript"]
    for s in doc["sessions"]:
        for a in s["artifacts"]:
            assert a["sessionId"] == s["sessionId"]
            if a["status"] in ("loaded_complete", "loaded_truncated"):
                assert a["evidence"]["locator"]


def test_stdout_markdown_when_no_md_path(fake_home, capsys):
    assert main(["scan", "--claude-home", fake_home.home]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Learned-state receipts")


def test_project_filter_narrows_the_scan(fake_home, capsys):
    assert main(["scan", "--claude-home", fake_home.home, "--project", "nope"]) == 0
    out = capsys.readouterr().out
    assert "0 scanned of 0 found" in out


def test_last_n_limits_sessions(fake_home, tmp_path):
    out_json = tmp_path / "r.json"
    main(["scan", "--claude-home", fake_home.home, "--last", "1",
          "--json", str(out_json), "--quiet"])
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    assert doc["scan"]["sessionsScanned"] == 1
    assert doc["sessions"][0]["sessionId"] == fake_home.session_2  # most recent


def test_refuses_to_write_inside_the_claude_home(fake_home, capsys):
    inside = os.path.join(fake_home.home, "receipts.json")
    rc = main(["scan", "--claude-home", fake_home.home, "--json", inside, "--quiet"])
    assert rc == 2
    assert "refusing to write" in capsys.readouterr().err
    assert not os.path.exists(inside)


def test_missing_claude_home_is_an_error(tmp_path, capsys):
    rc = main(["scan", "--claude-home", str(tmp_path / "nope")])
    assert rc == 2
    assert "no such Claude home" in capsys.readouterr().err


def test_scan_never_writes_under_the_claude_home(fake_home, tmp_path):
    before = {}
    for root, _dirs, files in os.walk(fake_home.home):
        for f in files:
            p = os.path.join(root, f)
            before[p] = (os.path.getsize(p), os.path.getmtime(p))
    main(["scan", "--claude-home", fake_home.home, "--json", str(tmp_path / "r.json"),
          "--quiet"])
    after = {}
    for root, _dirs, files in os.walk(fake_home.home):
        for f in files:
            p = os.path.join(root, f)
            after[p] = (os.path.getsize(p), os.path.getmtime(p))
    assert before == after


def test_markdown_names_the_truncated_index_lines(fake_home):
    md = render_markdown(scan(fake_home.home))
    assert "TRUNCATED" in md
    assert "index line(s) did not reach context" in md
    assert "near-duplicate" in md
    assert "never cited" in md


def test_markdown_survives_an_empty_home(tmp_path):
    (tmp_path / "projects").mkdir()
    md = render_markdown(scan(str(tmp_path)))
    assert "_No sessions in scope._" in md
    assert "_No learned-state mutations found" in md


def test_module_entry_point_runs(fake_home):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    proc = subprocess.run(
        [sys.executable, "-m", "receipts", "scan", "--claude-home", fake_home.home],
        capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "# Learned-state receipts" in proc.stdout


def test_no_subagents_flag_is_accepted(fake_home, capsys):
    assert main(["scan", "--claude-home", fake_home.home, "--no-subagents",
                 "--quiet"]) == 0
