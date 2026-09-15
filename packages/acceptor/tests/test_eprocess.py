# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Gate tests: supermartingale property under H0, power, serialisation, decision states."""

import json
import math
import random

import pytest

from acceptor import GateState, PairedBinaryGate, PairedBoundedGate, run_bench
from acceptor.eprocess import BETTING_STRATEGIES

ALPHA = 0.05
SEEDS = 2000
# One-sided binomial tolerance: alpha + 3 standard errors of a 2000-seed estimate.
TOL = ALPHA + 3 * math.sqrt(ALPHA * (1 - ALPHA) / SEEDS)


def _h0_binary_pairs(rng, n, p):
    """Both arms Bernoulli(p): q = P(cand wins | discordant) = 1/2 exactly (H0 boundary)."""
    return [(int(rng.random() < p), int(rng.random() < p)) for _ in range(n)]


# ---- (a) supermartingale property ----------------------------------------------------
@pytest.mark.parametrize("betting", BETTING_STRATEGIES)
def test_binary_gate_type_i_error_under_h0(betting):
    crossings = 0
    harm_crossings = 0
    for s in range(SEEDS):
        rng = random.Random(1_000 + s)
        g = PairedBinaryGate(alpha=ALPHA, betting=betting, harm_alpha=ALPHA)
        g.update_many(_h0_binary_pairs(rng, 60, 0.6))
        crossings += g.max_wealth >= 1 / ALPHA
        harm_crossings += g.harm_detected
    assert crossings / SEEDS <= TOL, f"{betting}: P(max wealth >= 1/alpha) = {crossings / SEEDS:.4f} > {TOL:.4f}"
    assert harm_crossings / SEEDS <= TOL


@pytest.mark.parametrize("betting", ("fixed", "mixture", "agrapa"))
@pytest.mark.parametrize("dist", ("uniform", "skewed"))
def test_bounded_gate_type_i_error_under_h0(betting, dist):
    """Mean-zero deltas: uniform on [-1, 1] and a skewed two-point law (0.9 w.p. 0.1, -0.1 w.p. 0.9)."""
    crossings = 0
    for s in range(SEEDS):
        rng = random.Random(5_000 + s)
        g = PairedBoundedGate(alpha=ALPHA, betting=betting)
        for _ in range(60):
            d = rng.uniform(-1, 1) if dist == "uniform" else (0.9 if rng.random() < 0.1 else -0.1)
            g.update(d)
        crossings += g.max_wealth >= 1 / ALPHA
    assert crossings / SEEDS <= TOL, f"{betting}/{dist}: {crossings / SEEDS:.4f} > {TOL:.4f}"


def test_wealth_is_nonnegative_and_factors_bounded():
    """Worst-case streams never drive wealth below zero for any admissible fraction."""
    for betting in BETTING_STRATEGIES:
        g = PairedBinaryGate(alpha=ALPHA, betting=betting, harm_alpha=ALPHA)
        g.update_many([(1, 0)] * 50 + [(0, 1)] * 50)
        assert g.wealth >= 0.0 and g.harm_wealth >= 0.0
    b = PairedBoundedGate(alpha=ALPHA, betting="fixed", lam=1.0 / (1 + 0.3) - 1e-9, mu0=0.3)
    b.update_many([-1.0] * 50)
    assert b.wealth >= 0.0


def test_invalid_betting_fractions_rejected():
    with pytest.raises(ValueError):
        PairedBinaryGate(betting="fixed", lam=1.5)  # factor 1 - 1.5 < 0 on a loss
    with pytest.raises(ValueError):
        PairedBinaryGate(betting="mixture", grid=(0.5, 1.2))
    with pytest.raises(ValueError):
        PairedBinaryGate(betting="ons", cap=1.0)  # must be strictly < 1/|g_min|
    with pytest.raises(ValueError):
        PairedBoundedGate(betting="fixed", lam=0.9, mu0=0.2)  # 0.9 > 1/(1 + 0.2)
    PairedBoundedGate(betting="fixed", lam=0.8, mu0=0.2)  # 0.8 < 0.8333 ok
    with pytest.raises(ValueError):
        PairedBoundedGate(mu0=1.0)


# ---- (b) power sanity ------------------------------------------------------------------
def test_power_planted_lift_0_3_n40():
    res = run_bench(regime="planted", n=40, lift=0.3, p_inc=0.5, k_null=0, seeds=400, policies=("gate_fixed", "gate_mixture"))
    assert res.policies["gate_fixed"].power >= 0.60, res.table()
    assert res.policies["gate_mixture"].power >= 0.55, res.table()


def test_exact_wealth_for_fixed_bet():
    g = PairedBinaryGate(alpha=0.05, betting="fixed", lam=0.5)
    for _ in range(12):
        st = g.update((0, 1))
    assert st is GateState.ACCEPT
    assert g.wealth == pytest.approx(1.5**12, rel=1e-12)
    assert g.p_value == pytest.approx(1 / 1.5**12)
    assert g.decided_at == 8  # 1.5**8 = 25.6 >= 20 is the first crossing
    assert g.n_wins == 12 and g.n_losses == 0


# ---- (c) serialisation round-trip ------------------------------------------------------
@pytest.mark.parametrize("betting", BETTING_STRATEGIES)
def test_binary_roundtrip_preserves_wealth(betting):
    rng = random.Random(42)
    pairs = [(int(rng.random() < 0.5), int(rng.random() < 0.7)) for _ in range(80)]
    g = PairedBinaryGate(alpha=0.05, betting=betting, max_pairs=200, harm_alpha=0.05)
    g.update_many(pairs[:37])
    blob = g.to_json()
    g2 = PairedBinaryGate.from_json(blob)
    assert abs(g2.wealth - g.wealth) < 1e-12 and g2.harm_wealth == g.harm_wealth
    assert g2.to_json() == blob
    # continuing both copies gives identical trajectories (the state is complete)
    for p in pairs[37:]:
        assert g.update(p) is g2.update(p)
        assert g.wealth == g2.wealth and g.harm_wealth == g2.harm_wealth
    assert json.loads(blob)["kind"] == "PairedBinaryGate"


@pytest.mark.parametrize("betting", BETTING_STRATEGIES)
def test_bounded_roundtrip_preserves_wealth(betting):
    rng = random.Random(7)
    g = PairedBoundedGate(alpha=0.05, betting=betting, mu0=0.1, harm_alpha=0.1, max_pairs=500)
    for _ in range(50):
        g.update(rng.uniform(-0.8, 1.0))
    g2 = PairedBoundedGate.from_dict(json.loads(json.dumps(g.to_dict())))
    assert abs(g2.wealth - g.wealth) < 1e-12
    assert g2.summary() == g.summary()
    for _ in range(50):
        d = rng.uniform(-1.0, 1.0)
        assert g.update(d) is g2.update(d)
    assert g.wealth == g2.wealth


# ---- decision states -------------------------------------------------------------------
def test_ties_are_discarded_but_consume_budget():
    g = PairedBinaryGate(alpha=0.05, betting="fixed", max_pairs=10)
    states = []
    for _ in range(5):
        states.append(g.update((1, 1)))
        states.append(g.update((0, 0)))
    assert g.wealth == 1.0 and g.n_ties == 10 and g.n_discordant == 0 and g.n_pairs == 10
    # ties burn budget: once 1.5**remaining < 20 the threshold is unreachable -> NSF at pair 3
    assert states[2] is GateState.NSF and states[1] is GateState.CONTINUE and g.decided_at == 3


def test_budget_exhaustion_rejects():
    g = PairedBinaryGate(alpha=0.5, betting="fixed", lam=0.5, max_pairs=2)  # threshold 2
    assert g.update((0, 1)) is GateState.CONTINUE  # wealth 1.5, one more win would reach 2.25
    assert g.update((1, 0)) is GateState.REJECT  # wealth 0.75, budget gone
    assert g.reason == "budget_exhausted" and g.remaining_budget == 0


def test_nsf_futility_stop_is_early_and_sound():
    g = PairedBinaryGate(alpha=0.05, betting="fixed", lam=0.5, max_pairs=8)
    assert g.update((1, 0)) is GateState.NSF  # 0.5 * 1.5**7 = 8.5 < 20: unreachable
    assert g.reason == "threshold_unreachable_within_budget"
    g = PairedBinaryGate(alpha=0.05, betting="mixture", max_pairs=12)
    states = [g.update((1, 0)) for _ in range(12)]
    assert GateState.NSF in states and states[-1] is GateState.NSF
    assert g.decided_at < 12


def test_harm_martingale_flags_regression_and_overrides_accept():
    g = PairedBinaryGate(alpha=0.05, betting="fixed", harm_alpha=0.05)
    for _ in range(8):
        assert g.update((1, 0)) in (GateState.CONTINUE, GateState.REJECT)
    assert g.state is GateState.REJECT and g.reason == "harm_detected" and g.harm_detected
    # post-acceptance canary: an accepted candidate that then regresses is flagged
    g = PairedBinaryGate(alpha=0.05, betting="fixed", harm_alpha=0.05)
    g.update_many([(0, 1)] * 10)
    assert g.state is GateState.ACCEPT
    g.update_many([(1, 0)] * 40)
    assert g.state is GateState.REJECT and g.reason == "harm_detected_after_decision"


def test_accept_is_sticky_and_anytime():
    g = PairedBinaryGate(alpha=0.05, betting="fixed")
    g.update_many([(0, 1)] * 10)
    assert g.state is GateState.ACCEPT
    g.update_many([(1, 0)] * 10)  # wealth falls back, decision stands (first crossing)
    assert g.state is GateState.ACCEPT and g.p_value <= 0.05


def test_bounded_gate_accepts_scores_or_deltas():
    g = PairedBoundedGate(alpha=0.05, betting="fixed")
    g.update((0.2, 0.7))
    g.update(0.5)
    assert g.n_pairs == 2 and g.mean_delta == pytest.approx(0.5)
    with pytest.raises(ValueError):
        g.update(1.5)
    with pytest.raises(ValueError):
        g.update((0.0, 1.2))
    with pytest.raises(ValueError):
        PairedBinaryGate().update((0, 2))


def test_gate_is_deterministic():
    rng = random.Random(3)
    pairs = [(int(rng.random() < 0.5), int(rng.random() < 0.6)) for _ in range(100)]
    a = PairedBinaryGate(betting="ons", harm_alpha=0.05)
    b = PairedBinaryGate(betting="ons", harm_alpha=0.05)
    a.update_many(pairs)
    b.update_many(pairs)
    assert a.to_json() == b.to_json()
