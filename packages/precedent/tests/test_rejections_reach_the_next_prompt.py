# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""A rejection the user made by hand must reach the next prompt.

README:37 promises "rejected drafts return as negative examples", and both
`precedent retire` and `precedent reject` tell the user so in as many words.
They wrote the rejected rule under the key `rule`; every reader asked for
`draft`.  The *reason* survived and the rule itself rendered as `null`, so the
next prompt said "this was rejected because X" without ever showing what X
applied to.

On the author's machine rejected.jsonl held three rows.  Two came from the
model rejecting its own drafts and worked.  The third -- the only rejection a
human ever made -- was the broken one.

Writers are normalised to `draft` now and `negative_body` reads either, so
rows already on disk keep working.  These tests pin both halves.
"""

import json

import pytest

from precedent import docket
from precedent.llm import load_negatives, negative_body

RULE = {
    "schemaVersion": 1, "hook": "PreToolUse", "tool": "Bash", "match": "all",
    "matchers": [{"type": "input_regex", "field": "command",
                  "regex": "(?i)headless"}],
    "action": "deny", "scope": "global", "id": "p-abcd1234",
    "status": "active", "message": "no headless", "topic": "t-1",
}


@pytest.fixture
def state_with_a_rule(tmp_path):
    from precedent.state import StateDir
    st = StateDir(str(tmp_path / "state"), claude_home=str(tmp_path / "home"))
    st.ensure()
    st.write_precedents([dict(RULE)])
    return st


def test_retiring_a_rule_records_the_rule_itself(state_with_a_rule):
    rc, _ = docket.retire_rule(state_with_a_rule, "p-abcd1234",
                               reason="too broad — 68 false interruptions")
    assert rc == 0
    rows = load_negatives(state_with_a_rule)
    assert rows, "retire wrote no negative example at all"
    body = negative_body(rows[0])
    assert body is not None, (
        "the rule was rejected and the next prompt will render it as null: "
        + json.dumps(rows[0], ensure_ascii=False)[:300])
    assert body.get("id") == "p-abcd1234"
    assert rows[0].get("reason", "").startswith("too broad")


def test_negatives_render_without_null(state_with_a_rule):
    docket.retire_rule(state_with_a_rule, "p-abcd1234", reason="too broad")
    rows = load_negatives(state_with_a_rule)
    rendered = json.dumps(negative_body(rows[0]), ensure_ascii=False)
    assert rendered != "null", "the negative example renders as the literal null"
    assert "headless" in rendered, rendered[:200]


def test_a_legacy_row_written_under_rule_still_reads(tmp_path):
    """Rows already on disk predate the fix and must keep working."""
    assert negative_body({"reason": "x", "rule": {"id": "old"}}) == {"id": "old"}
    assert negative_body({"reason": "x", "draft": {"id": "new"}}) == {"id": "new"}
    # a row carrying both prefers the canonical key
    assert negative_body({"draft": {"id": "new"}, "rule": {"id": "old"}}) == {"id": "new"}
    # and a genuinely empty body stays None rather than becoming {}
    assert negative_body({"reason": "x"}) is None


def test_every_writer_of_rejected_jsonl_uses_the_same_key():
    """The defect was three writers and one reader disagreeing. Pin it.

    Parsed rather than grepped: `"rule":` also appears inside the prompt
    template that tells the model what shape to emit, and flagging that is how
    a guard like this gets deleted for crying wolf.  Only dict literals passed
    to the two functions that actually append to rejected.jsonl count.
    """
    import ast
    import pathlib

    WRITERS = {"_append_rejected", "record_rejected"}
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "precedent"
    offenders = []
    for f in ("docket.py", "llm.py", "improve.py"):
        tree = ast.parse((src / f).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in WRITERS:
                continue
            for arg in node.args:
                dicts = [arg] if isinstance(arg, ast.Dict) else [
                    e for e in ast.walk(arg) if isinstance(e, ast.Dict)]
                for d in dicts:
                    keys = {k.value for k in d.keys
                            if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                    if "rule" in keys and "draft" not in keys:
                        offenders.append(f"{f}:{d.lineno}")
    assert not offenders, (
        "a writer appends to rejected.jsonl under the key `rule`; every "
        "reader asks for `draft`: " + ", ".join(offenders))
