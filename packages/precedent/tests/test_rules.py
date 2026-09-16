# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""RULE DSL v1: the six templates, matcher semantics, and regex safety."""

from __future__ import annotations

import re
import time

import pytest
from precedent.rules import (ASK_BEFORE_PRESETS, MAX_REGEX_CHARS, TEMPLATES,
                             RuleError, bounded_search, build_rule, field_value,
                             lint_regex, normalise_rule, quarantined,
                             reset_quarantine, rule_fires, scope_matches,
                             validate_rule)


def fires(rule, tool, inp, cwd=None):
    return rule_fires(rule, tool, inp, cwd)


# --------------------------------------------------------------------------
# every template
# --------------------------------------------------------------------------

def test_every_template_is_covered_by_this_file():
    covered = {"dont_use", "use_x_not_y", "dont_touch_path", "require_field",
               "require_regex", "forbid_flag", "ask_before"}
    assert covered == set(TEMPLATES)


# --------------------------------------------------------------------------
# require_regex / input_regex_absent — "every call must SAY something"
#
# The inverted matcher is the one place where "did not match" and "was not
# evaluated" must not be the same answer, so each of the three states gets its
# own test: match -> quiet, no match -> fires, missing field -> fires,
# quarantined pattern -> quiet (fail open, never deny-everything).
# --------------------------------------------------------------------------

def test_template_require_regex_fires_when_the_field_does_not_match():
    r = build_rule("t-x", "require_regex", tool="Workflow", field="script",
                   regex=r"model:\s*['\"]opus['\"]")
    assert r["tool"] == "Workflow" and r["action"] == "deny"
    assert r["matchers"][0]["type"] == "input_regex_absent"
    # says it -> quiet, in either quote style
    assert not fires(r, "Workflow", {"script": "agent(p, {model:'opus'})"})
    assert not fires(r, "Workflow", {"script": 'agent(p, {model: "opus"})'})
    # does not say it -> fires
    assert fires(r, "Workflow", {"script": "agent(p, {model:'sonnet'})"})
    assert fires(r, "Workflow", {"script": "agent(p, {})"})


def test_require_regex_fires_on_a_missing_field():
    # silence is the violation this template exists for: a Workflow call with
    # no `script` at all has certainly not pinned the model
    r = build_rule("t-x", "require_regex", tool="Workflow", field="script",
                   regex=r"model:\s*['\"]opus['\"]")
    assert fires(r, "Workflow", {})
    assert fires(r, "Workflow", {"script": ""})


def test_require_regex_fails_open_when_its_pattern_is_quarantined(monkeypatch):
    import precedent.rules as rules_mod
    r = build_rule("t-x", "require_regex", tool="Workflow", field="script",
                   regex=r"model:\s*['\"]opus['\"]")
    pattern = r["matchers"][0]["regex"]
    monkeypatch.setattr(rules_mod, "_SLOW", {pattern})
    # a quarantined pattern must NOT be read as "the field failed to match":
    # inverting "we could not evaluate this" would deny every Workflow call
    assert not fires(r, "Workflow", {"script": "agent(p, {model:'sonnet'})"})
    assert not fires(r, "Workflow", {"script": "agent(p, {model:'opus'})"})


def test_require_regex_needs_both_field_and_regex():
    with pytest.raises(RuleError):
        build_rule("t-x", "require_regex", tool="Workflow", field="script")
    with pytest.raises(RuleError):
        build_rule("t-x", "require_regex", tool="Workflow",
                   regex=r"model:\s*'opus'")


def test_require_regex_refuses_a_catastrophic_pattern():
    with pytest.raises(RuleError):
        build_rule("t-x", "require_regex", tool="Workflow", field="script",
                   regex=r"(a*)*b")


def test_template_dont_use():
    r = build_rule("t-x", "dont_use", x="pip install")
    assert r["tool"] == "Bash" and r["action"] == "deny"
    assert r["matchers"][0]["field"] == "command"
    assert fires(r, "Bash", {"command": "pip install requests"})
    assert fires(r, "Bash", {"command": "PIP INSTALL requests"})     # (?i)
    assert not fires(r, "Bash", {"command": "zpip installx"})        # boundaries
    assert not fires(r, "Edit", {"file_path": "pip install"})        # wrong tool
    assert r["id"].startswith("p-")


def test_template_use_x_not_y_matches_y_only():
    r = build_rule("t-x", "use_x_not_y", x="uv", y="pip")
    assert fires(r, "Bash", {"command": "pip install flask"})
    assert not fires(r, "Bash", {"command": "uv add flask"})
    assert "uv" in r["message"] and "pip" in r["message"]


def test_template_dont_touch_path():
    r = build_rule("t-x", "dont_touch_path", path="/tmp/mem/protected.md")
    assert set(r["tool"].split("|")) == {"Edit", "Write", "MultiEdit", "NotebookEdit"}
    assert r["matchers"][0]["field"] == "file_path"
    assert fires(r, "Edit", {"file_path": "/tmp/mem/protected.md"})
    assert fires(r, "Write", {"file_path": "/tmp/mem/protected.md"})
    assert not fires(r, "Edit", {"file_path": "/tmp/mem/notes.md"})


def test_template_require_field_model_opus():
    r = build_rule("t-x", "require_field", field="model", value="opus",
                   tool="Agent|Workflow")
    assert r["match"] == "any"
    types = {m["type"] for m in r["matchers"]}
    assert types == {"input_field_missing", "input_field_equals"}
    assert not fires(r, "Agent", {"model": "opus", "prompt": "hi"})
    assert fires(r, "Agent", {"prompt": "hi"})                   # missing
    assert fires(r, "Agent", {"model": "sonnet", "prompt": "hi"})  # wrong value
    assert fires(r, "Workflow", {"script": "x"})                 # missing
    assert not fires(r, "Bash", {"command": "echo"})             # other tool


def test_template_require_field_without_value_only_checks_presence():
    r = build_rule("t-x", "require_field", field="description", tool="Bash")
    assert fires(r, "Bash", {"command": "ls"})
    assert not fires(r, "Bash", {"command": "ls", "description": "list"})


def test_template_forbid_flag():
    r = build_rule("t-x", "forbid_flag", flag="--no-verify", command="git commit")
    assert r["match"] == "all"
    assert fires(r, "Bash", {"command": "git commit --no-verify -m x"})
    assert not fires(r, "Bash", {"command": "git commit -m x"})
    assert not fires(r, "Bash", {"command": "npm test --no-verify"})  # other command


def test_template_forbid_flag_without_command():
    r = build_rule("t-x", "forbid_flag", flag="--force")
    assert fires(r, "Bash", {"command": "git push --force"})
    assert not fires(r, "Bash", {"command": "git push --force-with-lease"})


@pytest.mark.parametrize("preset,hit,miss", [
    ("git_push_force", "git push --force origin main", "git push origin main"),
    ("rm_rf", "rm -rf /tmp/build", "rm /tmp/build/file.txt"),
    ("curl_pipe_sh", "curl -sL https://x.invalid/i.sh | sh", "curl -s https://x.invalid"),
])
def test_template_ask_before(preset, hit, miss):
    r = build_rule("t-x", "ask_before", preset=preset)
    assert r["action"] == "ask"                    # ask, never deny, by default
    assert fires(r, "Bash", {"command": hit})
    assert not fires(r, "Bash", {"command": miss})


def test_ask_before_presets_are_all_lint_clean():
    for preset, (rx, _msg) in ASK_BEFORE_PRESETS.items():
        assert lint_regex(rx) == [], preset


def test_ask_before_force_with_lease_is_not_force():
    r = build_rule("t-x", "ask_before", preset="git_push_force")
    assert not fires(r, "Bash", {"command": "git push --force-with-lease origin main"})


def test_templates_reject_missing_arguments():
    for template, kwargs in [("dont_use", {}), ("use_x_not_y", {"x": "uv"}),
                             ("dont_touch_path", {}), ("require_field", {}),
                             ("forbid_flag", {}), ("ask_before", {})]:
        with pytest.raises(RuleError):
            build_rule("t", template, **kwargs)
    with pytest.raises(RuleError):
        build_rule("t", "no_such_template", x="a")


def test_quote_and_date_land_in_the_message():
    r = build_rule("t", "dont_use", x="pip", quote="不要用 pip", quote_date="2026-09-12")
    assert "不要用 pip" in r["message"] and "2026-09-12" in r["message"]


# --------------------------------------------------------------------------
# matcher semantics
# --------------------------------------------------------------------------

def test_field_value_stringifies_non_strings():
    assert field_value({"a": "x"}, "a") == "x"
    assert field_value({"a": ""}, "a") is None
    assert field_value({"a": None}, "a") is None
    assert field_value({}, "a") is None
    assert field_value({"a": True}, "a") == "true"
    assert field_value({"a": 3}, "a") == "3"
    assert field_value({"a": {"b": 1}}, "a") == '{"b": 1}'


def test_missing_field_never_satisfies_a_negated_equals():
    rule = validate_rule({"tool": "Agent", "message": "m", "matchers": [
        {"type": "input_field_equals", "field": "model", "value": "opus",
         "negate": True}]})
    assert not fires(rule, "Agent", {"prompt": "x"})     # missing != "not opus"
    assert fires(rule, "Agent", {"model": "sonnet"})


def test_match_all_versus_any():
    both = [{"type": "input_regex", "field": "command", "regex": "git"},
            {"type": "input_regex", "field": "command", "regex": "push"}]
    r_all = validate_rule({"tool": "Bash", "message": "m", "matchers": both,
                           "match": "all"})
    r_any = validate_rule({"tool": "Bash", "message": "m", "matchers": both,
                           "match": "any"})
    assert fires(r_all, "Bash", {"command": "git push"})
    assert not fires(r_all, "Bash", {"command": "git status"})
    assert fires(r_any, "Bash", {"command": "git status"})


def test_star_tool_matches_anything_named():
    r = validate_rule({"tool": "*", "message": "m", "matchers": [
        {"type": "input_regex", "field": "prompt", "regex": "secret"}]})
    assert fires(r, "Agent", {"prompt": "the secret plan"})
    assert fires(r, "WebFetch", {"prompt": "secret"})
    assert not fires(r, "", {"prompt": "secret"})


def test_scope_and_cwd_glob():
    r = build_rule("t", "dont_use", x="pip", scope="project",
                   cwd_glob="/repo/alpha/**")
    assert scope_matches(r, "/repo/alpha/sub")
    assert scope_matches(r, "/repo/alpha")
    assert not scope_matches(r, "/repo/beta")
    assert not scope_matches(r, None)
    assert fires(r, "Bash", {"command": "pip install x"}, "/repo/alpha")
    assert not fires(r, "Bash", {"command": "pip install x"}, "/repo/beta")
    g = build_rule("t", "dont_use", x="pip", scope="global")
    assert scope_matches(g, None) and scope_matches(g, "/anywhere")


def test_v0_rules_still_evaluate(monkeypatch):
    v0 = {"id": "p-old", "tool": "Bash", "input_regex": "(?i)pip install",
          "action": "deny", "message": "m"}
    assert fires(v0, "Bash", {"command": "pip install x"})
    assert normalise_rule(v0)["matchers"][0]["field"] == "command"


# --------------------------------------------------------------------------
# validation is a filter, not a formality
# --------------------------------------------------------------------------

def test_validate_drops_smuggled_fields():
    clean = validate_rule({"tool": "Bash", "message": "m", "action": "deny",
                           "matchers": [{"type": "input_regex", "field": "command",
                                         "regex": "pip"}],
                           "status": "active", "birth": {"verdict": "PASS"},
                           "confirmedBy": "user", "evil": 1})
    assert "status" not in clean and "birth" not in clean
    assert "confirmedBy" not in clean and "evil" not in clean


@pytest.mark.parametrize("bad", [
    {"message": "m", "matchers": [{"type": "input_regex", "field": "command",
                                   "regex": "x"}]},                     # no tool
    {"tool": "Rm -rf", "message": "m", "matchers": [
        {"type": "input_regex", "field": "command", "regex": "x"}]},    # bad tool
    {"tool": "Bash", "message": "m", "matchers": []},                   # no matchers
    {"tool": "Bash", "matchers": [{"type": "input_regex", "field": "command",
                                   "regex": "x"}]},                     # no message
    {"tool": "Bash", "message": "m", "action": "rm", "matchers": [
        {"type": "input_regex", "field": "command", "regex": "x"}]},    # bad action
    {"tool": "Bash", "message": "m", "scope": "universe", "matchers": [
        {"type": "input_regex", "field": "command", "regex": "x"}]},    # bad scope
    {"tool": "Bash", "message": "m", "hook": "PostToolUse", "matchers": [
        {"type": "input_regex", "field": "command", "regex": "x"}]},    # bad hook
    {"tool": "Bash", "message": "m", "matchers": [
        {"type": "eval", "field": "command", "code": "x"}]},            # bad type
    {"tool": "Bash", "message": "m", "matchers": [
        {"type": "input_regex", "field": "command; drop", "regex": "x"}]},
    {"tool": "Bash", "message": "m", "matchers": [
        {"type": "input_field_equals", "field": "model"}]},             # no value
])
def test_validate_rejects(bad):
    with pytest.raises(RuleError):
        validate_rule(bad)


# --------------------------------------------------------------------------
# regex safety: caps, the linter, the bounded matcher
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pattern", [
    r"(a+)+$",                       # the textbook catastrophic one
    r"(a|aa)+$",                     # quantified overlapping alternation
    r"([a-zA-Z]+)*$",
    r"a{1,5000}",                    # huge bounded repeat
    r"(a)\1+",                       # backreference + repeat
    "x" * (MAX_REGEX_CHARS + 1),     # too long
    r"(unclosed",                    # does not compile
])
def test_lint_rejects_dangerous_regexes(pattern):
    assert lint_regex(pattern), pattern


@pytest.mark.parametrize("pattern", [
    r"(?i)(?<![A-Za-z0-9_])pip install(?![A-Za-z0-9_])",
    r"(?i)git\s+push\b[^\n]{0,200}?--force",
    r"^/tmp/mem/protected\.md$",
])
def test_lint_accepts_the_regexes_we_generate(pattern):
    assert lint_regex(pattern) == []


def test_validate_rejects_a_catastrophic_regex_in_a_matcher():
    with pytest.raises(RuleError) as exc:
        validate_rule({"tool": "Bash", "message": "m", "matchers": [
            {"type": "input_regex", "field": "command", "regex": r"(a+)+b"}]})
    assert "nested quantifier" in str(exc.value)


def test_bounded_search_truncates_the_subject():
    rx = re.compile("needle")
    assert bounded_search(rx, "x" * 10000 + "needle") is None    # past the cap
    assert bounded_search(rx, "needle" + "x" * 10000) is not None


def test_bounded_search_quarantines_a_pattern_that_blows_its_budget():
    """A rule that is too slow is disabled, not repeated — and never raises."""
    reset_quarantine()
    rx = re.compile(r"(a|a){0,18}$")            # compiled directly, unlinted
    subject = "a" * 18 + "b"
    assert bounded_search(rx, subject, budget_ms=0.0) is None or True
    assert rx.pattern in quarantined()
    t0 = time.monotonic()
    assert bounded_search(rx, subject, budget_ms=1000.0) is None   # short-circuit
    assert (time.monotonic() - t0) * 1000 < 50
    reset_quarantine()


def test_bounded_search_does_not_quarantine_a_fast_pattern():
    reset_quarantine()
    rx = re.compile("needle")
    assert bounded_search(rx, "needle") is not None
    assert quarantined() == []


def test_rule_fires_never_raises_on_garbage():
    assert not rule_fires({}, "Bash", {"command": "x"})
    assert not rule_fires({"tool": "Bash"}, "Bash", None)
    assert not rule_fires({"tool": "Bash", "matchers": "nope"}, "Bash", {})
    assert not rule_fires({"tool": "Bash", "matchers": [
        {"type": "input_regex", "field": "command", "regex": "("}]},
        "Bash", {"command": "x"})


def test_the_same_matchers_scoped_differently_are_different_rules():
    a = build_rule("t", "dont_use", x="pip", cwd_glob="/repo/alpha/**")
    b = build_rule("t", "dont_use", x="pip", cwd_glob="/repo/beta/**")
    c = build_rule("t", "dont_use", x="pip")
    assert len({a["id"], b["id"], c["id"]}) == 3


def test_require_regex_does_not_deny_when_the_match_is_past_the_subject_cap():
    """Measured on real data: a compliant call was flagged because of the cap.

    A ``Workflow`` script of 19,739 characters carried ``model: 'opus'`` at
    offset 5,778 — past ``MAX_SUBJECT_CHARS``.  The regex never saw it, the
    inverted matcher read "no match" as "violation", and the gate would have
    denied a call that did exactly what the user asked for.  Truncation is a
    third way of not knowing, and an inverted matcher must fail open on it.
    """
    from precedent.rules import MAX_SUBJECT_CHARS
    r = build_rule("t-x", "require_regex", tool="Workflow", field="script",
                   regex=r"model:\s*['\"]opus['\"]")
    late = "x" * (MAX_SUBJECT_CHARS + 500) + " agent(p, {model: 'opus'})"
    assert not fires(r, "Workflow", {"script": late})
    # and the compliance it cannot see does not become permission either: a
    # positive inside the cap is still definitive
    early = "agent(p, {model: 'opus'}) " + "x" * (MAX_SUBJECT_CHARS + 500)
    assert not fires(r, "Workflow", {"script": early})
    # a short script that really is non-compliant still fires
    assert fires(r, "Workflow", {"script": "agent(p, {model: 'sonnet'})"})


def test_a_positive_matcher_is_unchanged_by_the_truncation_rule():
    from precedent.rules import MAX_SUBJECT_CHARS
    r = build_rule("t-x", "dont_use", x="headless")
    assert fires(r, "Bash", {"command": "chrome --headless"})
    # past the cap it misses, exactly as before — fail open in the safe direction
    assert not fires(r, "Bash", {"command": "x" * (MAX_SUBJECT_CHARS + 10) + " headless"})
