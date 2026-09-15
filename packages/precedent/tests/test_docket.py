# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""THE DOCKET — confirm / reject / snooze, with the evidence attached.

The property that matters is not that the three verbs work; it is that nothing
disappears quietly.  A snooze comes back **older**; a rejection keeps its
reason and becomes a negative example; a pending entry over 7 days old is an
alarm, not a row in a list nobody reads.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from conftest import tree_fingerprint
from precedent.cli import main
from precedent.docket import (SNOOZE_DAYS, STARVATION_DAYS, build_entries,
                              confirm, read_decisions, reject, render_batch,
                              render_docket, snooze, starving)
from precedent.state import StateDir

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

RULE_CANDIDATE = {
    "schemaVersion": 1, "id": "p-1a2b3c4d", "hook": "PreToolUse", "tool": "Bash",
    "match": "all", "status": "candidate",
    "matchers": [{"type": "input_regex", "field": "command",
                  "regex": "(?i)(?<![A-Za-z0-9_])pip install"}],
    "action": "deny", "scope": "project",
    "message": "用 uv，不要用 pip", "quote": "不要用 pip，用 uv。",
    "quoteDate": "2026-09-01", "compiledAt": "2026-09-01T09:00:00+00:00",
    "topics": ["t-aaa"],
    "birth": {"verdict": "PASS", "counts": "命中 2/2 · t0 后可判定 7 次 · 误触 0",
              "t0": "2026-09-01T09:04:00Z", "failures": []},
}

FAILED_CANDIDATE = dict(RULE_CANDIDATE, id="p-bad",
                        birth={"verdict": "FAIL", "counts": "误触 3/5 = 60%",
                               "failures": ["误触率 60% > 2%"]})

WRITE_CANDIDATE = {
    "schemaVersion": 1, "id": "w-deadbeef", "kind": "write",
    "ts": "2026-09-14T22:17:00+00:00", "session": "sess-night",
    "agent": "subagent", "agentId": "night-fork", "tool": "Edit", "how": "tool",
    "path": "/home/u/.claude/skills/chrome-devtools/SKILL.md",
    "governed": "skills", "owner": "user",
    "ownerWhy": "never created by an agent, and it exists",
    "cwd": "/repo", "decision": "ask", "status": "pending",
    "snapshot": {"existed": True, "sha256": "a" * 64, "bytes": 1234,
                 "blob": "/state/blobs/" + "a" * 64},
    "diff": {"tool": "Edit", "summary": "+12 −3 lines", "addedLines": 12,
             "removedLines": 3},
}


@pytest.fixture
def state(tmp_path):
    st = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                       create=True)
    os.makedirs(st.claude_home, exist_ok=True)
    st.write_candidates([RULE_CANDIDATE, FAILED_CANDIDATE])
    with open(st.write_candidates_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(WRITE_CANDIDATE) + "\n")
    return st


# --------------------------------------------------------------------------
# the list
# --------------------------------------------------------------------------

def test_entries_carry_the_gate_counts_and_the_diff(state):
    entries = build_entries(state, now=NOW)
    by_id = {e["id"]: e for e in entries}
    assert set(by_id) == {"p-1a2b3c4d", "p-bad", "w-deadbeef"}

    rule = by_id["p-1a2b3c4d"]
    assert rule["kind"] == "rule" and rule["status"] == "pending"
    assert rule["gateVerdict"] == "PASS" and "命中 2/2" in rule["gateCounts"]
    assert any("原话" in e for e in rule["evidence"])
    assert "pip install" in rule["diff"]
    assert rule["ageDays"] == 14

    write = by_id["w-deadbeef"]
    assert write["kind"] == "write" and write["diff"] == "+12 −3 lines"
    assert any("subagent write via Edit" in e for e in write["evidence"])
    assert any("owner: user" in e for e in write["evidence"])
    assert any("aaaaaaaa" in e for e in write["evidence"])       # the snapshot


def test_the_docket_renders_evidence_and_the_three_verbs(state):
    md = render_docket(state, build_entries(state, now=NOW), now=NOW)
    assert "p-1a2b3c4d" in md and "+12 −3 lines" in md
    assert "docket confirm" in md and "reject" in md and "snooze" in md
    assert f"STARVATION：2 条已经等了 ≥{STARVATION_DAYS} 天" in md   # the two rules


def test_batch_digest_is_one_screen(state):
    md = render_batch(state, build_entries(state, now=NOW), now=NOW)
    assert "batch digest" in md
    assert "## 规则候选" in md and "## 治理树写入" in md
    assert "p-1a2b3c4d" in md and "night-fork" not in md   # compact, not verbose
    assert "子代理 1" in md


# --------------------------------------------------------------------------
# confirm
# --------------------------------------------------------------------------

def test_confirm_a_passing_rule_makes_it_active_and_certifies_it(state):
    rc, lines = confirm(state, "p-1a2b3c4d", now=NOW)
    assert rc == 0 and "confirmed" in lines[0]
    rule = [r for r in state.precedents() if r["id"] == "p-1a2b3c4d"][0]
    assert rule["status"] == "active" and rule["confirmedBy"] == "user"
    certs = [r.as_dict() for r in state.ledger().records()]
    assert certs[-1]["decision"] == "ACCEPT"
    assert certs[-1]["algorithm"] == "temporal-birth-gate/v1"
    # and it leaves the pending list
    assert "p-1a2b3c4d" not in {e["id"] for e in build_entries(state, now=NOW)}


def test_a_failed_rule_needs_force_and_the_force_is_recorded(state):
    rc, lines = confirm(state, "p-bad", now=NOW)
    assert rc == 1 and "did not PASS" in lines[0] and "PROCTOR" in lines[0]
    assert state.precedents() == []
    rc, _ = confirm(state, "p-bad", force=True, now=NOW)
    assert rc == 0
    rule = [r for r in state.precedents() if r["id"] == "p-bad"][0]
    assert rule["forcedPastGate"] == "FAIL"
    assert "--force past the gate" in state.ledger().records()[-1].as_dict()["note"]


def test_confirming_a_write_accepts_it_without_changing_ownership(state):
    rc, lines = confirm(state, "w-deadbeef", now=NOW)
    assert rc == 0 and "accepted write" in lines[0]
    assert "+12 −3 lines" in lines[1]
    assert "precedent own" in lines[2]
    assert read_decisions(state)["decisions"]["w-deadbeef"]["status"] == "confirmed"
    cert = state.ledger().records()[-1].as_dict()
    assert cert["decision"] == "ACCEPT" and cert["algorithm"] == "docket/v1"
    assert state.read_json(state.owners_path) is None      # ownership untouched


# --------------------------------------------------------------------------
# reject + snooze
# --------------------------------------------------------------------------

def test_rejecting_a_rule_keeps_it_as_a_negative_example(state):
    rc, lines = reject(state, "p-bad", reason="太宽了，会拦到 grep pip", now=NOW)
    assert rc == 0
    rows = [json.loads(x) for x in open(state.rejected_path, encoding="utf-8")]
    assert rows[-1]["id"] == "p-bad" and "太宽" in rows[-1]["reason"]
    assert rows[-1]["rule"]["matchers"]
    cert = state.ledger().records()[-1].as_dict()
    assert cert["decision"] == "REJECT" and "太宽" in cert["note"]
    assert "p-bad" not in {e["id"] for e in build_entries(state, now=NOW)}


def test_rejecting_a_write_points_at_the_pre_image(state):
    rc, lines = reject(state, "w-deadbeef", reason="夜里改了我验证过的技能", now=NOW)
    assert rc == 0
    assert "/state/blobs/" + "a" * 64 in lines[1]
    assert "precedent undo --session sess-night" in lines[2]


def test_a_snooze_expires_and_comes_back_older(state):
    rc, lines = snooze(state, "w-deadbeef", now=NOW)
    assert rc == 0 and f"{SNOOZE_DAYS} 天" in lines[0]
    entry = [e for e in build_entries(state, now=NOW) if e["id"] == "w-deadbeef"][0]
    assert entry["status"] == "snoozed"
    assert entry["snoozedUntil"][:10] == (NOW + timedelta(days=SNOOZE_DAYS)) \
        .date().isoformat()
    assert starving(build_entries(state, now=NOW)) == \
        [e for e in build_entries(state, now=NOW) if e["kind"] == "rule"]

    later = NOW + timedelta(days=SNOOZE_DAYS + 1)
    entry = [e for e in build_entries(state, now=later) if e["id"] == "w-deadbeef"][0]
    assert entry["status"] == "pending"
    assert entry["ageDays"] > SNOOZE_DAYS          # older, not gone
    assert entry in starving(build_entries(state, now=later))


def test_unknown_ids_are_refused_not_guessed(state):
    for verb in (confirm, reject, snooze):
        rc, lines = verb(state, "w-nope", now=NOW)
        assert rc == 2 and "no docket entry" in lines[0]


# --------------------------------------------------------------------------
# through the CLI
# --------------------------------------------------------------------------

def test_docket_cli_lists_snoozes_and_stays_read_only(state, capsys,
                                                      real_home_canary):
    before = tree_fingerprint(state.claude_home)
    args = ["--claude-home", state.claude_home, "--state-dir", state.root]
    assert main(["docket", "--batch"] + args) == 0
    out = capsys.readouterr().out
    assert "batch digest" in out and "w-deadbeef" in out

    assert main(["docket", "snooze", "w-deadbeef", "--days", "3"] + args) == 0
    assert "snoozed" in capsys.readouterr().out
    assert main(["docket"] + args) == 0
    assert "snoozed" in capsys.readouterr().out

    assert main(["docket", "reject", "p-bad", "--reason", "too broad"] + args) == 0
    capsys.readouterr()
    assert main(["docket", "confirm", "p-1a2b3c4d"] + args) == 0
    assert "confirmed p-1a2b3c4d" in capsys.readouterr().out

    assert tree_fingerprint(state.claude_home) == before
    real_home_canary()


def test_docket_json_export_drops_the_raw_record(state, tmp_path, capsys):
    out = str(tmp_path / "docket.json")
    assert main(["docket", "--json", out, "--quiet", "--claude-home",
                 state.claude_home, "--state-dir", state.root]) == 0
    payload = json.load(open(out, encoding="utf-8"))
    assert len(payload["entries"]) == 3
    assert all("raw" not in e for e in payload["entries"])
