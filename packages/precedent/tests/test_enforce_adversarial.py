# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""B-ENFORCE, ADVERSARIALLY — one section per safety invariant of the stage.

These are written to break the enforcement layer, not to demonstrate it:

1. ``settings.json`` — a randomised property test over pre-existing hook
   blocks (merge is idempotent and loses nothing; uninstall round-trips), plus
   the cases where a *merge* would really be a *replace*: a file that is not
   valid JSON, a file that is not an object, a ``hooks`` value that is not an
   object.  Regression for the bug that silently replaced a hand-edited
   ``settings.json`` with precedent's own six entries.
2. ``uninstall`` removes **exactly** ours: not a script the user parked next to
   ours, not an event key we took nothing out of.
3. the six hook scripts, fuzzed through a real subprocess: exit 0, stdout is
   empty or one JSON object, no ``deny``/``ask`` without a confirmed rule, no
   write outside the state dir, and never slower than the budget — including
   the 8 KB Bash command that used to take 32 s inside ``PreToolUse``.
4. ownership: the whole decision matrix, and the two symlink routes that used
   to make a governed tree invisible.

Everything runs against synthetic homes under ``tmp_path``; ``real_home_canary``
asserts the user's own ``~/.claude`` was untouched.
"""

from __future__ import annotations

import copy
import json
import os
import random
import stat
import subprocess
import sys
import time

import pytest
from conftest import tree_fingerprint
from precedent import _hooklib as H
from precedent.cli import main
from precedent.hooks import (HOOK_EVENTS, MARKER, SettingsUnreadable,
                             foreign_entries_in_our_hooks_dir, hooks_block,
                             install, is_precedent_entry, merge_settings,
                             read_settings, uninstall, uninstall_settings,
                             write_settings)
from precedent.hooks import status as hooks_status
from precedent.ownership import set_guard
from precedent.state import StateDir

SESSION = "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1"
HOOKS_DIR = "/st/hooks"


def _ours(script="pre_tool_use.py", event="PreToolUse", hooks_dir=HOOKS_DIR):
    return {"matcher": "Bash",
            "hooks": [{"type": "command",
                       "command": f"python3 {hooks_dir}/{script} {MARKER} {event}",
                       "timeout": 5}]}


# ==========================================================================
# 1. settings.json: a property test over random pre-existing hook blocks
# ==========================================================================

_EVENTS = ("PreToolUse", "PostToolUse", "Stop", "SessionStart", "Notification",
           "SubagentStop", "UserPromptSubmit", "InstructionsLoaded", "PreCompact")


def _random_entry(rng, i):
    return rng.choice([
        {"matcher": rng.choice(["Bash", "Write", "*", "Edit|Write", ""]),
         "hooks": [{"type": "command", "command": f"/bin/echo {i}",
                    "timeout": rng.choice([1, 5, 30])}]},
        {"hooks": [{"type": "command", "command": f"other-tool --run {i}"}]},
        {"matcher": "Bash", "hooks": []},
        f"a bare string entry {i}",                       # not even a dict
        {"matcher": 5, "hooks": [{"type": "command", "command": None}]},
        _ours(),                                          # a stale entry of ours
        # somebody else's precedent install, pointed at a *different* state dir
        _ours(hooks_dir="/other/state/hooks"),
    ])


def _random_settings(rng):
    doc = {"model": "opus", "env": {"A": "1"}, "permissions": {"allow": ["Bash"]}}
    if rng.random() < 0.85:
        doc["hooks"] = {ev: [_random_entry(rng, i)
                             for i in range(rng.randint(0, 3))]
                        for ev in rng.sample(_EVENTS, rng.randint(0, 6))}
    return doc


def _entries_of(doc, event):
    return ((doc.get("hooks") or {}).get(event) or []) if isinstance(doc, dict) else []


def _key(obj):
    return json.dumps(obj, sort_keys=True)


def test_merge_is_idempotent_and_never_loses_a_pre_existing_entry():
    """200 random settings.json files, each merged twice.

    Invariants: the second merge is a no-op; every key and every hook entry the
    user already had is still there, in order; and we leave exactly one entry of
    our own per event (two would run the hook twice per tool call).
    """
    rng = random.Random(20260915)
    block = hooks_block([], HOOKS_DIR)
    for _ in range(200):
        before = _random_settings(rng)
        frozen = copy.deepcopy(before)
        merged = merge_settings(before, block)

        assert _key(before) == _key(frozen), "merge_settings mutated its input"
        assert _key(merge_settings(merged, block)) == _key(merged)

        for k, v in frozen.items():
            if k != "hooks":
                assert merged[k] == v
        for event in (frozen.get("hooks") or {}):
            theirs = [e for e in _entries_of(frozen, event)
                      if not is_precedent_entry(e, HOOKS_DIR)]
            got = [_key(e) for e in _entries_of(merged, event)]
            assert got[:len(theirs)] == [_key(e) for e in theirs], (event, got)
        for event, _script, _timeout, _what in HOOK_EVENTS:
            mine = [e for e in _entries_of(merged, event)
                    if is_precedent_entry(e, HOOKS_DIR)]
            assert len(mine) == 1, (event, mine)


def _normalised(doc):
    """Collapse the three spellings of "no hooks here" before comparing.

    An absent ``hooks`` key, ``{"hooks": {}}`` and ``{"hooks": {"Stop": []}}``
    are the same thing to Claude Code, and ``uninstall`` cannot tell which of
    them the user had before the merge — so it keeps the ``hooks`` key it may
    not have created and drops the event lists it emptied.  The round trip is
    asserted up to that equivalence; what must *not* happen — an event key we
    took nothing out of being deleted — has its own test below.
    """
    out = dict(doc)
    hooks = out.get("hooks")
    if isinstance(hooks, dict):
        hooks = {k: v for k, v in hooks.items() if v != []}
        if hooks:
            out["hooks"] = hooks
        else:
            out.pop("hooks", None)
    return out


def test_uninstall_round_trips_a_random_settings_json():
    """install-then-uninstall is the identity on everything that was there."""
    rng = random.Random(4242)
    block = hooks_block([], HOOKS_DIR)
    for _ in range(200):
        before = _random_settings(rng)
        # a stale entry of ours that was already in the file is *ours* to remove,
        # so compare against the file with those taken out.
        baseline, _n = uninstall_settings(before, HOOKS_DIR)
        merged = merge_settings(before, block)
        back, removed = uninstall_settings(merged, HOOKS_DIR)
        assert removed >= len(HOOK_EVENTS)
        assert _key(_normalised(back)) == _key(_normalised(baseline))
        # and uninstall is idempotent too
        again, removed2 = uninstall_settings(back, HOOKS_DIR)
        assert removed2 == 0 and _key(again) == _key(back)


# ==========================================================================
# 1b. a merge that would really be a replace must be refused
# ==========================================================================

@pytest.fixture
def home(tmp_path):
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    os.makedirs(state.claude_home, exist_ok=True)
    return state


BROKEN = {
    "trailing comma": '{\n "model": "opus",\n "permissions": {"allow": []},\n}\n',
    "a comment": '// my settings\n{"model": "opus"}\n',
    "truncated": '{"model": "opus", "hooks": {"PreToolUse": [',
    "a json array": '[{"model": "opus"}]',
    "a json string": '"hello"',
    "hooks is a list": '{"model":"opus","hooks":[{"PreToolUse":[]}]}',
    "hooks is a string": '{"model":"opus","hooks":"none"}',
    "empty file": "",
}


@pytest.mark.parametrize("label", sorted(BROKEN))
def test_a_settings_json_we_cannot_merge_into_is_never_overwritten(
        home, label, real_home_canary):
    """The regression that matters most in this stage.

    ``json.load`` failing and the file not existing look identical to a reader
    with ``default=None``; treating the first like the second replaced the
    user's whole ``settings.json`` with our six entries (with a backup, and a
    message that said "merged").  Now: nothing is written and the reason is
    named.
    """
    settings = os.path.join(home.claude_home, "settings.json")
    with open(settings, "w", encoding="utf-8") as fh:
        fh.write(BROKEN[label])
    before = open(settings, encoding="utf-8").read()

    doc, kind, why = read_settings(settings)
    assert kind == "unreadable" and why

    out = install(home, [], settings_path=settings, write_settings_file=True)
    assert out["refused"] and out["changed"] is False
    assert out["backup"] is None                 # nothing to back up: nothing done
    assert open(settings, encoding="utf-8").read() == before
    assert os.listdir(home.backups_dir) == []
    # the scripts are still written — they are inert until settings points at them
    assert os.path.isfile(home.hook_script("pre_tool_use.py"))

    # uninstall is equally hands-off
    un = uninstall(home, settings)
    assert un["removed"] == 0 and un["changed"] is False and un["refused"]
    assert open(settings, encoding="utf-8").read() == before
    real_home_canary()


def test_the_cli_refuses_loudly_and_exits_non_zero(tmp_path, capsys,
                                                   real_home_canary):
    claude = tmp_path / "claude"
    claude.mkdir()
    settings = claude / "settings.json"
    settings.write_text('{"model": "opus",}\n', encoding="utf-8")
    before = settings.read_text(encoding="utf-8")
    rc = main(["hooks", "install", "claude-code", "--apply",
               "--claude-home", str(claude), "--state-dir", str(tmp_path / "st")])
    out = capsys.readouterr()
    assert rc == 2
    assert "REFUSED" in out.err and "not valid JSON" in out.err
    assert "REFUSED" in out.out            # the dry-run plan says so too
    assert settings.read_text(encoding="utf-8") == before
    real_home_canary()


def test_merge_settings_raises_rather_than_dropping_a_hooks_value():
    with pytest.raises(SettingsUnreadable):
        merge_settings({"hooks": ["not", "an", "object"]},
                       hooks_block([], HOOKS_DIR))
    # None is fine: there is nothing to lose
    assert merge_settings({"hooks": None}, hooks_block([], HOOKS_DIR))["hooks"]


# ==========================================================================
# 2. uninstall removes exactly ours
# ==========================================================================

def test_a_users_own_script_in_our_hooks_dir_is_never_removed(home,
                                                              real_home_canary):
    """Ours is marked twice — the directory *and* ``--precedent-hook``.

    Matching on the directory alone made anything the user parked next to our
    scripts ours to delete.
    """
    settings = os.path.join(home.claude_home, "settings.json")
    theirs = {"matcher": "Bash",
              "hooks": [{"type": "command",
                         "command": f"python3 {home.hooks_dir}/my_helper.py"}]}
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({"hooks": {"PreToolUse": [theirs]}}, fh)

    assert is_precedent_entry(theirs, home.hooks_dir) is False
    install(home, [], settings_path=settings, write_settings_file=True)
    doc = json.load(open(settings, encoding="utf-8"))
    assert theirs in doc["hooks"]["PreToolUse"]

    out = uninstall(home, settings)
    assert out["removed"] == len(HOOK_EVENTS)
    assert json.load(open(settings, encoding="utf-8")) == {
        "hooks": {"PreToolUse": [theirs]}}
    real_home_canary()


def test_a_foreign_script_in_our_hooks_dir_is_reported_as_drift(home):
    settings = os.path.join(home.claude_home, "settings.json")
    install(home, [], settings_path=settings, write_settings_file=True)
    doc = json.load(open(settings, encoding="utf-8"))
    doc["hooks"]["PreToolUse"].append(
        {"hooks": [{"type": "command",
                    "command": f"python3 {home.hooks_dir}/my_helper.py"}]})
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    st = hooks_status(home, [], settings)
    assert len(st["foreign"]) == 1
    assert any("without our marker" in d for d in st["drift"])


def test_uninstall_leaves_alone_an_event_it_took_nothing_from(home):
    settings = os.path.join(home.claude_home, "settings.json")
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({"hooks": {"Notification": [], "PreCompact": [
            {"hooks": [{"type": "command", "command": "mine.sh"}]}]}}, fh)
    install(home, [], settings_path=settings, write_settings_file=True)
    uninstall(home, settings)
    doc = json.load(open(settings, encoding="utf-8"))
    assert doc["hooks"]["Notification"] == []          # an empty list they had
    assert doc["hooks"]["PreCompact"][0]["hooks"][0]["command"] == "mine.sh"


def test_a_second_precedent_install_elsewhere_is_not_ours_to_remove():
    other = _ours(hooks_dir="/other/state/hooks")
    assert is_precedent_entry(other, HOOKS_DIR) is False
    out, removed = uninstall_settings({"hooks": {"PreToolUse": [other]}},
                                      HOOKS_DIR)
    assert removed == 0 and out["hooks"]["PreToolUse"] == [other]
    assert foreign_entries_in_our_hooks_dir(
        {"hooks": {"PreToolUse": [other]}}, HOOKS_DIR) == []


def test_write_settings_keeps_the_file_mode(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{}", encoding="utf-8")
    os.chmod(p, 0o600)
    write_settings(str(p), {"model": "opus"})
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert json.loads(p.read_text(encoding="utf-8")) == {"model": "opus"}
    assert [n for n in os.listdir(tmp_path) if ".tmp" in n] == []


# ==========================================================================
# 2b. only `hooks install/uninstall --apply` may write a settings.json
# ==========================================================================

READ_ONLY_COMMANDS = (
    ["init"], ["scan"], ["mine"], ["docket"], ["docket", "--batch"],
    ["report"], ["snapshot"], ["own"], ["hooks", "status", "claude-code"],
    ["hooks", "install", "claude-code"],          # dry run is the default
    ["hooks", "uninstall", "claude-code"],
)


@pytest.mark.parametrize("argv", READ_ONLY_COMMANDS,
                         ids=[" ".join(a) for a in READ_ONLY_COMMANDS])
def test_no_command_but_apply_touches_the_claude_home(mining_home, tmp_path,
                                                      argv, real_home_canary):
    settings = os.path.join(mining_home.home, "settings.json")
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({"model": "opus", "hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "theirs.sh"}]}]}}, fh)
    before = tree_fingerprint(mining_home.home)
    main(argv + ["--claude-home", mining_home.home,
                 "--state-dir", str(tmp_path / "st")])
    assert tree_fingerprint(mining_home.home) == before
    real_home_canary()


def test_apply_writes_the_settings_json_and_nothing_else_in_the_home(
        mining_home, tmp_path, real_home_canary):
    settings = os.path.join(mining_home.home, "settings.json")
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({"model": "opus"}, fh)
    before = tree_fingerprint(mining_home.home)
    rc = main(["hooks", "install", "claude-code", "--apply",
               "--claude-home", mining_home.home,
               "--state-dir", str(tmp_path / "st")])
    assert rc == 0
    after = tree_fingerprint(mining_home.home)
    assert set(after) == set(before)
    changed = {k for k in after if after[k] != before[k]}
    assert changed == {"settings.json"}
    assert json.load(open(settings, encoding="utf-8"))["model"] == "opus"
    # the backup is in the state dir, and it is the file as it was
    backups = os.listdir(os.path.join(str(tmp_path / "st"), "backups"))
    assert any(n.endswith(".json") and not n.endswith(".meta.json")
               for n in backups)
    real_home_canary()


# ==========================================================================
# 3. the six hook scripts, fuzzed
# ==========================================================================

@pytest.fixture
def suite(tmp_path):
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    mem = os.path.join(state.claude_home, "projects", "-repo", "memory")
    os.makedirs(mem, exist_ok=True)
    with open(os.path.join(mem, "project.md"), "w", encoding="utf-8") as fh:
        fh.write("# project\n\nuse uv, never pip\n")
    install(state, [])
    state.memory = mem
    return state


def _run(state, script, payload, timeout=30):
    env = dict(os.environ, PRECEDENT_STATE_DIR=state.root,
               PRECEDENT_CLAUDE_HOME=state.claude_home)
    body = payload if isinstance(payload, str) else json.dumps(payload)
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, state.hook_script(script), MARKER, "X"],
                          input=body, capture_output=True, text=True, env=env,
                          timeout=timeout)
    return proc, (time.monotonic() - t0) * 1000


def _fuzz_payloads(rng, target):
    """Hostile-but-plausible hook payloads, plus a few that are not JSON at all."""
    weird_paths = [target, target + "/../../../etc/passwd", "", None, 12,
                   "~/.claude/skills/x/SKILL.md", "/dev/null", "\x00evil",
                   os.path.dirname(target)]
    weird_cmds = [
        "echo hi > " + target,
        "sed " + "-e s/a/b/ " * 60 + "-i " + target,
        "tee " + "a" * 400 + " " + target,
        "cat <<'EOF'\n" + target + "\nEOF",
        "$(" * 60 + target + ")" * 60,
        "rm -rf /", "", None, ["not", "a", "string"],
        "|" * 200 + target, "A=" + target + " && python3 -c \"open('x','w')\"",
    ]
    out = []
    for _ in range(60):
        payload = {
            "hook_event_name": rng.choice(
                [e[0] for e in HOOK_EVENTS] + ["", None, "Nonsense"]),
            "session_id": rng.choice([SESSION, "", None, "../../etc/passwd",
                                      "x" * 4000, 17, {"a": 1}]),
            "cwd": rng.choice(["/repo", None, 5, "/"]),
            "tool_name": rng.choice(["Bash", "Write", "Edit", "MultiEdit",
                                     "NotebookEdit", "Agent", "", None, 3]),
            "tool_input": rng.choice([
                {"file_path": rng.choice(weird_paths),
                 "content": rng.choice(["x" * 5000, "", None])},
                {"command": rng.choice(weird_cmds)},
                {"file_path": target, "old_string": "uv", "new_string": "pip"},
                {"file_path": target,
                 "edits": [{"old_string": "uv", "new_string": "pip" * 50}] * 120},
                [1, 2, 3], "a string", None, {},
            ]),
            "agent_id": rng.choice(["night-fork", None, "", 4]),
            "files": rng.choice([
                [{"path": target, "content": "x"}], [target], {target: "x"},
                "nope", None, [None, 5, {"path": None}]]),
            "tool_response": rng.choice([{"is_error": True}, {}, None, "ok"]),
        }
        out.append(payload)
    out += ["", "not json", "[]", "null", "0", '{"a": ' + '[' * 200,
            '{"tool_input": {"command": "' + "x" * 30000 + '"}}']
    return out


@pytest.mark.parametrize("script", [e[1] for e in HOOK_EVENTS])
def test_fuzzed_payloads_never_break_a_hook(suite, script):
    """exit 0, stdout is empty or exactly one JSON object, never a block.

    ``deny``/``ask`` must not appear at all: ``precedents.json`` does not exist,
    so there is no confirmed precedent, so there is nothing that may block.

    Timing is asserted in :func:`test_a_hook_is_fast_relative_to_a_bare_python`
    rather than here, because the number this loop can measure is
    ``subprocess.run`` wall clock — interpreter startup plus scheduler — and on
    a loaded machine that says nothing about the hook.  Measured: the same
    payloads that pass at ~90 ms idle took 1,530 ms and 4,618 ms at load
    average 20, while the hook's own recorded work in production was 0.33–0.53
    ms.  A test that fails because the laptop is busy is a test that teaches
    people to ignore it.
    """
    rng = random.Random(1337)
    set_guard(suite, True)                 # the guard is armed; still no rules
    set_guard(suite, False)                # …and then disarmed: no active rule
    for payload in _fuzz_payloads(rng, os.path.join(suite.memory, "project.md")):
        proc, ms = _run(suite, script, payload)
        assert proc.returncode == 0, (payload, proc.stderr)
        text = proc.stdout.strip()
        if text:
            doc = json.loads(text)         # one object, or the contract is broken
            assert isinstance(doc, dict)
            assert "deny" not in json.dumps(doc) and "ask" not in json.dumps(doc)


@pytest.mark.parametrize("script", [e[1] for e in HOOK_EVENTS])
def test_a_hook_is_fast_relative_to_a_bare_python(suite, script):
    """The hook must not cost much more than starting Python at all.

    The invariant is "never break Claude Code", and the budget that matters is
    the hook's own work, not the interpreter it is hosted in.  So the ceiling is
    a *ratio* against a `python3 -c pass` measured on the same machine in the
    same second: whatever the load is doing to one, it is doing to the other.
    """
    baseline = []
    for _ in range(3):
        t0 = time.perf_counter()
        subprocess.run([sys.executable, "-c", "pass"], capture_output=True)
        baseline.append((time.perf_counter() - t0) * 1000)
    floor = min(baseline)
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": "echo hi"}, "cwd": suite.claude_home}
    best = min(_run(suite, script, payload)[1] for _ in range(3))
    # 8x a bare interpreter start, and never less than a 250 ms allowance so a
    # fast machine does not make the bound absurdly tight
    ceiling = max(floor * 8, 250)
    assert best < ceiling, (
        f"{script}: {best:.0f} ms vs a {floor:.0f} ms bare python "
        f"(ceiling {ceiling:.0f} ms)")


def test_no_hook_writes_outside_the_state_dir(suite, real_home_canary, tmp_path):
    """Fingerprint everything under tmp_path that is not the state dir."""
    rng = random.Random(99)
    target = os.path.join(suite.memory, "project.md")
    others = [str(tmp_path / d) for d in os.listdir(tmp_path)
              if str(tmp_path / d) != suite.root]
    before = {d: tree_fingerprint(d) for d in others}
    cwd_before = tree_fingerprint(os.getcwd())
    for script in [e[1] for e in HOOK_EVENTS]:
        for payload in _fuzz_payloads(rng, target)[:20]:
            _run(suite, script, payload)
    assert {d: tree_fingerprint(d) for d in others} == before
    assert tree_fingerprint(os.getcwd()) == cwd_before
    real_home_canary()


def test_an_eight_kilobyte_bash_command_is_decided_in_milliseconds(suite):
    """Regression: the old ``\\bsed\\b[^|;&]*?-i[^|;&]*?<path>`` search was
    quadratic in two places at once.  Thirty distinct governed-looking paths
    followed by a dense ``sed -i`` tail took **32 seconds** — past the hook's
    own 5 s timeout, on the hot path of every Bash call the agent makes.
    """
    home = suite.claude_home
    toks = " ".join(f"{home}/skills/a{i}/SKILL.md" for i in range(30))
    cmd = (toks + " " + "sed -i ab " * 600)[:8192]
    t0 = time.monotonic()
    for _ in range(5):
        H.bash_write_targets(cmd, home)
    assert (time.monotonic() - t0) / 5 * 1000 < 50

    proc, ms = _run(suite, "pre_tool_use.py",
                    {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                     "session_id": SESSION, "tool_input": {"command": cmd}})
    assert proc.returncode == 0 and ms < 300


@pytest.mark.parametrize("status", ["candidate", "retired", "", None, "ACTIVE",
                                    "active "])
def test_only_a_rule_whose_status_is_exactly_active_can_block(suite, status):
    rule = {"id": "p-x", "hook": "PreToolUse", "tool": "Bash", "action": "deny",
            "scope": "global", "message": "no pip",
            "matchers": [{"type": "input_regex", "field": "command",
                          "regex": "pip"}]}
    if status is not None:
        rule["status"] = status
    suite.write_precedents([rule])
    proc, _ms = _run(suite, "pre_tool_use.py",
                     {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                      "session_id": SESSION,
                      "tool_input": {"command": "pip install x"}})
    assert json.loads(proc.stdout or "{}") == {}

    rule["status"] = "active"
    suite.write_precedents([rule])
    proc, _ms = _run(suite, "pre_tool_use.py",
                     {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                      "session_id": SESSION,
                      "tool_input": {"command": "pip install x"}})
    hso = json.loads(proc.stdout)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"


@pytest.mark.parametrize("script,event", [(e[1], e[0]) for e in HOOK_EVENTS])
def test_only_the_deciding_hook_ever_prints_anything(suite, script, event):
    """A hook with nothing to say says nothing.

    For ``UserPromptSubmit`` and ``SessionStart``, Claude Code puts a hook's
    stdout in front of the model; an empty object is still a change to the
    prompt, and we have no reason to make one.
    """
    proc, _ms = _run(suite, script, {"hook_event_name": event,
                                     "session_id": SESSION,
                                     "prompt": "hello", "tool_name": "Read",
                                     "tool_input": {"file_path": "/repo/x.py"}})
    assert proc.stdout == ("{}\n" if event == "PreToolUse" else "")


# ==========================================================================
# 4. ownership
# ==========================================================================

OWNERSHIP_MATRIX = [
    # (agent kind, declared owner, guard on) -> decision
    ("subagent", "user", True, "ask"),
    ("subagent", "user", False, "allow"),
    ("subagent", "agent", True, "allow"),
    ("foreground", "user", True, "allow"),
    ("foreground", "agent", True, "allow"),
    ("foreground", "user", False, "allow"),
]


@pytest.mark.parametrize("kind,owner,guard,want", OWNERSHIP_MATRIX,
                         ids=[f"{k}-{o}-guard{'On' if g else 'Off'}"
                              for k, o, g, _ in OWNERSHIP_MATRIX])
def test_the_ownership_decision_matrix(suite, kind, owner, guard, want):
    from precedent.ownership import own
    target = os.path.join(suite.memory, "project.md")
    own(suite, target, owner)
    set_guard(suite, guard)
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
               "session_id": SESSION, "cwd": "/repo",
               "tool_input": {"file_path": target, "old_string": "uv",
                              "new_string": "pip"}}
    if kind == "subagent":
        payload["agent_id"] = "night-fork"
    proc, _ms = _run(suite, "pre_tool_use.py", payload)
    out = json.loads(proc.stdout or "{}")
    got = (out.get("hookSpecificOutput") or {}).get("permissionDecision", "allow")
    assert got == want
    # whatever the decision, the write is always recorded with its pre-image
    rec = [json.loads(x) for x in
           open(suite.write_candidates_path, encoding="utf-8") if x.strip()][-1]
    assert rec["owner"] == owner and rec["agent"] == kind
    assert os.path.isfile(rec["snapshot"]["blob"])


def test_ownership_never_denies_only_ever_asks(suite):
    """The guard is an `ask`, by construction, in every shape of payload."""
    rng = random.Random(5)
    from precedent.ownership import own
    target = os.path.join(suite.memory, "project.md")
    own(suite, target, "user")
    set_guard(suite, True)
    for payload in _fuzz_payloads(rng, target):
        proc, _ms = _run(suite, "pre_tool_use.py", payload)
        out = json.loads(proc.stdout or "{}")
        decision = (out.get("hookSpecificOutput") or {}).get("permissionDecision")
        assert decision in (None, "ask"), (decision, str(payload)[:100])


def test_a_symlinked_claude_home_is_still_a_governed_tree(tmp_path):
    """Regression: every dotfile manager makes ``~/.claude`` a symlink.

    The hook compared the *unresolved* home against a path the tool had already
    resolved, so the whole governed tree read as ungoverned: no snapshot, no
    candidate, no ask.
    """
    real = tmp_path / "elsewhere" / "claude"
    (real / "skills" / "x").mkdir(parents=True)
    (real / "skills" / "x" / "SKILL.md").write_text("hi\n", encoding="utf-8")
    link = tmp_path / "claude"
    os.symlink(str(real), str(link))
    for home in (str(link), str(real)):
        for path in (str(real / "skills" / "x" / "SKILL.md"),
                     str(link / "skills" / "x" / "SKILL.md")):
            assert H.classify_path(path, home) == "skills", (home, path)


def test_a_symlink_into_a_governed_tree_is_still_governed(tmp_path):
    """…and the same trick the other way round is a one-line ownership bypass."""
    home = tmp_path / "claude"
    (home / "skills" / "x").mkdir(parents=True)
    real = home / "skills" / "x" / "SKILL.md"
    real.write_text("hi\n", encoding="utf-8")
    side = tmp_path / "shortcut.md"
    os.symlink(str(real), str(side))
    assert H.classify_path(str(side), str(home)) == "skills"
    H.configure(str(tmp_path / "state"), str(home))
    assert H.write_targets("Write", {"file_path": str(side)}) == [
        (str(side), "tool")]
    assert H.bash_write_targets(f"echo x > {side}", str(home)) == [
        (str(side), "redirect")]


def test_classify_path_still_says_no_to_everything_else(tmp_path):
    home = str(tmp_path / "claude")
    for path in ("/repo/src/main.py", home + "/settings.json", "/repo/README.md",
                 home + "/projects/-a-b/session.jsonl", "/repo/.claude/foo.json",
                 home + "/skills/../../etc/passwd", "", None, 5):
        assert H.classify_path(path, home) is None, path


# ==========================================================================
# 5. the installed library is the library the tests import
# ==========================================================================

def test_the_installed_plib_is_hooklib_byte_for_byte(suite):
    """The README's central claim about the hook runtime, asserted.

    If these two ever diverge, every test in this file is testing code that is
    not the code Claude Code runs.
    """
    import precedent._hooklib as module
    with open(module.__file__, "rb") as fh:
        source = fh.read()
    with open(suite.hook_script("_plib.py"), "rb") as fh:
        installed = fh.read()
    assert installed == source


def test_more_governed_writes_than_the_cap_are_logged_not_dropped(suite):
    """A governed write we decline to record has to say so.

    The cap exists (a hook is not a place for unbounded work); a *silent* cap is
    the failure this whole tool is aimed at.
    """
    mem = suite.memory
    paths = []
    for i in range(H.MAX_TARGETS + 4):
        q = os.path.join(mem, f"f{i}.md")
        with open(q, "w", encoding="utf-8") as fh:
            fh.write("a\n")
        paths.append(q)
    cmd = " && ".join(f"echo x > {q}" for q in paths)
    proc, ms = _run(suite, "pre_tool_use.py",
                    {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                     "session_id": SESSION, "tool_input": {"command": cmd}})
    assert proc.returncode == 0 and ms < 300
    rows = [json.loads(x) for x in
            open(suite.write_candidates_path, encoding="utf-8") if x.strip()]
    assert len(rows) == H.MAX_TARGETS
    log = [json.loads(x) for x in
           open(suite.hooklog_path, encoding="utf-8") if x.strip()]
    trunc = [r for r in log if r.get("event") == "targets_truncated"]
    assert trunc and trunc[0]["n"] == H.MAX_TARGETS + 4
    assert trunc[0]["dropped"]


def test_a_four_megabyte_governed_file_is_still_inside_the_budget(suite):
    big = os.path.join(suite.memory, "big.md")
    with open(big, "w", encoding="utf-8") as fh:
        fh.write("x" * 3_900_000)
    for payload in (
        {"tool_name": "Write", "tool_input": {"file_path": big,
                                              "content": "y" * 3_900_000}},
        {"tool_name": "Edit", "tool_input": {"file_path": big,
                                             "old_string": "x",
                                             "new_string": "z"}},
        {"tool_name": "Bash", "tool_input": {"command": f"echo x > {big}"}},
    ):
        payload.update(hook_event_name="PreToolUse", session_id=SESSION)
        proc, ms = _run(suite, "pre_tool_use.py", payload)
        assert proc.returncode == 0 and ms < 300, (payload["tool_name"], ms)


def test_a_write_a_precedent_already_denied_is_not_queued_for_a_decision(suite):
    """The docket is for things that still need a human.

    A governed write that a confirmed precedent denied is still recorded — it is
    evidence either way — but as `blocked`, not as `pending`.
    """
    from precedent.docket import build_entries
    from precedent.ownership import own
    target = os.path.join(suite.memory, "project.md")
    own(suite, target, "user")
    set_guard(suite, True)
    guard = suite.precedents()
    suite.write_precedents(guard + [
        {"id": "p-nopip", "hook": "PreToolUse", "tool": "Edit", "action": "deny",
         "status": "active", "scope": "global", "message": "no pip",
         "matchers": [{"type": "input_regex", "field": "file_path",
                       "regex": "project"}]}])
    proc, _ms = _run(suite, "pre_tool_use.py",
                     {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                      "session_id": SESSION, "agent_id": "night-fork",
                      "tool_input": {"file_path": target, "old_string": "uv",
                                     "new_string": "pip"}})
    hso = json.loads(proc.stdout)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"        # the rule wins over the ask
    rec = [json.loads(x) for x in
           open(suite.write_candidates_path, encoding="utf-8") if x.strip()][-1]
    assert rec["status"] == "blocked" and rec["blockedBy"] == "p-nopip"
    assert os.path.isfile(rec["snapshot"]["blob"])    # evidence is still kept
    assert [e for e in build_entries(suite) if e["id"] == rec["id"]] == []
