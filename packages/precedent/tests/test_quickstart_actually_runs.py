# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""The six documented steps, run end to end on the tree we ship.

README's "five-minute quickstart" is init -> mine -> compile -> confirm ->
hooks install -> watch it deny.  The fixture home that ships with the repo
contained no corrections at all, so `mine` reported 0, no topic formed, the
docket stayed empty, and steps 2, 3 and 4 dead-ended on the one tree a reader
can actually run them against.  Every step worked; there was simply nothing
for them to work on, which is the kind of failure a per-command test cannot
see and a walk-through catches immediately.

The fixture now plants the README's own worked example — "use uv, not pip",
corrected twice, in two projects, a day apart — with enough quiet Bash calls
after the first correction that the temporal gate has something to measure.

This test is the walk-through.  It asserts the chain produces an enforced rule
that denies the thing and allows the alternative, because "the quickstart
runs" and "the quickstart does what it says" are different claims.
"""

import json
import os
import re
import subprocess
import sys

import pytest


def _run(*args, **kw):
    r = subprocess.run([sys.executable, "-m", "precedent", *args],
                       capture_output=True, text=True, **kw)
    return r


@pytest.fixture
def home_and_state(tmp_path, demo_home):
    return demo_home.home, str(tmp_path / "state")


def test_the_documented_six_steps_reach_an_enforced_rule(home_and_state):
    home, state = home_and_state
    common = ["--state-dir", state, "--claude-home", home]

    r = _run("init", *common)
    assert r.returncode == 0, r.stderr

    r = _run("mine", *common)
    assert r.returncode == 0, r.stderr
    topics = re.findall(r"t-[0-9a-f]{8}", r.stdout)
    assert topics, (
        "step 2 found no topic on the shipped fixture, so steps 3 and 4 have "
        "nothing to act on:\n" + r.stdout[:800])

    r = _run("compile", topics[0], *common)
    assert r.returncode == 0, r.stderr
    assert "PASS" in r.stdout, (
        "the compiled rule did not pass the birth gate on the fixture:\n"
        + r.stdout[:800])
    rules = re.findall(r"p-[0-9a-f]{8}", r.stdout)
    assert rules, r.stdout[:600]
    rule_id = rules[0]

    r = _run("docket", *common)
    assert "1 pending" in r.stdout or "1 条待办" in r.stdout, r.stdout[:400]

    r = _run("docket", "confirm", rule_id, *common)
    assert r.returncode == 0, r.stderr
    with open(os.path.join(state, "precedents.json"), encoding="utf-8") as fh:
        rules_on_disk = json.load(fh)["precedents"]
    assert [x["id"] for x in rules_on_disk if x["status"] == "active"] == [rule_id]

    r = _run("hooks", "install", "claude-code", "--apply", "--i-know", *common)
    assert r.returncode == 0, r.stderr


def _ask(state, home, command):
    payload = json.dumps({
        "session_id": "s", "transcript_path": "/tmp/t.jsonl", "cwd": home,
        "permission_mode": "acceptEdits", "hook_event_name": "PreToolUse",
        "tool_name": "Bash", "tool_input": {"command": command}})
    env = dict(os.environ, PRECEDENT_STATE_DIR=state,
               PRECEDENT_ALLOW_STATE_REDIRECT="1")
    r = subprocess.run(
        [sys.executable, os.path.join(state, "hooks", "pre_tool_use.py"),
         "--precedent-hook", "PreToolUse"],
        input=payload, capture_output=True, text=True, env=env)
    try:
        return (json.loads(r.stdout or "{}").get("hookSpecificOutput")
                or {}).get("permissionDecision")
    except ValueError:
        return None


def test_the_rule_the_quickstart_produces_actually_denies(home_and_state):
    """Running the steps is not the claim; the claim is that it then stops you."""
    home, state = home_and_state
    common = ["--state-dir", state, "--claude-home", home]
    _run("init", *common)
    topic = re.findall(r"t-[0-9a-f]{8}", _run("mine", *common).stdout)[0]
    rule = re.findall(r"p-[0-9a-f]{8}", _run("compile", topic, *common).stdout)[0]
    _run("docket", "confirm", rule, *common)
    _run("hooks", "install", "claude-code", "--apply", "--i-know", *common)

    assert _ask(state, home, "pip install requests") == "deny"
    # the alternative the correction actually named must stay allowed, or the
    # rule is not "use uv, not pip", it is "no package management"
    assert _ask(state, home, "uv add requests") is None
    assert _ask(state, home, "git status --short") is None
