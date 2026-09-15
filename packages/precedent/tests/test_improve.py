# Copyright 2026 The precedent authors.
# SPDX-License-Identifier: Apache-2.0
"""⑤ THE NIGHTLY IMPROVER — clustering, the bounded edit, and the four gates.

The improver is the component the literature is most pessimistic about
(SkillsBench: LLM-written skills, 0 benefit), so most of this file is about the
things that *stop* a draft: the declared-paths check, the lint, the birth gate
and "no evidence".  Every ``claude -p`` here is a PATH shim under ``tmp_path``.
"""

from __future__ import annotations

import json
import os

import pytest

from conftest import build_home_v2, sid, tree_fingerprint
from precedent.improve import (DEFAULT_TOTAL_BUDGET_USD, FailureCluster,
                               build_improve_prompt, cluster_failures,
                               enforce_declared_paths, improve, lint_draft,
                               parse_draft, render_improve)
from precedent.mine import mine
from precedent.proposals import read_proposals
from precedent.state import StateDir


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _state(tmp_path, home):
    return StateDir.open(str(tmp_path / "state"), home, create=True)


def _shim(tmp_path, answer, cost=0.02, returncode=0, name="claude"):
    """A ``claude -p`` stand-in that prints one JSON result envelope."""
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    payload = json.dumps({"type": "result", "subtype": "success",
                          "is_error": False, "result": answer,
                          "total_cost_usd": cost}, ensure_ascii=False)
    p = d / name
    p.write_text("#!/usr/bin/env python3\nimport sys\n"
                 f"sys.stdout.write({json.dumps(payload)})\n"
                 f"sys.exit({returncode})\n", encoding="utf-8")
    os.chmod(p, 0o755)
    return str(p)


@pytest.fixture
def failure_home(tmp_path):
    """Two sessions with the same failing Bash call, and a repeated retry."""
    ws = tmp_path / "proj"
    ws.mkdir()
    err = "ModuleNotFoundError: No module named 'requests'"

    def s(day):
        d = f"2026-09-{day:02d}"
        return [
            ("human", f"{d}T09:00:00.000Z", "run the script"),
            ("assistant", f"{d}T09:00:01.000Z", "Running."),
            ("tool_err", f"{d}T09:00:02.000Z", "Bash",
             {"command": "python main.py"}, err),
            ("tool", f"{d}T09:00:03.000Z", "Bash", {"command": "pip install requests"}),
            ("tool", f"{d}T09:00:04.000Z", "Bash", {"command": "pip install requests"}),
            ("human", f"{d}T09:00:05.000Z", "不要用 pip，用 uv。"),
            ("tool", f"{d}T09:00:06.000Z", "Bash", {"command": "pytest -q"}),
        ]

    home = build_home_v2(tmp_path, "fh", {sid(1): s(11), sid(2): s(12)},
                         cwd=str(ws))
    return home, str(ws)


GOOD_DRAFT = {
    "surface": "skill",
    "hypothesis": "the agent reaches for pip when a dependency is missing",
    "expected_effect": "the ModuleNotFoundError signature stops recurring",
    "declared_paths": ["PLACEHOLDER"],
    "edits": [{"path": "PLACEHOLDER",
               "content": "---\nname: deps\ndescription: install python "
                          "dependencies with uv\n---\n\nUse `uv add`.\n"}],
}


def _draft(path, **over):
    d = json.loads(json.dumps(GOOD_DRAFT))
    d["declared_paths"] = [path]
    d["edits"][0]["path"] = path
    d.update(over)
    return d


# --------------------------------------------------------------------------
# 1. clustering
# --------------------------------------------------------------------------

def test_tool_errors_cluster_by_normalised_signature(failure_home):
    home, ws = failure_home
    from precedent.mine import load_sessions
    clusters = cluster_failures(load_sessions(home), None, [])
    errs = [c for c in clusters if c.kind == "tool_error"]
    assert len(errs) == 1
    assert errs[0].count == 2
    assert len(errs[0].sessions) == 2
    assert "ModuleNotFoundError" in errs[0].key


def test_repeated_identical_calls_cluster_as_retries(failure_home):
    home, ws = failure_home
    from precedent.mine import load_sessions
    clusters = cluster_failures(load_sessions(home), None, [])
    retries = [c for c in clusters if c.kind == "retry"]
    assert retries and "pip install requests" in retries[0].key


def test_a_correction_with_no_confirmed_precedent_is_a_cluster(failure_home):
    home, ws = failure_home
    m = mine(home)
    clusters = cluster_failures(m.sessions, m, [])
    kinds = {c.kind for c in clusters}
    assert "correction_no_precedent" in kinds


def test_a_correction_the_precedent_already_covers_is_not_a_cluster(failure_home):
    home, ws = failure_home
    m = mine(home)
    topic = [t for t in m.topics if t.count >= 2]
    assert topic, "the fixture must produce a repeated topic"
    rule = {"id": "p-x", "status": "active", "topics": [topic[0].id],
            "message": "use uv, not pip"}
    clusters = cluster_failures(m.sessions, m, [rule])
    assert not any(c.topic_ids == [topic[0].id] and
                   c.kind == "correction_no_precedent" for c in clusters)


def test_clusters_below_the_threshold_are_dropped(failure_home):
    home, ws = failure_home
    from precedent.mine import load_sessions
    many = cluster_failures(load_sessions(home), None, [], min_count=1)
    few = cluster_failures(load_sessions(home), None, [], min_count=99)
    assert len(many) > len(few) == 0


def test_the_prompt_carries_the_signature_the_examples_and_the_negatives():
    c = FailureCluster(kind="tool_error", key="Bash: boom", count=3,
                       sessions=["s1"], examples=[{"error": "boom"}])
    prompt = build_improve_prompt(
        c, claude_home="/tmp/h",
        negatives=[{"reason": "touched a file it did not declare",
                    "draft": {"surface": "skill"}}])
    assert "Bash: boom" in prompt
    assert "occurrences: 3" in prompt
    assert "declared_paths" in prompt
    assert "REJECTED before" in prompt
    assert "touched a file it did not declare" in prompt


# --------------------------------------------------------------------------
# 2. the draft schema
# --------------------------------------------------------------------------

def test_a_well_formed_draft_parses(tmp_path):
    draft, err = parse_draft(json.dumps(_draft("/tmp/x/SKILL.md")))
    assert err == "" and draft["surface"] == "skill"


def test_a_draft_may_touch_only_one_file(tmp_path):
    bad = _draft("/tmp/x/SKILL.md")
    bad["declared_paths"] = ["/tmp/x/SKILL.md", "/tmp/y/SKILL.md"]
    draft, err = parse_draft(json.dumps(bad))
    assert draft is None and "bounded to ONE file" in err


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.update(surface="hook"), "not one of"),
    (lambda d: d.update(surface="settings"), "not one of"),
    (lambda d: d.pop("hypothesis"), "hypothesis"),
    (lambda d: d.update(expected_effect=""), "expected_effect"),
    (lambda d: d.update(edits=[]), "non-empty"),
    (lambda d: d.update(declared_paths=[]), "non-empty"),
])
def test_schema_violations_are_named(mutate, needle):
    d = _draft("/tmp/x/SKILL.md")
    mutate(d)
    draft, err = parse_draft(json.dumps(d))
    assert draft is None and needle in err


def test_a_draft_cannot_smuggle_an_active_status_into_a_rule():
    d = _draft("/tmp/x/SKILL.md", surface="precedent")
    d["rule"] = {"tool": "Bash", "action": "deny", "scope": "global",
                 "message": "no pip", "status": "active", "id": "p-evil",
                 "birth": {"verdict": "PASS"},
                 "matchers": [{"type": "input_regex", "field": "command",
                               "regex": "pip install"}]}
    draft, err = parse_draft(json.dumps(d))
    assert err == ""
    assert draft["rule"].get("status") != "active"
    assert draft["rule"].get("id") != "p-evil"
    assert "birth" not in draft["rule"]


def test_an_oversized_edit_is_refused():
    d = _draft("/tmp/x/SKILL.md")
    d["edits"][0]["content"] = "x" * 30_000
    draft, err = parse_draft(json.dumps(d))
    assert draft is None and "bounded" in err


def test_prose_around_the_json_is_tolerated():
    body = "Here you go:\n```json\n" + json.dumps(_draft("/tmp/x/SKILL.md")) \
           + "\n```\nHope that helps."
    draft, err = parse_draft(body)
    assert err == "" and draft["surface"] == "skill"


# --------------------------------------------------------------------------
# 3. diff == declared paths
# --------------------------------------------------------------------------

def test_an_undeclared_edit_is_rejected():
    d = _draft("/tmp/x/SKILL.md")
    d["edits"].append({"path": "/tmp/secret/SKILL.md", "content": "x"})
    problems = enforce_declared_paths(d)
    assert problems and "not declared" in problems[0]


def test_a_declared_path_that_is_never_edited_is_rejected():
    d = _draft("/tmp/x/SKILL.md")
    d["edits"][0]["path"] = "/tmp/other/SKILL.md"
    problems = enforce_declared_paths(d)
    assert any("not in the diff" in p for p in problems)


def test_a_dotdot_in_a_declared_path_is_rejected():
    d = _draft("/tmp/x/../../etc/SKILL.md")
    assert any("'..'" in p for p in enforce_declared_paths(d))


def test_the_matching_case_passes():
    assert enforce_declared_paths(_draft("/tmp/x/SKILL.md")) == []


# --------------------------------------------------------------------------
# 4. the lint
# --------------------------------------------------------------------------

def test_a_skill_without_frontmatter_is_rejected(tmp_path):
    d = _draft(str(tmp_path / "s" / "SKILL.md"))
    d["edits"][0]["content"] = "just prose\n"
    problems = lint_draft(d, str(tmp_path / "claude"))
    assert any("frontmatter" in p for p in problems)


def test_a_skill_frontmatter_missing_description_is_rejected(tmp_path):
    d = _draft(str(tmp_path / "s" / "SKILL.md"))
    d["edits"][0]["content"] = "---\nname: s\n---\n\nbody\n"
    assert any("`description`" in p for p in lint_draft(d, str(tmp_path)))


def test_a_skill_edit_must_write_skill_md(tmp_path):
    d = _draft(str(tmp_path / "s" / "notes.md"))
    assert any("must write SKILL.md" in p for p in lint_draft(d, str(tmp_path)))


def test_a_referenced_file_that_does_not_exist_is_rejected(tmp_path):
    root = tmp_path / "s"
    root.mkdir()
    d = _draft(str(root / "SKILL.md"))
    d["edits"][0]["content"] = ("---\nname: s\ndescription: d\n---\n\n"
                                "See [the guide](references/guide.md).\n")
    assert any("does not exist" in p for p in lint_draft(d, str(tmp_path)))
    (root / "references").mkdir()
    (root / "references" / "guide.md").write_text("g", encoding="utf-8")
    assert lint_draft(d, str(tmp_path)) == []


@pytest.mark.parametrize("secret", [
    "sk-ant-api03-AAAAAAAAAAAAAAAA",
    "ghp_AAAAAAAAAAAAAAAAAAAAAAAA",
    "AKIAIOSFODNN7EXAMPLE",
    "-----BEGIN RSA PRIVATE KEY-----",
    "api_key = AAAAAAAAAAAAAAAAAAAA",
])
def test_the_secret_scan_catches_what_it_claims_to(tmp_path, secret):
    d = _draft(str(tmp_path / "s" / "SKILL.md"))
    d["edits"][0]["content"] = (f"---\nname: s\ndescription: d\n---\n\n"
                                f"{secret}\n")
    assert any("looks like" in p for p in lint_draft(d, str(tmp_path)))


@pytest.mark.parametrize("fact", [
    "/Users/someone/projects/x",
    "localhost:8787",
    "192.168.1.14",
    "2026-09-15",
    "3f2a1b4c-0000-4111-8111-0123456789ab",
])
def test_session_specific_facts_cannot_enter_a_global_artifact(tmp_path, fact):
    claude_home = tmp_path / "claude"
    skill = claude_home / "skills" / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    d = _draft(str(skill))
    d["edits"][0]["content"] = (f"---\nname: s\ndescription: d\n---\n\n"
                                f"Run against {fact}.\n")
    problems = lint_draft(d, str(claude_home))
    assert any("global scope" in p for p in problems), problems


def test_the_same_fact_is_fine_in_a_project_scoped_artifact(tmp_path):
    claude_home = tmp_path / "claude"
    claude_home.mkdir()
    skill = tmp_path / "proj" / ".claude" / "skills" / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    d = _draft(str(skill))
    d["edits"][0]["content"] = ("---\nname: s\ndescription: d\n---\n\n"
                                "The dev server runs on localhost:8787.\n")
    assert lint_draft(d, str(claude_home)) == []


# --------------------------------------------------------------------------
# 5. routing
# --------------------------------------------------------------------------

def _run(tmp_path, home, answer, **kw):
    st = _state(tmp_path, home)
    m = mine(home)
    binary = _shim(tmp_path, answer)
    return st, m, improve(st, sessions=m.sessions, mine_result=m, top=1,
                          binary=binary, **kw)


def test_a_rule_draft_goes_to_the_temporal_birth_gate(tmp_path, failure_home):
    home, ws = failure_home
    m = mine(home)
    topic = [t for t in m.topics if t.count >= 2][0]
    # an over-broad rule: it fires on `pytest -q` after t0, which is a false fire
    rule = {"tool": "Bash", "action": "deny", "scope": "global",
            "message": "no installing",
            "matchers": [{"type": "input_regex", "field": "command",
                          "regex": "install|pytest"}]}
    d = _draft(str(tmp_path / "ignored" / "SKILL.md"), surface="precedent")
    d["rule"] = rule
    st = _state(tmp_path, home)
    binary = _shim(tmp_path, json.dumps(d))
    res = improve(st, sessions=m.sessions, mine_result=m, top=40,
                  binary=binary, min_count=1)
    # whichever cluster it drafted for, a precedent draft is judged by the gate
    props = [p for p in read_proposals(st) if p["surface"] == "precedent"]
    assert props
    assert props[0]["gate"]["gate"] == "temporal-birth-gate/v1"
    assert props[0]["decision"] in ("HOLD", "REJECT")


def test_a_rule_draft_with_no_correction_behind_it_is_insufficient(
        tmp_path, failure_home):
    home, ws = failure_home
    st = _state(tmp_path, home)
    m = mine(home)
    d = _draft(str(tmp_path / "i" / "SKILL.md"), surface="precedent")
    d["rule"] = {"tool": "Bash", "action": "deny", "scope": "global",
                 "message": "m",
                 "matchers": [{"type": "input_regex", "field": "command",
                               "regex": "python main.py"}]}
    from precedent.improve import _temporal_gate_for
    verdict, gate = _temporal_gate_for(
        d, FailureCluster(kind="tool_error", key="k", count=2), m)
    assert verdict == "INSUFFICIENT"
    assert "no t0" in gate["note"]


def test_a_non_rule_draft_with_no_cassette_is_held_with_no_evidence(
        tmp_path, failure_home):
    home, ws = failure_home
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    st, m, res = _run(tmp_path, home, json.dumps(_draft(str(skill))),
                      min_count=1)
    assert res.held, res.rejected
    assert "no evidence" in res.held[0]["reason"]
    prop = read_proposals(st)[0]
    assert prop["decision"] == "HOLD"
    assert prop["applied"] is False


def test_a_non_rule_draft_reaches_the_examiner_when_one_is_wired(
        tmp_path, failure_home):
    home, ws = failure_home
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    seen = []

    def examiner(draft, base):
        seen.append(draft["declared_paths"])
        return {"decision": "ACCEPT", "reason": "12-0", "proposalId": "d-fake"}

    st, m, res = _run(tmp_path, home, json.dumps(_draft(str(skill))),
                      min_count=1, examiner=examiner)
    assert seen == [[str(skill)]]
    assert res.accepted[0]["decision"] == "ACCEPT"


# --------------------------------------------------------------------------
# 6. rejection feeds back (step ⑤)
# --------------------------------------------------------------------------

def test_a_mechanically_rejected_draft_lands_in_rejected_jsonl(tmp_path,
                                                               failure_home):
    home, ws = failure_home
    bad = _draft("/tmp/declared/SKILL.md")
    bad["edits"][0]["path"] = "/tmp/actually/SKILL.md"
    st, m, res = _run(tmp_path, home, json.dumps(bad), min_count=1)
    assert res.accepted == [] and res.rejected
    rows = [json.loads(l) for l in open(st.rejected_path, encoding="utf-8")
            if l.strip()]
    assert rows
    reason = rows[-1]["reason"]
    assert "not declared" in reason or "not in the diff" in reason


def test_the_next_run_sees_the_rejection_as_a_negative_example(tmp_path,
                                                               failure_home):
    home, ws = failure_home
    st = _state(tmp_path, home)
    m = mine(home)
    bad = _draft("/tmp/declared/SKILL.md")
    bad["edits"][0]["path"] = "/tmp/actually/SKILL.md"
    binary = _shim(tmp_path, json.dumps(bad))
    improve(st, sessions=m.sessions, mine_result=m, top=1, binary=binary,
            min_count=1)

    from precedent.llm import load_negatives
    negs = load_negatives(st)
    assert negs
    clusters = cluster_failures(m.sessions, m, [], min_count=1)
    prompt = build_improve_prompt(clusters[0], claude_home=home,
                                  negatives=negs)
    assert "REJECTED before" in prompt
    assert negs[0]["reason"][:30] in prompt


def test_a_failed_model_call_is_a_rejection_not_a_crash(tmp_path, failure_home):
    home, ws = failure_home
    st = _state(tmp_path, home)
    m = mine(home)
    binary = _shim(tmp_path, "not json at all", returncode=1)
    res = improve(st, sessions=m.sessions, mine_result=m, top=1, binary=binary,
                  min_count=1)
    assert res.rejected and "claude -p failed" in res.rejected[0]["reason"]


# --------------------------------------------------------------------------
# 7. budget + safety
# --------------------------------------------------------------------------

def test_the_total_budget_stops_the_run_before_it_is_exceeded(tmp_path,
                                                              failure_home):
    home, ws = failure_home
    st = _state(tmp_path, home)
    m = mine(home)
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    binary = _shim(tmp_path, json.dumps(_draft(str(skill))), cost=0.30)
    res = improve(st, sessions=m.sessions, mine_result=m, top=10,
                  budget_usd=0.60, per_call_usd=0.30, binary=binary,
                  min_count=1)
    assert len([a for a in res.attempted if a["ran"]]) == 2
    assert res.spend_usd == pytest.approx(0.60)
    assert "budget" in res.stopped


def test_every_call_is_capped_and_never_skips_permissions(tmp_path,
                                                          failure_home):
    home, ws = failure_home
    st = _state(tmp_path, home)
    m = mine(home)
    argv_log = tmp_path / "argv.json"
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / "claude"
    p.write_text("#!/usr/bin/env python3\nimport json, sys\n"
                 f"json.dump(sys.argv[1:], open({json.dumps(str(argv_log))}, 'w'))\n"
                 "sys.stdout.write(json.dumps({'type':'result',"
                 "'subtype':'success','is_error':False,'result':'{}',"
                 "'total_cost_usd':0.01}))\n", encoding="utf-8")
    os.chmod(p, 0o755)
    improve(st, sessions=m.sessions, mine_result=m, top=1, binary=str(p),
            min_count=1)
    argv = json.load(open(argv_log, encoding="utf-8"))
    assert "--max-budget-usd" in argv
    assert "--no-session-persistence" in argv
    assert "--model" in argv and argv[argv.index("--model") + 1] == "sonnet"
    assert "--dangerously-skip-permissions" not in argv


def test_dry_run_calls_no_model_and_still_reports_the_clusters(tmp_path,
                                                              failure_home,
                                                              real_home_canary):
    home, ws = failure_home
    st = _state(tmp_path, home)
    m = mine(home)
    res = improve(st, sessions=m.sessions, mine_result=m, top=3,
                  dry_run=True, binary=str(tmp_path / "no-such-binary"),
                  min_count=1)
    assert res.spend_usd == 0.0
    assert res.clusters and all(not a["ran"] for a in res.attempted)
    assert not os.path.exists(st.path("spend.jsonl"))
    md = render_improve(res)
    assert "--dry-run" in md
    real_home_canary()


def test_improve_never_writes_into_the_claude_home(tmp_path, failure_home,
                                                   real_home_canary):
    home, ws = failure_home
    st = _state(tmp_path, home)
    m = mine(home)
    before = tree_fingerprint(home)
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    binary = _shim(tmp_path, json.dumps(_draft(str(skill))))
    improve(st, sessions=m.sessions, mine_result=m, top=3, binary=binary,
            min_count=1)
    assert tree_fingerprint(home) == before
    real_home_canary()


def test_the_improver_never_writes_the_edit_it_proposes(tmp_path, failure_home):
    home, ws = failure_home
    st = _state(tmp_path, home)
    m = mine(home)
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: old\n---\n\nold\n",
                     encoding="utf-8")
    before = skill.read_text(encoding="utf-8")
    binary = _shim(tmp_path, json.dumps(_draft(str(skill))))
    improve(st, sessions=m.sessions, mine_result=m, top=1, binary=binary,
            min_count=1)
    assert skill.read_text(encoding="utf-8") == before
    assert all(p["applied"] is False for p in read_proposals(st))
