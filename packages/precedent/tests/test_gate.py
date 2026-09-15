# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""THE TEMPORAL BIRTH GATE.

A correction expresses a policy from the moment it was made, so the gate is
built entirely out of *when* things happened:

(a) the rule must fire on the violating action recorded immediately before the
    correction;
(b) among the eligible actions AFTER t0, a fire confirmed by another correction
    in the next three human turns is a true positive and everything else is a
    tolerated (false) fire, capped at epsilon;
(c) everything before t0 is reported and not counted.

Each test below plants exactly one of those situations and asserts the verdict
*and* the counts, because a verdict without counts is unfalsifiable.
"""

from __future__ import annotations

from conftest import sid
from precedent.compile import compile_topics, run_temporal_gate
from precedent.mine import mine
from precedent.rules import build_rule

S1, S2, S3 = sid(1), sid(2), sid(3)


def day(d, h, m=0):
    return f"2026-09-{d:02d}T{h:02d}:{m:02d}:00.000Z"


def _pip_topic(result):
    for t in result.topics:
        if any("pip" in c.turn.text for c in t.members):
            return t
    raise AssertionError("no pip topic mined")


def _gate(home, **kw):
    """Mine ``home``, compile the pip rule, run the gate."""
    result = mine(home)
    topic = _pip_topic(result)
    rule, gate = compile_topics([topic], result, template="use_x_not_y",
                                x="uv", y="pip", **kw)
    return rule, gate, topic, result


BEFORE = [
    ("human", day(1, 9, 0), "帮我把依赖装好。"),
    ("assistant", day(1, 9, 1), "Installing."),
    ("tool", day(1, 9, 2), "Bash", {"command": "pip install requests"}),
    ("human", day(1, 9, 4), "不要用 pip，用 uv。"),        # t0
]


# --------------------------------------------------------------------------
# (a) HIT
# --------------------------------------------------------------------------

def test_hit_on_the_violating_action(home_builder):
    home = home_builder({S1: BEFORE + [
        ("assistant", day(1, 9, 5), "Switching."),
        ("tool", day(1, 9, 6), "Bash", {"command": "uv add requests"}),
        ("tool", day(1, 9, 7), "Bash", {"command": "uv add flask"}),
        ("tool", day(1, 9, 8), "Bash", {"command": "uv sync"}),
        ("human", day(1, 9, 9), "好的，谢谢。"),
    ]})
    rule, gate, topic, _ = _gate(home)
    assert gate.hit is True
    assert len(gate.hits) == 1
    assert gate.hits[0]["tool"] == "Bash"
    assert "pip install requests" in gate.hits[0]["summary"]
    assert gate.hits[0]["correctionQuote"].startswith("不要用 pip")
    assert gate.t0 == day(1, 9, 4)
    assert gate.verdict == "PASS"
    assert (gate.n_false_fires, gate.eligible_after) == (0, 3)


def test_no_hit_is_a_fail_however_quiet_the_rule_is(home_builder):
    """A rule that does not fire on what the user objected to is not a
    compilation of that correction, no matter how clean its post-t0 record."""
    home = home_builder({S1: BEFORE + [
        ("tool", day(1, 9, 6), "Bash", {"command": "uv add requests"}),
        ("tool", day(1, 9, 7), "Bash", {"command": "uv add flask"}),
        ("tool", day(1, 9, 8), "Bash", {"command": "uv sync"}),
        ("human", day(1, 9, 9), "好的。"),
    ]})
    result = mine(home)
    topic = _pip_topic(result)
    # a rule about something that never appears: quiet everywhere, useless
    rule = build_rule(topic.id, "dont_use", x="cargo")
    gate = run_temporal_gate(rule, [topic], result)
    assert gate.hit is False
    assert gate.n_false_fires == 0
    assert gate.verdict == "FAIL"
    assert any("HIT" in f for f in gate.failures)


# --------------------------------------------------------------------------
# (b) QUIET-AFTER
# --------------------------------------------------------------------------

def test_a_tolerated_post_t0_fire_fails_the_gate(home_builder):
    """One post-t0 fire that nobody objected to is a false fire: 1/4 > 2 %."""
    home = home_builder({S1: BEFORE + [
        ("tool", day(1, 9, 6), "Bash", {"command": "uv add requests"}),
        ("tool", day(1, 9, 7), "Bash", {"command": "uv add flask"}),
        ("tool", day(1, 9, 8), "Bash", {"command": "pip install extra"}),  # fire
        ("tool", day(1, 9, 9), "Bash", {"command": "uv sync"}),
        ("human", day(1, 10, 0), "好的，继续做别的。"),   # not a correction in T
    ]})
    _rule, gate, _topic, _ = _gate(home)
    assert gate.hit is True
    assert gate.eligible_after == 4
    assert gate.fires_after == 1
    assert gate.n_true_positives == 0
    assert gate.n_false_fires == 1
    assert round(gate.false_fire_rate, 3) == 0.25
    assert gate.verdict == "FAIL"
    assert any("QUIET-AFTER" in f for f in gate.failures)
    assert "pip install extra" in gate.false_fires[0]["value"]


def test_a_confirmed_post_t0_fire_is_a_true_positive_and_passes(home_builder):
    """The same fire, but the user objected again within three human turns:
    the rule was right, so it is not counted against it."""
    home = home_builder({S1: BEFORE + [
        ("tool", day(1, 9, 6), "Bash", {"command": "uv add requests"}),
        ("tool", day(1, 9, 7), "Bash", {"command": "uv add flask"}),
        ("tool", day(1, 9, 8), "Bash", {"command": "pip install extra"}),  # fire
        ("human", day(1, 9, 30), "我说过不要用 pip 了，用 uv。"),          # objection
        ("tool", day(1, 9, 40), "Bash", {"command": "uv sync"}),
        ("human", day(1, 10, 0), "好的。"),
    ]})
    _rule, gate, topic, _ = _gate(home)
    assert len(topic.members) == 2                 # both corrections, one topic
    assert gate.eligible_after == 4
    assert gate.fires_after == 1
    assert gate.n_true_positives == 1
    assert gate.n_false_fires == 0
    assert gate.false_fire_rate == 0.0
    assert gate.verdict == "PASS"
    tp = gate.true_positives[0]
    assert "pip install extra" in tp["value"]
    assert "我说过不要用 pip" in tp["confirmedQuote"]


def test_a_fire_confirmed_too_late_is_a_false_fire(home_builder):
    """The confirmation window is three human turns, not 'ever again'."""
    home = home_builder({S1: BEFORE + [
        ("tool", day(1, 9, 8), "Bash", {"command": "pip install extra"}),
        ("human", day(1, 9, 10), "顺便看一下日志。"),
        ("human", day(1, 9, 12), "再看一下配置。"),
        ("human", day(1, 9, 14), "还有那个脚本。"),
        ("human", day(1, 9, 16), "我说过不要用 pip 了，用 uv。"),   # 4th turn: too late
        ("tool", day(1, 9, 18), "Bash", {"command": "uv sync"}),
        ("tool", day(1, 9, 19), "Bash", {"command": "uv lock"}),
        ("tool", day(1, 9, 20), "Bash", {"command": "uv run pytest"}),
    ]})
    _rule, gate, _topic, _ = _gate(home)
    assert gate.n_true_positives == 0
    assert gate.n_false_fires == 1
    assert gate.verdict == "FAIL"


def test_epsilon_is_a_knob_and_the_counts_are_always_shown(home_builder):
    home = home_builder({S1: BEFORE + [
        ("tool", day(1, 9, 6), "Bash", {"command": "uv add requests"}),
        ("tool", day(1, 9, 7), "Bash", {"command": "uv add flask"}),
        ("tool", day(1, 9, 8), "Bash", {"command": "pip install extra"}),
        ("tool", day(1, 9, 9), "Bash", {"command": "uv sync"}),
        ("human", day(1, 10, 0), "好。"),
    ]})
    _rule, gate, _topic, _ = _gate(home, epsilon=0.5)
    assert gate.verdict == "PASS"                  # 25 % <= 50 %
    assert "1/4" in gate.counts()
    d = gate.to_dict()
    assert d["falseFires"] == 1 and d["eligibleAfter"] == 4
    assert d["gate"] == "temporal-birth-gate/v1"


# --------------------------------------------------------------------------
# INSUFFICIENT
# --------------------------------------------------------------------------

def test_insufficient_when_there_is_almost_no_post_t0_behaviour(home_builder):
    home = home_builder({S1: BEFORE + [
        ("tool", day(1, 9, 6), "Bash", {"command": "uv add requests"}),
        ("tool", day(1, 9, 7), "Bash", {"command": "uv add flask"}),
        ("human", day(1, 9, 9), "好。"),
    ]})
    _rule, gate, _topic, _ = _gate(home)
    assert gate.hit is True
    assert gate.eligible_after == 2
    assert gate.verdict == "INSUFFICIENT"
    assert any("INSUFFICIENT" in f for f in gate.failures)


def test_min_eligible_after_is_a_knob(home_builder):
    home = home_builder({S1: BEFORE + [
        ("tool", day(1, 9, 6), "Bash", {"command": "uv add requests"}),
        ("tool", day(1, 9, 7), "Bash", {"command": "uv add flask"}),
        ("human", day(1, 9, 9), "好。"),
    ]})
    _rule, gate, _topic, _ = _gate(home, min_eligible_after=2)
    assert gate.verdict == "PASS"


# --------------------------------------------------------------------------
# (c) pre-t0 is reported, not counted
# --------------------------------------------------------------------------

def test_pre_t0_fires_are_reported_and_never_counted(home_builder):
    """Ten pip calls before the user ever said anything: that is history, not a
    false-positive rate.  The v0 gate would have called this rule noisy."""
    pre = [("tool", day(1, 8, i), "Bash", {"command": f"pip install pkg{i}"})
           for i in range(10)]
    home = home_builder({S1: [("human", day(1, 7, 0), "装依赖")] + pre + [
        ("tool", day(1, 9, 2), "Bash", {"command": "pip install requests"}),
        ("human", day(1, 9, 4), "不要用 pip，用 uv。"),
        ("tool", day(1, 9, 6), "Bash", {"command": "uv add requests"}),
        ("tool", day(1, 9, 7), "Bash", {"command": "uv add flask"}),
        ("tool", day(1, 9, 8), "Bash", {"command": "uv sync"}),
        ("human", day(1, 9, 9), "好。"),
    ]})
    _rule, gate, _topic, _ = _gate(home)
    assert gate.eligible_before == 11 and gate.fires_before == 11
    assert gate.eligible_after == 3 and gate.n_false_fires == 0
    assert gate.verdict == "PASS"
    assert gate.to_dict()["preT0"] == {"eligible": 11, "fires": 11, "counted": False}


# --------------------------------------------------------------------------
# eligibility and cross-session accounting
# --------------------------------------------------------------------------

def test_a_session_that_never_used_the_tool_contributes_nothing(home_builder):
    home = home_builder({
        S1: BEFORE + [
            ("tool", day(1, 9, 6), "Bash", {"command": "uv add requests"}),
            ("tool", day(1, 9, 7), "Bash", {"command": "uv add flask"}),
            ("tool", day(1, 9, 8), "Bash", {"command": "uv sync"}),
            ("human", day(1, 9, 9), "好。"),
        ],
        S2: [("human", day(2, 9, 0), "看一下这个文件"),
             ("tool", day(2, 9, 1), "Edit", {"file_path": "/tmp/x.md",
                                             "old_string": "a", "new_string": "b"}),
             ("human", day(2, 9, 2), "行。")],
    })
    _rule, gate, _topic, _ = _gate(home)
    assert gate.eligible_after == 3          # S2 ran no Bash: not eligible
    assert gate.verdict == "PASS"


def test_a_later_uncorrected_session_still_counts_after_t0(home_builder):
    """The policy is global in time: a fire in another session after t0 counts."""
    home = home_builder({
        S1: BEFORE + [
            ("tool", day(1, 9, 6), "Bash", {"command": "uv add requests"}),
            ("tool", day(1, 9, 7), "Bash", {"command": "uv add flask"}),
            ("human", day(1, 9, 9), "好。"),
        ],
        S2: [("human", day(2, 9, 0), "起个服务"),
             ("tool", day(2, 9, 1), "Bash", {"command": "pip install flask"}),
             ("human", day(2, 9, 2), "行，跑起来了。")],
    })
    _rule, gate, _topic, _ = _gate(home)
    assert gate.eligible_after == 3
    assert gate.n_false_fires == 1
    assert gate.false_fires[0]["sessionId"] == S2
    assert gate.verdict == "FAIL"


def test_gate_evidence_is_stored_on_the_candidate(home_builder):
    home = home_builder({S1: BEFORE + [
        ("tool", day(1, 9, 6), "Bash", {"command": "uv add requests"}),
        ("tool", day(1, 9, 7), "Bash", {"command": "uv add flask"}),
        ("tool", day(1, 9, 8), "Bash", {"command": "uv sync"}),
        ("human", day(1, 9, 9), "好。"),
    ]})
    _rule, gate, topic, _ = _gate(home)
    d = gate.to_dict()
    for key in ("verdict", "counts", "t0", "epsilon", "hit", "nViolatingActions",
                "eligibleAfter", "truePositives", "falseFires", "falseFireRate",
                "preT0", "nToolCallsScanned", "correctedSessions", "topics"):
        assert key in d, key
    assert d["topics"] == [topic.id]
    assert d["correctedSessions"] == [S1]


def test_a_scoped_rule_is_not_eligible_where_it_cannot_fire(home_builder, tmp_path):
    """Counting out-of-scope calls as 'quiet' would flatter a narrow rule."""
    import os
    alpha = str(tmp_path / "alpha")
    home = home_builder({S1: BEFORE + [
        ("tool", day(1, 9, 6), "Bash", {"command": "uv add requests"}),
        ("tool", day(1, 9, 7), "Bash", {"command": "uv add flask"}),
        ("tool", day(1, 9, 8), "Bash", {"command": "uv sync"}),
        ("human", day(1, 9, 9), "好。"),
    ]}, cwd=alpha)
    result = mine(home)
    topic = _pip_topic(result)
    wide, gate_wide = compile_topics([topic], result, template="use_x_not_y",
                                     x="uv", y="pip")
    assert gate_wide.eligible_after == 3 and gate_wide.out_of_scope == 0

    _narrow, gate_narrow = compile_topics(
        [topic], result, template="use_x_not_y", x="uv", y="pip",
        cwd_glob=os.path.join(str(tmp_path), "beta") + "/**")
    assert gate_narrow.eligible_after == 0
    assert gate_narrow.out_of_scope == 4
    assert gate_narrow.verdict in ("FAIL", "INSUFFICIENT")
