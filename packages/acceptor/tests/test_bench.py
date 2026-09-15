# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""AcceptorBench reproduces the measured gate statistics and is deterministic."""

import json

import pytest

from acceptor import BenchConfig, run_bench
from acceptor.bench import main


# ---- (h) greedy ~40-50 % false commits at zero lift; gate <= alpha ----------------------
def test_stochastic_regime_false_commits():
    res = run_bench(regime="stochastic", n=40, t_candidates=10, p_inc=0.6, alpha=0.05, seeds=100)
    g = res.policies["greedy"]
    assert 0.40 <= g.false_commit_rate <= 0.50, res.table()
    for name in ("gate_fixed", "gate_mixture", "gate_ons", "gate_agrapa"):
        p = res.policies[name]
        assert p.false_commit_rate <= 0.05 + 0.02, (name, res.table())  # 1000 null decisions: SE ~ 0.7 %
        assert p.evals_per_decision < 40  # sequential stopping saves evaluations
    assert res.policies["mcnemar"].false_commit_rate <= 0.07


def test_planted_regime_reports_all_rates():
    res = run_bench(regime="planted", n=40, lift=0.3, k_null=4, n_harmful=2, harm=0.3, p_inc=0.5, seeds=60, harm_alpha=0.05)
    for name, p in res.policies.items():
        d = p.as_dict()
        assert p.n_true == 60 and p.n_null == 240 and p.n_harmful == 120
        assert 0.0 <= d["false_commit_rate"] <= 1.0 and 0.0 <= d["harmful_commit_rate"] <= 1.0
        assert 0.0 <= d["missed_improvement_rate"] <= 1.0
    assert res.policies["greedy"].power > 0.9
    assert res.policies["gate_fixed"].harmful_commit_rate <= 0.02
    assert res.policies["greedy"].harmful_commit_rate >= 0.0


def test_schedule_controls_run_level_fwer():
    res = run_bench(regime="stochastic", n=40, t_candidates=10, seeds=150, schedule=True, policies=("gate_fixed", "gate_mixture"))
    for p in res.policies.values():
        assert p.fwer <= 0.05 + 0.03


def test_bench_is_deterministic_and_json_serialisable():
    cfg = BenchConfig(regime="planted", n=20, lift=0.2, k_null=3, seeds=20, policies=("greedy", "gate_mixture"))
    a = run_bench(cfg).as_dict()
    b = run_bench(cfg).as_dict()
    assert a == b
    json.dumps(a)
    with pytest.raises(ValueError):
        BenchConfig(regime="other")
    with pytest.raises(ValueError):
        BenchConfig(policies=("nope",))


def test_cli(capsys):
    assert main(["--regime", "planted", "--n", "20", "--lift", "0.3", "--seeds", "10", "--k", "2", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["config"]["regime"] == "planted" and "gate_mixture" in out["policies"]
    assert main(["--regime", "stochastic", "--seeds", "5", "--policies", "greedy,gate_fixed"]) == 0
    assert "greedy" in capsys.readouterr().out
