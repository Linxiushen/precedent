# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""ADVERSARIAL TESTS — one per safety invariant, written to break the thing.

Every test here starts from "how would I get past this?" rather than "does the
happy path work?".  Four groups:

1. **regex safety** — a pattern that never returns wedges every tool call in
   Claude Code, so the linter, not the match-time budget, has to be the defence;
2. **hook / library parity + fail-open** — the gate's verdict is meaningless if
   the hook evaluates the same rule differently, and a malformed rule must
   never become "deny everything";
3. **the temporal birth gate** — every construction that would make the gate
   *looser* than specified (laundering a false fire into a true positive,
   cross-session confirmation, pre-t0 credit);
4. **LLM output is data** — budget, schema, gate, and the storage boundary.

Nothing here touches anything outside ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

import pytest
from conftest import sid, tree_fingerprint
from precedent.cli import main
from precedent.compile import compile_topics, run_temporal_gate
from precedent.hooks import install
from precedent.llm import MAX_BUDGET_USD, LLMUnavailable, claude_argv
from precedent.mine import analyse_text, mine
from precedent.rules import (ASK_BEFORE_PRESETS, MAX_RISKY_QUANTIFIERS,
                             MAX_SUBJECT_CHARS, RuleError, build_rule,
                             bounded_search, lint_regex, rule_fires,
                             validate_rule)
from precedent.state import StateDir

S1, S2, S3 = sid(1), sid(2), sid(3)


def day(d, h, m=0, s=0):
    return f"2026-09-{d:02d}T{h:02d}:{m:02d}:{s:02d}.000Z"


# ==========================================================================
# 1. REGEX SAFETY — the linter is the only real bound
# ==========================================================================

#: Every one of these was accepted by the v1 linter and then took longer than
#: ten seconds (unbounded, in practice) on a 4 096-character subject — the cap
#: the matcher truncates to.  They are the *adjacent quantifier* family: the
#: nested-quantifier check does not see them because no group is repeated.
REDOS = [
    r"a*a*a*b",
    r"\s*\s*\s*=",
    r".*.*.*=.*",
    r"x[a-z]*[a-z]*[a-z]*y",
    r"(?i)pip\s*\s*install",
    r"\w+\w+\w+!",
    r"(?i)(?:pip|uv)*(?:pip|uv)*x",
]


@pytest.mark.parametrize("pattern", REDOS)
def test_the_linter_rejects_adjacent_quantifier_redos(pattern):
    """`a*a*a*b` never returns inside the hook, and the hook holds the GIL.

    There is no mid-match deadline available to a stdlib-only tool, so a
    pattern of this shape must never be *stored*: one confirmed precedent
    carrying one of these would hang every Bash call Claude Code makes.
    """
    problems = lint_regex(pattern)
    assert problems, f"{pattern!r} passed the linter"
    assert "backtrack" in " ".join(problems) or "catastrophic" in " ".join(problems)


@pytest.mark.parametrize("pattern", REDOS)
def test_a_redos_pattern_cannot_be_validated_into_a_rule(pattern):
    with pytest.raises(RuleError):
        validate_rule({"tool": "Bash", "action": "deny", "scope": "global",
                       "message": "x",
                       "matchers": [{"type": "input_regex", "field": "command",
                                     "regex": pattern}]})


def test_too_many_repeats_in_one_pattern_is_refused():
    pattern = "(?i)" + "".join(f"[a-z]+{c}" for c in "abcdef")
    assert any(str(MAX_RISKY_QUANTIFIERS) in p for p in lint_regex(pattern))


def test_disjoint_adjacent_quantifiers_are_still_allowed():
    """The check is *overlap*, not adjacency: `a*b*` cannot be pumped."""
    assert lint_regex(r"(?i)a*b*c") == []
    assert lint_regex(r"(?i)pip\s+install\s+[\w.\-]+") == []


#: Worst-case subjects: 4 096 characters (the matcher's cap) of one repeated
#: character, chosen to be exactly what each stored pattern is hungriest for.
PUMPS = ["a" * MAX_SUBJECT_CHARS, " " * MAX_SUBJECT_CHARS, "-" * MAX_SUBJECT_CHARS,
         "/" * MAX_SUBJECT_CHARS, ("pip install " * 400)[:MAX_SUBJECT_CHARS],
         ("git push --force " * 250)[:MAX_SUBJECT_CHARS],
         ("rm -rf /tmp/x " * 300)[:MAX_SUBJECT_CHARS]]


def _every_pattern_precedent_can_emit() -> list[str]:
    out = [rx for rx, _msg in ASK_BEFORE_PRESETS.values()]
    rules = [
        build_rule("t", "dont_use", x="pip"),
        build_rule("t", "use_x_not_y", x="uv", y="pip install"),
        build_rule("t", "dont_touch_path", path="~/.claude/memory/protected.md"),
        build_rule("t", "forbid_flag", flag="--force", command="git push"),
        build_rule("t", "ask_before", x="rm -rf /"),
    ]
    for r in rules:
        out += [m["regex"] for m in r["matchers"] if m.get("type") == "input_regex"]
    return out


@pytest.mark.parametrize("pattern", _every_pattern_precedent_can_emit())
def test_every_pattern_precedent_can_emit_is_fast_on_a_worst_case_subject(pattern):
    """Not "does it lint clean" — does it *return*, on the nastiest input.

    300 ms is the whole hook budget including interpreter start, so a single
    regex gets a small fraction of it.
    """
    import re as _re
    rx = _re.compile(pattern)
    for subject in PUMPS:
        t0 = time.monotonic()
        rx.search(subject)
        ms = (time.monotonic() - t0) * 1000
        assert ms < 50, f"{pattern!r} took {ms:.0f} ms on {subject[:12]!r}…"


def test_bounded_search_truncates_before_matching():
    import re as _re
    rx = _re.compile("x$")
    assert bounded_search(rx, "a" * (MAX_SUBJECT_CHARS + 10) + "x") is None


# ==========================================================================
# 2. HOOK / LIBRARY PARITY, AND FAIL-OPEN
# ==========================================================================

def test_a_rule_with_no_usable_matcher_never_fires():
    """``all([])`` is True — which would make a malformed rule deny everything."""
    for matchers in (["not-a-dict"], [None], [123, "x"]):
        rule = {"tool": "Bash", "match": "all", "matchers": matchers,
                "action": "deny", "scope": "global", "message": "x"}
        assert rule_fires(rule, "Bash", {"command": "echo hello"}) is False
        rule_any = dict(rule, match="any")
        assert rule_fires(rule_any, "Bash", {"command": "echo hello"}) is False


PARITY_RULES = [
    dict(build_rule("t", "dont_use", x="pip"), id="p-1", status="active",
         scope="global"),
    dict(build_rule("t", "require_field", field="model", value="opus"),
         id="p-2", status="active", scope="global"),
    dict(build_rule("t", "dont_touch_path", path="/tmp/mem/protected.md"),
         id="p-3", status="active", scope="global"),
    dict(build_rule("t", "ask_before", preset="git_push_force"), id="p-4",
         status="active", scope="global"),
]
PARITY_CALLS = [
    ("Bash", {"command": "pip install requests"}),
    ("Bash", {"command": "uv add requests"}),
    ("Bash", {"command": "git push --force origin main"}),
    ("Bash", {"command": "git push --force-with-lease"}),
    ("Bash", {}),
    ("Bash", {"command": ""}),
    ("Agent", {"prompt": "x"}),
    ("Agent", {"prompt": "x", "model": "opus"}),
    ("Agent", {"prompt": "x", "model": "OPUS"}),
    ("Agent", {"prompt": "x", "model": None}),
    ("Workflow", {"scriptPath": "/tmp/x.py"}),
    ("Write", {"file_path": "/tmp/mem/protected.md"}),
    ("Write", {"file_path": "/tmp/mem/notes.md"}),
    ("Edit", {"file_path": 17}),
    ("Skill", {"skill": "pip"}),
]


def test_the_hook_and_the_library_decide_identically(tmp_path):
    """The gate replays rules with ``rule_fires``; the hook uses its own copy.

    If the two ever disagree, every birth-gate number is about a rule that is
    not the one being enforced.  This pins them together over the matrix.
    """
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    os.makedirs(state.claude_home, exist_ok=True)
    state.write_precedents(PARITY_RULES)
    install(state, PARITY_RULES)
    env = dict(os.environ, PRECEDENT_STATE_DIR=state.root, PRECEDENT_ALLOW_STATE_REDIRECT="1")
    for tool, tool_input in PARITY_CALLS:
        proc = subprocess.run(
            [sys.executable, state.hook_script_path],
            input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool,
                              "tool_input": tool_input}),
            capture_output=True, text=True, env=env, timeout=30)
        assert proc.returncode == 0
        out = json.loads(proc.stdout or "{}")
        hook_id = None
        if out:
            reason = out["hookSpecificOutput"]["permissionDecisionReason"]
            hook_id = re.match(r"\[precedent (\S+)\]", reason).group(1)
        lib_id = next((r["id"] for r in PARITY_RULES
                       if rule_fires(r, tool, tool_input, None)), None)
        assert hook_id == lib_id, (
            f"{tool} {tool_input}: hook={hook_id} library={lib_id}")


def test_the_hook_writes_nothing_into_the_claude_home(tmp_path):
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    os.makedirs(state.claude_home, exist_ok=True)
    with open(os.path.join(state.claude_home, "settings.json"), "w") as fh:
        json.dump({"model": "opus"}, fh)
    state.write_precedents(PARITY_RULES)
    install(state, PARITY_RULES)
    before = tree_fingerprint(state.claude_home)
    for tool, tool_input in PARITY_CALLS:
        subprocess.run([sys.executable, state.hook_script_path],
                       input=json.dumps({"hook_event_name": "PreToolUse",
                                         "tool_name": tool,
                                         "tool_input": tool_input}),
                       capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, PRECEDENT_STATE_DIR=state.root, PRECEDENT_ALLOW_STATE_REDIRECT="1"))
    assert tree_fingerprint(state.claude_home) == before


@pytest.mark.parametrize("payload", [
    "", "not json", "[]", "null", '{"tool_input": "a string"}',
    '{"hook_event_name": "PreToolUse"}', '{"tool_name": 17, "tool_input": []}',
    '{"hook_event_name":"PostToolUse","tool_name":"Bash",'
    '"tool_input":{"command":"pip install x"}}',
])
def test_the_hook_survives_hostile_stdin(tmp_path, payload):
    """Anything unparseable exits 0 with no decision — never a wedged agent."""
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    os.makedirs(state.claude_home, exist_ok=True)
    state.write_precedents(PARITY_RULES)
    install(state, PARITY_RULES)
    proc = subprocess.run([sys.executable, state.hook_script_path], input=payload,
                          capture_output=True, text=True, timeout=30,
                          env=dict(os.environ, PRECEDENT_STATE_DIR=state.root, PRECEDENT_ALLOW_STATE_REDIRECT="1"))
    assert proc.returncode == 0
    assert json.loads(proc.stdout or "{}") == {}


# ==========================================================================
# 3. THE TEMPORAL BIRTH GATE — every way to make it looser than specified
# ==========================================================================

def _pip_topic(result):
    for t in result.topics:
        if any("pip" in c.turn.text for c in t.members):
            return t
    raise AssertionError("no pip topic mined")


BEFORE = [
    ("human", day(1, 9, 0), "帮我把依赖装好。"),
    ("assistant", day(1, 9, 1), "Installing."),
    ("tool", day(1, 9, 2), "Bash", {"command": "pip install requests"}),
    ("human", day(1, 9, 4), "不要用 pip，用 uv。"),         # t0
]


def test_a_same_timestamp_turn_cannot_launder_a_false_fire(home_builder):
    """A fire is confirmed by *a correction*, not by *a timestamp*.

    Two human turns in one session can carry the same stamp.  Matching the
    following turns to the topic's corrections by timestamp let an unrelated
    turn stand in for the correction — turning a tolerated false fire into a
    true positive, and a FAIL into a PASS.  Confirmation is by turn identity.
    """
    home = home_builder({S1: BEFORE + [
        ("tool", day(1, 9, 6), "Bash", {"command": "pip install extra"}),   # fire
        ("human", day(1, 9, 7), "好的。"),
        ("human", day(1, 9, 8), "继续。"),
        ("human", day(1, 9, 9), "先这样。"),        # 3rd turn: NOT a correction
        ("tool", day(1, 9, 10), "Bash", {"command": "uv add a"}),
        ("tool", day(1, 9, 11), "Bash", {"command": "uv add b"}),
        # a real correction in the same topic, sharing 09:09's timestamp but
        # sitting outside the three-turn window
        ("human", day(1, 9, 9), "别用 pip 了，用 uv。"),
    ]})
    result = mine(home)
    topic = _pip_topic(result)
    rule, gate = compile_topics([topic], result, template="use_x_not_y",
                                x="uv", y="pip")
    assert gate.hit is True
    assert gate.n_true_positives == 0, gate.to_dict()["truePositiveExamples"]
    assert gate.n_false_fires == 1
    assert gate.verdict == "FAIL"


def test_a_correction_in_another_session_cannot_confirm_a_fire(home_builder):
    """Confirmation is same-session: the user cannot object in a session that
    had not started yet."""
    home = home_builder({
        S1: BEFORE + [
            ("tool", day(1, 9, 6), "Bash", {"command": "pip install extra"}),
            ("tool", day(1, 9, 7), "Bash", {"command": "uv add a"}),
            ("tool", day(1, 9, 8), "Bash", {"command": "uv add b"}),
            ("human", day(1, 9, 9), "好的，谢谢。"),
        ],
        S2: [
            ("human", day(2, 9, 0), "再装一个。"),
            ("tool", day(2, 9, 1), "Bash", {"command": "uv add c"}),
            ("human", day(2, 9, 2), "我说过了，不要用 pip。"),
        ]})
    result = mine(home)
    topic = _pip_topic(result)
    rule, gate = compile_topics([topic], result, template="use_x_not_y",
                                x="uv", y="pip")
    assert len(gate.corrected_sessions) == 2
    assert gate.n_true_positives == 0
    assert gate.n_false_fires == 1
    assert gate.verdict == "FAIL"


def test_epsilon_is_a_ceiling_not_a_target(home_builder):
    """1/50 == epsilon passes, 2/50 does not.  The comparison is `>`."""
    quiet = [("tool", day(1, 10, 0, i), "Bash", {"command": f"uv add p{i}"})
             for i in range(48)]
    one_fire = [("tool", day(1, 11, 0), "Bash", {"command": "pip install x"})]
    two_fires = one_fire + [("tool", day(1, 11, 1), "Bash",
                             {"command": "pip install y"})]
    home1 = home_builder({S1: BEFORE + quiet + one_fire
                          + [("tool", day(1, 12, 0), "Bash", {"command": "uv sync"}),
                             ("human", day(1, 12, 1), "好的。")]})
    result = mine(home1)
    _r, gate = compile_topics([_pip_topic(result)], result,
                              template="use_x_not_y", x="uv", y="pip")
    assert (gate.n_false_fires, gate.eligible_after) == (1, 50)
    assert gate.false_fire_rate == 0.02 and gate.verdict == "PASS"

    home2 = home_builder({S1: BEFORE + quiet + two_fires
                          + [("human", day(1, 12, 1), "好的。")]})
    result2 = mine(home2)
    _r2, gate2 = compile_topics([_pip_topic(result2)], result2,
                                template="use_x_not_y", x="uv", y="pip")
    assert (gate2.n_false_fires, gate2.eligible_after) == (2, 50)
    assert gate2.verdict == "FAIL"


def test_an_over_broad_rule_misfires_in_an_uncorrected_session(mining_home):
    """The whole point of the gate: `install` catches `npm install`, which the
    user never objected to, so the rule does not get born."""
    result = mine(mining_home.home)
    topic = _pip_topic(result)
    rule, gate = compile_topics([topic], result, template="dont_use", x="install")
    assert gate.hit is True
    assert gate.n_false_fires >= 1
    assert any("npm install" in f["value"] for f in gate.false_fires)
    assert gate.verdict == "FAIL"
    # …and the narrow rule for the same topic does get born
    _r2, gate2 = compile_topics([topic], result, template="use_x_not_y",
                                x="uv", y="pip")
    assert gate2.verdict == "PASS" and gate2.n_false_fires == 0


def test_the_counts_reconcile(mining_home):
    """fires_after == true + false, and nothing is counted twice."""
    result = mine(mining_home.home)
    topic = _pip_topic(result)
    for template, kw in (("use_x_not_y", {"x": "uv", "y": "pip"}),
                         ("dont_use", {"x": "install"}),
                         ("dont_use", {"x": "curl"})):
        _rule, gate = compile_topics([topic], result, template=template, **kw)
        d = gate.to_dict()
        assert d["firesAfter"] == d["truePositives"] + d["falseFires"]
        assert d["falseFires"] <= d["eligibleAfter"]
        assert d["preT0"]["counted"] is False
        assert (gate.eligible_after + gate.eligible_before + gate.unordered
                + gate.out_of_scope) == gate.n_tool_calls_scanned
        assert len({(f["sessionId"], f["locator"]) for f in gate.false_fires}) \
            == len(gate.false_fires)


def test_a_pre_t0_massacre_cannot_sink_a_rule_and_a_pre_t0_silence_cannot_save_one(
        home_builder):
    """(c): pre-t0 behaviour is evidence about the world, not about the rule."""
    noisy_before = [("tool", day(1, 8, i), "Bash", {"command": f"pip install p{i}"})
                    for i in range(9)]
    home = home_builder({S1: [("human", day(1, 8, 0), "装依赖。")] + noisy_before
                         + BEFORE[1:] + [
        ("tool", day(1, 9, 6), "Bash", {"command": "uv add a"}),
        ("tool", day(1, 9, 7), "Bash", {"command": "uv add b"}),
        ("tool", day(1, 9, 8), "Bash", {"command": "uv sync"}),
        ("human", day(1, 9, 9), "好的。"),
    ]})
    result = mine(home)
    _rule, gate = compile_topics([_pip_topic(result)], result,
                                 template="use_x_not_y", x="uv", y="pip")
    assert gate.fires_before >= 9
    assert gate.verdict == "PASS"
    assert gate.to_dict()["preT0"]["counted"] is False


def test_a_rule_scoped_to_a_directory_it_never_ran_in_is_insufficient(
        home_builder, tmp_path):
    """Out-of-scope actions are not eligible, so a rule cannot buy a PASS by
    scoping itself where nothing ever happened."""
    home = home_builder({S1: BEFORE + [
        ("tool", day(1, 9, 6), "Bash", {"command": "uv add a"}),
        ("tool", day(1, 9, 7), "Bash", {"command": "uv add b"}),
        ("tool", day(1, 9, 8), "Bash", {"command": "uv sync"}),
    ]})
    result = mine(home)
    topic = _pip_topic(result)
    rule = build_rule(topic.id, "use_x_not_y", x="uv", y="pip",
                      cwd_glob=str(tmp_path / "nowhere") + "/**")
    gate = run_temporal_gate(rule, [topic], result)
    assert gate.eligible_after == 0 and gate.out_of_scope > 0
    assert gate.hit is False
    assert gate.verdict != "PASS"


# ==========================================================================
# 4. LLM OUTPUT IS DATA
# ==========================================================================

@pytest.mark.parametrize("budget", [0, -1, 0.0, 1e9, float("nan"), "lots", None,
                                    MAX_BUDGET_USD * 2])
def test_a_budget_that_is_not_a_cap_is_refused_before_any_subprocess(budget):
    """``--max-budget-usd`` is a safety invariant; ``--llm-budget 0`` would
    hand the flag a value that caps nothing."""
    with pytest.raises(LLMUnavailable):
        claude_argv("hi", budget_usd=budget, binary="/bin/echo")


def test_the_argv_still_carries_the_budget_and_never_skips_permissions():
    argv = claude_argv("hi", budget_usd=0.3, binary="/bin/echo")
    assert "--max-budget-usd" in argv and argv[argv.index("--max-budget-usd") + 1] == "0.3"
    assert "--no-session-persistence" in argv
    assert not any("dangerously" in a for a in argv)


def test_a_hand_edited_candidate_cannot_be_confirmed_past_the_linter(
        mining_home, tmp_path, capsys):
    """candidates.json is precedent's own file, but it is not the hook's
    trust boundary: confirm re-validates against the *current* schema and the
    *current* linter, so a candidate compiled by an older, weaker build (or
    edited by hand) cannot be activated."""
    state_dir = str(tmp_path / "state")
    argv = ["--claude-home", mining_home.home, "--state-dir", state_dir]
    assert main(["mine", *argv, "--quiet"]) == 0
    state = StateDir.open(state_dir, mining_home.home)
    topics = json.loads(open(os.path.join(state.root, "topics.json"),
                             encoding="utf-8").read())["topics"]
    tid = topics[0]["id"]
    assert main(["compile", tid, *argv, "--template", "use_x_not_y",
                 "--x", "uv", "--y", "pip"]) in (0, 1)
    cands = state.candidates()
    assert cands
    cands[-1]["matchers"] = [{"type": "input_regex", "field": "command",
                              "regex": r".*.*.*=.*"}]
    cands[-1]["birth"] = dict(cands[-1].get("birth") or {}, verdict="PASS")
    state.write_candidates(cands)
    rc = main(["confirm", cands[-1]["id"], *argv])
    assert rc == 2
    assert "not a valid DSL v1 rule" in capsys.readouterr().err
    assert state.precedents() == []


def test_hooks_install_apply_refuses_the_real_claude_home_without_i_know(
        tmp_path, monkeypatch, capsys, real_home_canary):
    """The one command that may write a settings.json still refuses to write
    the user's own ~/.claude unless they say so explicitly."""
    from precedent import cli as cli_mod

    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    os.makedirs(state.claude_home, exist_ok=True)
    settings = os.path.join(state.claude_home, "settings.json")
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({"model": "opus"}, fh)
    before = tree_fingerprint(state.claude_home)

    monkeypatch.setattr(cli_mod, "is_real_claude_home", lambda p: True)
    rc = main(["hooks", "install", "claude-code", "--apply",
               "--claude-home", state.claude_home, "--state-dir", state.root])
    assert rc == 2
    assert "--i-know" in capsys.readouterr().err
    assert tree_fingerprint(state.claude_home) == before
    assert not os.listdir(state.backups_dir)
    real_home_canary()


def test_two_applies_in_the_same_second_keep_both_backups(tmp_path):
    """The backup is the only copy of what the user had; it must never be
    overwritten by the next one."""
    from datetime import datetime, timezone

    from precedent.hooks import backup_settings

    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    os.makedirs(state.claude_home, exist_ok=True)
    settings = os.path.join(state.claude_home, "settings.json")
    now = datetime(2026, 9, 15, 3, 30, 49, tzinfo=timezone.utc)
    with open(settings, "w", encoding="utf-8") as fh:
        fh.write('{"v": 1}')
    first = backup_settings(state, settings, now=now)
    with open(settings, "w", encoding="utf-8") as fh:
        fh.write('{"v": 2}')
    second = backup_settings(state, settings, now=now)
    assert first != second
    assert open(first, encoding="utf-8").read() == '{"v": 1}'
    assert open(second, encoding="utf-8").read() == '{"v": 2}'


# ==========================================================================
# 5. CHINESE (AND ENGLISH) FALSE POSITIVES
# ==========================================================================

NOT_CORRECTIONS = [
    "这个方案特别好，别的事情先不用担心。",
    "帮我鉴别一下这两个方案。",
    "请甄别一下日志里的这几条。",
    "我们先做用户识别模块。",
    "服务在不停地重启，看看日志。",
    "文件名是 dont-touch.md，帮我读一下。",
    "跑一下 stop-server.sh 看看。",
    "打开 revert.py 看一眼。",
    "undo/redo 这个模块谁写的。",
    "是不是应该先跑测试？",
    "我应该先跑测试吗？",
]


@pytest.mark.parametrize("text", NOT_CORRECTIONS)
def test_these_are_not_corrections(text):
    assert analyse_text(text).is_correction is False, text


PASTED = [
    "这是日志：\n```\n$ 不要用 pip\nERROR: don't use pip, use uv\n```\n帮我看看。",
    "看看这段代码：\n```python\n# 别用 requests，改用 httpx\nimport requests\n```\n为什么慢？",
    "~~~\nstop using curl, i said httpie\n~~~\n这是同事给的说明。",
]


@pytest.mark.parametrize("text", PASTED)
def test_a_pasted_log_or_code_block_is_not_the_user_correcting_anything(text):
    """A log line that reads "don't use pip" is something the user is *showing*
    the agent.  Mining it plants a bogus t0 and a bogus quote on the rule."""
    assert analyse_text(text).is_correction is False, text


def test_a_correction_that_quotes_code_inline_still_counts():
    """Only fenced blocks are stripped: inline code carries the subject."""
    got = analyse_text("不要用 `pip install`，改用 `uv add`。")
    assert got.is_correction is True
    assert "pip install" in got.sentence


def test_a_pasted_block_does_not_create_a_topic(home_builder):
    home = home_builder({S1: [
        ("human", day(1, 9, 0), "帮我看看这个构建日志。"),
        ("assistant", day(1, 9, 1), "Reading."),
        ("tool", day(1, 9, 2), "Bash", {"command": "cat build.log"}),
        ("human", day(1, 9, 3),
         "日志在这里：\n```\nERROR: don't use pip\n不要用 pip，用 uv\n```\n是什么意思？"),
    ]})
    result = mine(home)
    assert result.n_corrections == 0
    assert result.topics == []


def test_a_hostile_model_answer_is_rejected_not_crashed():
    """RecursionError is not a ValueError: 60 000 nested brackets in the answer
    must be a rejected draft, not a traceback out of the CLI."""
    from precedent.llm import extract_json_rules
    assert extract_json_rules("[" * 60000 + "]" * 60000) == []
    assert extract_json_rules("") == []
    assert extract_json_rules("no json here at all") == []


def test_at_most_three_drafts_are_ever_considered():
    from precedent.llm import MAX_DRAFTS, extract_json_rules
    many = json.dumps([{"tool": "Bash", "n": i} for i in range(50)])
    assert len(extract_json_rules(many)) == MAX_DRAFTS


def test_a_trailing_catch_all_is_refused_and_says_why():
    """`\\s+.+` is quadratic on a 4 096-char subject and buys nothing: a search
    does not need a trailing wildcard.  The rejection must say so."""
    problems = lint_regex(r"(?i)pip\s+install\s+.+")
    assert problems and "trailing" in problems[0]
    assert lint_regex(r"(?i)pip\s+install\b") == []
