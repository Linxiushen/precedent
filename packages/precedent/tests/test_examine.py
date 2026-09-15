# Copyright 2026 The precedent authors.
# SPDX-License-Identifier: Apache-2.0
"""THE EXAMINER — cassette selection, case compilation, the runner, the parser.

Every test builds its own Claude home under ``tmp_path``; ``real_home_canary``
proves the user's own ``~/.claude`` was never touched.  No test in this file
runs a real ``claude`` binary: the subprocess is either a PATH shim written
into ``tmp_path`` or an injected runner callable.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from conftest import build_home_v2, sid, tree_fingerprint
from precedent.examine import (CASE_SCHEMA_VERSION, Candidate, PairedOutcome,
                               build_case, build_cassettes, candidate_eligible,
                               detect_verification, escape_literal, eval_argv,
                               examine, fallback_pairs, git_snapshot,
                               grade_trace, parse_eval_json, precedent_graders,
                               resolve_candidate, run_plugin_eval,
                               write_eval_suite)
from precedent.llm import LLMUnavailable
from precedent.mine import load_sessions, mine
from precedent.state import StateDir


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _state(tmp_path, home):
    return StateDir.open(str(tmp_path / "state"), home, create=True)


def _cassette_session(ws, day, command=".venv/bin/python -m pytest -q"):
    d = f"2026-09-{day:02d}"
    return [
        ("human", f"{d}T09:00:00.000Z", "fix the parser and run the tests"),
        ("assistant", f"{d}T09:00:01.000Z", "Editing."),
        ("tool", f"{d}T09:00:02.000Z", "Edit",
         {"file_path": os.path.join(ws, "parser.py"), "old_string": "a",
          "new_string": "b"}),
        ("tool", f"{d}T09:00:03.000Z", "Bash", {"command": command}),
        ("human", f"{d}T09:00:09.000Z", "thanks"),
    ]


@pytest.fixture
def cassettes_home(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    spec = {sid(1): _cassette_session(str(ws), 11),
            sid(2): _cassette_session(str(ws), 12, "npm test"),
            sid(3): [("human", "2026-09-13T09:00:00.000Z", "just chatting"),
                     ("assistant", "2026-09-13T09:00:01.000Z", "ok"),
                     ("tool", "2026-09-13T09:00:02.000Z", "Read",
                      {"file_path": str(ws / "parser.py")})]}
    home = build_home_v2(tmp_path, "exam", spec, cwd=str(ws))
    return home, str(ws)


@pytest.fixture
def skill_candidate(tmp_path):
    d = tmp_path / "skills" / "verify-first"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: verify-first\ndescription: run the project's own test "
        "command after every edit\n---\n\nAfter editing, run the tests.\n",
        encoding="utf-8")
    return str(d)


# --------------------------------------------------------------------------
# 1. cassette selection
# --------------------------------------------------------------------------

def test_detect_verification_takes_the_last_matching_bash_call(tmp_path):
    ws = tmp_path / "w"
    ws.mkdir()
    home = build_home_v2(tmp_path, "h", {sid(1): [
        ("tool", "2026-09-01T00:00:01.000Z", "Bash", {"command": "pytest -q"}),
        ("tool", "2026-09-01T00:00:02.000Z", "Bash", {"command": "ls"}),
        ("tool", "2026-09-01T00:00:03.000Z", "Bash", {"command": "make test"}),
    ]}, cwd=str(ws))
    sess = load_sessions(home)[0]
    got = detect_verification(sess)
    assert got["kind"] == "make"
    assert got["command"] == "make test"


@pytest.mark.parametrize("command,kind", [
    ("pytest -q tests/", "pytest"),
    (".venv/bin/python -m pytest", "pytest"),
    ("npm run test -- --watch=false", "npm-test"),
    ("cargo test --all", "cargo-test"),
    ("go test ./...", "go-test"),
    ("make check", "make"),
    ("ruff check src", "ruff"),
    ("mypy src", "mypy"),
    ("npx tsc --noEmit", "tsc"),
])
def test_verification_patterns(tmp_path, command, kind):
    ws = tmp_path / "w"
    ws.mkdir()
    home = build_home_v2(tmp_path, "h" + kind, {sid(1): [
        ("tool", "2026-09-01T00:00:01.000Z", "Bash", {"command": command})]},
        cwd=str(ws))
    assert detect_verification(load_sessions(home)[0])["kind"] == kind


def test_a_session_without_an_oracle_is_skipped_with_a_reason(cassettes_home):
    home, ws = cassettes_home
    sessions = load_sessions(home)
    cass, skipped = build_cassettes(sessions, [], None, allow_git=False)
    assert {c.session_id for c in cass} == {sid(1), sid(2)}
    assert len(skipped) == 1
    assert "no terminal verification command" in skipped[0]["reason"]


def test_an_active_precedent_alone_makes_a_session_a_cassette(cassettes_home):
    home, ws = cassettes_home
    rule = {"schemaVersion": 1, "id": "p-aaa", "hook": "PreToolUse",
            "tool": "Bash", "match": "all", "action": "deny", "scope": "global",
            "status": "active", "message": "no pip",
            "matchers": [{"type": "input_regex", "field": "command",
                          "regex": "pip install"}]}
    cass, skipped = build_cassettes(load_sessions(home), [rule], None,
                                    allow_git=False)
    assert len(cass) == 3                      # the chat-only session now counts
    assert all(c.n_precedent_graders >= 1 for c in cass)


def test_precedent_graders_only_express_rules_they_can_express():
    expressible = {"schemaVersion": 1, "id": "p-1", "hook": "PreToolUse",
                   "tool": "Bash|Edit", "match": "all", "action": "deny",
                   "scope": "global", "status": "active", "message": "m",
                   "matchers": [{"type": "input_regex", "field": "command",
                                 "regex": "pip install"}]}
    inexpressible = {"schemaVersion": 1, "id": "p-2", "hook": "PreToolUse",
                     "tool": "Agent", "match": "all", "action": "deny",
                     "scope": "global", "status": "active", "message": "m",
                     "matchers": [{"type": "input_field_missing",
                                   "field": "model"}]}
    inactive = dict(expressible, id="p-3", status="candidate")
    logging_only = dict(expressible, id="p-4", action="log")
    got = precedent_graders([expressible, inexpressible, inactive, logging_only])
    names = {g["name"] for g in got}
    assert names == {"precedent-p-1-Bash", "precedent-p-1-Edit"}
    # a deny precedent is "this must never happen": max 0, not min 1
    assert all(g["max"] == 0 and "min" not in g for g in got)


# --------------------------------------------------------------------------
# 2. ties are free
# --------------------------------------------------------------------------

def test_project_scoped_candidate_is_a_tie_in_another_project(tmp_path):
    other = tmp_path / "otherproj"
    (other / ".claude" / "skills" / "x").mkdir(parents=True)
    skill = other / ".claude" / "skills" / "x" / "SKILL.md"
    skill.write_text("---\nname: x\ndescription: d\n---\n\nbody\n", encoding="utf-8")
    home = build_home_v2(tmp_path, "h", {sid(1): []}, cwd=str(tmp_path / "elsewhere"))
    st = _state(tmp_path, home)
    cand = resolve_candidate(st, str(skill))
    assert cand.scope_root == str(os.path.realpath(other))

    class C:
        cwd = str(tmp_path / "elsewhere")
    ok, why = candidate_eligible(cand, C())
    assert ok is False and "cannot load" in why
    C.cwd = str(other)
    assert candidate_eligible(cand, C())[0] is True


def test_free_ties_are_never_executed_and_cost_nothing(tmp_path, cassettes_home,
                                                       real_home_canary):
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / ".claude" / "skills" / "y").mkdir(parents=True)
    skill = elsewhere / ".claude" / "skills" / "y" / "SKILL.md"
    skill.write_text("---\nname: y\ndescription: d\n---\n\nbody\n", encoding="utf-8")

    calls = []

    def never(argv, cwd):                     # pragma: no cover - must not run
        calls.append(argv)
        raise AssertionError("a tie by construction must not be executed")

    res = examine(st, str(skill), runs=2, allow_git=False, force_fallback=True,
                  runner=never)
    assert calls == []
    assert res.outcomes == []
    assert len(res.ties_free) == 4            # 2 cassettes x 2 runs
    assert all(o.executed is False and o.tie for o in res.ties_free)
    assert res.cost_usd == 0.0
    assert any("free ties" in n for n in res.notes)
    real_home_canary()


# --------------------------------------------------------------------------
# 3. the eval suite
# --------------------------------------------------------------------------

def test_case_yaml_is_valid_json_and_carries_the_documented_shape(
        tmp_path, cassettes_home, skill_candidate):
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    m = mine(home)
    cand = resolve_candidate(st, skill_candidate)
    cass, _ = build_cassettes(load_sessions(home), [], cand, allow_git=False,
                              turns_by_session=m.turns_by_session)
    suite = write_eval_suite(str(tmp_path / "suite"), cand, cass, [], runs=2,
                             allow_bash=True)

    manifest = json.load(open(os.path.join(
        suite["root"], ".claude-plugin", "plugin.json"), encoding="utf-8"))
    assert manifest["name"] == "precedent-verify-first"
    assert os.path.isfile(os.path.join(suite["skill"], "SKILL.md"))

    entry = suite["cases"][0]
    case = json.load(open(os.path.join(entry["dir"], "case.yaml"), encoding="utf-8"))
    assert case["schema_version"] == CASE_SCHEMA_VERSION
    assert case["runs"] == 2
    assert case["context"]["history_file"] == "history.jsonl"
    # history_file requires execution.prompt (the resumed session needs a turn)
    assert case["execution"]["prompt"]
    assert [g["type"] for g in case["graders"]] == ["tool_used", "regex"]
    assert case["graders"][0]["tool"] == "Bash"


def test_history_file_is_the_prefix_before_the_fork_point(
        tmp_path, cassettes_home, skill_candidate):
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    cand = resolve_candidate(st, skill_candidate)
    cass, _ = build_cassettes(load_sessions(home), [], cand, allow_git=False)
    suite = write_eval_suite(str(tmp_path / "suite"), cand, cass, [], runs=1)
    entry = [c for c in suite["cases"] if c["case"].endswith("11111111")][0]
    hist = os.path.join(entry["dir"], "history.jsonl")
    lines = [json.loads(l) for l in open(hist, encoding="utf-8") if l.strip()]
    cassette = [c for c in cass if c.case_name == entry["case"]][0]
    assert len(lines) == cassette.fork_line - 1
    # the verification command itself is on the far side of the fork: the
    # prefix stops *before* it, which is what makes the resumed arm have to
    # decide to run it
    verification = cassette.verification["command"]
    assert not any(verification in json.dumps(rec, ensure_ascii=False)
                   for rec in lines)
    assert verification in open(cassette.transcript, encoding="utf-8").read()


def test_scaffold_script_restores_a_git_snapshot_when_one_exists(tmp_path):
    if not __import__("shutil").which("git"):
        pytest.skip("git is not installed")
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "commit.gpgsign=false", "commit", "-qm", "one",
                  "--allow-empty"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                       capture_output=True)
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env,
                   capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false",
                    "commit", "-qm", "two"], check=True, env=env,
                   capture_output=True)

    snap = git_snapshot(str(repo), None)
    assert snap and snap["sha"] and snap["repo"] == str(os.path.realpath(repo))

    from precedent.examine import _scaffold_script
    script = _scaffold_script(snap)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    subprocess.run(["bash", "-c", script], cwd=str(sandbox), check=True,
                   capture_output=True)
    assert (sandbox / "a.txt").read_text(encoding="utf-8") == "hello\n"


def test_git_snapshot_is_none_when_there_is_no_repo(tmp_path):
    assert git_snapshot(str(tmp_path), None) is None
    assert git_snapshot(None, None) is None
    assert git_snapshot(str(tmp_path / "nope"), None) is None


def test_no_git_snapshot_means_no_scaffold_script(tmp_path, cassettes_home,
                                                  skill_candidate):
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    cand = resolve_candidate(st, skill_candidate)
    cass, _ = build_cassettes(load_sessions(home), [], cand, allow_git=False)
    case = build_case(cand, cass[0], [], runs=2)
    assert "scaffold_script" not in case["context"]


# --------------------------------------------------------------------------
# 4. the runner
# --------------------------------------------------------------------------

def test_eval_argv_is_the_spec_argv_when_every_flag_is_supported():
    argv, dropped = eval_argv("/suite", "/suite/out.json", max_cost_usd=1.5,
                              runs=2, binary="/bin/claude",
                              supported={"--json", "--trust-plugin",
                                         "--no-publish", "--max-cost-usd",
                                         "--runs", "--ablation", "--scaffold"})
    assert dropped == []
    assert argv[:4] == ["/bin/claude", "plugin", "eval", "/suite"]
    for flag in ("--json", "--trust-plugin", "--no-publish", "--max-cost-usd",
                 "--runs"):
        assert flag in argv
    assert argv[argv.index("--runs") + 1] == "2"
    assert argv[argv.index("--max-cost-usd") + 1] == "1.5"
    assert "--dangerously-skip-permissions" not in argv


def test_eval_argv_drops_and_reports_flags_this_build_does_not_advertise():
    argv, dropped = eval_argv("/suite", "/o.json", max_cost_usd=1, runs=2,
                              binary="/bin/claude",
                              supported={"--json", "--no-publish",
                                         "--max-cost-usd", "--runs",
                                         "--ablation", "--scaffold"})
    assert dropped == ["--trust-plugin"]
    assert "--trust-plugin" not in argv


def _shim(tmp_path, body: str) -> str:
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / "claude"
    p.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    os.chmod(p, 0o755)
    return str(p)


def test_early_access_is_detected_as_unavailable_not_as_a_failed_candidate(tmp_path):
    binary = _shim(tmp_path, "import sys\n"
                             "if '--help' in sys.argv:\n"
                             "    print('--json --no-publish --runs')\n"
                             "    sys.exit(0)\n"
                             "print('`plugin eval` is currently in early access')\n"
                             "sys.exit(0)\n")
    run = run_plugin_eval(str(tmp_path), str(tmp_path / "out.json"),
                          binary=binary)
    assert run.unavailable is True
    assert "early access" in run.reason
    assert run.payload is None


def test_a_results_json_is_parsed_rather_than_treated_as_unavailable(tmp_path):
    payload = {"cases": [{"name": "c1", "arms": {"with": [{"score": 1.0}],
                                                 "without": [{"score": 0.0}]}}],
               "costUsd": 0.42}
    out = tmp_path / "out.json"
    binary = _shim(tmp_path, "import sys, json\n"
                             "if '--help' in sys.argv:\n"
                             "    print('--json --no-publish --runs "
                             "--max-cost-usd --ablation --scaffold')\n"
                             "    sys.exit(0)\n"
                             f"open({json.dumps(str(out))}, 'w').write("
                             f"{json.dumps(json.dumps(payload))})\n")
    run = run_plugin_eval(str(tmp_path), str(out), binary=binary)
    assert run.unavailable is False
    assert run.cost_usd == 0.42
    assert parse_eval_json(run.payload)[0].pair() == (0, 1)


def test_missing_binary_is_unavailable_not_a_crash(tmp_path):
    run = run_plugin_eval(str(tmp_path), str(tmp_path / "o.json"),
                          binary=str(tmp_path / "no-such-claude"), probe=False)
    assert run.unavailable is True
    assert "could not run" in run.reason


# --------------------------------------------------------------------------
# 5. parsing paired outcomes
# --------------------------------------------------------------------------

def test_parse_eval_json_pairs_the_two_arms_run_by_run():
    payload = {"cases": [
        {"name": "c1", "arms": {
            "with": [{"score": 1.0, "passed": True, "costUsd": 0.01},
                     {"score": 0.5, "passed": False, "costUsd": 0.01}],
            "without": [{"score": 0.0, "passed": False, "costUsd": 0.02},
                        {"score": 1.0, "passed": True, "costUsd": 0.02}]}},
        {"name": "c2", "arms": {"with": [{"score": 1.0}], "without": []}},
    ]}
    got = parse_eval_json(payload)
    assert [(o.case, o.run_index, o.without, o.with_) for o in got] == [
        ("c1", 0, 0, 1), ("c1", 1, 1, 0)]
    assert got[0].cost_usd == pytest.approx(0.03)
    assert got[0].instance_id == "c1#0"


def test_a_run_that_errored_is_a_failure_whatever_its_score():
    got = parse_eval_json({"cases": [{"name": "c", "arms": {
        "with": [{"score": 1.0, "passed": True, "error": "timeout"}],
        "without": [{"score": 1.0, "passed": True}]}}]})
    assert got[0].pair() == (1, 0)


# --------------------------------------------------------------------------
# 6. the local graders (the fallback implements the same language)
# --------------------------------------------------------------------------

def _granted_command(argv) -> str:
    """The verification command the case granted this arm, read back from argv.

    The two cassettes in the fixture have *different* oracles (`pytest` and
    `npm test`), so a stub runner that always emits the same command would
    accidentally test only one of them.
    """
    for a in argv:
        if isinstance(a, str) and a.startswith("Bash(") and a.endswith("*)"):
            return a[len("Bash("):-len("*)")]
    return "ls"


def _events(*tools):
    return [{"type": "assistant",
             "message": {"content": [{"type": "tool_use", "name": n,
                                      "input": i}]}} for n, i in tools]


def test_grade_trace_tool_used_min_and_max():
    graders = [{"type": "tool_used", "name": "ran", "tool": "Bash",
                "input_match": escape_literal("pytest -q"), "min": 1,
                "weight": 1}]
    ok = grade_trace(graders, _events(("Bash", {"command": "pytest -q"})), "")
    assert ok["passed"] is True and ok["score"] == 1.0
    bad = grade_trace(graders, _events(("Bash", {"command": "ls"})), "")
    assert bad["passed"] is False

    forbid = [{"type": "tool_used", "name": "no-pip", "tool": "Bash",
               "input_match": "pip install", "max": 0, "weight": 1}]
    assert grade_trace(forbid, _events(("Bash", {"command": "ls"})), "")["passed"]
    assert not grade_trace(forbid,
                           _events(("Bash", {"command": "pip install x"})),
                           "")["passed"]


def test_grade_trace_regex_targets_and_match_modes():
    ev = _events(("Bash", {"command": "pytest -q"}))
    assert grade_trace([{"type": "regex", "name": "t", "target": "trace",
                         "pattern": "pytest", "match": "contains",
                         "weight": 1}], ev, "")["passed"]
    assert grade_trace([{"type": "regex", "name": "t", "target": "last_message",
                         "pattern": "done", "match": "contains",
                         "weight": 1}], ev, "all done")["passed"]
    assert grade_trace([{"type": "regex", "name": "t", "target": "last_message",
                         "pattern": "oops", "match": "not_contains",
                         "weight": 1}], ev, "all done")["passed"]
    assert grade_trace([{"type": "regex", "name": "t", "target": "trace",
                         "pattern": "pytest", "match": "count:1",
                         "weight": 1}], ev, "")["passed"]


def test_grade_trace_reports_graders_it_cannot_implement_instead_of_passing_them():
    got = grade_trace([{"type": "llm", "name": "judge", "criteria": "good?",
                        "weight": 1}], [], "x")
    assert got["nScored"] == 0 and got["nSkipped"] == 1
    assert "not implemented" in got["graders"][0]["reason"]
    # an unimplemented grader must not silently count as a pass
    assert got["score"] == 0.0


def test_grade_trace_weights_are_honoured():
    ev = _events(("Bash", {"command": "pytest"}))
    got = grade_trace([
        {"type": "regex", "name": "a", "target": "trace", "pattern": "pytest",
         "weight": 3},
        {"type": "regex", "name": "b", "target": "trace", "pattern": "nope",
         "weight": 1}], ev, "")
    assert got["score"] == pytest.approx(0.75) and got["passed"] is False


# --------------------------------------------------------------------------
# 7. the paired `claude -p` fallback
# --------------------------------------------------------------------------

def test_fallback_runs_two_arms_and_only_the_with_arm_gets_the_candidate(
        tmp_path, cassettes_home, skill_candidate, real_home_canary):
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    seen = []

    def runner(argv, cwd):
        seen.append(argv)
        with_arm = "--plugin-dir" in argv
        events = _events(("Bash", {"command": _granted_command(argv)})) \
            if with_arm else _events(("Bash", {"command": "ls"}))
        return {"events": events, "last_message": "done", "cost_usd": 0.01,
                "error": None}

    res = examine(st, skill_candidate, runs=1, allow_git=False,
                  force_fallback=True, runner=runner, max_cost_usd=1.0,
                  allow_bash=True)
    assert len(res.outcomes) == 2                      # two cassettes, one run
    assert all(o.pair() == (0, 1) for o in res.outcomes)
    assert len(seen) == 4                              # 2 cases x 2 arms
    for argv in seen:
        assert "--dangerously-skip-permissions" not in argv
        assert "--no-session-persistence" in argv
        assert "--max-budget-usd" in argv
        assert "--output-format" in argv
    with_calls = [a for a in seen if "--plugin-dir" in a]
    assert len(with_calls) == 2
    assert all("--append-system-prompt" in a for a in with_calls)
    real_home_canary()


def test_fallback_stops_before_a_call_that_would_break_the_cost_cap(
        tmp_path, cassettes_home, skill_candidate):
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    n = {"calls": 0}

    def runner(argv, cwd):
        n["calls"] += 1
        return {"events": [], "last_message": "", "cost_usd": 0.25,
                "error": None}

    res = examine(st, skill_candidate, runs=2, allow_git=False,
                  force_fallback=True, runner=runner, max_cost_usd=0.50,
                  arm_budget_usd=0.25)
    assert n["calls"] == 2                     # 0.25 + 0.25 == the cap
    assert res.fallback["stopped"]
    assert "cap" in res.fallback["stopped"]
    spend = [json.loads(l) for l in
             open(st.path("spend.jsonl"), encoding="utf-8") if l.strip()]
    assert len(spend) == 2
    assert all(r["command"].startswith("examine") for r in spend)


def test_a_dead_arm_scores_zero_rather_than_passing_vacuously(
        tmp_path, cassettes_home, skill_candidate):
    home, ws = cassettes_home
    st = _state(tmp_path, home)

    def runner(argv, cwd):
        if "--plugin-dir" in argv:
            return {"events": [], "last_message": "", "cost_usd": 0.0,
                    "error": "timed out after 900s"}
        return {"events": _events(("Bash", {"command": _granted_command(argv)})),
                "last_message": "ok", "cost_usd": 0.0, "error": None}

    res = examine(st, skill_candidate, runs=1, allow_git=False,
                  force_fallback=True, runner=runner, allow_bash=True)
    assert all(o.pair() == (1, 0) for o in res.outcomes)


# --------------------------------------------------------------------------
# 8. candidates
# --------------------------------------------------------------------------

def test_resolve_candidate_accepts_a_dir_a_skill_md_and_a_claude_md(
        tmp_path, cassettes_home, skill_candidate):
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    a = resolve_candidate(st, skill_candidate)
    b = resolve_candidate(st, os.path.join(skill_candidate, "SKILL.md"))
    assert a.content_hash == b.content_hash == a.content_hash
    assert a.surface == "skill" and a.name == "verify-first"

    cm = tmp_path / "proj" / "CLAUDE.md"
    cm.parent.mkdir(parents=True)
    cm.write_text("# rules\n\nuse uv\n", encoding="utf-8")
    c = resolve_candidate(st, str(cm))
    assert c.surface == "claude_md"
    assert any("wrapper is a measurement artifact" in n for n in c.notes)


def test_resolve_candidate_accepts_a_rule_id(tmp_path, cassettes_home):
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    rule = {"schemaVersion": 1, "id": "p-abc", "hook": "PreToolUse",
            "tool": "Bash", "match": "all", "action": "deny", "scope": "global",
            "status": "active", "message": "no pip",
            "matchers": [{"type": "input_regex", "field": "command",
                          "regex": "pip install"}]}
    st.write_precedents([rule])
    cand = resolve_candidate(st, "p-abc")
    assert cand.surface == "precedent" and cand.rule["id"] == "p-abc"


def test_an_unknown_candidate_is_a_clear_error(tmp_path, cassettes_home):
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    with pytest.raises(LLMUnavailable) as exc:
        resolve_candidate(st, "not-a-thing")
    assert "neither a path" in str(exc.value)


# --------------------------------------------------------------------------
# 9. read-only on the Claude home
# --------------------------------------------------------------------------

def test_examine_never_writes_into_the_claude_home(tmp_path, cassettes_home,
                                                   skill_candidate,
                                                   real_home_canary):
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    before = tree_fingerprint(home)

    def runner(argv, cwd):
        return {"events": [], "last_message": "", "cost_usd": 0.0, "error": None}

    examine(st, skill_candidate, runs=1, allow_git=False, force_fallback=True,
            runner=runner)
    assert tree_fingerprint(home) == before
    real_home_canary()


def test_dry_run_writes_the_suite_and_runs_nothing(tmp_path, cassettes_home,
                                                   skill_candidate):
    home, ws = cassettes_home
    st = _state(tmp_path, home)

    def never(argv, cwd):                     # pragma: no cover
        raise AssertionError("--dry-run must not run an arm")

    res = examine(st, skill_candidate, runs=2, allow_git=False, dry_run=True,
                  runner=never)
    assert res.run is None and res.outcomes == []
    assert res.suite["nCases"] == 2
    assert os.path.isfile(os.path.join(res.suite["cases"][0]["dir"], "case.yaml"))
    assert any("--dry-run" in n for n in res.notes)


# --------------------------------------------------------------------------
# 10. the Bash grant is opt-in
# --------------------------------------------------------------------------

def test_the_arms_are_read_only_unless_allow_bash_is_asked_for(
        tmp_path, cassettes_home, skill_candidate):
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    cand = resolve_candidate(st, skill_candidate)
    cass, _ = build_cassettes(load_sessions(home), [], cand, allow_git=False)

    off = build_case(cand, cass[0], [], runs=1)
    assert off["execution"]["allowed_tools"] == ["Read", "Glob", "Grep"]
    assert "read-only-arms" in off["tags"]

    on = build_case(cand, cass[0], [], runs=1, allow_bash=True)
    assert any(t.startswith("Bash(") for t in on["execution"]["allowed_tools"])
    assert "bash-granted" in on["tags"]


def test_read_only_arms_are_reported_as_a_reason_not_a_silent_zero(
        tmp_path, cassettes_home, skill_candidate):
    home, ws = cassettes_home
    st = _state(tmp_path, home)

    def runner(argv, cwd):
        assert not any(isinstance(a, str) and a.startswith("Bash(")
                       for a in argv)
        return {"events": [], "last_message": "ok", "cost_usd": 0.0,
                "error": None}

    res = examine(st, skill_candidate, runs=1, allow_git=False,
                  force_fallback=True, runner=runner)
    assert all(o.tie for o in res.outcomes)
    assert any("--allow-bash is off" in n for n in res.notes)


def test_a_proposal_candidate_whose_file_does_not_exist_yet_still_compiles(
        tmp_path, cassettes_home):
    """A nightly draft names a path that is not on disk — that is the point."""
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    from precedent.proposals import append_proposal
    pid = append_proposal(st, {
        "ts": "2026-09-15T00:00:00+00:00", "kind": "improver-draft",
        "surface": "skill", "name": "SKILL.md",
        "declaredPaths": [os.path.join(home, "skills", "brand-new", "SKILL.md")],
        "hypothesis": "h", "expectedEffect": "e",
        "content": "---\nname: brand-new\ndescription: d\n---\n\nbody\n"})
    assert not os.path.exists(os.path.join(home, "skills", "brand-new"))

    cand = resolve_candidate(st, pid)
    assert cand.surface == "skill"
    res = examine(st, pid, runs=1, allow_git=False, dry_run=True)
    assert res.suite["nCases"] == 2
    body = open(os.path.join(res.suite["skill"], "SKILL.md"),
                encoding="utf-8").read()
    assert "brand-new" in body


def test_an_inapplicable_grader_is_dropped_not_failed(tmp_path, cassettes_home,
                                                      skill_candidate):
    """Read-only arms cannot satisfy `tool_used: Bash`.

    Scoring it as a failure for *both* arms makes every pair a tie and the exam
    unable to say anything; dropping it leaves the weak oracle, which can.
    """
    home, ws = cassettes_home
    st = _state(tmp_path, home)
    cand = resolve_candidate(st, skill_candidate)
    cass, _ = build_cassettes(load_sessions(home), [], cand, allow_git=False)

    weak = build_case(cand, cass[0], [], runs=1)
    assert [g["type"] for g in weak["graders"]] == ["regex"]
    assert "WEAK" in weak["description"]

    strong = build_case(cand, cass[0], [], runs=1, allow_bash=True)
    assert [g["type"] for g in strong["graders"]] == ["tool_used", "regex"]


def test_an_exam_that_cannot_discriminate_is_not_paid_for(tmp_path,
                                                          cassettes_home,
                                                          skill_candidate):
    """A precedent-only cassette with no expressible grader must not cost money."""
    home, ws = cassettes_home
    st = _state(tmp_path, home)

    def never(argv, cwd):                     # pragma: no cover
        raise AssertionError("an unanswerable exam must not spend anything")

    # a rule whose tool is never granted and whose grader needs min 1
    import precedent.examine as ex_mod
    real = ex_mod.verification_graders

    def only_impossible(verification, allow_bash=True):
        return [{"type": "tool_used", "name": "impossible", "tool": "Bash",
                 "input_match": "x", "min": 1, "weight": 1}]

    ex_mod.verification_graders = only_impossible
    try:
        res = examine(st, skill_candidate, runs=2, allow_git=False,
                      force_fallback=True, runner=never)
    finally:
        ex_mod.verification_graders = real
    assert res.outcomes == []
    assert len(res.ties_free) == 4
    assert all(o.source == "tie-unsatisfiable" for o in res.ties_free)
    assert any("not worth paying for" in n for n in res.notes)
