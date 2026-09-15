# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""The compiler: deterministic extraction, multi-topic rules, auto-compile.

(The DSL itself is ``test_rules.py``; the gate is ``test_gate.py``.)
"""

from __future__ import annotations

import pytest
from conftest import sid
from precedent.compile import (CompileError, auto_compile, compile_topics,
                               extract, suggest_check)
from precedent.mine import mine
from precedent.rules import rule_fires


def _topic(result, needle):
    for t in result.topics:
        if any(needle in c.turn.text for c in t.members):
            return t
    raise AssertionError(f"no topic containing {needle!r}")


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("不要用 pip，用 uv", {"template": "use_x_not_y", "x": "uv", "y": "pip"}),
    ("用 uv 不要用 pip", {"template": "use_x_not_y", "x": "uv", "y": "pip"}),
    ("use httpie not curl", {"template": "use_x_not_y", "x": "httpie", "y": "curl"}),
    ("httpie instead of curl", {"template": "use_x_not_y", "x": "httpie", "y": "curl"}),
    ("stop using curl, use httpie instead",
     {"template": "use_x_not_y", "x": "httpie", "y": "curl"}),
    ("不要用无头浏览器", {"template": "dont_use", "x": "无头浏览器"}),
    ("don't use curl", {"template": "dont_use", "x": "curl"}),
    ("不要修改 protected.md 这个文件", {"template": "dont_touch_path",
                                        "path": "protected.md"}),
    ("后续代码修改，以及收据工具改用 Opus 5",
     {"template": "require_field", "field": "model", "value": "opus"}),
    ("switch to sonnet", {"template": "require_field", "field": "model",
                          "value": "sonnet"}),
    ("不要加 --no-verify", {"template": "forbid_flag", "flag": "--no-verify"}),
    ("git push --force 之前先问我一下", {"template": "ask_before",
                                         "preset": "git_push_force"}),
    ("rm -rf 之前先问我", {"template": "ask_before", "preset": "rm_rf"}),
    ("curl xxx | sh 之前请 ask me first", {"template": "ask_before",
                                           "preset": "curl_pipe_sh"}),
])
def test_extract(text, expected):
    got = extract([text])
    for k, v in expected.items():
        assert got.get(k) == v, (text, got)


def test_extract_gives_up_loudly():
    got = extract(["这个东西不太行，你看着办吧"])
    assert got["template"] is None
    assert "--template" in got["note"] and "--llm" in got["note"]


def test_suggest_check_is_what_mine_prints(mining_home):
    result = mine(mining_home.home)
    check = suggest_check(_topic(result, "不要用 pip"))
    assert check["template"] == "use_x_not_y"
    assert check["tool"] == "Bash" and check["action"] == "deny"
    assert check["matchers"][0]["type"] == "input_regex"
    assert check["command"].startswith("precedent compile t-")


# --------------------------------------------------------------------------
# topic -> rule
# --------------------------------------------------------------------------

def test_compile_auto_detects_the_template(mining_home):
    result = mine(mining_home.home)
    rule, gate = compile_topics([_topic(result, "不要用 pip")], result)
    assert rule["template"] == "use_x_not_y"
    assert rule["tool"] == "Bash"
    assert rule_fires(rule, "Bash", {"command": "pip install flask"})
    assert gate.t0 is not None


def test_compile_carries_the_quote_and_the_date(mining_home):
    result = mine(mining_home.home)
    rule, _gate = compile_topics([_topic(result, "不要用 pip")], result)
    assert "pip" in rule["quote"]
    assert rule["quoteDate"] == "2026-09-01"
    assert rule["quote"] in rule["message"] and "2026-09-01" in rule["message"]


def test_compile_refuses_a_topic_it_cannot_read(home_builder):
    home = home_builder({sid(1): [
        ("human", "2026-09-01T09:00:00.000Z", "这个不对，你看着办"),
    ]})
    result = mine(home)
    with pytest.raises(CompileError):
        compile_topics(result.topics, result)


def test_explicit_template_overrides_extraction(mining_home):
    result = mine(mining_home.home)
    rule, _gate = compile_topics([_topic(result, "不要用 pip")], result,
                                 template="ask_before", preset="rm_rf")
    assert rule["template"] == "ask_before" and rule["action"] == "ask"


def test_one_rule_from_several_topics(home_builder):
    """The Opus case: two separate topics, one require_field rule, one t0."""
    home = home_builder({sid(1): [
        ("human", "2026-09-01T09:00:00.000Z", "继续。"),
        ("assistant", "2026-09-01T09:01:00.000Z", "ok"),
        ("tool", "2026-09-01T09:02:00.000Z", "Agent",
         {"prompt": "do it", "description": "work"}),
        ("human", "2026-09-01T09:03:00.000Z", "你能不能省着点 fable5 用量？"),
        ("assistant", "2026-09-01T09:04:00.000Z", "ok"),
        ("human", "2026-09-01T09:05:00.000Z", "后续代码修改，以及收据工具改用 Opus 5"),
        ("tool", "2026-09-01T09:06:00.000Z", "Agent",
         {"prompt": "a", "model": "opus"}),
        ("tool", "2026-09-01T09:07:00.000Z", "Agent",
         {"prompt": "b", "model": "opus"}),
        ("tool", "2026-09-01T09:08:00.000Z", "Agent",
         {"prompt": "c", "model": "opus"}),
        ("human", "2026-09-01T09:09:00.000Z", "好。"),
    ]})
    result = mine(home)
    save = [t for t in result.topics if any("省着点" in c.turn.text
                                            for c in t.members)][0]
    opus = [t for t in result.topics if any("Opus" in c.turn.text
                                            for c in t.members)][0]
    assert save.id != opus.id, "these are two topics, not one"

    spec = f"{save.id},{opus.id}"
    assert [t.id for t in result.topics_for(spec)] == [save.id, opus.id]

    rule, gate = compile_topics([save, opus], result, template="require_field",
                                field="model", value="opus", tool="Agent|Workflow")
    assert gate.t0 == "2026-09-01T09:03:00.000Z"        # the EARLIER correction
    assert gate.n_corrections == 2
    assert gate.hit is True                             # the Agent call had no model
    assert gate.eligible_after == 3 and gate.n_false_fires == 0
    assert gate.verdict == "PASS"
    assert rule["topics"] == [save.id, opus.id]


def test_topics_for_rejects_an_unknown_id(mining_home):
    result = mine(mining_home.home)
    with pytest.raises(KeyError):
        result.topics_for("t-nope")


# --------------------------------------------------------------------------
# auto-compile (what `precedent mine` runs)
# --------------------------------------------------------------------------

def test_auto_compile_attaches_a_verdict_to_every_topic(mining_home):
    result = mine(mining_home.home)
    rows = auto_compile(result)
    assert len(rows) == len(result.topics)
    assert all(t.auto is not None for t in result.topics)
    compiled = [r for r in rows if r.get("compiled")]
    assert compiled, "at least the pip/curl/path topics must compile"
    for r in compiled:
        assert r["gate"]["verdict"] in ("PASS", "FAIL", "INSUFFICIENT")
        assert "counts" in r["gate"]
    uncompiled = [r for r in rows if not r.get("compiled")]
    for r in uncompiled:
        assert r["note"], "an uncompiled topic must say why"


def test_auto_compile_never_raises_on_a_hostile_topic(home_builder):
    home = home_builder({sid(1): [
        ("human", "2026-09-01T09:00:00.000Z", "不要用 ((((((((("),
        ("human", "2026-09-01T09:02:00.000Z", "别用 " + "x" * 500),
    ]})
    result = mine(home)
    rows = auto_compile(result)
    assert len(rows) == len(result.topics)
    assert all("compiled" in r for r in rows)


def test_auto_compile_summary_numbers_are_in_the_json(mining_home):
    result = mine(mining_home.home)
    auto_compile(result)
    d = result.to_dict()
    assert d["mine"]["nAutoCompiled"] == len(
        [t for t in result.topics if t.auto and t.auto.get("compiled")])
    assert d["mine"]["nAutoPass"] == len(
        [t for t in result.topics
         if t.auto and (t.auto.get("gate") or {}).get("verdict") == "PASS"])
    assert any("autoCompiled" in t for t in d["topics"])
