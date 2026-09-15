# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Certificate ledger: hash chain, tamper detection, resume, funnel alarms."""

import json

import pytest

from acceptor import Certificate, Ledger, funnel, verify_chain
from acceptor.ledger import GENESIS_HASH


def _cert(cid, decision, rnd=1, alpha=0.01, cum=0.01, **kw):
    fields = dict(
        candidate_id=cid,
        round=rnd,
        algorithm="PairedBinaryGate/fixed",
        decision=decision,
        alpha_spent=alpha,
        cumulative_alpha=cum,
        metrics={"wealth": 21.3, "n_pairs": 17},
        evidence_refs=["eval://run/1"],
        evaluator_id="sealed-eval-v1",
        verification_rung="execution",
        closure="human_on",
        note="",
    )
    fields.update(kw)
    return Certificate(**fields)


def test_append_and_verify(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    c1 = led.append(_cert("c1", "ACCEPT"))
    c2 = led.append(_cert("c2", "REJECT", alpha=0.008, cum=0.018))
    ev = led.append_event("c1", "activated", {"activated": True})
    assert c1.prev_hash == GENESIS_HASH and c2.prev_hash == c1.hash and ev.prev_hash == c2.hash
    assert len(c1.hash) == 64 and led.head == ev.hash and len(led) == 3
    ok, idx, msg = led.verify_chain()
    assert ok and idx is None
    assert [c.candidate_id for c in led.certificates()] == ["c1", "c2"]
    assert led.cumulative_alpha() == pytest.approx(0.018)


def test_immutability_and_validation():
    c = _cert("x", "HOLD")
    with pytest.raises(Exception):
        c.decision = "ACCEPT"  # frozen dataclass
    with pytest.raises(ValueError):
        _cert("x", "MAYBE")
    with pytest.raises(ValueError):
        _cert("x", "ACCEPT", verification_rung="vibes")
    with pytest.raises(ValueError):
        _cert("x", "ACCEPT", closure="nobody")


# ---- (f) tamper detection ----------------------------------------------------------------
def test_modifying_one_line_breaks_the_chain(tmp_path):
    path = tmp_path / "ledger.jsonl"
    led = Ledger(path)
    for i in range(5):
        led.append(_cert(f"c{i}", "REJECT" if i % 2 else "ACCEPT"))
    lines = path.read_text().splitlines()
    rec = json.loads(lines[2])
    rec["decision"] = "ACCEPT" if rec["decision"] == "REJECT" else "REJECT"
    lines[2] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")
    ok, idx, msg = Ledger(path).verify_chain()
    assert not ok and idx == 2 and "modified" in msg


def test_deleting_or_reordering_breaks_the_chain(tmp_path):
    path = tmp_path / "ledger.jsonl"
    led = Ledger(path)
    for i in range(4):
        led.append(_cert(f"c{i}", "HOLD"))
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
    ok, idx, _ = Ledger(path).verify_chain()
    assert not ok and idx == 1
    path.write_text("\n".join([lines[0], lines[2], lines[1], lines[3]]) + "\n")
    ok, idx, _ = Ledger(path).verify_chain()
    assert not ok and idx == 1
    assert verify_chain([]) == (True, None, "ok (0 records)")


def test_reopened_ledger_continues_chain(tmp_path):
    path = tmp_path / "ledger.jsonl"
    a = Ledger(path).append(_cert("day1", "HOLD"))
    led = Ledger(path)  # "next day"
    b = led.append(_cert("day1", "ACCEPT", rnd=2))
    assert b.prev_hash == a.hash and led.verify_chain()[0]


def test_funnel_and_alarms(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    for i in range(25):
        led.append(_cert(f"n{i}", "REJECT"))
    f = led.funnel(window=20)
    assert f.proposed == 25 and f.accepted == 0 and f.rejected == 25
    assert any(a.startswith("no_accepts") for a in f.alarms)

    led.append(_cert("good", "HOLD"))
    led.append(_cert("good", "ACCEPT", rnd=2))
    f = led.funnel(window=20)
    assert f.accepted == 1 and f.held == 0 and not any(a.startswith("no_accepts") for a in f.alarms)
    assert any(a.startswith("accepted_never_activated") for a in f.alarms)

    led.append_event("good", "activated", {"activated": True})
    led.append_event("good", "attributed", {"lift": 0.001, "cost": 12.5})
    f = led.funnel(window=20)
    assert f.activated == 1 and f.attributed == 1
    assert not any(a.startswith("accepted_never_activated") for a in f.alarms)
    assert any(a.startswith("zero_lift_with_cost") for a in f.alarms)
    assert f.as_dict()["attributed_cost_total"] == 12.5

    for i in range(12):
        led.append(_cert(f"pending{i}", "HOLD"))
    f = led.funnel(window=20, max_hold_backlog=10)
    assert f.held == 12 and any(a.startswith("hold_backlog") for a in f.alarms)
    assert led.verify_chain()[0]
    assert funnel([]).proposed == 0
