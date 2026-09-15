# Copyright 2026 The precedent authors.
# SPDX-License-Identifier: Apache-2.0
"""STAGE C, ADVERSARIALLY — what happens when the parts we do not own lie.

The stage-C suites test the components against a cooperative ``claude``.  This
file assumes the opposite, because every cap in ``examine`` / ``improve`` is a
comparison against a number a subprocess handed us:

* a binary that reports ``$0.00`` (or ``NaN``) for every call, so the dollar
  cap can only be held by *our own* reservation;
* a ``claude plugin eval`` whose results JSON invents cases that were never
  compiled -- the proposer writing its own score sheet (方案 §2: Camp B's seven
  evaluator leaks are one bug, "the evaluator sits inside the proposer's view");
* a drafting model that labels a ``settings.json`` edit as a rule;
* a re-examination that rewinds the wealth process to buy a friendlier answer.

Every test builds its own Claude home under ``tmp_path``; ``real_home_canary``
proves the user's own ``~/.claude`` was never touched, and no test runs a real
``claude`` binary.
"""

from __future__ import annotations

import json
import math
import os

import pytest

from conftest import build_home_v2, sid, tree_fingerprint
from precedent import accept as accept_mod
from precedent.accept import accept_outcomes, verify_acceptance
from precedent.examine import (PairedOutcome, examine, parse_eval_json)
from precedent.improve import improve, lint_draft
from precedent.llm import json_safe, sane_cost, total_spend
from precedent.mine import mine
from precedent.proposals import read_proposals
from precedent.state import StateDir


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _state(tmp_path, home, name="state"):
    return StateDir.open(str(tmp_path / name), home, create=True)


def _cassette(ws, day, command=".venv/bin/python -m pytest -q"):
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
def exam_pair(tmp_path):
    """Two cassette sessions in one workspace, plus a global skill candidate."""
    ws = tmp_path / "repo"
    ws.mkdir()
    home = build_home_v2(tmp_path, "advhome",
                         {sid(1): _cassette(str(ws), 11),
                          sid(2): _cassette(str(ws), 12, "npm test")},
                         cwd=str(ws))
    skill = tmp_path / "skills" / "verify-first"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: verify-first\ndescription: run the project's own test "
        "command after every edit\n---\n\nAfter editing, run the tests.\n",
        encoding="utf-8")
    return home, str(ws), str(skill)


def _shim(tmp_path, answer, cost=0.02, returncode=0, name="claude"):
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


def _failure_home(tmp_path):
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
            ("tool", f"{d}T09:00:03.000Z", "Bash",
             {"command": "pip install requests"}),
            ("tool", f"{d}T09:00:04.000Z", "Bash",
             {"command": "pip install requests"}),
            ("human", f"{d}T09:00:05.000Z", "不要用 pip，用 uv。"),
        ]
    return build_home_v2(tmp_path, "advfail",
                         {sid(1): s(11), sid(2): s(12)}, cwd=str(ws)), str(ws)


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


def _pairs(spec, case="case-x"):
    """``"01"`` -> one PairedOutcome without=0 with=1."""
    return [PairedOutcome(case=case, run_index=i, without=int(p[0]),
                          with_=int(p[1])) for i, p in enumerate(spec)]


# ==========================================================================
# 1. the dollar cap must hold without trusting the binary
# ==========================================================================

def test_a_binary_that_reports_zero_cost_cannot_buy_more_arms_than_the_cap(
        tmp_path, exam_pair, real_home_canary):
    """The only honest bound is ``floor(cap / per-call cap)`` calls.

    ``--max-budget-usd`` is a flag on a subprocess.  If that subprocess ignores
    it *and* reports ``$0.00``, a spend meter that only adds up what it was told
    never trips: before this was enforced, a $0.50 exam made 8 arm calls, each
    of which the flag alone allowed to cost $0.25 -- $2.00 of exposure against
    a $0.50 cap.
    """
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    n = {"calls": 0}

    def runner(argv, cwd):
        n["calls"] += 1
        return {"events": [], "last_message": "", "cost_usd": 0.0,
                "error": None}

    res = examine(st, skill, runs=2, allow_git=False, force_fallback=True,
                  runner=runner, max_cost_usd=0.50, arm_budget_usd=0.25,
                  allow_bash=True)
    assert n["calls"] == 2, "the reservation, not the report, is the cap"
    assert res.fallback["maxCalls"] == 2
    assert res.fallback["reservedUsd"] == pytest.approx(0.50)
    assert "committed" in res.fallback["stopped"]
    real_home_canary()


def test_a_nan_cost_does_not_switch_off_every_budget_check(tmp_path, exam_pair):
    """``NaN > x`` is ``False``, so one NaN would disable every cap at once."""
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    n = {"calls": 0}

    def runner(argv, cwd):
        n["calls"] += 1
        return {"events": [], "last_message": "", "cost_usd": float("nan"),
                "error": None}

    res = examine(st, skill, runs=2, allow_git=False, force_fallback=True,
                  runner=runner, max_cost_usd=0.50, arm_budget_usd=0.25,
                  allow_bash=True)
    assert n["calls"] == 2
    assert res.fallback["untrustedCostReports"] == 2
    assert math.isfinite(res.fallback["spentUsd"])
    assert "not a finite number" in res.fallback["note"]


def test_the_spend_ledger_stays_real_json_and_a_finite_total(tmp_path, exam_pair):
    """``json.dumps`` emits a bare ``NaN`` token; nothing outside Python reads it."""
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)

    def runner(argv, cwd):
        return {"events": [], "last_message": "", "cost_usd": float("inf"),
                "error": None}

    examine(st, skill, runs=1, allow_git=False, force_fallback=True,
            runner=runner, max_cost_usd=0.50, arm_budget_usd=0.25,
            allow_bash=True)
    raw = open(st.path("spend.jsonl"), encoding="utf-8").read()
    assert "NaN" not in raw and "Infinity" not in raw
    for line in raw.splitlines():
        json.loads(line)                       # would raise on a bare NaN token
    assert math.isfinite(total_spend(st))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"),
                                   -1.0, "0.30", None, {}, "free"])
def test_sane_cost_only_books_finite_non_negative_numbers(value):
    got = sane_cost(value)
    assert isinstance(got, float) and math.isfinite(got) and got >= 0.0


def test_json_safe_survives_a_nested_non_finite_float():
    assert json_safe({"a": [float("nan"), {"b": float("inf")}], "c": 1.5}) == \
        {"a": [None, {"b": None}], "c": 1.5}


def test_improve_stops_at_the_reserved_budget_when_the_shim_reports_nothing(
        tmp_path):
    """A drafting model that reports ``$0.00`` must not buy ``--top`` calls."""
    home, ws = _failure_home(tmp_path)
    st = _state(tmp_path, home)
    m = mine(home)
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    binary = _shim(tmp_path, json.dumps(_draft(str(skill))), cost=0.0)
    res = improve(st, sessions=m.sessions, mine_result=m, top=10,
                  budget_usd=0.60, per_call_usd=0.30, binary=binary,
                  min_count=1)
    assert len([a for a in res.attempted if a["ran"]]) == 2
    assert res.reserved_usd == pytest.approx(0.60)
    assert "committed" in res.stopped


def test_improve_bills_the_examiner_against_the_same_budget(tmp_path):
    """One examine per draft, each handed the whole budget, is how $0.60 -> $3.00."""
    home, ws = _failure_home(tmp_path)
    st = _state(tmp_path, home)
    m = mine(home)
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: d\n---\n", encoding="utf-8")
    binary = _shim(tmp_path, json.dumps(_draft(str(skill))), cost=0.0)
    handed = []

    def examiner(draft, base):
        # an examiner that honours the cap it is handed (`cmd_improve`'s does:
        # it passes it straight through as `--max-cost-usd`)
        allowed = base["budgetRemainingUsd"]
        handed.append(allowed)
        return {"decision": "NSF", "reason": "no effect",
                "proposalId": "d-x", "costUsd": min(0.25, allowed)}

    res = improve(st, sessions=m.sessions, mine_result=m, top=4,
                  budget_usd=0.60, per_call_usd=0.10, binary=binary,
                  min_count=1, examiner=examiner)
    # the examiner never gets more than what is left of --budget-usd ...
    assert handed and all(h <= 0.60 for h in handed)
    assert handed == sorted(handed, reverse=True), "the remainder must shrink"
    # ... and what it spent is inside the budget the run printed
    assert max(res.spend_usd, res.reserved_usd) <= 0.60 + 1e-9
    assert res.spend_usd > 0, "the exam is billed, not free"


def test_an_examiner_that_overspends_is_stopped_on_the_next_draft(tmp_path):
    """The bill is reconciled after every draft, not only at the end."""
    home, ws = _failure_home(tmp_path)
    st = _state(tmp_path, home)
    m = mine(home)
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: d\n---\n", encoding="utf-8")
    binary = _shim(tmp_path, json.dumps(_draft(str(skill))), cost=0.0)
    seen = []

    def examiner(draft, base):
        seen.append(base["budgetRemainingUsd"])
        return {"decision": "NSF", "reason": "no effect",
                "proposalId": "d-x", "costUsd": 5.00}     # ignores its cap

    res = improve(st, sessions=m.sessions, mine_result=m, top=6,
                  budget_usd=0.60, per_call_usd=0.10, binary=binary,
                  min_count=1, examiner=examiner)
    assert len(seen) == 1, "one overspend, then the run stops"
    assert res.stopped and "budget" in res.stopped


def test_a_draft_that_cannot_be_paid_for_is_held_not_waved_through(tmp_path):
    home, ws = _failure_home(tmp_path)
    st = _state(tmp_path, home)
    m = mine(home)
    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    binary = _shim(tmp_path, json.dumps(_draft(str(skill))), cost=0.30)
    calls = []

    def examiner(draft, base):
        calls.append(base["budgetRemainingUsd"])
        return {"decision": "ACCEPT", "reason": "12-0", "proposalId": "d-y"}

    res = improve(st, sessions=m.sessions, mine_result=m, top=1,
                  budget_usd=0.30, per_call_usd=0.30, binary=binary,
                  min_count=1, examiner=examiner)
    assert calls == [], "the whole budget went to the draft; no exam was affordable"
    assert res.held and "budget was exhausted" in res.held[0]["reason"]
    assert not res.accepted


# ==========================================================================
# 2. the evaluator does not get to invent its own evidence
# ==========================================================================

def _eval_shim(tmp_path, payload, name="claude"):
    """A ``claude`` that answers ``plugin eval --help`` and then a results JSON."""
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "argv = sys.argv[1:]\n"
        "if '--help' in argv:\n"
        "    sys.stdout.write('--json --no-publish --max-cost-usd --runs "
        "--ablation --scaffold\\n')\n"
        "    sys.exit(0)\n"
        "out = argv[argv.index('--json') + 1]\n"
        f"open(out, 'w').write({json.dumps(json.dumps(payload))})\n"
        "sys.exit(0)\n", encoding="utf-8")
    os.chmod(p, 0o755)
    return str(p)


def _sweep(names, runs=1):
    return {"cases": [{"name": n, "arms": {
        "with": [{"passed": True, "score": 1.0} for _ in range(runs)],
        "without": [{"passed": False, "score": 0.0} for _ in range(runs)]}}
        for n in names]}


def test_a_results_json_full_of_cases_we_never_compiled_is_discarded(
        tmp_path, exam_pair, real_home_canary):
    """The eval subprocess is handed a plugin directory built around the
    candidate.  Evidence it reports for a case this run never compiled is the
    proposer writing its own score sheet, so none of it is paired."""
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    binary = _eval_shim(tmp_path, _sweep([f"case-INVENTED-{i}" for i in range(50)]))
    res = examine(st, skill, runs=1, allow_git=False, binary=binary,
                  max_cost_usd=1.0)
    assert res.outcomes == []
    assert any("did not compile" in n for n in res.notes)
    real_home_canary()


def test_a_fabricated_sweep_cannot_reach_an_accept_end_to_end(tmp_path,
                                                              exam_pair):
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    binary = _eval_shim(tmp_path, _sweep([f"case-INVENTED-{i}" for i in range(50)]))
    res = examine(st, skill, runs=1, allow_git=False, binary=binary,
                  max_cost_usd=1.0)
    acc = accept_outcomes(st, res.candidate, res.all_outcomes)
    assert acc.decision != "ACCEPT"
    assert acc.n_informative == 0


def test_a_real_case_name_still_pairs_so_the_filter_is_not_a_blanket_no(
        tmp_path, exam_pair):
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    # compile the suite first so we know the names this run will accept
    plan = examine(st, skill, runs=1, allow_git=False, dry_run=True)
    names = [c["case"] for c in plan.suite["cases"]]
    assert names
    binary = _eval_shim(tmp_path, _sweep(names + ["case-INVENTED"]))
    res = examine(st, skill, runs=1, allow_git=False, binary=binary,
                  max_cost_usd=1.0)
    assert sorted({o.case for o in res.outcomes}) == sorted(names)
    assert any("did not compile" in n for n in res.notes)


def test_more_runs_than_we_asked_for_are_discarded(tmp_path, exam_pair):
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    plan = examine(st, skill, runs=1, allow_git=False, dry_run=True)
    names = [c["case"] for c in plan.suite["cases"]]
    binary = _eval_shim(tmp_path, _sweep(names, runs=20))
    res = examine(st, skill, runs=1, allow_git=False, binary=binary,
                  max_cost_usd=1.0)
    assert len(res.outcomes) == len(names)
    assert any("asked for 1" in n for n in res.notes)


def test_the_same_case_run_twice_in_one_payload_is_counted_once():
    dropped = []
    payload = {"cases": [
        {"name": "case-a", "arms": {"with": [{"passed": True}],
                                    "without": [{"passed": False}]}},
        {"name": "case-a", "arms": {"with": [{"passed": True}],
                                    "without": [{"passed": False}]}}]}
    out = parse_eval_json(payload, allowed_cases={"case-a"}, max_runs=1,
                          dropped=dropped)
    assert len(out) == 1
    assert dropped and "more than once" in dropped[0]


def test_a_non_finite_cost_in_the_eval_json_does_not_poison_the_total():
    out = parse_eval_json(
        {"cases": [{"name": "c", "arms": {
            "with": [{"passed": True, "costUsd": float("nan")}],
            "without": [{"passed": False, "costUsd": "lots"}]}}]},
        allowed_cases={"c"}, max_runs=1)
    assert len(out) == 1 and math.isfinite(out[0].cost_usd)


# ==========================================================================
# 3. ties are free — and free means never executed
# ==========================================================================

def test_an_ineligible_cassette_never_reaches_the_runner_or_the_eval_suite(
        tmp_path, exam_pair):
    """A project-scoped candidate in another project's session is a tie by
    construction; a tie that costs a model call is not a free tie."""
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    other = tmp_path / "elsewhere" / ".claude" / "skills" / "x"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text(
        "---\nname: x\ndescription: only for the other project\n---\n\nhi\n",
        encoding="utf-8")

    def runner(argv, cwd):                                # pragma: no cover
        raise AssertionError("an ineligible cassette was executed")

    res = examine(st, str(other), runs=2, allow_git=False, force_fallback=True,
                  runner=runner, max_cost_usd=1.0)
    assert res.outcomes == []
    assert len(res.ties_free) == 4                    # 2 cassettes x 2 runs
    assert all(not o.executed for o in res.ties_free)
    assert not os.path.isdir(st.path("exams")) or not any(
        os.scandir(st.path("exams")))
    assert not os.path.exists(st.path("spend.jsonl"))


def test_free_ties_are_in_the_pair_count_but_cost_no_alpha(tmp_path, exam_pair):
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    ties = [PairedOutcome(case=f"case-{i}", run_index=0, without=0, with_=0,
                          executed=False, tie_reason="scope")
            for i in range(20)]
    acc = accept_outcomes(st, ("cand:x", "h" * 16), ties)
    assert acc.n_pairs == 20 and acc.n_free_ties == 20
    assert acc.n_informative == 0
    assert acc.decision != "ACCEPT"
    assert acc.schedule["spent"] == 0.0, "no evidence was examined at that level"


# ==========================================================================
# 4. the acceptance wiring: certificates, rewinds, re-badging
# ==========================================================================

def test_a_rewound_re_examination_cannot_be_certified_as_an_accept(tmp_path,
                                                                   exam_pair):
    """Re-running the exam on a *subset* until the arms agree is free alpha.

    The first run is honest and long; the second replays a shorter, friendlier
    slice.  ``acceptor``'s anti-rewind fields catch it, and the check now runs
    BEFORE the certificate is chained, so the ACCEPT never happens.
    """
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    long_run = _pairs(["01"] * 4 + ["10"] * 4)
    accept_outcomes(st, ("cand:r", "hash-r"), long_run)
    short_run = _pairs(["01"] * 3, case="case-x")
    acc = accept_outcomes(st, ("cand:r", "hash-r"), short_run)
    assert acc.decision != "ACCEPT"
    assert any("went backwards" in n for n in acc.notes), acc.notes


def test_identical_content_under_a_fresh_candidate_id_is_refused(tmp_path,
                                                                 exam_pair):
    """A fresh id buys a fresh alpha: 90 % false ACCEPT at K=100 (acceptor)."""
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    accept_outcomes(st, ("cand:first", "same-content-hash"), _pairs(["01"] * 6))
    acc = accept_outcomes(st, ("cand:second", "same-content-hash"),
                          _pairs(["01"] * 6))
    assert acc.decision != "ACCEPT"
    assert any("already proposed" in n for n in acc.notes), acc.notes


def test_duplicate_paired_observations_are_dropped_before_the_gate(tmp_path,
                                                                   exam_pair):
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    one = PairedOutcome(case="case-a", run_index=0, without=0, with_=1)
    acc = accept_outcomes(st, ("cand:d", "hash-d"), [one] * 8)
    assert acc.n_pairs == 1
    assert any("duplicate" in n for n in acc.notes)


def test_every_certificate_this_stage_writes_replays_clean(tmp_path, exam_pair):
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    accept_outcomes(st, ("cand:a", "hash-a"), _pairs(["01", "10", "01"]))
    accept_outcomes(st, ("cand:b", "hash-b"), _pairs(["00", "11"]))
    audit = verify_acceptance(st)
    assert audit["ok"], audit["problems"]
    assert audit["checked"] == 2


def test_a_hand_edited_certificate_makes_the_audit_fail_loudly(tmp_path,
                                                              exam_pair):
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    accept_outcomes(st, ("cand:a", "hash-a"), _pairs(["01"] * 5))
    path = st.ledger_path
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    rows.append(dict(rows[-1], round=99, n_pairs=0, n_informative=0))
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    audit = verify_acceptance(st)
    assert not audit["ok"]
    assert any("backwards" in p for p in audit["problems"])


# ==========================================================================
# 5. an LLM draft cannot relabel a protected file as a rule
# ==========================================================================

@pytest.mark.parametrize("path, needle", [
    ("~/.claude/settings.json", "settings.json"),
    ("~/.claude/settings.local.json", "settings.local.json"),
    ("/tmp/proj/.claude/hooks/precedent_pre.py", "hooks"),
    ("~/.precedent/ledger.jsonl", "ledger.jsonl"),
    ("~/.claude/.credentials.json", ".credentials.json"),
    ("/tmp/x/.git/config", ".git"),
])
def test_a_draft_cannot_put_a_protected_file_on_the_docket(path, needle):
    """``surface`` is a label the model picked; the path is what a human edits.

    ``skill`` and ``claude_md`` are pinned to a basename, ``precedent`` is not,
    so without a path guard a draft could label a ``settings.json`` rewrite as
    a rule and land it on the docket for the user to paste in.
    """
    draft = {"surface": "precedent",
             "declared_paths": [path],
             "edits": [{"path": path, "content": "{}"}],
             "rule": {"tool": "Bash", "matchers": [], "action": "deny",
                      "message": "m"}}
    problems = lint_draft(draft, os.path.expanduser("~/.claude"))
    assert problems and any(needle in p for p in problems), problems


def test_a_protected_path_is_rejected_before_it_becomes_a_proposal(tmp_path):
    home, ws = _failure_home(tmp_path)
    st = _state(tmp_path, home)
    m = mine(home)
    evil = os.path.join(home, "settings.json")
    draft = {"surface": "precedent",
             "hypothesis": "hooks would catch this",
             "expected_effect": "the signature stops",
             "declared_paths": [evil],
             "edits": [{"path": evil, "content": '{"hooks": {}}'}],
             "rule": {"tool": "Bash", "match": "all",
                      "matchers": [{"type": "input_regex", "field": "command",
                                    "regex": "^pip install"}],
                      "action": "deny", "scope": "global",
                      "message": "use uv, not pip (2026-09-11)"}}
    binary = _shim(tmp_path, json.dumps(draft), cost=0.01)
    before = tree_fingerprint(home)
    res = improve(st, sessions=m.sessions, mine_result=m, top=1, binary=binary,
                  min_count=1)
    assert res.rejected and "settings.json" in res.rejected[0]["reason"]
    assert not res.accepted and not res.held
    assert read_proposals(st) == [], "a rejected draft never becomes a proposal"
    assert tree_fingerprint(home) == before
    rejected = [json.loads(l) for l in
                open(st.rejected_path, encoding="utf-8") if l.strip()]
    assert rejected and rejected[0]["source"] == "improve"


def test_a_legitimate_skill_path_still_passes_the_guard(tmp_path):
    ok = str(tmp_path / "skills" / "deps" / "SKILL.md")
    os.makedirs(os.path.dirname(ok), exist_ok=True)
    assert lint_draft(_draft(ok), str(tmp_path / "home")) == []


# ==========================================================================
# 6. nothing in stage C writes a byte the user did not ask for
# ==========================================================================

def test_the_whole_adversarial_path_writes_nothing_into_the_claude_home(
        tmp_path, exam_pair, real_home_canary):
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    before = tree_fingerprint(home)
    binary = _eval_shim(tmp_path, _sweep(["case-INVENTED"]))
    res = examine(st, skill, runs=1, allow_git=False, binary=binary,
                  max_cost_usd=1.0)
    acc = accept_outcomes(st, res.candidate, res.all_outcomes)
    assert tree_fingerprint(home) == before
    assert acc.certificate is not None
    props = read_proposals(st)
    assert props and props[0]["applied"] is False
    real_home_canary()


# ==========================================================================
# 7. the ceilings that are ours to hold
# ==========================================================================

@pytest.mark.parametrize("kw, needle", [
    ({"max_cost_usd": 1e9}, "--max-cost-usd"),
    ({"max_cost_usd": float("nan")}, "--max-cost-usd"),
    ({"max_cost_usd": -1}, "--max-cost-usd"),
    ({"runs": 100000}, "--runs"),
    ({"runs": 0}, "--runs"),
    ({"runs": -3}, "--runs"),
])
def test_an_unbounded_ceiling_is_refused_before_anything_runs(
        tmp_path, exam_pair, kw, needle):
    """A cap a nightly cron can set to ``1e9`` is not a cap."""
    from precedent.llm import LLMUnavailable
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)

    def runner(argv, cwd):                                # pragma: no cover
        raise AssertionError("a refused ceiling must cost nothing")

    with pytest.raises(LLMUnavailable) as exc:
        examine(st, skill, allow_git=False, force_fallback=True, runner=runner,
                **kw)
    assert needle in str(exc.value)
    assert not os.path.exists(st.path("spend.jsonl"))


def test_the_official_evaluators_own_spend_reaches_the_ledger(tmp_path,
                                                             exam_pair):
    """`claude plugin eval` is one subprocess, so its cost is its own report —
    which is exactly why it has to be written down rather than dropped."""
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    plan = examine(st, skill, runs=1, allow_git=False, dry_run=True)
    names = [c["case"] for c in plan.suite["cases"]]
    payload = dict(_sweep(names), costUsd=0.42)
    binary = _eval_shim(tmp_path, payload)
    examine(st, skill, runs=1, allow_git=False, binary=binary, max_cost_usd=1.0)
    rows = [json.loads(l) for l in open(st.path("spend.jsonl"), encoding="utf-8")
            if l.strip()]
    evals = [r for r in rows if "plugin eval" in r["command"]]
    assert evals and evals[0]["costUsd"] == pytest.approx(0.42)
    assert evals[0]["reportedByEvaluator"] is True
    assert total_spend(st) == pytest.approx(0.42)


def test_confirming_a_proposal_does_not_create_the_file_it_names(tmp_path,
                                                                 exam_pair):
    """The dangerous direction: the declared path does not exist yet."""
    from precedent.docket import build_entries, confirm
    home, ws, skill = exam_pair
    st = _state(tmp_path, home)
    target = tmp_path / "not-yet" / "SKILL.md"
    acc = accept_outcomes(st, ("cand:new", "hash-new"), _pairs(["01"] * 12),
                          surface="skill", declared_paths=[str(target)])
    assert acc.decision == "ACCEPT"
    entry = [e for e in build_entries(st) if e["kind"] == "proposal"][0]
    rc, lines = confirm(st, entry["id"])
    assert rc == 0
    assert not target.exists() and not target.parent.exists()
    assert "不会替你写这个文件" in "\n".join(lines)


def test_only_skill_bundle_files_are_copied_into_the_plugin_directory(
        tmp_path, exam_pair):
    """The directory swept here is named by the candidate, and a nightly draft
    picks that name; everything copied ends up inside a plugin handed to a
    model."""
    from precedent.examine import write_eval_suite, Candidate, build_cassettes
    from precedent.mine import load_sessions
    home, ws, skill = exam_pair
    d = tmp_path / "skills" / "bundled"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: b\ndescription: d\n---\n\nhi\n",
                                encoding="utf-8")
    (d / "helper.py").write_text("print(1)\n", encoding="utf-8")
    (d / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-secret\n", encoding="utf-8")
    (d / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n",
                              encoding="utf-8")
    (d / "notes.sqlite").write_text("binary-ish\n", encoding="utf-8")

    st = _state(tmp_path, home)
    cand = Candidate(id="cand:b", surface="skill", paths=[str(d / "SKILL.md")],
                     name="bundled", text=(d / "SKILL.md").read_text(),
                     content_hash="h")
    sessions = load_sessions(home, None, None)
    cassettes, _ = build_cassettes(sessions, [], cand, allow_git=False)
    suite = write_eval_suite(str(tmp_path / "suite"), cand, cassettes, [])
    assert suite["bundled"] == ["helper.py"]
    got = set(os.listdir(suite["skill"]))
    assert got == {"SKILL.md", "helper.py"}
