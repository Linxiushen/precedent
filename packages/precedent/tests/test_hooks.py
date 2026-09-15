# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""The enforcement layer: the settings block, the merge, and the hook script.

The script is exercised **end to end** — written to disk and fed a real
PreToolUse payload on stdin through a subprocess — because that is the only
way to test the thing Claude Code will actually run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from precedent.hooks import (HOOK_EVENTS, MARKER, backup_path_for,
                             backup_settings, hooks_block,
                             hooks_key_was_ours,
                             install, is_precedent_entry, merge_settings,
                             pre_tool_matcher, render_hook_script, render_plan,
                             render_scripts, render_status, settings_diff,
                             settings_text, status, uninstall,
                             uninstall_settings)
from precedent.state import StateDir

DENY = {"id": "p-aaa", "hook": "PreToolUse", "tool": "Bash",
        "input_regex": "(?i)(?<![A-Za-z0-9_])pip install(?![A-Za-z0-9_])",
        "action": "deny", "message": "用 uv，不要用 pip", "scope": "project",
        "quote": "不要用 pip，用 uv。", "quoteDate": "2026-09-01", "status": "active"}
ASK = {"id": "p-bbb", "hook": "PreToolUse", "tool": "Edit|Write",
       "input_regex": r"(?i)/tmp/mem/protected\.md", "action": "ask",
       "message": "不要修改 protected.md", "scope": "project",
       "quote": "不要修改 protected.md 这个文件", "quoteDate": "2026-09-03",
       "status": "active"}
RETIRED = dict(DENY, id="p-ccc", input_regex="(?i)npm", status="retired")


@pytest.fixture
def hooked(tmp_path):
    """A state dir with the script installed and two active precedents."""
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    os.makedirs(state.claude_home, exist_ok=True)
    state.write_precedents([DENY, ASK, RETIRED])
    install(state, [DENY, ASK])
    return state


def _run(state, payload, env=None):
    e = dict(os.environ, PRECEDENT_STATE_DIR=state.root, PRECEDENT_HOOK_DEBUG="1")
    e.update(env or {})
    proc = subprocess.run([sys.executable, state.hook_script_path],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=e, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout or "{}"), proc.stderr


# --------------------------------------------------------------------------
# the script, end to end
# --------------------------------------------------------------------------

def test_hook_denies_a_matching_bash_command(hooked):
    out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": "pip install requests"}})
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    reason = hso["permissionDecisionReason"]
    assert reason.startswith("[precedent p-aaa]")
    assert "用 uv，不要用 pip" in reason
    assert "2026-09-01" in reason
    assert "不要用 pip，用 uv。" in reason


def test_hook_asks_on_a_protected_path(hooked):
    out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Write",
                           "tool_input": {"file_path": "/tmp/mem/protected.md",
                                          "content": "x"}})
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    # the same rule covers Edit
    out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                           "tool_input": {"file_path": "/tmp/mem/protected.md",
                                          "old_string": "a", "new_string": "b"}})
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_hook_stays_silent_when_nothing_matches(hooked):
    for payload in (
        {"hook_event_name": "PreToolUse", "tool_name": "Bash",
         "tool_input": {"command": "uv add flask"}},
        {"hook_event_name": "PreToolUse", "tool_name": "Read",
         "tool_input": {"file_path": "/tmp/mem/protected.md"}},
        {"hook_event_name": "PreToolUse", "tool_name": "Write",
         "tool_input": {"file_path": "/tmp/mem/notes.md", "content": "x"}},
        {"hook_event_name": "PostToolUse", "tool_name": "Bash",
         "tool_input": {"command": "pip install requests"}},
    ):
        out, _ = _run(hooked, payload)
        assert out == {}, payload


def test_hook_ignores_retired_rules(hooked):
    out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": "npm install express"}})
    assert out == {}


def test_hook_is_fail_open(hooked, tmp_path):
    # torn stdin: exit 0, NO stdout, and the error lands in hooklog.jsonl
    proc = subprocess.run([sys.executable, hooked.hook_script_path], input="not json",
                          capture_output=True, text=True,
                          env=dict(os.environ, PRECEDENT_STATE_DIR=hooked.root))
    assert proc.returncode == 0 and proc.stdout.strip() == ""
    log = open(hooked.hooklog_path, encoding="utf-8").read()
    assert '"event": "error"' in log and "JSONDecodeError" in log
    # missing precedents.json
    out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": "pip install x"}},
                  env={"PRECEDENT_STATE_DIR": str(tmp_path / "nowhere"),
                       "PRECEDENT_HOOK_DEBUG": ""})
    assert out == {}
    # an uncompilable regex is skipped, the rest of the rules still apply
    hooked.write_precedents([dict(DENY, id="p-bad", input_regex="("), DENY])
    out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": "pip install x"}})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_first_matching_rule_wins(hooked):
    hooked.write_precedents([dict(ASK, tool="Bash", input_regex="(?i)pip"), DENY])
    out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": "pip install x"}})
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


# --------------------------------------------------------------------------
# the settings block
# --------------------------------------------------------------------------

def test_hooks_block_is_one_entry_per_event_with_timeouts_and_markers():
    block = hooks_block([DENY, ASK, dict(DENY, id="p-dup")], "/s/hooks")
    assert list(block) == [e[0] for e in HOOK_EVENTS]
    # ONE PreToolUse entry, whose matcher is every tool an active rule names
    # union the ownership set — two entries would run the hook twice per call.
    assert len(block["PreToolUse"]) == 1
    assert block["PreToolUse"][0]["matcher"] == \
        "Bash|Edit|MultiEdit|NotebookEdit|Write"
    assert block["PostToolUse"][0]["matcher"] == "Write|Edit|Bash"
    for event, script, timeout, _what in HOOK_EVENTS:
        hook = block[event][0]["hooks"][0]
        assert hook["timeout"] == timeout
        assert hook["command"].endswith(f"{MARKER} {event}")
        assert f"/s/hooks/{script}" in hook["command"]
    # events that are not tool-scoped carry no matcher at all
    assert "matcher" not in block["UserPromptSubmit"][0]
    assert "matcher" not in block["Stop"][0]


def test_pre_tool_matcher_covers_ownership_and_star():
    assert pre_tool_matcher([]) == "Bash|Edit|MultiEdit|NotebookEdit|Write"
    assert "Agent" in pre_tool_matcher([{"tool": "Agent|Workflow"}])
    assert pre_tool_matcher([{"tool": "*"}]) == "*"


def test_merge_settings_is_non_destructive():
    existing = {"model": "opus", "hooks": {"PreToolUse": [{"matcher": "Task",
                                                           "hooks": [{"type": "command",
                                                                      "command": "mine"}]}]}}
    block = hooks_block([DENY], "/s/hooks")
    merged = merge_settings(existing, block)
    assert merged["model"] == "opus"
    assert merged["hooks"]["PreToolUse"][0]["matcher"] == "Task"
    assert len(merged["hooks"]["PreToolUse"]) == 2
    # idempotent
    assert merge_settings(merged, block) == merged
    assert existing["hooks"]["PreToolUse"] == [{"matcher": "Task",
                                                "hooks": [{"type": "command",
                                                           "command": "mine"}]}]


def test_merge_settings_from_nothing():
    merged = merge_settings(None, hooks_block([DENY], "/s/hooks"))
    assert set(merged["hooks"]) == {e[0] for e in HOOK_EVENTS}


def test_merge_replaces_our_own_stale_entry_rather_than_duplicating_it():
    """A rule whose tool changes must not leave the old matcher behind."""
    first = merge_settings(None, hooks_block([], "/s/hooks"))
    second = merge_settings(first, hooks_block([{"tool": "Agent"}], "/s/hooks"))
    assert len(second["hooks"]["PreToolUse"]) == 1
    assert "Agent" in second["hooks"]["PreToolUse"][0]["matcher"]


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------

def test_render_plan_shows_the_merge_and_the_script_and_writes_nothing(tmp_path):
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"), create=True)
    os.makedirs(state.claude_home)
    settings = os.path.join(state.claude_home, "settings.json")
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({"model": "opus"}, fh)
    before = os.path.getmtime(settings)

    plan, merged = render_plan(state, [DENY], settings, apply=False)
    assert settings in plan and "backup" in plan
    assert '"permissionDecision"' in plan          # the script body is included
    assert "PreToolUse" in json.dumps(merged)
    assert merged["model"] == "opus"
    assert os.path.getmtime(settings) == before
    assert not os.path.exists(state.hook_script_path)


def test_dry_run_prints_the_concrete_backup_path_and_the_exact_diff(tmp_path):
    """--dry-run answers the two questions you ask before an --apply: where does
    my old file go, and what exactly changes."""
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"), create=True)
    os.makedirs(state.claude_home)
    settings = os.path.join(state.claude_home, "settings.json")
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({"model": "opus"}, fh, indent=2)

    from datetime import datetime, timezone
    now = datetime(2026, 9, 15, 8, 3, 30, tzinfo=timezone.utc)
    plan, merged = render_plan(state, [DENY], settings, apply=False, now=now)

    # (1) a path, not a <ts> placeholder, and it is the one --apply would use.
    predicted = backup_path_for(state, settings, now=now)
    assert predicted is not None and "<ts>" not in plan
    assert os.path.dirname(predicted) == state.backups_dir
    assert os.path.basename(predicted) == "settings-20260915T080330Z.json"
    assert predicted in plan

    # (2) a real unified diff, from the bytes on disk to the bytes --apply writes
    assert "## settings.json — the exact diff" in plan
    assert "```diff" in plan
    assert "+  \"hooks\": {" in plan
    assert "+            \"command\": \"python3 " in plan
    assert " \"model\": \"opus\"," in plan or "-  \"model\": \"opus\"" in plan
    assert os.path.exists(settings) and not os.path.exists(predicted)


def test_the_dry_run_diff_is_the_bytes_apply_actually_writes(tmp_path):
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"), create=True)
    os.makedirs(state.claude_home)
    settings = os.path.join(state.claude_home, "settings.json")
    with open(settings, "w", encoding="utf-8") as fh:
        fh.write('{\n    "model": "opus"\n}\n')          # the user's own indent
    before = open(settings, encoding="utf-8").read()

    _plan, merged = render_plan(state, [DENY], settings, apply=False)
    diff = settings_diff(settings, merged)
    assert diff, "a merge that changes the file must produce a diff"

    out = install(state, [DENY], settings_path=settings, write_settings_file=True)
    after = open(settings, encoding="utf-8").read()
    assert after == settings_text(merged)
    assert open(out["backup"], encoding="utf-8").read() == before

    # the diff the dry run printed is the diff between the backup it took and
    # the file it wrote — body lines identical, only the ---/+++ labels differ.
    import difflib
    real = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                     n=3, lineterm=""))
    assert [l for l in diff if not l.startswith(("---", "+++"))] == \
           [l for l in real if not l.startswith(("---", "+++"))]


def test_dry_run_says_none_when_there_is_nothing_to_back_up(tmp_path):
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"), create=True)
    os.makedirs(state.claude_home)
    settings = os.path.join(state.claude_home, "settings.json")   # absent

    plan, _merged = render_plan(state, [DENY], settings, apply=False)
    assert "backup             : none" in plan
    assert "does not exist" in plan
    assert backup_path_for(state, settings) is None

    # and once installed, a second dry run is idempotent: empty diff, no backup
    install(state, [DENY], settings_path=settings, write_settings_file=True)
    plan2, _ = render_plan(state, [DENY], settings, apply=False)
    assert "idempotent         : yes" in plan2
    assert "(empty — the file on disk is already byte-identical" in plan2
    assert "backup             : none — nothing to change" in plan2


def test_backup_path_for_never_collides_and_never_writes(tmp_path):
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"), create=True)
    os.makedirs(state.claude_home)
    settings = os.path.join(state.claude_home, "settings.json")
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({"model": "opus"}, fh)
    from datetime import datetime, timezone
    now = datetime(2026, 9, 15, 8, 3, 30, tzinfo=timezone.utc)

    first = backup_path_for(state, settings, now=now)
    assert first == backup_settings(state, settings, now=now)
    second = backup_path_for(state, settings, now=now)      # same second
    assert second != first and second.endswith("-2.json")
    assert second == backup_settings(state, settings, now=now)
    assert not os.path.exists(backup_path_for(state, settings, now=now))


def test_install_writes_only_under_the_state_dir(tmp_path):
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"), create=True)
    os.makedirs(state.claude_home)
    out = install(state, [DENY])
    assert all(p.startswith(state.root + os.sep) for p in out["scripts"])
    assert out["settings"] is None and out["changed"] is False
    for _event, script, _timeout, _what in HOOK_EVENTS:
        assert os.path.exists(os.path.join(state.hooks_dir, script)), script
    assert os.path.exists(os.path.join(state.hooks_dir, "_plib.py"))
    assert os.access(state.hook_script_path, os.X_OK)
    assert os.path.exists(state.install_receipt_path)
    assert os.listdir(state.claude_home) == []     # the Claude home is untouched


def test_install_then_uninstall_round_trips_settings_json_byte_for_byte(tmp_path):
    """The file the user gets back is the file they had — no `"hooks": {}` scar."""
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"), create=True)
    os.makedirs(state.claude_home)
    settings = os.path.join(state.claude_home, "settings.json")
    original = '{\n  "model": "claude-fable-5-1[1m]",\n  "effortLevel": "xhigh"\n}\n'
    with open(settings, "w", encoding="utf-8") as fh:
        fh.write(original)

    install(state, [DENY], settings_path=settings, write_settings_file=True)
    assert hooks_key_was_ours(state) is True
    assert "precedent-hook" in open(settings, encoding="utf-8").read()

    out = uninstall(state, settings)
    assert out["removed"] == len(HOOK_EVENTS) and out["changed"]
    back = json.loads(open(settings, encoding="utf-8").read())
    assert back == json.loads(original)
    assert "hooks" not in back
    assert open(settings, encoding="utf-8").read() == original


def test_a_second_apply_does_not_forget_who_created_the_hooks_key(tmp_path):
    """The receipt answer is sticky.

    On the second `--apply` the `hooks` key exists *because the first one made
    it*.  Recording "it existed" there would make `uninstall` leave a
    `"hooks": {}` behind — the exact scar this pair of fixes removes.
    """
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"), create=True)
    os.makedirs(state.claude_home)
    settings = os.path.join(state.claude_home, "settings.json")
    original = '{\n  "model": "opus"\n}\n'
    with open(settings, "w", encoding="utf-8") as fh:
        fh.write(original)

    install(state, [DENY], settings_path=settings, write_settings_file=True)
    assert hooks_key_was_ours(state) is True
    for _ in range(3):                       # idempotent re-runs
        install(state, [DENY], settings_path=settings, write_settings_file=True)
        assert hooks_key_was_ours(state) is True

    uninstall(state, settings)
    assert open(settings, encoding="utf-8").read() == original


def test_uninstall_keeps_a_hooks_key_the_user_already_had(tmp_path):
    """Their own hooks — and their own empty `hooks` object — survive."""
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"), create=True)
    os.makedirs(state.claude_home)
    settings = os.path.join(state.claude_home, "settings.json")
    theirs = {"model": "opus", "hooks": {"PreToolUse": [
        {"matcher": "Task", "hooks": [{"type": "command", "command": "mine.sh"}]}]}}
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump(theirs, fh, indent=2)

    install(state, [DENY], settings_path=settings, write_settings_file=True)
    assert hooks_key_was_ours(state) is False
    uninstall(state, settings)
    assert json.loads(open(settings, encoding="utf-8").read()) == theirs

    # and an empty hooks object of their own is not ours to delete either
    empty = {"model": "opus", "hooks": {}}
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump(empty, fh, indent=2)
    install(state, [DENY], settings_path=settings, write_settings_file=True)
    assert hooks_key_was_ours(state) is False
    uninstall(state, settings)
    assert json.loads(open(settings, encoding="utf-8").read()) == empty


def test_uninstall_dry_run_shows_the_same_result_as_the_apply(tmp_path):
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"), create=True)
    os.makedirs(state.claude_home)
    settings = os.path.join(state.claude_home, "settings.json")
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({"model": "opus"}, fh, indent=2)
    install(state, [DENY], settings_path=settings, write_settings_file=True)

    plan, merged = render_plan(state, [DENY], settings, uninstall=True)
    assert "hooks" not in merged
    assert "entries to remove  : %d" % len(HOOK_EVENTS) in plan
    uninstall(state, settings)
    assert json.loads(open(settings, encoding="utf-8").read()) == merged


def test_render_hook_script_bakes_in_the_state_dir(tmp_path):
    body = render_hook_script(str(tmp_path / "st ate"))
    assert str(tmp_path / "st ate") in body
    assert "PRECEDENT_STATE_DIR" in body
    compile(body, "pre_tool_use.py", "exec")       # it is valid Python


def test_every_generated_script_compiles_and_is_silent_without_a_library(tmp_path):
    """A script whose _plib.py is missing must exit 0 and print nothing."""
    for name, body in render_scripts(str(tmp_path / "state")).items():
        compile(body, name, "exec")
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    script = lonely / "stop.py"
    script.write_text(render_scripts(str(tmp_path / "state"))["stop.py"],
                      encoding="utf-8")
    proc = subprocess.run([sys.executable, str(script)],
                          input='{"hook_event_name":"Stop"}',
                          capture_output=True, text=True)
    assert proc.returncode == 0 and proc.stdout.strip() == ""


# --------------------------------------------------------------------------
# the DSL v1 matchers, through the real script
# --------------------------------------------------------------------------

REQUIRE_OPUS = {
    "id": "p-opus", "schemaVersion": 1, "hook": "PreToolUse",
    "tool": "Agent|Workflow", "match": "any",
    "matchers": [{"type": "input_field_missing", "field": "model"},
                 {"type": "input_field_equals", "field": "model",
                  "value": "opus", "negate": True}],
    "action": "deny", "scope": "project", "status": "active",
    "message": "Agent/Workflow 必须带 model=opus",
    "quote": "后续代码修改，以及收据工具改用 Opus 5", "quoteDate": "2026-09-15",
}
SCOPED = {
    "id": "p-scope", "hook": "PreToolUse", "tool": "Bash", "match": "all",
    "matchers": [{"type": "input_regex", "field": "command", "regex": "(?i)pip"}],
    "action": "deny", "scope": "project", "cwd_glob": "/repo/alpha/**",
    "status": "active", "message": "alpha only",
}
LOGGED = dict(SCOPED, id="p-log", action="log", scope="global", cwd_glob=None,
              message="just counting")


def test_hook_evaluates_require_field(hooked):
    hooked.write_precedents([REQUIRE_OPUS])
    out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                           "tool_input": {"prompt": "x"}})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "Opus 5" in out["hookSpecificOutput"]["permissionDecisionReason"]
    out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                           "tool_input": {"prompt": "x", "model": "sonnet"}})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                           "tool_input": {"prompt": "x", "model": "opus"}})
    assert out == {}
    out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": "echo hi"}})
    assert out == {}


def test_hook_honours_cwd_scope(hooked):
    hooked.write_precedents([SCOPED])
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": "pip install x"}}
    out, _ = _run(hooked, dict(payload, cwd="/repo/alpha/sub"))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    out, _ = _run(hooked, dict(payload, cwd="/repo/beta"))
    assert out == {}
    out, _ = _run(hooked, payload)                  # no cwd at all: not scoped in
    assert out == {}


def test_hook_log_action_allows_but_records(hooked):
    hooked.write_precedents([LOGGED])
    out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": "pip install x"}})
    assert out == {}
    log = open(hooked.hooklog_path, encoding="utf-8").read()
    assert '"action": "log"' in log and "p-log" in log


def test_hook_only_confirmed_precedents_can_block(hooked):
    """status must be exactly "active"; a candidate never blocks anything."""
    for status in ("candidate", "retired", None, "ACTIVE"):
        rule = dict(DENY)
        if status is None:
            rule.pop("status")
        else:
            rule["status"] = status
        hooked.write_precedents([rule])
        out, _ = _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                               "tool_input": {"command": "pip install x"}})
        assert out == {}, status


def test_hook_is_fast_enough(hooked):
    """The budget is 300 ms including interpreter start; measure it."""
    import time
    hooked.write_precedents([DENY, ASK, REQUIRE_OPUS, SCOPED, LOGGED])
    t0 = time.monotonic()
    for _ in range(3):
        _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                      "tool_input": {"command": "pip install requests"}})
    per_call = (time.monotonic() - t0) / 3 * 1000
    assert per_call < 300, f"{per_call:.0f} ms per hook call"
    rows = [json.loads(x) for x in
            open(hooked.hooklog_path, encoding="utf-8") if x.strip()]
    decided = [r for r in rows if r.get("event") in ("deny", "ask", "allow")]
    assert decided and all(r["ms"] < 100 for r in decided)


def test_hook_logs_every_decision(hooked):
    _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                  "tool_input": {"command": "pip install requests"}})
    _run(hooked, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                  "tool_input": {"command": "uv add x"}})
    rows = [json.loads(x) for x in
            open(hooked.hooklog_path, encoding="utf-8") if x.strip()]
    events = [r["event"] for r in rows]
    assert "deny" in events and "allow" in events


# --------------------------------------------------------------------------
# install --apply: backup, non-destructive merge, idempotence, uninstall
# --------------------------------------------------------------------------

@pytest.fixture
def synthetic(tmp_path):
    """A synthetic Claude home with an existing settings.json of its own."""
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    os.makedirs(state.claude_home, exist_ok=True)
    settings = os.path.join(state.claude_home, "settings.json")
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump({"model": "opus",
                   "hooks": {"PreToolUse": [{"matcher": "Task",
                                             "hooks": [{"type": "command",
                                                        "command": "theirs.sh"}]}]}},
                  fh, indent=2)
    return state, settings


def test_install_apply_backs_up_then_merges(synthetic, real_home_canary):
    state, settings = synthetic
    before = open(settings, encoding="utf-8").read()
    out = install(state, [DENY, ASK], settings_path=settings,
                  write_settings_file=True)
    assert out["changed"] is True
    assert out["backup"] and os.path.isfile(out["backup"])
    assert open(out["backup"], encoding="utf-8").read() == before
    assert out["backup"].startswith(state.backups_dir + os.sep)

    now = json.load(open(settings, encoding="utf-8"))
    assert now["model"] == "opus"                      # theirs survives
    theirs = [e for e in now["hooks"]["PreToolUse"] if e["matcher"] == "Task"]
    assert theirs and theirs[0]["hooks"][0]["command"] == "theirs.sh"
    ours = [e for e in now["hooks"]["PreToolUse"]
            if is_precedent_entry(e, state.hooks_dir)]
    assert {e["matcher"] for e in ours} == {"Bash|Edit|MultiEdit|NotebookEdit|Write"}
    assert set(now["hooks"]) >= {e[0] for e in HOOK_EVENTS}
    real_home_canary()


def test_install_apply_is_idempotent(synthetic):
    state, settings = synthetic
    install(state, [DENY], settings_path=settings, write_settings_file=True)
    first = open(settings, encoding="utf-8").read()
    n_backups = len(os.listdir(state.backups_dir))
    out = install(state, [DENY], settings_path=settings, write_settings_file=True)
    assert out["changed"] is False
    assert open(settings, encoding="utf-8").read() == first
    assert len(os.listdir(state.backups_dir)) == n_backups   # no pointless backup


def test_uninstall_removes_only_our_entries(synthetic, real_home_canary):
    state, settings = synthetic
    install(state, [DENY, ASK], settings_path=settings, write_settings_file=True)
    out = uninstall(state, settings)
    assert out["changed"] is True and out["removed"] >= len(HOOK_EVENTS)
    assert out["backup"] and os.path.isfile(out["backup"])
    now = json.load(open(settings, encoding="utf-8"))
    assert now["model"] == "opus"
    assert now["hooks"]["PreToolUse"] == [{"matcher": "Task",
                                           "hooks": [{"type": "command",
                                                      "command": "theirs.sh"}]}]
    assert "PostToolUse" not in now["hooks"] and "Stop" not in now["hooks"]
    real_home_canary()


def test_uninstall_is_idempotent(synthetic):
    state, settings = synthetic
    install(state, [DENY], settings_path=settings, write_settings_file=True)
    uninstall(state, settings)
    body = open(settings, encoding="utf-8").read()
    out = uninstall(state, settings)
    assert out["changed"] is False and out["removed"] == 0
    assert open(settings, encoding="utf-8").read() == body


def test_uninstall_leaves_a_foreign_settings_file_alone(tmp_path):
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    os.makedirs(state.claude_home, exist_ok=True)
    settings = os.path.join(state.claude_home, "settings.json")
    payload = {"hooks": {"PreToolUse": [{"matcher": "Bash",
                                         "hooks": [{"type": "command",
                                                    "command": "someone-else.py"}]}]}}
    with open(settings, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    body = open(settings, encoding="utf-8").read()
    out = uninstall(state, settings)
    assert out["removed"] == 0 and out["changed"] is False
    assert open(settings, encoding="utf-8").read() == body


def test_uninstall_settings_is_pure():
    ours = f"python3 /st/hooks/pre.py {MARKER} PreToolUse"
    existing = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": ours}]},
        {"matcher": "Task", "hooks": [{"type": "command", "command": "keep"}]}]}}
    out, removed = uninstall_settings(existing, "/st/hooks")
    assert removed == 1
    assert len(existing["hooks"]["PreToolUse"]) == 2      # input untouched
    assert out["hooks"]["PreToolUse"][0]["matcher"] == "Task"


def test_backup_of_a_missing_settings_file_is_a_noop(tmp_path):
    state = StateDir.open(str(tmp_path / "state"), str(tmp_path / "claude"),
                          create=True)
    assert backup_settings(state, str(tmp_path / "claude" / "settings.json")) is None
