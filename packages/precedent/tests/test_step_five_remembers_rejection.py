# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Step (5): a rule you threw out does not walk back in.

The loop is propose -> accept -> enforce -> audit -> re-evolve, and the claim
that makes it a loop rather than a pipeline is that step (5) feeds what was
learned back in.  It did -- to the proposer.  Rejected drafts became negative
examples in the next prompt.  The *acceptor* never looked.

So: retire a rule for being too broad, let it be proposed again, and
`confirm` walked it back to `active` with rc=0 and no mention of the
rejection.  The birth gate cannot catch this by construction: the rule passed
the gate the first time, which is why it was enforced at all.  And the rule a
user is most likely to see proposed again is precisely the one they just
threw out, because the correction that produced it is still sitting in the
transcript.

`prior_rejection` closes it.  Matching is by id first and then by behaviour,
because a re-compile that rewords the message and re-derives a different id
produces a rule that interrupts exactly the same calls.
"""

import json
import os

import pytest

from precedent import docket
from precedent.state import StateDir

BIRTH = {"gate": "temporal-birth-gate/v1", "verdict": "PASS",
         "counts": "hit 1/1 · after-t0 false 0/50",
         "t0": "2026-09-01T00:00:00Z", "hit": True}

RULE = {"schemaVersion": 1, "hook": "PreToolUse", "tool": "Bash", "match": "all",
        "matchers": [{"type": "input_regex", "field": "command",
                      "regex": "(?i)headless"}],
        "action": "deny", "scope": "global", "id": "p-abcd1234",
        "status": "active", "message": "no headless", "topic": "t-1",
        "birth": BIRTH}

REASON = "too broad — would have interrupted 68 legitimate calls"


@pytest.fixture
def after_retiring(tmp_path):
    """A state where RULE was enforced and then thrown out by hand."""
    st = StateDir(str(tmp_path / "state"), claude_home=str(tmp_path / "home"))
    st.ensure()
    st.write_precedents([dict(RULE)])
    rc, _ = docket.retire_rule(st, "p-abcd1234", reason=REASON)
    assert rc == 0
    assert [r["status"] for r in st.precedents()] == ["retired"]
    return st


def _propose(state, rule):
    with open(state.path("candidates.json"), "w", encoding="utf-8") as fh:
        json.dump({"schemaVersion": 1,
                   "candidates": [dict(rule, status="candidate")]}, fh)


def _status(state, rule_id):
    return next((r["status"] for r in state.precedents() if r["id"] == rule_id), None)


def test_the_same_rule_does_not_come_back(after_retiring):
    _propose(after_retiring, RULE)
    rc, msgs = docket.confirm_rule(after_retiring, "p-abcd1234")
    assert rc == 1, "a retired rule was confirmed straight back to active"
    assert _status(after_retiring, "p-abcd1234") == "retired"
    text = " ".join(str(m) for m in msgs)
    assert REASON in text, "the refusal did not quote the reason: " + text[:300]


def test_a_reworded_rule_with_the_same_behaviour_does_not_come_back(after_retiring):
    """The bypass a rewrite would find: new id, new message, same interruptions."""
    sneaky = dict(RULE, id="p-99999999", message="please do not use headless")
    _propose(after_retiring, sneaky)
    rc, msgs = docket.confirm_rule(after_retiring, "p-99999999")
    assert rc == 1, "the same rule under a new id walked back in"
    assert _status(after_retiring, "p-99999999") is None


def test_force_still_lets_the_user_overrule_themselves(after_retiring):
    """A guard the user cannot get past is a guard they will delete."""
    _propose(after_retiring, RULE)
    rc, _ = docket.confirm_rule(after_retiring, "p-abcd1234", force=True)
    assert rc == 0
    assert _status(after_retiring, "p-abcd1234") == "active"


def test_an_unrelated_rule_is_not_caught_by_the_memory(after_retiring):
    """False positives here would make `confirm` useless. Different matcher, different rule."""
    other = dict(RULE, id="p-11112222",
                 matchers=[{"type": "input_regex", "field": "command",
                            "regex": "(?i)rm -rf"}])
    _propose(after_retiring, other)
    rc, msgs = docket.confirm_rule(after_retiring, "p-11112222")
    assert rc == 0, " ".join(str(m) for m in msgs)[:300]
    assert _status(after_retiring, "p-11112222") == "active"


def test_a_different_scope_is_a_different_rule(after_retiring):
    """The documented way out: re-scope it rather than re-propose it.

    This is the exact move the README tells the user to make after a rule is
    retired for being too broad, so it must not be blocked.
    """
    scoped = dict(RULE, id="p-33334444", scope="project",
                  cwd_glob="/home/me/one-project*")
    _propose(after_retiring, scoped)
    rc, msgs = docket.confirm_rule(after_retiring, "p-33334444")
    assert rc == 0, " ".join(str(m) for m in msgs)[:300]
    assert _status(after_retiring, "p-33334444") == "active"


def test_a_rejection_written_under_the_old_key_still_counts(tmp_path):
    """Rows on disk from before 2026-09-18 used `rule`, not `draft`."""
    st = StateDir(str(tmp_path / "state"), claude_home=str(tmp_path / "home"))
    st.ensure()
    with open(st.path("rejected.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "p-abcd1234", "at": "2026-09-15T00:00:00Z",
                             "reason": REASON, "source": "retire",
                             "rule": dict(RULE)}, ensure_ascii=False) + "\n")
    _propose(st, RULE)
    rc, msgs = docket.confirm_rule(st, "p-abcd1234")
    assert rc == 1, "a legacy rejection row was ignored"
    assert REASON in " ".join(str(m) for m in msgs)


def test_no_rejection_file_at_all_is_not_an_error(tmp_path):
    st = StateDir(str(tmp_path / "state"), claude_home=str(tmp_path / "home"))
    st.ensure()
    assert not os.path.exists(st.path("rejected.jsonl"))
    _propose(st, RULE)
    rc, _ = docket.confirm_rule(st, "p-abcd1234")
    assert rc == 0
