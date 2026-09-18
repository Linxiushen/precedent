# Copyright 2026 The precedent authors.
# SPDX-License-Identifier: Apache-2.0
"""② ACCEPT — the wiring between paired outcomes and ``acceptor``.

These tests are about the *wiring*, not the statistics: ``acceptor`` has 112
tests of its own for the martingale.  What is asserted here is that precedent
hands it the right stream (floor and gate disjoint, ties included, order owned
by the acceptor), spells the decision the ledger's vocabulary, writes one
hash-chained certificate, puts the result on the docket, and **never applies
anything**.
"""

from __future__ import annotations

import json
import os

import pytest

from acceptor import Certificate, SpendSchedule
from conftest import tree_fingerprint
from precedent.accept import (DEFAULT_HARM_ALPHA, accept_outcomes,
                              load_protected, load_schedule, render_acceptance,
                              save_protected, verify_acceptance)
from precedent.docket import build_entries, confirm, reject, render_batch
from precedent.examine import Candidate, PairedOutcome
from precedent.proposals import read_proposals
from precedent.state import StateDir


def _state(tmp_path, name="state"):
    home = tmp_path / "claude"
    home.mkdir(exist_ok=True)
    (home / "settings.json").write_text("{}\n", encoding="utf-8")
    return StateDir.open(str(tmp_path / name), str(home), create=True)


def _cand(cid="cand:x", chash=None, surface="skill"):
    return Candidate(id=cid, surface=surface, name="x",
                     content_hash=chash or ("a" * 64))


def _pairs(spec, case_prefix="c"):
    """``[(without, with), ...]`` -> outcomes, one case each."""
    return [PairedOutcome(case=f"{case_prefix}{i}", run_index=0, without=o,
                          with_=w) for i, (o, w) in enumerate(spec)]


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------

def test_a_clear_win_is_accepted_and_certified(tmp_path, real_home_canary):
    st = _state(tmp_path)
    res = accept_outcomes(st, _cand(), _pairs([(0, 1)] * 12))
    assert res.decision == "ACCEPT"
    assert res.n_informative == 12
    assert res.certificate["decision"] == "ACCEPT"
    assert res.certificate["hash"]
    assert res.certificate["algorithm"].startswith("PairedBinaryGate")
    assert res.certificate["evidence_hash"]
    ok, _idx, _msg = st.ledger().verify_chain()
    assert ok
    real_home_canary()


def test_a_clear_loss_is_rejected_by_the_harm_martingale(tmp_path):
    st = _state(tmp_path)
    res = accept_outcomes(st, _cand(), _pairs([(1, 0)] * 12))
    assert res.decision == "REJECT"
    assert res.gate["harm_detected"] is True
    assert res.certificate["decision"] == "REJECT"


def test_all_ties_cost_no_alpha_and_say_no_effect(tmp_path):
    st = _state(tmp_path)
    res = accept_outcomes(st, _cand(), _pairs([(1, 1)] * 6))
    assert res.n_informative == 0
    assert res.schedule["rounds_spent"] == 0
    assert res.schedule["roundUsed"] == 0
    assert res.certificate["alpha_spent"] == 0.0
    assert any("no effect" in n.lower() for n in res.notes)
    assert any("released" in n for n in res.notes)


def test_a_mixed_stream_that_has_not_decided_is_hold(tmp_path):
    st = _state(tmp_path)
    res = accept_outcomes(st, _cand(), _pairs([(0, 1), (1, 0), (0, 1)]))
    assert res.decision in ("HOLD", "NSF")
    assert res.certificate["decision"] == res.decision


def test_every_decision_is_a_word_the_ledger_knows(tmp_path):
    from acceptor.ledger import DECISIONS
    st = _state(tmp_path)
    for i, spec in enumerate(([(0, 1)] * 12, [(1, 0)] * 12, [(1, 1)] * 6,
                              [(0, 1), (1, 0)])):
        res = accept_outcomes(st, _cand(cid=f"c{i}", chash=str(i) * 64),
                              _pairs(spec, case_prefix=f"s{i}-"))
        assert res.decision in DECISIONS


# --------------------------------------------------------------------------
# ties are free
# --------------------------------------------------------------------------

def test_free_ties_are_counted_but_move_no_wealth(tmp_path):
    st = _state(tmp_path)
    free = [PairedOutcome(case=f"t{i}", run_index=0, without=0, with_=0,
                          executed=False, tie_reason="not loadable")
            for i in range(20)]
    res = accept_outcomes(st, _cand(), free + _pairs([(0, 1)] * 12))
    assert res.n_free_ties == 20
    assert res.n_pairs == 32
    assert res.n_informative == 12
    assert res.decision == "ACCEPT"
    # 20 ties in the stream must not change the verdict a 12-0 run produces
    st2 = _state(tmp_path, "state2")
    bare = accept_outcomes(st2, _cand(), _pairs([(0, 1)] * 12))
    assert res.gate["wealth"] == pytest.approx(bare.gate["wealth"])


# --------------------------------------------------------------------------
# the spend schedule
# --------------------------------------------------------------------------

def test_one_examine_run_spends_one_cths_round(tmp_path):
    st = _state(tmp_path)
    fresh = SpendSchedule()
    a = accept_outcomes(st, _cand(cid="a", chash="a" * 64), _pairs([(0, 1)] * 12))
    b = accept_outcomes(st, _cand(cid="b", chash="b" * 64),
                        _pairs([(0, 1)] * 12, case_prefix="d"))
    assert a.alpha == pytest.approx(fresh.next_alpha(1))
    assert b.alpha == pytest.approx(fresh.next_alpha(2))
    assert b.alpha < a.alpha
    assert load_schedule(st).rounds_spent == 2


def test_the_same_content_hash_reuses_its_round_instead_of_buying_a_fresh_alpha(
        tmp_path):
    st = _state(tmp_path)
    first = accept_outcomes(st, _cand(chash="c" * 64), _pairs([(0, 1)] * 12))
    second = accept_outcomes(st, _cand(chash="c" * 64),
                             _pairs([(0, 1)] * 12, case_prefix="e"))
    assert second.alpha == pytest.approx(first.alpha)
    assert load_schedule(st).rounds_spent == 1
    assert any("already holds schedule round" in n for n in second.notes)


def test_cumulative_alpha_never_goes_backwards(tmp_path):
    st = _state(tmp_path)
    seen = []
    for i in range(3):
        res = accept_outcomes(st, _cand(cid=f"k{i}", chash=str(i) * 64),
                              _pairs([(0, 1)] * 12, case_prefix=f"p{i}-"))
        seen.append(res.certificate["cumulative_alpha"])
    assert seen == sorted(seen)
    assert verify_acceptance(st)["ok"] is True


# --------------------------------------------------------------------------
# the task floor
# --------------------------------------------------------------------------

def test_the_floor_starts_incomplete_and_an_accept_says_so(tmp_path):
    st = _state(tmp_path)
    res = accept_outcomes(st, _cand(), _pairs([(0, 1)] * 12))
    assert res.floor["verdict"] == "INCOMPLETE"
    assert res.decision == "ACCEPT"
    assert any("NOT a" in n and "no-regression" in n for n in res.notes)


def test_require_floor_turns_an_unproven_accept_into_a_hold(tmp_path):
    st = _state(tmp_path)
    res = accept_outcomes(st, _cand(), _pairs([(0, 1)] * 12),
                          require_floor=True)
    assert res.decision == "HOLD"
    assert "--require-floor" in res.reason


def test_a_regression_on_a_protected_case_blocks_however_good_the_gate_is(
        tmp_path):
    st = _state(tmp_path)
    corpus = load_protected(st)
    for _ in range(2):                       # the incumbent passes it k/k
        corpus.record_incumbent("prot1", True)
    save_protected(st, corpus)

    outcomes = _pairs([(0, 1)] * 12)
    outcomes += [PairedOutcome(case="prot1", run_index=i, without=1, with_=0)
                 for i in range(2)]
    res = accept_outcomes(st, _cand(), outcomes)
    assert res.decision == "BLOCKED"
    assert res.floor["verdict"] == "BLOCKED"
    assert "prot1" in res.floor["confirmed_flips"]
    assert res.certificate["decision"] == "BLOCKED"


def test_protected_cases_are_kept_out_of_the_gate_stream(tmp_path):
    """acceptor's own docs: feeding the protected corpus to the gate makes the
    harm martingale fire ~100 % of the time on an equal-quality candidate."""
    st = _state(tmp_path)
    corpus = load_protected(st)
    for _ in range(2):
        corpus.record_incumbent("prot1", True)
    save_protected(st, corpus)

    outcomes = _pairs([(0, 1)] * 12)
    outcomes += [PairedOutcome(case="prot1", run_index=i, without=1, with_=1)
                 for i in range(2)]
    res = accept_outcomes(st, _cand(), outcomes)
    assert res.n_floor_pairs == 2
    assert res.n_pairs == 12                 # the two protected pairs are not here
    assert "prot1#0" not in res.order["instance_ids"]


def test_this_runs_baseline_arm_builds_tomorrows_protected_corpus(tmp_path):
    st = _state(tmp_path)
    accept_outcomes(st, _cand(), [
        PairedOutcome(case="c1", run_index=i, without=1, with_=1)
        for i in range(2)])
    assert load_protected(st).protected_tasks() == ["c1"]


# --------------------------------------------------------------------------
# the acceptor owns the order
# --------------------------------------------------------------------------

def test_the_evaluation_order_is_committed_and_recorded(tmp_path):
    st = _state(tmp_path)
    res = accept_outcomes(st, _cand(), _pairs([(0, 1)] * 5))
    assert res.order["instance_set_hash"]
    assert set(res.order["instance_ids"]) == {f"c{i}#0" for i in range(5)}
    assert res.certificate["instance_set_hash"] == res.order["instance_set_hash"]
    assert res.certificate["instance_ids"] == res.order["instance_ids"]


def test_a_reordered_stream_produces_the_same_committed_order(tmp_path):
    st_a = _state(tmp_path, "a")
    st_b = _state(tmp_path, "b")
    pairs = _pairs([(0, 1), (1, 0), (0, 1), (1, 1)])
    a = accept_outcomes(st_a, _cand(), pairs)
    b = accept_outcomes(st_b, _cand(), list(reversed(pairs)))
    # the order is derived from the candidate's content hash, not from the
    # order the proposer happened to hand them over in
    assert a.order["instance_ids"] == b.order["instance_ids"]
    assert a.gate["evidence_hash"] == b.gate["evidence_hash"]


# --------------------------------------------------------------------------
# the docket: never auto-apply
# --------------------------------------------------------------------------

def test_an_accept_lands_on_the_docket_and_applies_nothing(tmp_path,
                                                           real_home_canary):
    st = _state(tmp_path)
    target = tmp_path / "skills" / "x" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\nname: x\ndescription: d\n---\n\nold body\n",
                      encoding="utf-8")
    before = target.read_text(encoding="utf-8")

    res = accept_outcomes(st, _cand(), _pairs([(0, 1)] * 12),
                          declared_paths=[str(target)],
                          hypothesis="running the tests helps")
    assert res.decision == "ACCEPT"
    entries = [e for e in build_entries(st) if e["kind"] == "proposal"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["status"] == "pending"
    assert entry["gateVerdict"] == "ACCEPT"
    assert entry["raw"]["applied"] is False
    assert target.read_text(encoding="utf-8") == before

    rc, lines = confirm(st, entry["id"])
    assert rc == 0
    blob = "\n".join(lines)
    assert "没有、也不会替你写这个文件" in blob
    assert str(target) in blob
    assert target.read_text(encoding="utf-8") == before   # still untouched
    real_home_canary()


def test_a_rejected_proposal_becomes_a_negative_example(tmp_path):
    st = _state(tmp_path)
    res = accept_outcomes(st, _cand(), _pairs([(1, 0)] * 12),
                          surface="skill", declared_paths=["/tmp/x/SKILL.md"])
    entry = [e for e in build_entries(st) if e["kind"] == "proposal"][0]
    rc, lines = reject(st, entry["id"], reason="太宽了")
    assert rc == 0
    rows = [json.loads(l) for l in open(st.rejected_path, encoding="utf-8")
            if l.strip()]
    assert rows[-1]["reason"] == "太宽了"
    assert rows[-1]["draft"]["surface"] == "skill"


def test_confirming_a_gate_rejected_proposal_needs_force(tmp_path):
    st = _state(tmp_path)
    accept_outcomes(st, _cand(), _pairs([(1, 0)] * 12))
    entry = [e for e in build_entries(st) if e["kind"] == "proposal"][0]
    rc, lines = confirm(st, entry["id"])
    assert rc == 1
    assert "PROCTOR" in "\n".join(lines)
    rc2, _ = confirm(st, entry["id"], force=True)
    assert rc2 == 0


def test_the_batch_digest_lists_proposals(tmp_path):
    st = _state(tmp_path)
    accept_outcomes(st, _cand(), _pairs([(0, 1)] * 12), surface="skill",
                    declared_paths=["/tmp/p/SKILL.md"])
    md = render_batch(st, build_entries(st))
    assert "## Proposals" in md
    assert "never auto-applied" in md


# --------------------------------------------------------------------------
# read-only
# --------------------------------------------------------------------------

def test_accept_never_writes_into_the_claude_home(tmp_path, real_home_canary):
    st = _state(tmp_path)
    before = tree_fingerprint(st.claude_home)
    accept_outcomes(st, _cand(), _pairs([(0, 1)] * 12))
    assert tree_fingerprint(st.claude_home) == before
    real_home_canary()


def test_the_render_names_the_numbers_it_decided_on(tmp_path):
    st = _state(tmp_path)
    res = accept_outcomes(st, _cand(), _pairs([(0, 1)] * 12))
    md = render_acceptance(res)
    assert "ACCEPT" in md
    assert "不一致 12" in md
    assert "伤害鞅" in md
    assert "TaskFloor" in md
    assert "不会自动应用" in md


def test_persist_false_writes_nothing_at_all(tmp_path):
    st = _state(tmp_path)
    res = accept_outcomes(st, _cand(), _pairs([(0, 1)] * 12), persist=False)
    assert res.decision == "ACCEPT"
    assert res.certificate is None and res.proposal_id is None
    assert read_proposals(st) == []
    assert load_schedule(st).rounds_spent == 0


# --------------------------------------------------------------------------
# adversarial: the audit has to notice a rewound wealth process
# --------------------------------------------------------------------------

def test_verify_acceptance_catches_a_hand_rewound_certificate(tmp_path):
    """Anytime validity covers *stopping*, not restoring an earlier snapshot.

    ``precedent`` keeps lifecycle events, docket decisions and gate
    certificates in one chain, so the ledger runs non-strict — which is exactly
    why ``verify_acceptance`` exists: the anti-rewind fields are written on
    every gate certificate and replayed through ``acceptor``'s own monitor.
    """
    st = _state(tmp_path)
    real = accept_outcomes(st, _cand(), _pairs([(0, 1)] * 12))
    assert verify_acceptance(st)["ok"] is True

    rewound = Certificate(
        candidate_id=real.certificate["candidate_id"],
        round=real.certificate["round"] + 1,
        algorithm=real.certificate["algorithm"], decision="ACCEPT",
        alpha_spent=real.certificate["alpha_spent"],
        cumulative_alpha=real.certificate["cumulative_alpha"],
        content_hash=real.certificate["content_hash"],
        evidence_hash="deadbeef", prev_evidence_hash="deadbeef",
        n_pairs=2, n_informative=2,            # fewer pairs than last time
        schedule_round=real.certificate["schedule_round"])
    st.ledger().append(rewound)

    got = verify_acceptance(st)
    assert got["ok"] is False
    assert any("rewound" in p or "n_pairs went backwards" in p
               for p in got["problems"])


def test_two_candidates_cannot_share_one_schedule_round(tmp_path):
    st = _state(tmp_path)
    a = accept_outcomes(st, _cand(cid="a", chash="a" * 64), _pairs([(0, 1)] * 12))
    forged = Certificate(
        candidate_id="b", round=10, algorithm="PairedBinaryGate/mixture",
        decision="ACCEPT", alpha_spent=a.certificate["alpha_spent"],
        cumulative_alpha=a.certificate["cumulative_alpha"],
        content_hash="z" * 64, schedule_round=a.certificate["schedule_round"],
        n_pairs=12, n_informative=12)
    st.ledger().append(forged)
    got = verify_acceptance(st)
    assert got["ok"] is False
    assert any("one round funds one hypothesis" in p for p in got["problems"])


def test_proposals_are_counted_in_the_audit_funnel(tmp_path):
    """A queue the nightly loop fills and the funnel does not show is the
    exact failure mode the funnel exists to catch."""
    from precedent.audit import funnel, hook_health
    from precedent.docket import confirm as docket_confirm
    st = _state(tmp_path)
    accept_outcomes(st, _cand(), _pairs([(0, 1)] * 12), surface="skill",
                    declared_paths=["/tmp/q/SKILL.md"])
    entries = build_entries(st)
    fun = funnel(st, entries, {"installed": False}, hook_health(st))
    assert fun["stages"]["proposed"] == 1
    assert fun["stages"]["accepted"] == 0

    pid = [e for e in entries if e["kind"] == "proposal"][0]["id"]
    docket_confirm(st, pid)
    fun2 = funnel(st, build_entries(st), {"installed": False}, hook_health(st))
    assert fun2["accepted"]["proposals"] == [pid]
    assert fun2["stages"]["accepted"] == 1
