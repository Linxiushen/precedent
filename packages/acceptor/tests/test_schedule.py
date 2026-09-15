# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Spend schedule: Z verification, summability, naive over-spend, alpha-investing."""

import math

import pytest

from acceptor import AlphaInvesting, SpendSchedule, Z_CTHS, cths_constant

K = 1_000_000


def test_z_matches_documented_value():
    z = cths_constant(K)
    assert abs(z - Z_CTHS) < 1e-6, z
    assert abs(z - 3.39) < 0.005  # SEA's quoted Z ~= 3.39
    # naive normaliser 1/(2 k ln^2(k+1)) sums to Z/2 = 1.69 x delta0
    assert abs(Z_CTHS / 2.0 - 1.694) < 0.001


def test_schedule_sums_to_delta0_over_1e6_terms_plus_tail():
    delta0 = 0.05
    s = SpendSchedule(delta0=delta0)
    partial = 0.0
    for k in range(1, K + 1):
        partial += s.next_alpha(k)
    tail = delta0 / (s.Z * math.log(K + 1.5))  # analytic remainder beyond K
    assert partial < delta0  # a finite prefix never over-spends
    assert abs(partial + tail - delta0) < 1e-6, (partial, tail)
    # the 10^6-term prefix spends ~97.9 % of the budget (slow log tail, by design)
    assert 0.975 < partial / delta0 < 0.982


def test_schedule_is_decreasing_and_positive():
    s = SpendSchedule(delta0=0.1)
    prev = float("inf")
    for k in range(1, 1000):
        a = s.next_alpha(k)
        assert 0.0 < a < prev
        prev = a
    assert s.next_alpha(1) == pytest.approx(0.1 / (Z_CTHS * math.log(2) ** 2))


def test_schedule_state_and_roundtrip():
    s = SpendSchedule(delta0=0.05)
    drawn = [s.draw() for _ in range(5)]
    assert drawn == [s.next_alpha(k) for k in range(1, 6)]
    assert s.rounds_spent == 5 and s.spent() == pytest.approx(sum(drawn))
    assert s.remaining_budget() == pytest.approx(0.05 - sum(drawn))
    s2 = SpendSchedule.from_dict(s.to_dict())
    assert s2.remaining_budget() == pytest.approx(s.remaining_budget()) and s2.draw() == s.next_alpha(6)


def test_schedule_rejects_overspending_normaliser():
    with pytest.raises(ValueError):
        SpendSchedule(delta0=0.05, Z=2.0)  # the naive constant
    SpendSchedule(delta0=0.05, Z=4.0)  # conservative is fine


def test_alpha_investing_dynamics():
    p = AlphaInvesting(alpha=0.05, spend_fraction=0.5)
    w0 = p.wealth
    a1 = p.next_alpha()
    assert 0 < a1 <= w0 / (1 + w0)
    p.report(False)
    assert p.wealth == pytest.approx(w0 - a1 / (1 - a1))
    a2 = p.next_alpha()
    p.report(True)
    assert p.wealth == pytest.approx(w0 - a1 / (1 - a1) + 0.05)  # pay-back omega = alpha
    assert p.rounds_spent == 2
    for _ in range(200):  # never negative, never exceeds the admissible bet
        a = p.next_alpha()
        assert 0.0 <= a <= p.wealth / (1 + p.wealth) + 1e-15
        p.report(False)
    assert p.wealth >= 0.0
    p2 = AlphaInvesting.from_dict(p.to_dict())
    assert p2.wealth == p.wealth and p2.rounds_spent == p.rounds_spent
    with pytest.raises(RuntimeError):
        AlphaInvesting().report(True)
    with pytest.raises(ValueError):
        AlphaInvesting(alpha=0.05, payout=0.1)
