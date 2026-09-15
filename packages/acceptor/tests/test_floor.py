# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Protected corpus / task floor: closed form vs simulation, k-of-n semantics, BWT/FWT."""

import json
import math
import random

import pytest

from acceptor import ProtectedCorpus, TaskFloor, false_block_rate, transfer_report
from acceptor.floor import binomial_tail

P = 0.6
REPS = 3000


def _simulate_false_block(N, k, reps, seed):
    """Exactly the model of research/tools/gate_power_sim.py, driven through ProtectedCorpus."""
    rng = random.Random(seed)
    blocks = 0
    for _ in range(reps):
        corpus = ProtectedCorpus(k_stability=k, k_confirm=k, n_confirm=k)
        for t in range(N):
            for _ in range(k):
                corpus.record_incumbent(f"t{t}", rng.random() < P)
            if corpus.is_protected(f"t{t}"):
                for _ in range(k):
                    corpus.record_candidate(f"t{t}", rng.random() < P)  # zero true lift
        blocks += corpus.check().blocked
    return blocks / reps


# ---- (e) closed form matches simulation ------------------------------------------------
@pytest.mark.parametrize("N", (10, 20, 40))
@pytest.mark.parametrize("k", (1, 2, 3))
def test_false_block_closed_form_matches_simulation(N, k):
    closed = false_block_rate([P] * N, k_stability=k, k_confirm=k, n_confirm=k)
    sim = _simulate_false_block(N, k, REPS, seed=100 * N + k)
    se = math.sqrt(max(closed * (1 - closed), 1e-4) / REPS)
    assert abs(closed - sim) <= 4 * se + 0.005, (N, k, closed, sim)


def test_false_block_reproduces_gate_power_sim_numbers():
    # research/tools/gate_power_sim.py: k=1 -> 93-100 %, k=2 -> 44-91 %, k=3 -> 13-43 % (N = 10..40)
    assert 0.93 <= false_block_rate([P] * 10, 1, 1, 1) <= 0.95
    assert false_block_rate([P] * 40, 1, 1, 1) > 0.99
    assert 0.43 <= false_block_rate([P] * 10, 2, 2, 2) <= 0.46
    assert 0.90 <= false_block_rate([P] * 40, 2, 2, 2) <= 0.92
    assert 0.12 <= false_block_rate([P] * 10, 3, 3, 3) <= 0.14
    assert 0.42 <= false_block_rate([P] * 40, 3, 3, 3) <= 0.44


def test_false_block_edge_cases():
    assert false_block_rate([1.0] * 50, 1, 1, 1) == 0.0  # deterministic tasks never false-block
    assert false_block_rate([], 2, 2, 2) == 0.0
    assert false_block_rate([0.6] * 5, 1, 1, 1, membership_given=True) == pytest.approx(1 - 0.6**5)
    # k-of-n with n > k is more lenient than k-of-k... no: it is *stricter* to flip (needs k fails of n)
    assert false_block_rate([0.6] * 10, 2, 2, 3) > false_block_rate([0.6] * 10, 2, 2, 2)  # more attempts, more chances
    assert false_block_rate([0.6] * 10, 2, 3, 3) < false_block_rate([0.6] * 10, 2, 2, 3)
    assert binomial_tail(3, 2, 0.4) == pytest.approx(3 * 0.4**2 * 0.6 + 0.4**3)
    with pytest.raises(ValueError):
        false_block_rate([0.5], 1, 2, 1)


def test_membership_requires_observed_stability():
    c = TaskFloor(k_stability=2, k_confirm=2)
    c.record_incumbent("a", True)
    assert not c.is_protected("a")  # only 1 of 2 repeats observed
    c.record_incumbent("a", True)
    assert c.is_protected("a")
    c.record_incumbent("b", True)
    c.record_incumbent("b", False)
    assert not c.is_protected("b")  # flaky task never enters the corpus
    c.record_incumbent("b", True)
    c.record_incumbent("b", True)
    assert c.is_protected("b")  # last k repeats all passed
    assert c.protected_tasks() == ["a", "b"]


def test_flip_counts_only_after_k_of_n_confirmation():
    c = ProtectedCorpus(k_stability=1, k_confirm=2, n_confirm=3)
    c.record_incumbent("a", True)
    c.record_candidate("a", False)
    v = c.check()
    assert not v.blocked and v.pending_flips == ("a",) and v.n_protected == 1
    c.record_candidate("a", True)
    c.record_candidate("a", False)  # 2 of 3 failed -> confirmed
    v = c.check()
    assert v.blocked and v.confirmed_flips == ("a",) and v.pending_flips == ()
    c.reset_candidate()
    assert not c.check().blocked
    # an unprotected task can never block
    c.record_incumbent("z", False)
    c.record_candidate("z", False)
    c.record_candidate("z", False)
    c.record_candidate("z", False)
    assert not c.check().blocked


def test_expected_false_block_and_roundtrip():
    c = ProtectedCorpus(k_stability=2, k_confirm=2)
    for t in range(5):
        c.record_incumbent(f"t{t}", True)
        c.record_incumbent(f"t{t}", True)
    # Laplace-smoothed pass prob 3/4 for 2/2 passes; explicit probs override
    assert c.expected_false_block() == pytest.approx(false_block_rate([0.75] * 5, 2, 2, 2, membership_given=True))
    assert c.expected_false_block({f"t{t}": 1.0 for t in range(5)}) == 0.0
    c2 = ProtectedCorpus.from_dict(json.loads(json.dumps(c.to_dict())))
    assert c2.to_dict() == c.to_dict() and c2.protected_tasks() == c.protected_tasks()


def test_transfer_report_separates_bwt_and_fwt():
    r = transfer_report(protected=[(1.0, 1.0), (1.0, 0.0), (0.5, 0.5)], target=[(0.0, 1.0), (0.2, 0.6)])
    assert r.bwt == pytest.approx(-1 / 3) and r.fwt == pytest.approx(0.7)
    assert r.protected_regressions == 1 and r.target_improvements == 2
    assert transfer_report([], []).bwt is None
    assert set(r.as_dict()) == {"bwt", "fwt", "n_protected", "n_target", "protected_regressions", "target_improvements"}
