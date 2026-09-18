# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""AUDIT — the funnel, the four STARVATION alarms, the spend meter.

Each alarm here corresponds to a documented production failure, so each one has
a test that reproduces the shape of that failure on a synthetic home.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from precedent.audit import (alarms, daily_rows, funnel, hook_health,
                             spend_meter, transcript_spend)
from precedent.docket import build_entries
from precedent.hooks import install
from precedent.hooks import status as hooks_status
from precedent.state import StateDir

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

RULE = {"id": "p-aaa", "hook": "PreToolUse", "tool": "Bash", "action": "deny",
        "status": "active", "message": "用 uv", "scope": "project",
        "matchers": [{"type": "input_regex", "field": "command", "regex": "pip"}],
        "birth": {"verdict": "PASS", "counts": "2/2"}}
CANDIDATE = dict(RULE, id="p-bbb", status="candidate",
                 compiledAt="2026-09-01T00:00:00+00:00")


@pytest.fixture
def state(tmp_path):
    st = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                       create=True)
    os.makedirs(st.claude_home, exist_ok=True)
    return st


def log(state, rows, path=None):
    with open(path or state.hooklog_path, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _settings(state):
    return os.path.join(state.claude_home, "settings.json")


# --------------------------------------------------------------------------
# the funnel
# --------------------------------------------------------------------------

def test_funnel_counts_accepted_but_not_activated(state):
    state.write_candidates([CANDIDATE])
    state.write_precedents([RULE])
    st = hooks_status(state, [RULE], _settings(state))
    fun = funnel(state, build_entries(state, now=NOW), st, now=NOW)
    assert fun["stages"]["proposed"] == 1
    assert fun["stages"]["accepted"] == 1
    assert fun["stages"]["activated"] == 0       # nothing installed yet
    assert fun["stages"]["attributed"] == 0
    assert fun["installed"] is False


def test_funnel_activates_only_what_the_installed_matcher_covers(state):
    state.write_precedents([RULE])
    install(state, [RULE], settings_path=_settings(state),
            write_settings_file=True)
    st = hooks_status(state, [RULE], _settings(state))
    fun = funnel(state, build_entries(state, now=NOW), st, now=NOW)
    assert fun["stages"]["activated"] == 1 and fun["installed"] is True

    # a rule confirmed *after* the install names a tool the installed matcher
    # does not carry: accepted, not activated — the gap the funnel exists for
    late = dict(RULE, id="p-late", tool="Agent")
    state.write_precedents([RULE, late])
    st = hooks_status(state, [RULE, late], _settings(state))
    fun = funnel(state, build_entries(state, now=NOW), st, now=NOW)
    assert fun["stages"]["accepted"] == 2 and fun["stages"]["activated"] == 1


def test_funnel_attributes_from_the_hook_log(state):
    state.write_precedents([RULE])
    install(state, [RULE], settings_path=_settings(state),
            write_settings_file=True)
    log(state, [{"event": "allow", "ts": "2026-09-14T10:00:00Z", "ms": 3},
                {"event": "deny", "rule": "p-aaa", "ts": "2026-09-14T10:01:00Z"},
                {"event": "ask", "rule": "p-own-guard",
                 "ts": "2026-09-14T10:02:00Z"}])
    st = hooks_status(state, [RULE], _settings(state))
    fun = funnel(state, build_entries(state, now=NOW), st, now=NOW)
    assert fun["stages"]["attributed"] == 2
    assert fun["firesByRule"] == {"p-aaa": 1, "p-own-guard": 1}
    assert fun["hookCalls"] == 3 and fun["hookFires"] == 2


# --------------------------------------------------------------------------
# the four alarms
# --------------------------------------------------------------------------

def _alarm_codes(state, now=NOW, rules=None):
    rules = rules if rules is not None else state.precedents()
    st = hooks_status(state, rules, _settings(state))
    entries = build_entries(state, now=now)
    health = hook_health(state, now=now)
    fun = funnel(state, entries, st, health=health, now=now)
    return {a["code"]: a for a in alarms(state, entries, fun, health, st, now=now)}


def test_no_alarms_on_a_clean_install(state):
    install(state, [], settings_path=_settings(state), write_settings_file=True)
    assert _alarm_codes(state) == {}


def test_alarm_pending_over_seven_days(state):
    state.write_candidates([CANDIDATE])
    install(state, [], settings_path=_settings(state), write_settings_file=True)
    a = _alarm_codes(state)["PENDING"]
    assert a["level"] == "STARVATION" and "p-bbb" in a["message"]
    assert "14" in a["message"]                      # the age, in days
    assert a["action"] == "precedent docket --batch"


def test_alarm_hook_errors_are_never_silent(state):
    install(state, [], settings_path=_settings(state), write_settings_file=True)
    log(state, [{"event": "error", "ts": "2026-09-15T09:00:00Z",
                 "error": "JSONDecodeError: torn stdin"},
                {"event": "precedents_unreadable", "ts": "2026-09-15T09:00:01Z"},
                {"event": "regex_quarantined", "ts": "2026-09-15T09:00:02Z"}])
    a = _alarm_codes(state)["HOOK"]
    assert "JSONDecodeError" in a["message"]
    # English is the documented default; the alarms used to be hard-coded
    # Chinese and printed as-is under --lang en.
    assert "1 unreadable precedents.json" in a["message"]


def test_alarm_confirmed_but_not_installed(state):
    state.write_precedents([RULE])
    a = _alarm_codes(state)["NOT_INSTALLED"]
    assert "enforcement is 0" in a["message"]
    assert a["action"].startswith("precedent hooks install")


def test_alarm_no_fires_in_fourteen_days(state):
    state.write_precedents([RULE])
    install(state, [RULE], settings_path=_settings(state),
            write_settings_file=True,
            now=NOW - timedelta(days=30))
    receipt = json.load(open(state.install_receipt_path, encoding="utf-8"))
    receipt["installedAt"] = (NOW - timedelta(days=30)).isoformat()
    state.write_json(state.install_receipt_path, receipt)
    log(state, [{"event": "allow", "ts": "2026-09-14T10:00:00Z"}] * 40)
    a = _alarm_codes(state)["NO_FIRES"]
    assert "30 days ago" in a["message"] and "40 hook calls" in a["message"]
    # one fire and the alarm goes away
    log(state, [{"event": "deny", "rule": "p-aaa", "ts": "2026-09-14T11:00:00Z"}])
    assert "NO_FIRES" not in _alarm_codes(state)


def test_alarm_install_drift(state):
    state.write_precedents([RULE])
    install(state, [RULE], settings_path=_settings(state),
            write_settings_file=True)
    os.remove(state.hook_script("stop.py"))
    a = _alarm_codes(state)["DRIFT"]
    assert "stop.py" in a["message"]
    assert a["action"] == "precedent hooks status"


# --------------------------------------------------------------------------
# the spend meter
# --------------------------------------------------------------------------

class _FakeReceipt:
    def __init__(self, models, usage):
        self.models = models
        self.usage = usage


class _FakeScan:
    def __init__(self, receipts):
        self.session_receipts = receipts


def test_transcript_spend_prices_at_list_rate_and_says_it_is_an_estimate():
    scan = _FakeScan([
        _FakeReceipt(["claude-opus-5"], {"input_tokens": 1_000_000,
                                         "output_tokens": 100_000,
                                         "cache_read_input_tokens": 2_000_000,
                                         "cache_creation_input_tokens": 200_000}),
        _FakeReceipt(["claude-haiku-4-5"], {"input_tokens": 1_000_000,
                                            "output_tokens": 0,
                                            "cache_read_input_tokens": 0,
                                            "cache_creation_input_tokens": 0}),
        _FakeReceipt(["some-future-model"], {"input_tokens": 5,
                                             "output_tokens": 5,
                                             "cache_read_input_tokens": 0,
                                             "cache_creation_input_tokens": 0}),
    ])
    out = transcript_spend(scan)
    # 1M in @ $5 + 0.1M out @ $25 + 2M cache-read @ $0.50 + 0.2M writes @ $6.25
    assert out["byModel"]["claude-opus-5"]["usd"] == pytest.approx(
        5.0 + 2.5 + 1.0 + 1.25)
    assert out["byModel"]["claude-haiku-4-5"]["usd"] == pytest.approx(1.0)
    assert out["unpricedModels"] == ["some-future-model"]
    assert out["estimate"] is True
    assert out["tokens"]["input_tokens"] == 2_000_005


def test_spend_meter_keeps_our_own_calls_exact(state):
    with open(state.spend_path, "w", encoding="utf-8") as fh:
        for row in ({"ts": "2026-09-14T10:00:00Z", "command": "compile --llm",
                     "costUsd": 0.0123, "ok": True},
                    {"ts": "2026-09-15T10:00:00Z", "command": "compile --llm",
                     "costUsd": 0.02, "ok": False}):
            fh.write(json.dumps(row) + "\n")
    meter = spend_meter(state, None)
    assert meter["ours"]["calls"] == 2
    assert meter["ours"]["usd"] == pytest.approx(0.0323)
    assert meter["ours"]["byCommand"]["compile --llm"]["calls"] == 2
    assert meter["ours"]["budgetRefusals"] == 1
    assert meter["transcripts"] is None and meter["totalUsd"] == meter["ours"]["usd"]


# --------------------------------------------------------------------------
# the daily digest
# --------------------------------------------------------------------------

def test_daily_rows_bucket_by_day_and_stop_at_the_window(state):
    state.write_candidates([CANDIDATE])                  # 2026-09-01: too old
    with open(state.write_candidates_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "w-1", "ts": "2026-09-14T08:00:00+00:00",
                             "path": "/x/CLAUDE.md", "tool": "Write",
                             "agent": "subagent", "decision": "ask",
                             "snapshot": {}, "diff": {}}) + "\n")
    log(state, [{"event": "allow", "ts": "2026-09-14T09:00:00Z"},
                {"event": "deny", "rule": "p-aaa", "ts": "2026-09-14T09:01:00Z"},
                {"event": "error", "ts": "2026-09-14T09:02:00Z"}])
    log(state, [{"event": "session_stop", "ts": "2026-09-14T23:00:00Z",
                 "session": "s"}], path=state.funnel_path)
    with open(state.spend_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-09-14T10:00:00Z", "costUsd": 0.05}) + "\n")

    rows = daily_rows(state, build_entries(state, now=NOW), days=7, now=NOW)
    assert [r["day"] for r in rows] == ["2026-09-14"]     # 09-01 is out of window
    r = rows[0]
    assert r["proposed"] == 1 and r["writes"] == 1
    assert r["calls"] == 2 and r["fires"] == 1 and r["errors"] == 1
    assert r["sessions"] == 1 and r["usd"] == pytest.approx(0.05)
