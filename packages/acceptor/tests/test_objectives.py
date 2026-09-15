# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Hard constraints short-circuit before statistics; Pareto helpers."""

import pytest

from acceptor import Constraint, GateState, HardConstraints, MultiObjective, PairedBinaryGate, default_constraints, pareto_front, pareto_tiebreak


def _decide(mo, cand_metrics, inc_metrics, pairs):
    """The pipeline every adapter should follow: mechanical screen first, e-process second."""
    screen = mo.screen(cand_metrics, inc_metrics)
    if not screen.passed:
        return "BLOCKED", screen.violations, None
    g = PairedBinaryGate(alpha=0.05, betting="fixed")
    g.update_many(pairs)
    return g.state.value, (), g


# ---- (g) constraints short-circuit before statistics -----------------------------------
def test_constraint_violation_blocks_even_overwhelming_evidence():
    mo = MultiObjective(default_constraints(max_token_increase=0.10))
    winning = [(0, 1)] * 30  # the statistical gate alone would ACCEPT with wealth 1.5**30
    inc = {"tokens": 1000, "latency": 1.0, "harness_size": 100, "safety_pass_rate": 1.0, "activation_rate": 1.0,
           "safety_n": 40, "activation_n": 40}
    cand = dict(inc, tokens=1200)  # +20 % tokens
    decision, violations, gate = _decide(mo, cand, inc, winning)
    assert decision == "BLOCKED" and gate is None
    assert any("tokens" in v for v in violations)
    # same evidence, constraint satisfied -> the gate runs and accepts
    decision, _, gate = _decide(mo, dict(inc, tokens=1050), inc, winning)
    assert decision == "ACCEPT" and gate.state is GateState.ACCEPT


def test_missing_metric_fails_closed():
    mo = MultiObjective(default_constraints())
    inc = {"tokens": 1, "latency": 1, "harness_size": 1, "safety_pass_rate": 1, "activation_rate": 1,
           "safety_n": 40, "activation_n": 40}
    r = mo.screen({k: v for k, v in inc.items() if k != "safety_pass_rate"}, inc)
    assert not r.passed and r.decision == "BLOCKED" and "safety_pass_rate" in r.violations[0]
    r = mo.screen(inc, {})  # incumbent baseline missing for relative constraints
    assert not r.passed and len(r.violations) == 3
    assert mo.screen(inc, inc).passed


def test_safety_and_activation_floors():
    hc = HardConstraints([Constraint("safety_pass_rate", "min", 1.0), Constraint("activation_rate", "min", 0.5)])
    assert hc.check({"safety_pass_rate": 1.0, "activation_rate": 0.9}).passed
    r = hc.check({"safety_pass_rate": 0.98, "activation_rate": 0.2})
    assert not r.passed and len(r.violations) == 2 and r.checked == 2
    assert Constraint("latency", "max", 2.0).check({"latency": 2.5}) is not None
    assert Constraint("tokens", "max_abs_increase", 100).check({"tokens": 250}, {"tokens": 200}) is None
    assert Constraint("tokens", "max_rel_increase", 0.1).check({"tokens": 5}, {"tokens": 0}) is not None
    with pytest.raises(ValueError):
        Constraint("x", "bogus", 1.0)
    assert HardConstraints.from_dict(hc.as_dict()).as_dict() == hc.as_dict()


def test_pareto_tiebreak_reward_then_latency_then_cost():
    cands = {
        "a": {"reward": 0.9, "latency": 2.0, "cost": 1.0},
        "b": {"reward": 0.9, "latency": 1.0, "cost": 5.0},
        "c": {"reward": 0.8, "latency": 0.1, "cost": 0.1},
        "d": {"reward": 0.9, "latency": 1.0, "cost": 2.0},
        "e": {"reward": 0.9},  # missing keys sort last within the tie
    }
    assert pareto_tiebreak(cands) == ["d", "b", "a", "e", "c"]
    front = pareto_front(cands)
    # d dominates b (same reward/latency, lower cost) and e (missing = worst); a and d are incomparable
    assert front == ["a", "c", "d"]
    mo = MultiObjective()
    assert mo.rank(cands)[0] == "d" and set(mo.front(cands)) == set(front)
