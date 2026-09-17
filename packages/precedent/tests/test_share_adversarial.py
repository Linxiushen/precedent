# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""The share card, reviewed as if someone were trying to make it leak.

``precedent audit --share`` is the one thing this tool emits that is *meant* to
leave the machine it ran on.  Everything else — the digest, the docket, the
ledger — is local by construction and carries paths and quotes on purpose.  So
this file is not a feature test; it is an adversarial review with an assertion
attached to each step.

The tree is ``scripts/make_fixture_home.py --hostile``: a Claude home built to
be as hostile as a real one can be, with one of every shape a reviewer would
think of planted in a *different kind of field* — a project slug, a workspace
directory name, a skill directory name, a YAML ``description``, a memory body,
a tool input, a human turn, a session id.  The list is
``make_fixture_home.HOSTILE_PLANTS`` and the tests iterate it, so widening the
review is one line in the fixture, not an edit here.

Three claims, in order of how badly they would hurt:

1. **Nothing planted reaches the card** — in either language, in text or in
   JSON, whichever way the CLI is driven.
2. **When something does survive, ``--verify`` refuses rather than prints**,
   for a leak in the English rendering, a leak *only* in the Chinese rendering,
   a leak only in the JSON, a free-form word in an alphabet no rule covers, and
   a fragment of this machine's own paths.
3. **The refusal never echoes what it found.**  A refusal message that quoted
   the leak would be the leak, with an exit code attached.

There is a fourth claim that is easy to forget and just as important: the card
is still *printed* on a hostile tree.  A verifier that refuses everything is a
verifier people pass ``--no-verify`` to, which is worse than not having one.
"""

from __future__ import annotations

import json
import os

import pytest
from precedent import i18n, share
from precedent.cli import main
from precedent.share import (SCHEMA_FIELDS, build_card, card_chars, card_words,
                             foreign_tokens, leaks, render_card, secrets_of,
                             shareable)
from precedent.state import StateDir

#: Every way a user can ask for the shareable card.
SHARE_INVOCATIONS = [
    ["audit", "--share"],
    ["audit", "--share", "--lang", "en"],
    ["audit", "--share", "--lang", "zh"],
    ["audit", "--share", "--json"],
    ["audit", "--share", "--json", "--lang", "zh"],
    ["audit", "--share", "--no-verify"],
    ["audit", "--share", "--no-verify", "--lang", "zh"],
]


def _card(home):
    from precedent.cli import _run_scan
    state = StateDir.open(home.state_dir, home.home, create=True)
    result = _run_scan(state, project=None, last_n=None)
    return result, state, build_card(result, state)


def _every_card(home, capsys) -> str:
    """Run every ``--share`` invocation and return everything they printed."""
    blob = ""
    for argv in SHARE_INVOCATIONS:
        rc = main(argv + ["--claude-home", home.home,
                          "--state-dir", home.state_dir])
        out = capsys.readouterr()
        assert rc == 0, f"{argv} exited {rc}: {out.err}"
        assert out.out, f"{argv} printed nothing"
        blob += out.out + "\n"
    return blob


# --------------------------------------------------------------------------
# 0. the fixture is actually hostile
# --------------------------------------------------------------------------

def test_every_plant_really_is_in_the_tree(hostile_home):
    """A negative test against a clean tree proves nothing at all."""
    hay = ""
    for dirpath, _dirs, files in os.walk(hostile_home.home):
        for fn in files:
            with open(os.path.join(dirpath, fn), encoding="utf-8",
                      errors="replace") as fh:
                hay += fh.read()
    missing = [(what, lit) for what, lit in hostile_home.plants if lit not in hay]
    assert missing == [], f"the fixture is not hostile: {missing}"
    assert len(hostile_home.plants) >= 16


def test_the_plants_are_spread_across_different_kinds_of_field(hostile_home):
    """A secret only ever in a human turn would test one code path."""
    home = hostile_home.home
    slug = hostile_home.slug
    # a project slug
    assert os.path.isdir(os.path.join(home, "projects", slug))
    # a skill directory named after the customer
    assert os.path.isfile(os.path.join(home, "skills", hostile_home.skill,
                                       "SKILL.md"))
    # a workspace directory named after the customer
    assert os.path.isdir(os.path.join(home, "_workspaces", hostile_home.company))
    # an artifact whose *name* is the customer
    mem = os.path.join(home, "projects", slug, "memory")
    assert os.path.isfile(os.path.join(mem, f"{hostile_home.company}-contacts.md"))
    # …whose YAML description is a phone number
    head = open(os.path.join(mem, f"{hostile_home.company}-contacts.md"),
                encoding="utf-8").read()
    assert "description: the finance contact is 139" in head


# --------------------------------------------------------------------------
# 1. nothing planted reaches the card
# --------------------------------------------------------------------------

def test_no_plant_reaches_any_share_invocation(hostile_home, capsys,
                                               real_home_canary):
    """The claim, stated once, over every way of asking for the card."""
    blob = _every_card(hostile_home, capsys)
    leaked = [(what, lit) for what, lit in hostile_home.plants
              if lit.lower() in blob.lower()]
    assert leaked == [], f"the share card leaked {leaked}"
    real_home_canary()


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_no_plant_reaches_the_renderer_in_either_language(hostile_home, lang):
    """Below the CLI: the renderer itself, in each language, plus the JSON."""
    _result, _state, card = _card(hostile_home)
    payload = shareable(card)
    blob = (render_card(payload, lang=lang)
            + json.dumps(payload, ensure_ascii=False))
    for what, lit in hostile_home.plants:
        assert lit.lower() not in blob.lower(), f"{what} leaked in {lang}"


def test_the_card_still_prints_on_a_hostile_tree(hostile_home, capsys):
    """A verifier that refuses everything is a verifier nobody leaves on."""
    rc = main(["audit", "--share", "--claude-home", hostile_home.home,
               "--state-dir", hostile_home.state_dir])
    out = capsys.readouterr()
    assert rc == 0 and "share card" in out.out and out.err == ""
    assert "0 leaks" in out.out


def test_the_card_has_no_field_outside_the_declared_schema(hostile_home):
    """``SCHEMA_FIELDS`` is what the JSON check treats as schema, not content."""
    _result, _state, card = _card(hostile_home)

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from keys(v)

    # `byKind` is keyed by the artifact kinds, which are themselves a closed
    # vocabulary, so a card key is either schema or one of those.
    allowed = set(SCHEMA_FIELDS) | set(share.ARTIFACT_KINDS) | {"other"}
    unknown = sorted(set(keys(card)) - allowed)
    assert unknown == [], f"undeclared card fields: {unknown}"


def test_secrets_of_sees_the_customer_name_not_just_the_whole_slug(hostile_home):
    """The literal test has to work on the *fragment* a leak would show."""
    result, state, _card_ = _card(hostile_home)
    secrets = secrets_of(result, state)
    assert hostile_home.slug in secrets              # the whole slug
    assert hostile_home.company in secrets           # …and the customer name
    assert hostile_home.sessions[0] in secrets
    # and it still does not treat the card's own legend as a secret
    for word in ("skill", "memory", "MEMORY.md", "CLAUDE.md", "rule", "global"):
        assert word not in secrets


# --------------------------------------------------------------------------
# 2. when something survives, --verify refuses
# --------------------------------------------------------------------------

def _leaky_renderer(monkeypatch, suffix: str, only_lang: str | None = None):
    """Break the renderer on purpose, optionally in one language only."""
    real = share.render_card

    def leaky(card, lang=None, local=False, verified=None):
        text = real(card, lang=lang, local=local, verified=verified)
        if only_lang is None or (lang or i18n.get_lang()) == only_lang:
            text += f"\ndebug: {suffix}\n"
        return text

    monkeypatch.setattr("precedent.cli.render_card", leaky)


@pytest.mark.parametrize("what,which", [
    ("api key", 0), ("phone (cn)", 2), ("email", 4), ("wechat id", 5),
    ("home path", 6), ("customer name", 8), ("session uuid", 10),
    ("git remote (ssh)", 12), ("client-named skill", 14),
])
def test_verify_refuses_when_a_plant_survives_into_the_english_card(
        hostile_home, capsys, monkeypatch, what, which):
    label, literal = hostile_home.plants[which]
    assert label == what, "the plant list moved; update the indices"
    _leaky_renderer(monkeypatch, literal)
    rc = main(["audit", "--share", "--claude-home", hostile_home.home,
               "--state-dir", hostile_home.state_dir])
    cap = capsys.readouterr()
    assert rc == 2, f"{what} was printed instead of refused"
    assert cap.out == "", f"{what}: the card was printed anyway"
    assert "REFUSING" in cap.err
    assert literal not in cap.err, f"{what}: the refusal echoed the leak"


@pytest.mark.parametrize("which", [0, 6, 8, 10, 14])
def test_a_leak_only_in_the_chinese_card_is_caught_from_the_english_one(
        hostile_home, capsys, monkeypatch, which):
    """The whole point of verifying every language, not the one you printed.

    Before this, ``--share`` re-scrubbed only the rendering it was about to
    print: a string that reached the card through a Chinese-only format string
    was invisible to an English-speaking maintainer and shipped anyway.
    """
    what, literal = hostile_home.plants[which]
    _leaky_renderer(monkeypatch, literal, only_lang="zh")
    rc = main(["audit", "--share", "--lang", "en", "--claude-home",
               hostile_home.home, "--state-dir", hostile_home.state_dir])
    cap = capsys.readouterr()
    assert rc == 2, f"{what} leaked in zh and the en card printed anyway"
    assert cap.out == ""
    assert literal not in cap.err


def test_a_leak_only_in_the_json_is_caught_from_the_text_card(
        hostile_home, capsys, monkeypatch):
    """The JSON is a separate rendering and gets its own pass."""
    real = share.shareable

    def leaky(card):
        out = real(card)
        out["tool"] = dict(out["tool"], name=hostile_home.token)
        return out

    monkeypatch.setattr("precedent.cli.shareable", leaky)
    rc = main(["audit", "--share", "--claude-home", hostile_home.home,
               "--state-dir", hostile_home.state_dir])
    cap = capsys.readouterr()
    assert rc == 2 and cap.out == ""
    assert hostile_home.token not in cap.err


def test_the_refusal_names_the_kind_and_the_offset_and_nothing_else(
        hostile_home, capsys, monkeypatch):
    _leaky_renderer(monkeypatch, hostile_home.plants[0][1])
    main(["audit", "--share", "--claude-home", hostile_home.home,
          "--state-dir", hostile_home.state_dir])
    err = capsys.readouterr().err
    body = [ln.strip() for ln in err.splitlines() if ln.startswith("  - ")]
    assert body, err
    for line in body:
        kind, _at, offset = line[4:].partition(" @ ")
        assert kind.replace("_", "").isalpha(), line
        assert offset.strip().isdigit(), line


def test_verify_is_on_by_default_and_no_verify_is_the_only_way_past_it(
        hostile_home, capsys, monkeypatch):
    _leaky_renderer(monkeypatch, hostile_home.plants[0][1])
    args = ["--claude-home", hostile_home.home, "--state-dir",
            hostile_home.state_dir]
    assert main(["audit", "--share"] + args) == 2
    capsys.readouterr()
    assert main(["audit", "--share", "--no-verify"] + args) == 0
    assert hostile_home.plants[0][1] in capsys.readouterr().out


# --------------------------------------------------------------------------
# the allowlist: the check that catches what no blocklist was written for
# --------------------------------------------------------------------------

def test_the_vocabulary_catches_a_word_no_shape_rule_would(hostile_home,
                                                           capsys, monkeypatch):
    """A surname is not a token, a path, an email or a phone number.

    ``leaks`` is a blocklist and blocklists fail quietly.  ``Pemberton`` matches
    none of its patterns and is not a string from this machine, so only the
    allowlist can see it.
    """
    word = "Pemberton"
    assert leaks(word) == []                       # no shape rule fires
    assert [f["kind"] for f in foreign_tokens(word, "en")] == ["foreign_word"]
    _leaky_renderer(monkeypatch, word)
    rc = main(["audit", "--share", "--claude-home", hostile_home.home,
               "--state-dir", hostile_home.state_dir])
    cap = capsys.readouterr()
    assert rc == 2 and cap.out == ""
    assert "foreign_word" in cap.err


def test_the_vocabulary_catches_an_alphabet_nobody_wrote_a_rule_for(
        hostile_home, capsys, monkeypatch):
    """Cyrillic, Greek, Devanagari — a per-shape blocklist covers none of them."""
    name = "Клиент"
    assert leaks(name) == []
    kinds = {f["kind"] for f in foreign_tokens(name, "en")}
    assert kinds == {"foreign_letter"}
    _leaky_renderer(monkeypatch, name)
    rc = main(["audit", "--share", "--claude-home", hostile_home.home,
               "--state-dir", hostile_home.state_dir])
    cap = capsys.readouterr()
    assert rc == 2 and cap.out == ""
    assert "foreign_letter" in cap.err


def test_a_chinese_project_name_is_foreign_even_in_the_chinese_card(hostile_home):
    """The zh card is full of Han characters; a *name* still is not one of them."""
    allowed = card_chars("zh")
    assert "个" in allowed and "会" in allowed          # the counted nouns
    found = {f["match"] for f in foreign_tokens("客户：鼎晖投资", "zh")}
    assert found, "a Chinese customer name passed the Chinese vocabulary"
    assert found & set("鼎晖投资")


def test_the_real_card_uses_only_its_own_vocabulary(hostile_home, demo_home):
    """The allowlist is tight enough to be worth having, on both trees."""
    for home in (hostile_home, demo_home):
        _result, _state, card = _card(home)
        payload = shareable(card)
        for lang in i18n.LANGS:
            text = render_card(payload, lang=lang, verified=23)
            assert foreign_tokens(text, lang) == [], (home.home, lang)


def test_the_vocabulary_is_derived_from_the_string_table_not_hand_written():
    """Add a string id and the allowlist widens; add a *value* and it does not."""
    words = card_words("en")
    assert "artifacts" in words and "docket" in words
    # …and none of the local-only block's words, which --share never renders
    assert "claudehome" not in words
    for leaked in ("northwind", "jdoe", "treasury", "invoices"):
        assert leaked not in words


def test_verify_checks_does_not_move_with_the_machine(hostile_home, demo_home):
    """The receipt's number must be the same for two people on two laptops."""
    assert share.verify_checks() == share.verify_checks(langs=i18n.LANGS)
    a, _s, _c = _card(hostile_home)
    b, _s2, _c2 = _card(demo_home)
    assert len(secrets_of(a)) != len(secrets_of(b))     # the inputs differ…
    assert share.verify_checks() == 23                  # …the receipt does not


def test_what_is_verified_is_byte_for_byte_what_is_printed(hostile_home, capsys,
                                                           monkeypatch):
    """A check that blesses a *nearly* identical rendering has a hole in it.

    Two ways the printed card used to differ from the checked one: the receipt
    line (``verified : …``) was appended after the check, and the JSON was
    re-serialised with ``indent=2``.  Both are caught here by recording exactly
    what the verifier looked at and comparing it to stdout.
    """
    seen: list[str] = []
    real_leaks = share.leaks

    def spy_leaks(text, secrets=()):
        seen.append(text)
        return real_leaks(text, secrets)

    monkeypatch.setattr("precedent.cli.leaks", spy_leaks)
    monkeypatch.setattr("precedent.cli.foreign_tokens",
                        lambda text, lang=None: (seen.append(text) or []))

    args = ["--claude-home", hostile_home.home, "--state-dir",
            hostile_home.state_dir]
    assert main(["audit", "--share", "--lang", "en"] + args) == 0
    printed = capsys.readouterr().out
    assert printed.rstrip("\n") in [x.rstrip("\n") for x in seen], \
        "the printed text is not one of the strings that was verified"

    seen.clear()
    assert main(["audit", "--share", "--json"] + args) == 0
    printed = capsys.readouterr().out
    assert printed.rstrip("\n") in [x.rstrip("\n") for x in seen], \
        "the printed JSON is not the JSON that was verified"


def test_the_receipt_line_itself_is_inside_the_verified_bytes(hostile_home,
                                                              capsys):
    assert main(["audit", "--share", "--claude-home", hostile_home.home,
                 "--state-dir", hostile_home.state_dir]) == 0
    out = capsys.readouterr().out
    assert "23 checks" in out
    # …and the line it is on passes the same allowlist as the rest of the card
    assert foreign_tokens(out, "en") == []


@pytest.mark.parametrize("word", ["machine", "vocabulary", "scrub", "claude",
                                  "duplicate", "index", "docket", "leaks"])
def test_a_directory_named_after_one_of_the_cards_own_words_is_not_a_leak(
        tmp_path, capsys, word):
    """The other failure mode: a control that refuses every card is switched off.

    Each of these is a fragment of something the card itself prints — the
    receipt line's ``this machine's names``, the legend's ``CLAUDE.md``, the
    ``near-duplicate`` label.  A user whose path happens to contain one must
    still get a card.  ``machine`` is not hypothetical: it is why
    :data:`precedent.share._FRAGMENT_RX` splits on punctuation rather than on
    path separators.
    """
    import precedent_fixture_home as fixture

    home = str(tmp_path / word / "claude-home")
    fixture.build(home, force=True)
    rc = main(["audit", "--share", "--claude-home", home,
               "--state-dir", str(tmp_path / word / "state")])
    cap = capsys.readouterr()
    assert rc == 0, f"a path containing {word!r} was refused: {cap.err}"
    assert "0 leaks" in cap.out
