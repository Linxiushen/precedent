# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""``precedent audit --share``: the card, and the proof that it leaks nothing.

The fixture (``scripts/make_fixture_home.py``, the same tree the README's card
comes from) plants four things a share card must never contain:

    a credential      ``sk-demo-NOTREAL-…`` in a human turn
    a home path       the absolute path of the fake Claude home
    a project name    ``-Users-demo-code-acme-payments``
    a session id      ``a1b2c3d4-1111-4111-8111-…``

Every test here asserts on the *rendered bytes*, not on an internal flag: the
only claim worth making is that the string is not in the output.
"""

from __future__ import annotations

import json
import os

import pytest
from precedent import i18n, share
from precedent.cli import main
from precedent.scrub import findings as scrub_findings
from precedent.share import (ALARM_CODES, ARTIFACT_KINDS, build_card, leaks,
                             project_labels, render_card, secrets_of, shareable,
                             session_labels)
from precedent.state import StateDir


def _card(demo_home, **kw):
    from precedent.cli import _run_scan
    state = StateDir.open(demo_home.state_dir, demo_home.home, create=True)
    result = _run_scan(state, project=None, last_n=None)
    return result, state, build_card(result, state, **kw)


# --------------------------------------------------------------------------
# the four planted secrets
# --------------------------------------------------------------------------

def test_the_share_card_contains_none_of_the_four_planted_secrets(
        demo_home, real_home_canary):
    result, state, card = _card(demo_home)
    payload = shareable(card)
    for lang in i18n.LANGS:
        text = render_card(payload, lang=lang)
        blob = text + json.dumps(payload, ensure_ascii=False)
        assert demo_home.token not in blob, lang
        assert demo_home.email not in blob, lang
        assert demo_home.home not in blob, lang
        assert demo_home.project_a not in blob, lang
        assert demo_home.project_b not in blob, lang
        for sid in demo_home.sessions:
            assert sid not in blob, lang
            assert sid[:8] not in blob, lang
        # ...and the names it replaced them with ARE there
        assert "project-A" in blob and "s1" in blob
    real_home_canary()


def test_the_secrets_really_are_in_the_tree_the_card_was_built_from(demo_home):
    """The negative tests above are worthless if the fixture is clean."""
    hay = ""
    for dirpath, _dirs, files in os.walk(demo_home.home):
        for fn in files:
            with open(os.path.join(dirpath, fn), encoding="utf-8",
                      errors="replace") as fh:
                hay += fh.read()
    assert demo_home.token in hay
    assert demo_home.email in hay
    assert demo_home.project_a in hay
    assert demo_home.sessions[0] in hay


def test_the_full_report_does_quote_the_tree_so_the_card_is_the_difference(
        demo_home, tmp_path, real_home_canary):
    """`report` is for you and carries paths; `audit --share` is the other one."""
    md = str(tmp_path / "digest.md")
    rc = main(["report", "--md", md, "--quiet", "--claude-home", demo_home.home,
               "--state-dir", demo_home.state_dir])
    assert rc == 0
    digest = open(md, encoding="utf-8").read()
    assert demo_home.home in digest            # the digest is a local document
    assert demo_home.token not in digest       # …but scrub still masks the key
    real_home_canary()


# --------------------------------------------------------------------------
# the card is counts-only by construction
# --------------------------------------------------------------------------

def test_every_string_in_the_card_comes_from_a_closed_vocabulary(demo_home):
    _result, _state, card = _card(demo_home)
    payload = shareable(card)
    allowed = (set(ARTIFACT_KINDS) | {"other"} | set(ALARM_CODES)
               | {"precedent", share.CARD_ID, "0.1.0", card["generatedOn"],
                  "global"})

    def walk(node, path="$"):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            ok = (node in allowed
                  or node.startswith("project-") or node.startswith("s")
                  and node[1:].isdigit()
                  or node.endswith("%") or node == "n/a")
            assert ok, f"free-form string {node!r} at {path}"
        else:
            assert isinstance(node, (int, bool)), f"{type(node)} at {path}"

    walk(payload)


def test_local_block_carries_the_mapping_and_share_drops_it(demo_home):
    _result, _state, card = _card(demo_home)
    assert card["local"]["claudeHome"] == demo_home.home
    slugs = {p["slug"] for p in card["local"]["projects"]}
    assert demo_home.project_a in slugs
    assert "local" not in shareable(card)
    assert demo_home.project_a in render_card(card, local=True)
    assert demo_home.project_a not in render_card(shareable(card), local=False)


# --------------------------------------------------------------------------
# --verify
# --------------------------------------------------------------------------

def test_verify_refuses_to_print_when_something_survives(demo_home, capsys,
                                                         monkeypatch):
    """Break the renderer on purpose; --verify must refuse, not print."""
    real = share.render_card

    def leaky(card, lang=None, local=False, verified=None):
        return real(card, lang=lang, local=local, verified=verified) + \
            f"\ndebug: {demo_home.home}\n"

    monkeypatch.setattr("precedent.cli.render_card", leaky)
    rc = main(["audit", "--share", "--claude-home", demo_home.home,
               "--state-dir", demo_home.state_dir])
    cap = capsys.readouterr()
    assert rc == 2
    assert cap.out == ""                       # nothing was printed
    assert "REFUSING" in cap.err
    # the refusal names the kind and the offset, never the content
    assert demo_home.home not in cap.err


def test_no_verify_prints_the_same_card_minus_the_receipt_line(demo_home, capsys):
    args = ["--claude-home", demo_home.home, "--state-dir", demo_home.state_dir]
    assert main(["audit", "--share"] + args) == 0
    verified = capsys.readouterr().out
    assert main(["audit", "--share", "--no-verify"] + args) == 0
    plain = capsys.readouterr().out
    assert "verified" in verified and "verified" not in plain
    assert plain.strip() in verified


def test_leaks_catches_the_shapes_scrub_allowlists(demo_home):
    """scrub keeps UUIDs and p-ids on purpose; a card must not have them."""
    uuid = "a1b2c3d4-1111-4111-8111-aaaaaaaaaaaa"
    assert scrub_findings(uuid) == []          # scrub allowlists it, by design
    assert [f["kind"] for f in leaks(uuid)] == ["session_uuid"]
    assert any(f["kind"] == "rule_id" for f in leaks("rule p-1a2b3c4d fired"))
    assert any(f["kind"] == "abs_path" for f in leaks("see /Users/leon/.claude"))
    assert any(f["kind"] == "home_path" for f in leaks("see ~/.precedent/x"))
    assert any(f["kind"] == "token" for f in leaks("key sk-abcdefghijklmnop"))
    assert leaks("41 of 58 citable artifacts (71%)") == []


def test_secrets_of_does_not_cry_wolf_on_the_cards_own_vocabulary(demo_home):
    result, state, card = _card(demo_home)
    secrets = secrets_of(result, state)
    assert demo_home.project_a in secrets
    assert demo_home.home in secrets
    assert demo_home.sessions[0] in secrets
    # the words the card legitimately prints are not treated as secrets
    for word in ("skill", "memory", "MEMORY.md", "CLAUDE.md", "rule"):
        assert word not in secrets
    assert leaks(render_card(shareable(card)), secrets) == []


# --------------------------------------------------------------------------
# the labels
# --------------------------------------------------------------------------

def test_labels_are_positional_and_carry_no_information_about_the_name():
    labels = project_labels(["-zzz-last", "-aaa-first", None, "-zzz-last"])
    assert labels == {"-zzz-last": "project-A", "-aaa-first": "project-B",
                      None: "global"}
    assert session_labels(["u2", "u1", "u2"]) == {"u2": "s1", "u1": "s2"}
    many = project_labels([f"-p{i}" for i in range(28)])
    assert many["-p25"] == "project-Z" and many["-p26"] == "project-AA"


# --------------------------------------------------------------------------
# the CLI surface
# --------------------------------------------------------------------------

def test_json_is_machine_readable_and_matches_the_text(demo_home, capsys):
    args = ["--claude-home", demo_home.home, "--state-dir", demo_home.state_dir]
    assert main(["audit", "--share", "--json"] + args) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["card"] == share.CARD_ID
    assert "local" not in doc
    assert doc["corpus"]["sessions"] == 3
    assert doc["findings"]["truncated"]["n"] == 1
    assert doc["findings"]["staleIndex"]["n"] == 1
    assert doc["findings"]["nearDuplicates"]["pairs"] == 1
    assert doc["findings"]["unattendedWrites"]["bypass"] == 2
    assert main(["audit", "--share"] + args) == 0
    text = capsys.readouterr().out
    assert str(doc["findings"]["neverCited"]["n"]) in text


def test_the_card_is_one_screen(demo_home, capsys):
    assert main(["audit", "--share", "--claude-home", demo_home.home,
                 "--state-dir", demo_home.state_dir]) == 0
    lines = capsys.readouterr().out.rstrip("\n").split("\n")
    assert len(lines) <= 24, f"{len(lines)} lines is not one screen"
    assert max(i18n.display_width(x) for x in lines) <= 100


def test_audit_never_writes_into_the_claude_home(demo_home, real_home_canary):
    from conftest import tree_fingerprint
    before = tree_fingerprint(demo_home.home)
    for argv in (["audit"], ["audit", "--share"], ["audit", "--share", "--json"]):
        assert main(argv + ["--claude-home", demo_home.home,
                            "--state-dir", demo_home.state_dir]) == 0
    assert tree_fingerprint(demo_home.home) == before
    real_home_canary()


def test_audit_works_before_init_has_ever_run(demo_home, tmp_path, capsys):
    """A stranger's first command should not require a second one."""
    fresh = str(tmp_path / "never-created")
    assert main(["audit", "--share", "--claude-home", demo_home.home,
                 "--state-dir", fresh]) == 0
    assert "share card" in capsys.readouterr().out
    assert not os.path.exists(fresh)


@pytest.mark.parametrize("lang,needle", [("en", "never cited"),
                                         ("zh", "从未被引用")])
def test_the_card_speaks_both_languages(demo_home, capsys, lang, needle):
    assert main(["audit", "--share", "--lang", lang, "--claude-home",
                 demo_home.home, "--state-dir", demo_home.state_dir]) == 0
    assert needle in capsys.readouterr().out
