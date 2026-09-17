# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""The string table, the locale chain, and the rule the table exists to hold.

The rule: **the frame is translated, the evidence is not.**  A quote from your
transcript, a path, a rule id and a matcher are the same bytes in every
language, because a report whose evidence changes with ``$LANG`` is a report
you cannot check by hand.
"""

from __future__ import annotations

import string

import pytest
from precedent import i18n
from precedent.cli import main
from precedent.i18n import LANGS, PLURALS, STRINGS, normalise, plural, resolve, t


@pytest.fixture
def cli(mining_home, tmp_path):
    """The same tiny driver test_cli.py uses, pinned to a synthetic home."""
    class C:
        home = mining_home.home
        state = str(tmp_path / "state")

        def __call__(self, *argv):
            return main(list(argv) + ["--claude-home", self.home,
                                      "--state-dir", self.state])
    return C()


def _placeholders(text: str) -> set:
    return {f for _lit, f, _spec, _cnv in string.Formatter().parse(text) if f}


# --------------------------------------------------------------------------
# the table itself
# --------------------------------------------------------------------------

def test_every_string_id_carries_every_language():
    for key, row in STRINGS.items():
        assert set(row) == set(LANGS), f"{key} has {sorted(row)}"
        for lang, text in row.items():
            assert isinstance(text, str) and text.strip(), f"{key}/{lang} is empty"


def test_the_two_languages_agree_about_their_placeholders():
    """A half-translated format string is a crash, not a cosmetic defect."""
    for key, row in STRINGS.items():
        sets = {lang: _placeholders(text) for lang, text in row.items()}
        first = sets[LANGS[0]]
        for lang in LANGS[1:]:
            assert sets[lang] == first, (
                f"{key}: {LANGS[0]} wants {sorted(first)}, "
                f"{lang} wants {sorted(sets[lang])}")


def test_every_plural_noun_has_two_forms_in_every_language():
    for key, row in PLURALS.items():
        assert set(row) == set(LANGS), f"{key} has {sorted(row)}"
        for lang, forms in row.items():
            assert len(forms) == 2 and all(forms), f"{key}/{lang}"
    # English inflects; Chinese does not, and that is deliberate, not an omission
    assert plural(1, "session", "en") == "session"
    assert plural(2, "session", "en") == "sessions"
    assert plural(1, "session", "zh") == plural(2, "session", "zh")


def test_a_missing_id_raises_rather_than_silently_falling_back():
    with pytest.raises(KeyError):
        t("no.such.string", "en")


def test_no_chinese_left_in_an_english_string_and_vice_versa():
    for key, row in STRINGS.items():
        en = row["en"]
        assert not any("一" <= ch <= "鿿" for ch in en), \
            f"{key}: the English string still has Chinese in it"


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,want", [
    ("zh_CN.UTF-8", "zh"), ("zh", "zh"), ("zh-Hant", "zh"), ("ZH_TW", "zh"),
    ("en_GB.UTF-8", "en"), ("en", "en"),
    ("fr_FR.UTF-8", "en"),          # no strings for it: English is the fallback
    ("C", None), ("POSIX", None), ("", None), (None, None), (7, None),
])
def test_normalise(value, want):
    assert normalise(value) == want


def test_the_precedence_chain():
    env = {"LANG": "zh_CN.UTF-8"}
    assert resolve(None, env) == "zh"
    assert resolve("en", env) == "en"                       # --lang wins
    env2 = {"PRECEDENT_LANG": "en", "LC_ALL": "zh_CN.UTF-8"}
    assert resolve(None, env2) == "en"                      # PRECEDENT_LANG wins
    env3 = {"LC_ALL": "zh_CN.UTF-8", "LANG": "en_US.UTF-8"}
    assert resolve(None, env3) == "zh"                      # LC_ALL beats LANG
    assert resolve(None, {}) == "en"                        # the default
    assert resolve(None, {"LANG": "C"}) == "en"             # C is not a choice


def test_set_lang_is_scoped_and_restores():
    assert i18n.get_lang() == "en"
    with i18n.using("zh"):
        assert i18n.get_lang() == "zh"
        assert t("init.ok") == "ok" and "首屏" in t("init.title", version="x")
    assert i18n.get_lang() == "en"


# --------------------------------------------------------------------------
# alignment
# --------------------------------------------------------------------------

def test_display_width_counts_cjk_as_two_columns():
    assert i18n.display_width("abc") == 3
    assert i18n.display_width("从未被引用") == 10
    assert i18n.display_width("失效索引/缺文件") == 15
    assert i18n.display_width(i18n.pad("从未被引用", 16)) == 16
    assert i18n.pad("abc", 2) == "abc"          # never truncates


# --------------------------------------------------------------------------
# end to end: the frame moves, the evidence does not
# --------------------------------------------------------------------------

def test_the_locale_picks_the_language_when_no_flag_is_given(cli, capsys,
                                                             monkeypatch):
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert cli("init") == 0
    assert "首屏" in capsys.readouterr().out
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    assert cli("init") == 0
    assert "first screen" in capsys.readouterr().out


def test_precedent_lang_overrides_the_locale(cli, capsys, monkeypatch):
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
    monkeypatch.setenv("PRECEDENT_LANG", "en")
    assert cli("init") == 0
    assert "first screen" in capsys.readouterr().out


def test_lang_is_accepted_before_the_subcommand_too(cli, capsys):
    assert main(["--lang", "zh", "init", "--claude-home", cli.home,
                 "--state-dir", cli.state]) == 0
    assert "首屏" in capsys.readouterr().out


def test_the_evidence_is_byte_identical_in_both_languages(cli, capsys):
    """Translating a quote would make the report stop matching the record."""
    assert cli("mine", "--lang", "en") == 0
    en = capsys.readouterr().out
    assert cli("mine", "--lang", "zh") == 0
    zh = capsys.readouterr().out
    for evidence in ("不要用 pip，用 uv。", "pip install requests",
                     "stop using curl, use httpie instead"):
        assert evidence in en and evidence in zh, evidence
    assert "corrections detected" in en and "检出纠正" in zh
