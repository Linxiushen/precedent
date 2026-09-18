# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Enforcement may be turned off. It may not be turned off silently.

`PRECEDENT_STATE_DIR` used to win at run time unconditionally.  Exporting it
to an empty directory made the hook find no precedents, return `{}`, and allow
everything -- with nothing printed anywhere and nothing in the hook log.  On
the author's live machine this took one command to reproduce against the one
rule actually being enforced.

SECURITY.md enumerates four ways enforcement can be defeated.  This was the
cheapest of them and it was not on the list, which is the part that makes it
a defect rather than a documented limitation.

The redirect still exists -- the test suite depends on it -- but it now
requires PRECEDENT_ALLOW_STATE_REDIRECT alongside, and says on stderr when it
declines.  These tests assert the three properties that matter: the bypass is
refused, the refusal is audible, and the opt-in still works so that nobody is
tempted to rip the guard out again.
"""

import json
import os
import subprocess
import sys

import pytest

from precedent import hooks

PAYLOAD = json.dumps({
    "session_id": "s", "transcript_path": "/tmp/t.jsonl", "cwd": "/w",
    "permission_mode": "acceptEdits", "hook_event_name": "PreToolUse",
    "tool_name": "Bash", "tool_input": {"command": "chromium --headless x"},
})

RULE = {
    "schemaVersion": 1, "hook": "PreToolUse", "tool": "Bash", "match": "all",
    "matchers": [{"type": "input_regex", "field": "command",
                  "regex": "(?i)(?<![A-Za-z0-9_])headless(?![A-Za-z0-9_])"}],
    "action": "deny", "scope": "global", "id": "p-test0001",
    "status": "active", "message": "no headless",
}


@pytest.fixture
def enforcing(tmp_path):
    """A state dir with one active deny, and the hook scripts that read it."""
    state = tmp_path / "state"
    (state / "hooks").mkdir(parents=True)
    (state / "precedents.json").write_text(
        json.dumps({"schemaVersion": 1, "precedents": [RULE]}), encoding="utf-8")
    for name, body in hooks.render_scripts(str(state)).items():
        (state / "hooks" / name).write_text(body, encoding="utf-8")
    import shutil

    from precedent import _hooklib
    shutil.copyfile(_hooklib.__file__, str(state / "hooks" / "_plib.py"))
    return state


def _run(enforcing, env_extra=None):
    env = dict(os.environ)
    env.pop("PRECEDENT_STATE_DIR", None)
    env.pop("PRECEDENT_ALLOW_STATE_REDIRECT", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(enforcing / "hooks" / "pre_tool_use.py"),
         "--precedent-hook", "PreToolUse"],
        input=PAYLOAD, capture_output=True, text=True, env=env)


def _decision(out):
    try:
        return (json.loads(out or "{}").get("hookSpecificOutput") or {}
                ).get("permissionDecision")
    except Exception:
        return None


def test_the_rule_denies_at_all(enforcing):
    """Guard for the rest of the file: without this, every test below is vacuous."""
    assert _decision(_run(enforcing).stdout) == "deny"


def test_redirecting_the_state_dir_does_not_disable_the_rule(tmp_path, enforcing):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _run(enforcing, {"PRECEDENT_STATE_DIR": str(empty)})
    assert _decision(r.stdout) == "deny", (
        "one environment variable turned enforcement off: " + r.stdout[:300])


def test_the_refusal_is_audible(tmp_path, enforcing):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _run(enforcing, {"PRECEDENT_STATE_DIR": str(empty)})
    assert "PRECEDENT_ALLOW_STATE_REDIRECT" in r.stderr, r.stderr[:300]
    assert str(empty) in r.stderr


def test_the_opt_in_still_redirects(tmp_path, enforcing):
    """Turning it off on purpose must keep working, or someone will delete the guard."""
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _run(enforcing, {"PRECEDENT_STATE_DIR": str(empty),
                         "PRECEDENT_ALLOW_STATE_REDIRECT": "1"})
    assert _decision(r.stdout) is None, r.stdout[:300]
    assert "PRECEDENT_ALLOW_STATE_REDIRECT" not in r.stderr


def test_a_redirect_to_the_same_path_is_not_a_redirect(enforcing):
    """No spurious warning for an env var that agrees with the baked-in path."""
    r = _run(enforcing, {"PRECEDENT_STATE_DIR": str(enforcing)})
    assert _decision(r.stdout) == "deny"
    assert "PRECEDENT_ALLOW_STATE_REDIRECT" not in r.stderr


def test_the_claude_home_redirect_is_guarded_too(tmp_path, enforcing):
    other = tmp_path / "other-home"
    other.mkdir()
    r = _run(enforcing, {"PRECEDENT_CLAUDE_HOME": str(other)})
    assert "PRECEDENT_ALLOW_STATE_REDIRECT" in r.stderr, r.stderr[:300]
