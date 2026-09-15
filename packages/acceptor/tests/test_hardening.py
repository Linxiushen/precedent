# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the three review lenses (statistics, API, adversarial gaming).

Each test names the finding it pins.  The rule everywhere: a fix may *delay* or *refuse*
a decision, never weaken the null -- every gate here must still be a nonnegative
supermartingale under its stated H0.
"""

import json
import math
import random

import pytest

from acceptor import (
    AlphaInvesting,
    CandidateMonitor,
    Certificate,
    ConcurrentWrite,
    Constraint,
    GateState,
    HardConstraints,
    InstanceSampler,
    Ledger,
    LedgerCorrupt,
    LedgerViolation,
    MultiObjective,
    OrderViolation,
    PairedBinaryGate,
    PairedBoundedGate,
    ProtectedCorpus,
    SpendSchedule,
    content_hash,
    default_constraints,
    false_block_rate,
    funnel,
    pareto_tiebreak,
    verify_chain,
)
from acceptor.eprocess import BETTING_STRATEGIES, WEALTH_CAP

ALPHA = 0.05


def _cert(cid, decision="HOLD", rnd=1, **kw):
    kw.setdefault("alpha_spent", 0.05)
    kw.setdefault("cumulative_alpha", max(0.05, kw["alpha_spent"]))
    return Certificate(candidate_id=cid, round=rnd, algorithm="PairedBinaryGate/fixed", decision=decision, **kw)


# ====================================================================================
# STATISTICS lens
# ====================================================================================
def test_harm_null_is_anchored_at_harm_mu0_not_mu0():
    """STAT-major: an equal-quality candidate must not be flagged 'harm' because mu0 > 0.

    The harm null is E[d] >= harm_mu0 (default 0), independent of the superiority margin.
    Mirroring at mu0 flagged an E[d] = 0 stream 98.8 % of the time at mu0 = 0.2.
    """
    flagged = flagged_legacy = 0
    seeds = 400
    for s in range(seeds):
        rng = random.Random(9_000 + s)
        deltas = [rng.uniform(-1, 1) for _ in range(150)]
        g = PairedBoundedGate(alpha=ALPHA, betting="mixture", harm_alpha=ALPHA, mu0=0.2)
        legacy = PairedBoundedGate(alpha=ALPHA, betting="mixture", harm_alpha=ALPHA, mu0=0.2, harm_mu0=0.2)
        for d in deltas:
            g.update(d)
            legacy.update(d)
        flagged += g.harm_detected
        flagged_legacy += legacy.harm_detected
    assert flagged / seeds <= ALPHA + 3 * math.sqrt(ALPHA * (1 - ALPHA) / seeds), flagged / seeds
    assert flagged_legacy / seeds > 0.5  # the old anchoring is what the fix removed
    assert g.summary()["harm_mu0"] == 0.0


@pytest.mark.parametrize("harm_mu0", (-0.3, 0.0, 0.3))
def test_harm_martingale_stays_nonnegative_for_every_harm_mu0(harm_mu0):
    """STAT: re-anchoring must not break the nonnegativity bound lambda <= 1/|g_min|."""
    for betting in BETTING_STRATEGIES:
        g = PairedBoundedGate(alpha=ALPHA, betting=betting, harm_alpha=ALPHA, mu0=0.2, harm_mu0=harm_mu0)
        g.update_many([1.0] * 60 + [-1.0] * 60)
        assert g.wealth >= 0.0 and g.harm_wealth >= 0.0
        b = PairedBinaryGate(alpha=ALPHA, betting=betting, harm_alpha=ALPHA, harm_mu0=harm_mu0)
        b.update_many([(0, 1)] * 60 + [(1, 0)] * 60)
        assert b.wealth >= 0.0 and b.harm_wealth >= 0.0


def test_harm_type_i_error_under_its_own_null():
    """STAT: P(harm fires) <= harm_alpha when E[d] = harm_mu0 exactly (the H0' boundary)."""
    seeds, fires = 600, 0
    for s in range(seeds):
        rng = random.Random(4_100 + s)
        g = PairedBoundedGate(alpha=0.5, betting="fixed", harm_alpha=ALPHA, harm_mu0=-0.2)
        for _ in range(120):
            g.update(-0.2 + rng.uniform(-0.8, 0.8))  # E[d] = harm_mu0
        fires += g.harm_detected
    assert fires / seeds <= ALPHA + 3 * math.sqrt(ALPHA * (1 - ALPHA) / seeds), fires / seeds


@pytest.mark.parametrize("betting", BETTING_STRATEGIES)
def test_bounded_gate_type_i_includes_ons(betting):
    """STAT-minor: the bounded type-I test omitted 'ons', leaving that path unverified."""
    seeds, crossings = 1200, 0
    for s in range(seeds):
        rng = random.Random(7_700 + s)
        g = PairedBoundedGate(alpha=ALPHA, betting=betting)
        for _ in range(80):
            g.update(1.0 if rng.random() < 0.5 else -1.0)  # +-1 two-point, mean 0
        crossings += g.max_wealth >= 1 / ALPHA
    assert crossings / seeds <= ALPHA + 3 * math.sqrt(ALPHA * (1 - ALPHA) / seeds), crossings / seeds


@pytest.mark.parametrize("betting", BETTING_STRATEGIES)
def test_tight_supermartingale_bound_pure_discordant(betting):
    """STAT-minor: the old H0 tests (n=60, p=0.6) crossed ~2 % against a 6.5 % tolerance.

    Pure-discordant q = 1/2 over a long horizon puts the measured rate right at the bound,
    so an anti-conservative implementation would actually fail.
    """
    seeds, n, crossings = 3000, 250, 0
    for s in range(seeds):
        rng = random.Random(21_000 + s)
        g = PairedBinaryGate(alpha=ALPHA, betting=betting)
        for _ in range(n):
            g.update((0, 1) if rng.random() < 0.5 else (1, 0))  # every pair discordant
        crossings += g.max_wealth >= 1 / ALPHA
    rate = crossings / seeds
    assert rate <= ALPHA + 3 * math.sqrt(ALPHA * (1 - ALPHA) / seeds), f"{betting}: {rate:.4f}"
    assert rate > 0.005, f"{betting}: {rate:.4f} -- suspiciously low, is the gate even betting?"


def test_min_informative_only_delays_acceptance():
    """ADV-critical: a decision may not rest on a handful of hand-picked pairs.

    Requiring n_informative >= m intersects the crossing event with another, so the type-I
    bound is preserved (it can only shrink) -- and the targeted attack stops working.
    """
    targeted = [(1, 1)] * 20 + [(0, 0)] * 12 + [(0, 1)] * 8
    naive = PairedBinaryGate(alpha=ALPHA, betting="fixed", max_pairs=40)
    naive.update_many(targeted)
    assert naive.state is GateState.ACCEPT and naive.n_discordant == 8
    guarded = PairedBinaryGate(alpha=ALPHA, betting="fixed", max_pairs=40, min_informative=20)
    guarded.update_many(targeted)
    assert guarded.state is not GateState.ACCEPT
    # ... and with enough discordant evidence it still accepts.
    ok = PairedBinaryGate(alpha=ALPHA, betting="fixed", max_pairs=60, min_informative=20)
    ok.update_many([(0, 1)] * 25)
    assert ok.state is GateState.ACCEPT and ok.accepted_at == 20


def test_min_informative_type_i_error_stays_below_alpha():
    seeds, crossings = 800, 0
    for s in range(seeds):
        rng = random.Random(3_300 + s)
        g = PairedBinaryGate(alpha=ALPHA, betting="mixture", max_pairs=120, min_informative=15)
        g.update_many([(int(rng.random() < 0.6), int(rng.random() < 0.6)) for _ in range(120)])
        crossings += g.state is GateState.ACCEPT
    assert crossings / seeds <= ALPHA + 3 * math.sqrt(ALPHA * (1 - ALPHA) / seeds)


def test_selection_diagnostic_flags_a_filtered_stream():
    """STAT-major: a stream filtered on the incumbent's cached outcome breaks the null."""
    g = PairedBinaryGate(alpha=ALPHA, betting="fixed")
    g.update_many([(0, 1), (0, 0)] * 8)  # incumbent failed every instance
    assert g.summary()["selection_suspected"] is True
    h = PairedBinaryGate(alpha=ALPHA, betting="fixed")
    h.update_many([(1, 1), (0, 1), (1, 0)] * 5)
    assert h.summary()["selection_suspected"] is False
    # the bounded gate does not claim the diagnostic (no notion of "incumbent passed")
    b = PairedBoundedGate(alpha=ALPHA)
    b.update_many([0.5] * 20)
    assert b.summary()["selection_suspected"] is False


def test_floor_confirmation_window_is_frozen_so_the_closed_form_holds():
    """STAT/API-major: '>= k fails of >= n runs' let extra re-runs manufacture flips.

    Closed form 0.695 vs 0.996 simulated at 5 runs/task before the fix.
    """
    c = ProtectedCorpus(k_stability=2, k_confirm=2, n_confirm=2)
    c.record_incumbent("a", True)
    c.record_incumbent("a", True)
    for passed in (True, True, False, False, False):
        c.record_candidate("a", passed)
    v = c.check()
    assert v.verdict == "PASS" and not v.blocked and c.extra_runs() == {"a": 3}

    closed = false_block_rate([0.6] * 20, 2, 2, 2)
    rng = random.Random(5)
    blocks = 0
    reps = 1500
    for _ in range(reps):
        corp = ProtectedCorpus(k_stability=2, k_confirm=2, n_confirm=2, min_protected=0)
        for t in range(20):
            for _ in range(2):
                corp.record_incumbent(f"t{t}", rng.random() < 0.6)
            if corp.is_protected(f"t{t}"):
                for _ in range(5):  # an adapter that re-runs far beyond n_confirm
                    corp.record_candidate(f"t{t}", rng.random() < 0.6)
        blocks += corp.check().blocked
    assert abs(blocks / reps - closed) < 0.05, (blocks / reps, closed)


def test_expected_false_block_admits_when_it_is_only_a_prior():
    """STAT-minor: the Laplace default is (k+1)/(k+2) by construction, not a measurement."""
    c = ProtectedCorpus(k_stability=2, k_confirm=2)
    for t in range(3):
        c.record_incumbent(f"t{t}", True)
        c.record_incumbent(f"t{t}", True)
    assert set(c.pass_prob_evidence().values()) == {"prior_only"}
    with pytest.raises(ValueError, match="prior"):
        c.expected_false_block(require_evidence=True)
    assert c.expected_false_block({f"t{t}": 0.99 for t in range(3)}, require_evidence=True) < 0.01
    c.record_incumbent("t0", True)  # a run beyond the membership window is evidence
    assert c.pass_prob_evidence()["t0"] == "measured"


def test_alpha_investing_exhaustion_is_reported_not_a_crash():
    """STAT-minor: next_alpha() -> 0.0 and every gate rejects alpha=0."""
    p = AlphaInvesting(alpha=0.05, spend_fraction=1.0)
    p.next_alpha()
    p.report(False)
    assert p.exhausted and p.next_alpha() == 0.0
    with pytest.raises(ValueError, match="exhausted"):
        p.require_alpha()
    with pytest.raises(ValueError):
        PairedBinaryGate(alpha=0.0)


# ====================================================================================
# API / resumability lens
# ====================================================================================
def test_alpha_investing_round_trips_between_next_alpha_and_report():
    """API-major: the pending level was dropped by to_dict(), breaking the documented protocol."""
    p = AlphaInvesting(alpha=0.05)
    a = p.next_alpha()
    p2 = AlphaInvesting.from_dict(json.loads(json.dumps(p.to_dict())))
    p2.report(True)  # used to raise RuntimeError("call next_alpha() before report()")
    assert p2.wealth == pytest.approx(p.wealth + p.payout)
    assert p.next_alpha() == a  # a second call returns the same level, it does not re-draw


def test_gate_rejects_parameters_its_strategy_ignores():
    """API-minor: a mis-specified gate used to construct silently."""
    with pytest.raises(ValueError, match="does not use"):
        PairedBinaryGate(betting="fixed", cap=0.1, grid=(0.9,))
    with pytest.raises(ValueError, match="does not use"):
        PairedBinaryGate(betting="ons", lam=0.99)
    with pytest.raises(ValueError, match="does not use"):
        PairedBoundedGate(betting="mixture", lam=0.3)
    PairedBinaryGate(betting="ons", cap=0.4, lam0=0.1)  # relevant params are fine


def test_gate_state_version_is_checked():
    """API-minor: _restore wrote `version` but never read it."""
    g = PairedBinaryGate(alpha=ALPHA, betting="fixed")
    g.update_many([(0, 1)] * 3)
    d = g.to_dict()
    d["version"] = 99
    with pytest.raises(ValueError, match="version"):
        PairedBinaryGate.from_dict(d)
    with pytest.raises(ValueError, match="cannot restore"):
        PairedBoundedGate.from_dict(g.to_dict())
    with pytest.raises(ValueError, match="cannot restore"):
        ProtectedCorpus.from_dict(g.to_dict() | {"k_stability": 1, "k_confirm": 1})


def test_wealth_never_overflows_to_inf_or_nan():
    """API-minor: a long canary overflowed to inf, then NaN, then broke ledger canonicalisation."""
    for betting in ("fixed", "mixture"):
        kw = {"lam": 1.0} if betting == "fixed" else {"grid": (0.5, 1.0)}
        g = PairedBinaryGate(alpha=ALPHA, betting=betting, harm_alpha=ALPHA, **kw)
        g.update_many([(0, 1)] * 5000)
        blob = g.to_json()
        assert "Infinity" not in blob and "NaN" not in blob
        assert math.isfinite(g.wealth) and g.wealth <= WEALTH_CAP
        g.update((1, 0))
        assert math.isfinite(g.wealth) and not math.isnan(g.wealth)
        # a certificate built from this summary is still canonicalisable
        Ledger  # noqa: B018 - the point is that json.dumps below does not raise
        json.dumps(g.summary(), allow_nan=False)


def test_accept_time_survives_the_harm_canary():
    """API/STAT-minor: decided_at was overwritten, losing the original acceptance round."""
    g = PairedBinaryGate(alpha=ALPHA, betting="fixed", harm_alpha=ALPHA)
    g.update_many([(0, 1)] * 10)
    assert g.state is GateState.ACCEPT and g.accepted_at == 8
    w_at_accept = g.wealth_at_decision
    g.update_many([(1, 0)] * 40)
    assert g.state is GateState.REJECT and g.reason == "harm_detected_after_decision"
    s = g.summary()
    assert s["accepted_at"] == 8 and s["wealth_at_decision"] == w_at_accept
    assert s["harm_detected_at"] > 8 and s["wealth"] < 1.0
    assert PairedBinaryGate.from_json(g.to_json()).summary() == s


def test_max_rel_increase_signs_a_decrease_from_zero():
    """API-minor: a decrease from an incumbent of 0 was reported as an inf % increase."""
    c = Constraint("tokens", "max_rel_increase", 0.1)
    assert c.check({"tokens": -5}, {"tokens": 0}) is None
    assert c.check({"tokens": 0}, {"tokens": 0}) is None
    assert c.check({"tokens": 5}, {"tokens": 0}) is not None


def test_ledger_survives_a_torn_trailing_line(tmp_path):
    """API-minor: a partial line made Ledger(path) and verify_chain() raise JSONDecodeError."""
    path = tmp_path / "l.jsonl"
    led = Ledger(path)
    led.append(_cert("c1", "ACCEPT"))
    with open(path, "a") as f:
        f.write('{"candidate_id": "c2", "ro')
    reopened = Ledger(path)  # no exception
    ok, idx, msg = reopened.verify_chain()
    assert not ok and idx == 1 and "unparseable" in msg
    assert reopened.corrupt_at == 1 and len(reopened.certificates()) == 1
    with pytest.raises(LedgerCorrupt):
        reopened.append(_cert("c3", "HOLD"))


def test_two_ledger_writers_are_detected(tmp_path):
    """API-minor: a stale cached head silently produced a broken chain."""
    path = tmp_path / "l.jsonl"
    a, b = Ledger(path), Ledger(path)
    a.append(_cert("c1"))
    with pytest.raises(ConcurrentWrite):
        b.append(_cert("c2"))
    assert Ledger(path).verify_chain()[0]  # the chain was never corrupted


def test_tail_truncation_needs_an_anchor(tmp_path):
    """API-minor: the chain alone cannot see tail truncation; the docstring now says so."""
    path = tmp_path / "l.jsonl"
    led = Ledger(path)
    for i in range(5):
        led.append(_cert(f"c{i}"))
    head = led.head
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:3]) + "\n")
    assert Ledger(path).verify_chain()[0] is True  # undetectable without an anchor
    ok, _, msg = Ledger(path).verify_chain(expected_head=head)
    assert not ok and "anchored head" in msg


# ====================================================================================
# ADVERSARIAL lens
# ====================================================================================
def test_committed_instance_order_refuses_reordering():
    """ADV-critical: wins-first ordering turned ~2 % false ACCEPT into 66-89 %."""
    sampler = InstanceSampler([f"i{k}" for k in range(60)], salt="s3cret", reuse_window=3)
    order = sampler.draw(content_hash("def edit(): ..."), 40)
    assert len(order.instance_ids) == 40 and order.n_fresh == 40 and order.stale_fraction == 0.0
    g = PairedBinaryGate(alpha=ALPHA, betting="fixed", max_pairs=40).bind_instances(order)
    assert g.instance_set_hash == order.instance_set_hash and g.next_instance == order.instance_ids[0]
    g.update((0, 1), instance_id=order.instance_ids[0])
    with pytest.raises(OrderViolation):
        g.update((0, 1), instance_id=order.instance_ids[7])
    with pytest.raises(OrderViolation):
        g.update((0, 1))  # a bound gate will not take an anonymous pair
    g.update((1, 0), instance_id=order.instance_ids[1])
    assert g.n_pairs == 2


def test_instance_sampler_is_deterministic_rotating_and_resumable():
    pool = [f"i{k}" for k in range(20)]
    a = InstanceSampler(pool, salt="x", reuse_window=2)
    b = InstanceSampler(pool, salt="x", reuse_window=2)
    ch = content_hash({"src": "v1"})
    assert a.draw(ch, 8).instance_ids == b.draw(ch, 8).instance_ids
    assert a.draw(content_hash({"src": "v2"}), 8).instance_ids != a.draw(ch, 8).instance_ids
    restored = InstanceSampler.from_json(a.to_json())
    assert restored.to_dict() == a.to_dict()
    # rotation prefers unused instances
    s = InstanceSampler(pool, salt="y", reuse_window=10)
    first = set(s.draw(content_hash("a"), 10).instance_ids)
    second = s.draw(content_hash("b"), 10)
    assert not (first & set(second.instance_ids)) and second.n_fresh == 10
    third = s.draw(content_hash("c"), 10)  # the pool is now exhausted: stale, and it says so
    assert third.n_fresh == 0 and third.stale_fraction == 1.0
    assert s.exposure()["max_uses"] == 2


def test_evidence_hash_pins_the_fed_sequence():
    """ADV-critical: certificates must pin the data, not just the wealth."""
    a = PairedBinaryGate(alpha=ALPHA, betting="fixed")
    b = PairedBinaryGate(alpha=ALPHA, betting="fixed")
    pairs = [(0, 1), (1, 0), (1, 1), (0, 1)]
    a.update_many(pairs)
    b.update_many(list(reversed(pairs)))
    assert a.wealth == pytest.approx(b.wealth)  # same wealth ...
    assert a.evidence_hash != b.evidence_hash  # ... different evidence
    c = PairedBinaryGate(alpha=ALPHA, betting="fixed")
    c.update_many(pairs)
    assert c.evidence_hash == a.evidence_hash
    assert PairedBinaryGate.from_json(a.to_json()).evidence_hash == a.evidence_hash


def test_ledger_refuses_a_rewound_gate(tmp_path):
    """ADV-critical: snapshot-and-rewind accepted ~100 % of null candidates."""
    led = Ledger(tmp_path / "l.jsonl")
    g = PairedBinaryGate(alpha=ALPHA, betting="fixed")
    g.update_many([(0, 1), (1, 0), (0, 1)] * 5)
    snapshot = g.to_json()
    led.append(Certificate.from_gate("c", 1, g, cumulative_alpha=ALPHA))
    g.update_many([(1, 0)] * 6)  # a losing batch
    led.append(Certificate.from_gate("c", 2, g, cumulative_alpha=ALPHA))
    rewound = PairedBinaryGate.from_json(snapshot)  # "that batch never happened"
    rewound.update_many([(0, 1)] * 6)
    with pytest.raises(LedgerViolation, match="rewound|backwards|restored"):
        led.append(Certificate.from_gate("c", 3, rewound, cumulative_alpha=ALPHA))
    # replaying an already-superseded evidence hash is refused too
    with pytest.raises(LedgerViolation):
        led.append(_cert("c", rnd=4, n_pairs=99, evidence_hash=json.loads(snapshot)["evidence_hash"]))


def test_ledger_refuses_re_proposal_and_level_changes(tmp_path):
    """ADV-major: fresh ids for identical content bought a fresh alpha (90 % at K=100)."""
    led = Ledger(tmp_path / "l.jsonl")
    ch = content_hash("the same edit")
    led.append(_cert("v1", content_hash=ch))
    with pytest.raises(LedgerViolation, match="already proposed"):
        led.append(_cert("v1-renamed", content_hash=ch))
    assert [c.candidate_id for c in led.find(ch)] == ["v1"]
    with pytest.raises(LedgerViolation, match="alpha_spent changed"):
        led.append(_cert("v1", rnd=2, alpha_spent=0.2, cumulative_alpha=0.25, content_hash=ch))
    led.append(_cert("v1", "ACCEPT", rnd=2, content_hash=ch))
    with pytest.raises(LedgerViolation, match="terminal"):
        led.append(_cert("v1", "ACCEPT", rnd=3, content_hash=ch))
    led.append(_cert("v1", "REJECT", rnd=3, content_hash=ch))  # the harm canary may follow


def test_cumulative_alpha_cannot_be_walked_back(tmp_path):
    """ADV-major: cumulative_alpha() summed the *latest* alpha_spent, so a 0.0 erased it."""
    led = Ledger(tmp_path / "l.jsonl")
    led.append(_cert("a", alpha_spent=0.05, cumulative_alpha=0.05))
    led.append(_cert("b", alpha_spent=0.03, cumulative_alpha=0.08))
    led.append(_cert("a", "ACCEPT", rnd=2, alpha_spent=0.0, cumulative_alpha=0.08))
    assert led.cumulative_alpha() == pytest.approx(0.08)
    with pytest.raises(ValueError, match="cumulative_alpha"):
        _cert("c", alpha_spent=0.05, cumulative_alpha=0.01)


def test_floor_is_tri_state_and_not_vacuously_satisfied():
    """ADV-major: an empty corpus / never-run candidate / pending flips all read as 'not blocked'."""
    assert ProtectedCorpus().check().verdict == "INCOMPLETE"
    c = ProtectedCorpus(k_stability=2, k_confirm=2, n_confirm=2)
    for t in range(5):
        c.record_incumbent(f"t{t}", True)
        c.record_incumbent(f"t{t}", True)
    v = c.check()
    assert v.verdict == "INCOMPLETE" and not v.blocked and not v.passed and v.coverage == 0.0
    for t in range(5):
        c.record_candidate(f"t{t}", False)  # one failure each: below n_confirm
    v = c.check()
    assert v.verdict == "INCOMPLETE" and v.n_evaluated == 5 and len(v.unevaluated) == 5
    for t in range(5):
        c.record_candidate(f"t{t}", True)
    v = c.check()
    assert v.verdict == "PASS" and v.passed and v.complete and v.coverage == 1.0
    assert ProtectedCorpus(min_protected=10).check().verdict == "INCOMPLETE"
    c.reset_candidate()
    assert c.check().verdict == "INCOMPLETE"  # a reset candidate has no evidence again
    assert set(v.as_dict()) >= {"verdict", "coverage", "complete", "unevaluated", "n_complete"}


def test_rates_need_denominators():
    """ADV-major: 0-of-0 reported as 1.0 (or inf, or True) passed the safety/activation floors."""
    mo = MultiObjective(default_constraints())
    base = {"tokens": 1, "latency": 1, "harness_size": 1}
    inc = dict(base, safety_pass_rate=1.0, activation_rate=1.0, safety_n=40, activation_n=40)
    assert mo.screen(inc, inc).passed
    r = mo.screen(dict(base, safety_pass_rate=1.0, activation_rate=1.0), inc)  # no denominators
    assert not r.passed and len(r.violations) == 2 and all("support" in v for v in r.violations)
    r = mo.screen(dict(inc, safety_n=0, activation_n=0), inc)
    assert not r.passed and len(r.violations) == 2

    hc = HardConstraints([Constraint("safety_pass_rate", "min", 1.0)])
    for bogus in (float("inf"), float("nan"), True, "1.0", None):
        assert hc.check({"safety_pass_rate": bogus}) .violations, bogus
    assert hc.check({"safety_pass_rate": 1.0}).passed
    with pytest.raises(ValueError):
        Constraint("x", "min", 1.0, support_metric="x_n", min_support=0)


def test_activation_alarm_is_per_candidate():
    """ADV-major: one activation event silenced the alarm for every accepted candidate."""
    records = [_cert(f"a{i}", "ACCEPT").as_dict() for i in range(10)]
    f = funnel(records)
    assert any(a.startswith("accepted_never_activated") for a in f.alarms)
    records.append({"type": "event", "candidate_id": "a0", "event": "activated", "payload": {}})
    f = funnel(records)
    assert f.activated == 0  # an empty payload is not evidence
    assert len(f.accepted_unactivated_ids) == 10
    records[-1]["payload"] = {"activated": True}
    f = funnel(records)
    assert f.activated == 1 and len(f.accepted_unactivated_ids) == 9
    assert any(a.startswith("accepted_never_activated") for a in f.alarms)
    for i in range(1, 10):
        records.append({"type": "event", "candidate_id": f"a{i}", "event": "activated", "payload": {"activated": True}})
    assert not any(a.startswith("accepted_never_activated") for a in funnel(records).alarms)


def test_zero_lift_alarm_uses_the_median():
    """ADV-major: a single large claimed lift hid many zero-lift attributions in the mean."""
    records = [_cert(f"a{i}", "ACCEPT").as_dict() for i in range(6)]
    for i in range(5):
        records.append({"type": "event", "candidate_id": f"a{i}", "event": "attributed", "payload": {"lift": 0.0, "cost": 10}})
    records.append({"type": "event", "candidate_id": "a5", "event": "attributed", "payload": {"lift": 5.0, "cost": 10}})
    f = funnel(records)
    assert f.attributed_lift_mean > 0.5 and f.attributed_lift_median == 0.0
    assert any(a.startswith("zero_lift_with_cost") for a in f.alarms)


def test_schedule_reserve_release_does_not_burn_a_round():
    """ADV-minor: a burst of junk proposals permanently starved the loop."""
    s = SpendSchedule(delta0=0.05)
    a = s.reserve()
    s.release()  # the candidate never reached the gate: no evidence was examined
    assert s.rounds_spent == 0 and s.spent() == 0.0 and s.reserve() == a
    s.commit(content_hash("cand-1"))
    assert s.rounds_spent == 1 and s.round_of(content_hash("cand-1")) == 1
    s2 = SpendSchedule.from_dict(json.loads(json.dumps(s.to_dict())))
    assert s2.to_dict() == s.to_dict()
    s2.reserve()
    assert SpendSchedule.from_dict(json.loads(json.dumps(s2.to_dict()))).pending == s2.pending


def test_pareto_tiebreak_is_not_decided_by_submission_order():
    """ADV-minor: exact ties were resolved by dict insertion order."""
    m = {"reward": 0.9, "latency": 1.0, "cost": 1.0}
    ab = pareto_tiebreak({"a": dict(m), "b": dict(m)})
    ba = pareto_tiebreak({"b": dict(m), "a": dict(m)})
    assert ab == ba
    hashes = {"a": content_hash("zzz"), "b": content_hash("aaa")}
    assert pareto_tiebreak({"a": dict(m), "b": dict(m)}, content_hashes=hashes) == pareto_tiebreak(
        {"b": dict(m), "a": dict(m)}, content_hashes=hashes
    )


def test_candidate_monitor_matches_the_ledger_rules():
    """The bench's guard and the ledger's guard must be the same rules."""
    mon = CandidateMonitor()
    mon.accept(_cert("c", n_pairs=10, n_informative=4, evidence_hash="a" * 64))
    with pytest.raises(LedgerViolation):
        mon.validate(_cert("c", rnd=2, n_pairs=3, evidence_hash="b" * 64))
    mon.accept(_cert("c", rnd=2, n_pairs=20, n_informative=9, evidence_hash="b" * 64))
    with pytest.raises(LedgerViolation, match="round"):
        mon.validate(_cert("c", rnd=2, n_pairs=30))


def test_certificate_from_gate_carries_the_audit_fields():
    g = PairedBinaryGate(alpha=ALPHA, betting="mixture", max_pairs=40)
    g.update_many([(0, 1)] * 6)
    c = Certificate.from_gate("cand-1", 1, g, cumulative_alpha=ALPHA, content_hash=content_hash("src"))
    assert c.decision in ("HOLD", "ACCEPT") and c.n_pairs == 6 and c.n_informative == 6
    assert c.evidence_hash == g.evidence_hash and c.algorithm == "PairedBinaryGate/mixture"
    assert json.dumps(c.as_dict(), allow_nan=False)


def test_ledger_exposure_reports_instance_reuse(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(_cert("a", instance_ids=["i1", "i2", "i3"]))
    led.append(_cert("b", instance_ids=["i2", "i3", "i4"]))
    e = led.exposure()
    assert e["instances_recorded"] and e["max_instance_uses"] == 2
    assert e["stale_fraction"]["b"] == pytest.approx(2 / 3)
    assert Ledger(tmp_path / "empty.jsonl").exposure()["instances_recorded"] is False


def test_adversarial_bench_measures_each_attack():
    """ADV-minor: the bench had no adversarial regime, so none of this was measurable."""
    from acceptor import run_adversarial

    res = run_adversarial(seeds=40, n=40, gate_betting=("fixed",), rewind_evals=100)
    by = {a.attack: a for a in res.attacks}
    assert set(by) == {"reorder", "rewind", "select", "repropose", "targeted"}
    for attack in ("reorder", "rewind", "targeted"):
        a = by[attack]
        assert a.naive_rate > 0.5, (attack, a.as_dict())
        assert a.guarded_rate <= 0.15, (attack, a.as_dict())
    assert by["repropose"].naive_rate > by["repropose"].guarded_rate
    assert "attack" in res.table() and json.dumps(res.as_dict())


def test_schedule_round_binds_alpha_to_the_draw(tmp_path):
    """ADV-major: nothing linked the level a gate ran at to a schedule draw."""
    led = Ledger(tmp_path / "l.jsonl", schedule_delta0=0.05)
    s = SpendSchedule(delta0=0.05)
    a1 = s.draw()
    led.append(_cert("c1", alpha_spent=a1, cumulative_alpha=s.spent(), schedule_round=1))
    with pytest.raises(LedgerViolation, match="already used"):
        led.append(_cert("c2", alpha_spent=a1, cumulative_alpha=s.spent(), schedule_round=1))
    with pytest.raises(LedgerViolation, match="schedule level"):
        led.append(_cert("c2", alpha_spent=0.05, cumulative_alpha=0.08, schedule_round=2))
    a2 = s.draw()
    led.append(_cert("c2", alpha_spent=a2, cumulative_alpha=s.spent(), schedule_round=2))
    assert led.cumulative_alpha() == pytest.approx(a1 + a2)


def test_cumulative_alpha_may_not_go_backwards_across_records(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(_cert("a", alpha_spent=0.03, cumulative_alpha=0.03))
    led.append(_cert("b", alpha_spent=0.02, cumulative_alpha=0.05))
    with pytest.raises(LedgerViolation, match="cumulative_alpha went backwards"):
        led.append(_cert("c", alpha_spent=0.01, cumulative_alpha=0.01))


def test_load_gate_dispatches_on_kind():
    """API-minor: a caller holding a blob had to know the class up front."""
    from acceptor import load_gate

    a = PairedBinaryGate(alpha=ALPHA, betting="mixture")
    a.update_many([(0, 1), (1, 0)])
    b = PairedBoundedGate(alpha=ALPHA, betting="ons", mu0=0.1)
    b.update_many([0.3, -0.2])
    for g in (a, b):
        restored = load_gate(g.to_json())
        assert type(restored) is type(g) and restored.to_json() == g.to_json()
    with pytest.raises(ValueError, match="unknown gate kind"):
        load_gate({"kind": "Nope"})
