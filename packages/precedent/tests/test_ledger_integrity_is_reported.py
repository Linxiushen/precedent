# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Every command that mentions the ledger must agree about it.

`precedent report` printed "chain ok" over a ledger it had already verified as
broken -- same file, same second, while `precedent init` printed "chain
BROKEN".  The verification ran and was correct; `cmd_report` assigned it to
`_ok` and threw it away, and `render_headline` fell back to a parameter that
defaulted to True.

1,060 tests did not catch it because every one of them asked a single command
whether it was right.  None asked two commands whether they agreed.  That is
the shape of test this file adds: the property is not "report can detect
tampering", it is "no command reports integrity it did not verify".
"""

import json
import subprocess
import sys

import pytest


def _run(*args, **kw):
    return subprocess.run([sys.executable, "-m", "precedent", *args],
                          capture_output=True, text=True, **kw)


def _chain_word(text):
    """The word after 'chain' in the ledger line, or None if absent."""
    for line in text.splitlines():
        if "chain " in line and "ledger" in line:
            return line.split("chain ", 1)[1].rstrip(") ").strip()
    return None


@pytest.fixture
def initialised(tmp_path, demo_home):
    state = tmp_path / "state"
    r = _run("init", "--state-dir", str(state),
             "--claude-home", demo_home.home)
    assert r.returncode == 0, r.stderr
    return state, demo_home.home


def _tamper(ledger_path):
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert lines, "the ledger is empty — this test would prove nothing"
    rec = json.loads(lines[0])
    # A *valid* value, flipped.  Writing garbage here trips schema validation
    # before the chain is ever checked, which is fail-closed but proves
    # nothing about the chain.  HOLD -> ACCEPT is the edit an attacker would
    # actually make: it is legal, it reverses the decision, and only the hash
    # betrays it.
    rec["decision"] = "ACCEPT" if rec.get("decision") != "ACCEPT" else "REJECT"
    lines[0] = json.dumps(rec)
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


COMMANDS = [("init",), ("report", "--no-mine")]


@pytest.mark.parametrize("cmd", COMMANDS, ids=lambda c: c[0])
def test_a_clean_ledger_is_reported_clean(initialised, cmd):
    state, home = initialised
    r = _run(*cmd, "--state-dir", str(state), "--claude-home", home)
    assert r.returncode == 0, r.stderr
    assert _chain_word(r.stdout) == "ok", r.stdout[:600]


@pytest.mark.parametrize("cmd", COMMANDS, ids=lambda c: c[0])
def test_a_tampered_ledger_is_reported_broken(initialised, cmd):
    state, home = initialised
    _tamper(state / "ledger.jsonl")
    r = _run(*cmd, "--state-dir", str(state), "--claude-home", home)
    assert _chain_word(r.stdout) == "BROKEN", (
        f"{cmd[0]} reported {_chain_word(r.stdout)!r} on a tampered ledger\n"
        + r.stdout[:600])


def test_the_two_commands_never_disagree(initialised):
    """The property the single-command tests cannot express."""
    state, home = initialised
    said = {}
    for cmd in COMMANDS:
        r = _run(*cmd, "--state-dir", str(state), "--claude-home", home)
        said[cmd[0]] = _chain_word(r.stdout)
    assert len(set(said.values())) == 1, f"clean ledger, disagreement: {said}"

    _tamper(state / "ledger.jsonl")
    said = {}
    for cmd in COMMANDS:
        r = _run(*cmd, "--state-dir", str(state), "--claude-home", home)
        said[cmd[0]] = _chain_word(r.stdout)
    assert len(set(said.values())) == 1, f"tampered ledger, disagreement: {said}"
    assert set(said.values()) == {"BROKEN"}, said


def test_render_headline_refuses_to_guess():
    """The parameter has no default, so a new caller cannot forget it.

    This is the test that would have prevented the bug, and it is a signature
    test rather than a behaviour test on purpose: the behaviour was correct
    everywhere it was passed.
    """
    import inspect

    from precedent.report import render_digest, render_headline

    for fn in (render_headline, render_digest):
        p = inspect.signature(fn).parameters["ledger_ok"]
        assert p.default is inspect.Parameter.empty, (
            f"{fn.__name__}'s ledger_ok has default {p.default!r} — an "
            f"integrity flag that defaults to a value will be forgotten")
        assert p.kind is inspect.Parameter.KEYWORD_ONLY, fn.__name__


def test_an_unparseable_ledger_never_reports_ok(initialised):
    """The other tamper: a value the schema rejects outright.

    It fails closed -- the command exits non-zero and prints no "chain ok" --
    but it fails closed by raising, so the user sees a traceback rather than
    the sentence that tells them what is wrong.  Pinned here so that the day
    someone makes it graceful, they cannot accidentally make it silent.
    """
    state, home = initialised
    p = state / "ledger.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["decision"] = "NOT-A-VALID-DECISION"
    lines[0] = json.dumps(rec)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for cmd in COMMANDS:
        r = _run(*cmd, "--state-dir", str(state), "--claude-home", home)
        assert _chain_word(r.stdout) != "ok", f"{cmd[0]} said ok: {r.stdout[:400]}"
        assert r.returncode != 0, f"{cmd[0]} exited 0 on a corrupt ledger"
