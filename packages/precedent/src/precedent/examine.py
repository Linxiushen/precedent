# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""THE EXAMINER — history becomes an exam.

``precedent examine --candidate <path-or-id>`` turns the sessions you already
have into a paired, two-armed exam for one candidate artifact (a skill, a
CLAUDE.md edit, a rule) and hands the paired outcomes to the acceptance gate.

The pipeline, in order:

1. **Cassette selection.**  A session is an eligible cassette when it carries a
   *detectable terminal verification command* (the last ``Bash`` call matching
   :data:`VERIFICATION_PATTERNS` — ``pytest``, ``npm test``, ``cargo test``,
   ``make check``, ``ruff``, ``mypy``, …), **or** when there is at least one
   confirmed precedent that can be expressed as a grader.  A session with
   neither has nothing to score against and is reported as skipped, with the
   reason.  This is the honest version of "replay": the verification command is
   the session's own, user-written oracle, not one we invented.

2. **Compilation into ``claude plugin eval`` cases.**  A temporary *plugin*
   directory is built around the candidate (``.claude-plugin/plugin.json`` plus
   ``skills/<name>/SKILL.md``), and each cassette becomes
   ``evals/<case>/case.yaml`` (schema 1.1) with

   * ``context.scaffold_script`` — ``git archive`` of the commit the repository
     was at when the session ran, unpacked into the sandbox.  Only when a git
     snapshot is actually recoverable; otherwise the field is omitted and the
     case says so.
   * ``context.history_file`` — the session transcript truncated to the records
     *before* the fork point, so the arm resumes where the session was.
   * ``graders`` — ``tool_used`` + ``regex`` on the verification command, plus
     one ``tool_used … max: 0`` grader per confirmed precedent that binds to a
     tool and an input regex (the precedent as an exam question).

3. **Execution.**  ``claude plugin eval <dir> --json out.json --trust-plugin
   --no-publish --max-cost-usd <cap> --runs 2`` (flags the installed binary
   does not advertise are dropped and *reported* — see :func:`eval_argv`).
   When the sub-command answers "``plugin eval`` is currently in early access"
   — or is otherwise unavailable — we fall back to a **paired ``claude -p``
   runner** that executes the same two arms itself and applies the *same
   graders*, implemented locally in :func:`grade_trace`.

4. **Parsing.**  ``cases[].arms.with[i]`` / ``cases[].arms.without[i]`` become
   paired binary outcomes ``(y_without, y_with)``, one per run index, which is
   exactly what ``acceptor.PairedBinaryGate`` consumes.

**Ties are free.**  A cassette in which the candidate could not have been
loaded at all — a project-scoped artifact and a session in another project — is
a tie *by construction*: the two arms are the same run.  Those cases are never
executed, cost nothing, and are fed to the gate as ties so the accounting stays
honest (``n_pairs`` counts them; the wealth process ignores them).

Everything here is read-only on the Claude home: cassettes are read, the eval
suite is written into a temporary directory or under ``<state>/exams/``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from .llm import (LLMUnavailable, check_budget, claude_bin, record_spend,
                  sane_cost)
from .rules import TOOLS, normalise_rule, scope_matches

__all__ = [
    "BUNDLE_EXTENSIONS",
    "CASE_SCHEMA_VERSION",
    "MAX_EXAM_COST_USD",
    "MAX_RUNS",
    "check_exam_cost",
    "Candidate",
    "Cassette",
    "ExamineResult",
    "PairedOutcome",
    "PluginEvalRun",
    "VERIFICATION_PATTERNS",
    "SPEC_EVAL_FLAGS",
    "build_case",
    "build_cassettes",
    "candidate_eligible",
    "detect_verification",
    "escape_literal",
    "eval_argv",
    "examine",
    "git_snapshot",
    "grade_trace",
    "parse_eval_json",
    "precedent_graders",
    "probe_eval_flags",
    "resolve_candidate",
    "run_plugin_eval",
    "write_eval_suite",
]

#: ``case.yaml`` schema this module emits.  The installed binary reports the
#: highest major it supports; 1.x is what 2.1.x speaks.
CASE_SCHEMA_VERSION = "1.1"

#: The argv the design calls for, in order.  What actually runs is this list
#: filtered by :func:`probe_eval_flags` — a flag the installed binary does not
#: advertise is dropped and named in the result rather than silently passed
#: (an unknown option makes commander exit before anything runs).
SPEC_EVAL_FLAGS = ("--json", "--trust-plugin", "--no-publish", "--max-cost-usd",
                   "--runs", "--ablation", "--scaffold")

#: Terminal verification commands, most specific first.  Each is a *command*
#: regex; the cassette's grader is built from the literal command that matched,
#: not from this pattern, so a grader never matches more than the session did.
VERIFICATION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("pytest", r"(?:^|[;&|]\s*)(?:[\w./\-]*python[\d.]*\s+-m\s+)?pytest\b"),
    ("tox", r"(?:^|[;&|]\s*)tox\b"),
    ("nox", r"(?:^|[;&|]\s*)nox\b"),
    ("unittest", r"python[\d.]*\s+-m\s+unittest\b"),
    ("npm-test", r"(?:^|[;&|]\s*)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b"),
    ("jest", r"(?:^|[;&|]\s*)(?:npx\s+)?jest\b"),
    ("vitest", r"(?:^|[;&|]\s*)(?:npx\s+)?vitest\b"),
    ("cargo-test", r"(?:^|[;&|]\s*)cargo\s+(?:test|clippy)\b"),
    ("go-test", r"(?:^|[;&|]\s*)go\s+(?:test|vet)\b"),
    ("make", r"(?:^|[;&|]\s*)make\s+(?:test|check|lint|ci)\b"),
    ("gradle", r"(?:^|[;&|]\s*)\.?/?gradlew?\s+(?:test|check)\b"),
    ("maven", r"(?:^|[;&|]\s*)mvn\s+(?:test|verify)\b"),
    ("rspec", r"(?:^|[;&|]\s*)(?:bundle\s+exec\s+)?rspec\b"),
    ("ruff", r"(?:^|[;&|]\s*)ruff\s+(?:check|format)\b"),
    ("mypy", r"(?:^|[;&|]\s*)mypy\b"),
    ("tsc", r"(?:^|[;&|]\s*)(?:npx\s+)?tsc\b"),
    ("eslint", r"(?:^|[;&|]\s*)(?:npx\s+)?eslint\b"),
)

_VERIFY_RX = [(name, re.compile(rx)) for name, rx in VERIFICATION_PATTERNS]

#: The sub-command prints this and exits 0 when the feature is gated off for
#: the account.  Anything matching here means "no plugin eval here" — not "the
#: candidate failed".
_UNAVAILABLE_RX = re.compile(
    r"early access|not enabled|is not available|unavailable|unknown command|"
    r"unknown option|requires a newer|not supported", re.IGNORECASE)

MAX_HISTORY_LINES = 4000
MAX_HISTORY_BYTES = 4_000_000
MAX_CASES = 24
MAX_PRECEDENT_GRADERS = 6
DEFAULT_RUNS = 2
#: More runs than this is a typo, not an experiment: every run is two paid
#: arms per cassette, and `--runs 100000` would also build 100000 tie objects
#: per ineligible cassette before anything was executed.
MAX_RUNS = 20
DEFAULT_MAX_COST_USD = 1.00
#: The ceiling on ``--max-cost-usd`` for one exam.  ``llm.MAX_BUDGET_USD`` caps
#: a single call; this caps the run, because a cap you can set to ``1e9`` on a
#: nightly cron is not a cap (cost is the silent killer -- 方案 §1.3).
MAX_EXAM_COST_USD = 20.00
#: Per ``claude -p`` call in the fallback runner.  ``llm.MAX_BUDGET_USD`` caps it.
DEFAULT_ARM_BUDGET_USD = 0.25
FALLBACK_MAX_TURNS = 12
#: What a skill bundle is made of.  A file next to ``SKILL.md`` is copied into
#: the plugin directory only when its extension is one of these -- see
#: :func:`write_eval_suite` for why a directory named by a *draft* must not be
#: swept wholesale.
BUNDLE_EXTENSIONS = frozenset({
    ".md", ".txt", ".py", ".sh", ".js", ".ts", ".json", ".yaml", ".yml",
    ".toml", ".csv", ".png", ".jpg", ".jpeg", ".svg", ".gif",
})


# --------------------------------------------------------------------------
# candidates
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    """The artifact under examination."""

    id: str
    surface: str                      # skill | claude_md | precedent | file
    paths: list[str] = field(default_factory=list)
    name: str = "candidate"
    description: str = ""
    text: str = ""                    # the artifact body, for the with-arm
    rule: dict | None = None          # set when surface == "precedent"
    content_hash: str = ""
    scope_root: str | None = None     # project dir the artifact belongs to, if any
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "surface": self.surface, "paths": self.paths,
                "name": self.name, "description": self.description,
                "contentHash": self.content_hash, "scopeRoot": self.scope_root,
                "chars": len(self.text), "notes": self.notes,
                "rule": self.rule}


def check_exam_cost(max_cost_usd) -> float:
    """The whole-exam ceiling, or :class:`LLMUnavailable`.  No subprocess yet."""
    try:
        c = float(max_cost_usd)
    except (TypeError, ValueError):
        raise LLMUnavailable(f"--max-cost-usd {max_cost_usd!r} is not a number")
    if c != c or c < 0 or c > MAX_EXAM_COST_USD:
        raise LLMUnavailable(
            f"--max-cost-usd must be >= 0 and <= {MAX_EXAM_COST_USD:g} USD for "
            f"one exam (got {max_cost_usd!r}); a ceiling that can be set to "
            f"anything is not a ceiling, and this one runs on a nightly cron")
    return c


def _slug(text: str, fallback: str = "candidate") -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(text or "")).strip("-.")
    return (s or fallback)[:48]


def _read(path: str, limit: int = 200_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _frontmatter_name(text: str) -> tuple[str | None, str | None]:
    """``(name, description)`` from a SKILL.md frontmatter block, best effort."""
    if not text.startswith("---"):
        return None, None
    end = text.find("\n---", 3)
    if end < 0:
        return None, None
    name = desc = None
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip().lower(), v.strip().strip("'\"")
        if k == "name" and not name:
            name = v
        elif k == "description" and not desc:
            desc = v
    return name, desc


def _project_root_for(path: str) -> str | None:
    """The project directory a project-scoped artifact belongs to, if any.

    ``<proj>/.claude/skills/x/SKILL.md`` -> ``<proj>``; ``<proj>/CLAUDE.md`` ->
    ``<proj>``.  A user-level artifact (``~/.claude/skills/**``) is global and
    returns ``None`` — it is loadable in every session.
    """
    p = os.path.realpath(path)
    home_claude = os.path.realpath(os.path.expanduser("~/.claude"))
    if p == home_claude or p.startswith(home_claude + os.sep):
        return None
    parts = p.split(os.sep)
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] == ".claude":
            return os.sep.join(parts[:i]) or os.sep
    base = os.path.basename(p)
    if base in ("CLAUDE.md", "CLAUDE.local.md"):
        return os.path.dirname(p)
    return None


def resolve_candidate(state, spec: str) -> Candidate:
    """``--candidate`` -> a :class:`Candidate`.  A path, or a rule/proposal id."""
    from acceptor import content_hash

    raw = str(spec or "").strip()
    if not raw:
        raise LLMUnavailable("--candidate is required")
    path = os.path.realpath(os.path.expanduser(raw))

    if os.path.isdir(path):
        skill = os.path.join(path, "SKILL.md")
        if os.path.isfile(skill):
            return _skill_candidate(skill, content_hash)
        raise LLMUnavailable(
            f"{raw}: a directory candidate must hold SKILL.md (found none)")
    if os.path.isfile(path):
        base = os.path.basename(path)
        if base == "SKILL.md":
            return _skill_candidate(path, content_hash)
        text = _read(path)
        surface = "claude_md" if base in ("CLAUDE.md", "CLAUDE.local.md") else "file"
        return Candidate(
            id=f"cand:{_slug(base)}", surface=surface, paths=[path],
            name=_slug(os.path.splitext(base)[0], "candidate"),
            description=f"the candidate edit to {base}",
            text=text, content_hash=content_hash(text),
            scope_root=_project_root_for(path),
            notes=[] if surface != "claude_md" else [
                "a CLAUDE.md candidate is carried into the with-arm as a skill "
                "(a plugin-root CLAUDE.md is not loaded as project context); "
                "the wrapper is a measurement artifact, not part of the edit"])

    # not a path: a rule id or a proposal id
    for rule in list(state.candidates()) + list(state.precedents()):
        if rule.get("id") == raw or raw in (rule.get("topics") or []):
            return _rule_candidate(rule, content_hash)
    from .proposals import read_proposals
    for prop in read_proposals(state):
        if prop.get("id") == raw:
            paths = [p for p in (prop.get("declaredPaths") or [])
                     if isinstance(p, str)]
            body = prop.get("content") or ""
            return Candidate(
                id=raw, surface=prop.get("surface") or "file", paths=paths,
                name=_slug(prop.get("name") or raw), text=body,
                description=prop.get("hypothesis", "")[:200],
                content_hash=content_hash(body),
                scope_root=_project_root_for(paths[0]) if paths else None)
    raise LLMUnavailable(
        f"--candidate {raw!r} is neither a path on disk nor a known rule / "
        f"proposal id (`precedent docket` lists the ids)")


def _skill_candidate(skill_md: str, content_hash) -> Candidate:
    text = _read(skill_md)
    name, desc = _frontmatter_name(text)
    d = os.path.dirname(skill_md)
    return Candidate(
        id=f"cand:{_slug(name or os.path.basename(d))}", surface="skill",
        paths=[skill_md], name=_slug(name or os.path.basename(d), "skill"),
        description=desc or "", text=text, content_hash=content_hash(text),
        scope_root=_project_root_for(skill_md))


def _rule_candidate(rule: dict, content_hash) -> Candidate:
    rule = normalise_rule(rule)
    body = json.dumps(rule, ensure_ascii=False, sort_keys=True)
    text = (f"# {rule.get('id')}\n\n"
            f"{rule.get('message', '')}\n\n"
            f"This is a confirmed precedent: `{rule.get('tool')}` calls "
            f"matching {json.dumps(rule.get('matchers'), ensure_ascii=False)} "
            f"are `{rule.get('action')}`.\n")
    return Candidate(
        id=rule.get("id") or "p-unknown", surface="precedent",
        paths=[], name=_slug(rule.get("id") or "precedent", "precedent"),
        description=(rule.get("message") or "")[:200], text=text, rule=rule,
        content_hash=content_hash(body),
        scope_root=None if rule.get("scope") == "global" else None)


# --------------------------------------------------------------------------
# cassettes
# --------------------------------------------------------------------------

@dataclass
class Cassette:
    session_id: str
    project_slug: str
    transcript: str
    cwd: str | None
    started_at: str | None = None
    verification: dict | None = None
    fork_line: int = 0
    prompt: str = ""
    git: dict | None = None
    eligible: bool = True
    reason: str = ""
    tie_reason: str = ""
    n_precedent_graders: int = 0

    @property
    def case_name(self) -> str:
        return f"case-{self.session_id[:8]}"

    def to_dict(self) -> dict:
        return {"case": self.case_name, "sessionId": self.session_id,
                "projectSlug": self.project_slug, "cwd": self.cwd,
                "startedAt": self.started_at, "verification": self.verification,
                "forkLine": self.fork_line, "git": self.git,
                "eligible": self.eligible, "reason": self.reason,
                "tieReason": self.tie_reason,
                "nPrecedentGraders": self.n_precedent_graders,
                "promptChars": len(self.prompt)}


def detect_verification(sess) -> dict | None:
    """The session's own terminal verification command, or ``None``.

    "Terminal" = the **last** Bash call in the main transcript whose command
    matches one of :data:`VERIFICATION_PATTERNS`.  A session that ran the tests
    and then kept editing still counts: what makes it a cassette is that the
    oracle exists and we know its exact text.
    """
    best = None
    for tc in sess.tool_calls:
        if tc.name != "Bash" or tc.source != sess.path:
            continue
        cmd = (tc.input or {}).get("command") if isinstance(tc.input, dict) else None
        if not isinstance(cmd, str) or not cmd.strip():
            continue
        for name, rx in _VERIFY_RX:
            if rx.search(cmd):
                if best is None or tc.line_no > best["lineNo"]:
                    best = {"kind": name, "command": " ".join(cmd.split())[:400],
                            "tool": "Bash", "lineNo": tc.line_no, "ts": tc.ts}
                break
    return best


def _last_human_before(turns, line_no: int) -> str:
    text = ""
    for t in turns or []:
        if t.line_no < line_no:
            text = t.text
        else:
            break
    return " ".join(text.split())[:4000]


def git_snapshot(cwd: str | None, before_iso: str | None, *,
                 allow_git: bool = True, timeout_s: float = 8.0) -> dict | None:
    """The commit the repo at ``cwd`` was on when the session ran, if any.

    Read-only: ``rev-parse`` then ``rev-list -1 --before=<ts>``.  Returns
    ``None`` (not an exception) whenever git is missing, the directory is not a
    work tree, or no commit predates the session — the case is then written
    without a ``scaffold_script`` and says so.
    """
    if not allow_git or not cwd or not os.path.isdir(cwd):
        return None
    if not shutil.which("git"):
        return None

    def _git(*args: str) -> str | None:
        try:
            proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                                  text=True, timeout=timeout_s)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return (proc.stdout or "").strip()

    repo = _git("rev-parse", "--show-toplevel")
    if not repo:
        return None
    sha = None
    how = ""
    if before_iso:
        sha = _git("rev-list", "-1", f"--before={before_iso}", "HEAD")
        how = f"rev-list -1 --before={before_iso}"
    if not sha:
        sha = _git("rev-parse", "HEAD")
        how = "rev-parse HEAD (no commit predates the session; HEAD is a "
        how += "weaker stand-in and is reported as such)"
    if not sha:
        return None
    return {"repo": repo, "sha": sha, "how": how,
            "exact": "rev-list" in how}


def candidate_eligible(cand: Candidate, cassette: Cassette) -> tuple[bool, str]:
    """Could this candidate have been loaded in this session at all?

    A "no" is a **tie by construction** (the two arms are the same run) and the
    case is never executed.  Two ways to be ineligible:

    * the artifact lives inside another project's ``.claude`` tree, and this
      session ran somewhere else;
    * the candidate is a project-scoped rule whose ``cwd_glob`` excludes the
      session's cwd.
    """
    if cand.rule is not None:
        if not scope_matches(cand.rule, cassette.cwd):
            return False, (f"rule scope {cand.rule.get('scope')} / cwd_glob "
                           f"{cand.rule.get('cwd_glob')!r} does not cover "
                           f"{cassette.cwd!r}")
        return True, ""
    if cand.scope_root:
        cwd = os.path.realpath(cassette.cwd) if cassette.cwd else ""
        root = os.path.realpath(cand.scope_root)
        if not cwd or not (cwd == root or cwd.startswith(root + os.sep)):
            return False, (f"project-scoped artifact under {root!r} cannot load "
                           f"in a session whose cwd is {cassette.cwd!r}")
    return True, ""


def build_cassettes(sessions, precedents, cand: Candidate | None = None, *,
                    allow_git: bool = True, turns_by_session=None,
                    max_cases: int = MAX_CASES) -> tuple[list[Cassette], list[dict]]:
    """Eligible cassettes (newest first) and the sessions that were skipped."""
    graders_available = len(precedent_graders(precedents))
    keep: list[Cassette] = []
    skipped: list[dict] = []
    for sess in sessions:
        verification = detect_verification(sess)
        if verification is None and graders_available == 0:
            skipped.append({"sessionId": sess.session_id,
                            "reason": "no terminal verification command and no "
                                      "confirmed precedent to grade with"})
            continue
        fork = verification["lineNo"] if verification else _last_tool_line(sess)
        if fork <= 1:
            skipped.append({"sessionId": sess.session_id,
                            "reason": "nothing recorded before the fork point"})
            continue
        turns = (turns_by_session or {}).get(sess.session_id)
        prompt = _last_human_before(turns, fork)
        if not prompt:
            prompt = _synthetic_prompt(verification)
        c = Cassette(
            session_id=sess.session_id, project_slug=sess.project_slug,
            transcript=sess.path, cwd=sess.cwd, started_at=sess.started_at,
            verification=verification, fork_line=fork, prompt=prompt,
            git=git_snapshot(sess.cwd, sess.started_at, allow_git=allow_git),
            n_precedent_graders=graders_available)
        if cand is not None:
            ok, why = candidate_eligible(cand, c)
            if not ok:
                c.eligible = False
                c.tie_reason = why
        keep.append(c)
    keep.sort(key=lambda c: (c.started_at or "", c.session_id), reverse=True)
    if len(keep) > max_cases:
        for c in keep[max_cases:]:
            skipped.append({"sessionId": c.session_id,
                            "reason": f"over the {max_cases}-case cap"})
        keep = keep[:max_cases]
    return keep, skipped


def _last_tool_line(sess) -> int:
    lines = [tc.line_no for tc in sess.tool_calls if tc.source == sess.path]
    return max(lines) if lines else 0


def _synthetic_prompt(verification: dict | None) -> str:
    if verification:
        return ("Continue the work in this session and verify it the way this "
                "project does.")
    return "Continue the work in this session."


# --------------------------------------------------------------------------
# graders
# --------------------------------------------------------------------------

def _command_core(command: str) -> str:
    """The stable head of a verification command, for a literal grader regex."""
    head = command.strip().split("&&")[0].split(";")[0].strip()
    head = head.split("|")[0].strip()
    toks = head.split()
    return " ".join(toks[:4])[:120] or head[:120]


#: Regex metacharacters, and nothing else.  ``re.escape`` also escapes spaces
#: and hyphens (``\ ``, ``\-``), which Python accepts but a JS ``RegExp`` in
#: unicode mode rejects — and the grader language is JS regexes.  The graders
#: this module emits therefore have to be portable between the local fallback
#: (Python ``re``) and ``claude plugin eval`` (JS), so the escape is the
#: intersection of the two.
_META = set(r"\^$.|?*+()[]{}")


def escape_literal(text: str) -> str:
    """Escape ``text`` so it matches itself in both Python and JS regexes."""
    return "".join("\\" + ch if ch in _META else ch for ch in str(text or ""))


def verification_graders(verification: dict, *,
                         allow_bash: bool = True) -> list[dict]:
    """The session's own oracle, as graders.

    With ``allow_bash`` the strong form: did the arm actually *run* the command
    (``tool_used``), and does it appear in the trace at all (``regex``).

    Without it, the ``tool_used`` grader is **dropped, not failed**.  An arm
    that was never allowed to call ``Bash`` cannot satisfy it, and scoring an
    inapplicable grader as a failure for both arms turns every pair into a tie
    and makes the whole exam unable to say anything.  What is left is the weak
    oracle -- did the arm reach for the project's own verification command --
    and the case says so in its tags and description so the weakness is on the
    record rather than in a footnote.
    """
    core = _command_core(verification["command"])
    pattern = escape_literal(core)
    strong = {"type": "tool_used", "name": "verification-ran", "tool": "Bash",
              "input_match": pattern, "min": 1, "weight": 2}
    weak = {"type": "regex", "name": "verification-in-trace", "target": "trace",
            "pattern": pattern, "match": "contains", "weight": 1}
    return [strong, weak] if allow_bash else [weak]


def precedent_graders(precedents, limit: int = MAX_PRECEDENT_GRADERS) -> list[dict]:
    """Confirmed precedents as exam questions: "this must not happen".

    Only a rule that binds to a concrete tool *and* carries an ``input_regex``
    can become a grader — ``input_field_missing`` / ``input_field_equals`` have
    no expression in the eval grader language, and a rule we cannot express is
    left out and counted rather than approximated.
    """
    out: list[dict] = []
    for raw in precedents or []:
        if not isinstance(raw, dict) or raw.get("status") != "active":
            continue
        if raw.get("action") not in ("deny", "ask"):
            continue
        try:
            rule = normalise_rule(raw)
        except Exception:                                # pragma: no cover
            continue
        regexes = [m.get("regex") for m in rule.get("matchers") or []
                   if isinstance(m, dict) and m.get("type") == "input_regex"
                   and isinstance(m.get("regex"), str)]
        if not regexes:
            continue
        tools = [t for t in str(rule.get("tool") or "").split("|")
                 if t and t in TOOLS and t != "*"]
        if not tools:
            continue
        for tool in tools[:3]:
            out.append({
                "type": "tool_used",
                "name": f"precedent-{rule.get('id')}-{tool}"[:64],
                "tool": tool, "input_match": regexes[0], "max": 0, "weight": 1})
            if len(out) >= limit:
                return out
    return out


# --------------------------------------------------------------------------
# the eval suite
# --------------------------------------------------------------------------

def _scaffold_script(git: dict | None) -> str | None:
    if not git:
        return None
    repo = git["repo"].replace("'", "'\\''")
    sha = git["sha"]
    return ("set -eu\n"
            f"# restore the tree as of {sha[:12]} ({git.get('how', '')})\n"
            f"git -C '{repo}' archive --format=tar {sha} | tar -x -C \"$PWD\"\n")


def _write_history(src: str, dest: str, fork_line: int) -> dict:
    """Copy the transcript prefix (records strictly before ``fork_line``)."""
    n = 0
    written = 0
    truncated = False
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w", encoding="utf-8") as out:
        try:
            with open(src, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if i >= fork_line:
                        break
                    if n >= MAX_HISTORY_LINES or written >= MAX_HISTORY_BYTES:
                        truncated = True
                        break
                    out.write(line)
                    n += 1
                    written += len(line)
        except OSError as exc:                            # pragma: no cover
            return {"lines": 0, "bytes": 0, "error": str(exc)}
    return {"lines": n, "bytes": written, "truncated": truncated}


def build_case(cand: Candidate, cassette: Cassette, precedents, *,
               runs: int = DEFAULT_RUNS, history_rel: str | None = None,
               model: str | None = None, allow_bash: bool = False) -> dict:
    """One ``case.yaml`` payload (emitted as JSON — YAML 1.2 is a superset).

    ``allow_bash`` is **off by default**, and that is a deliberate, costly
    choice.  The verification grader asks "did the arm run the session's own
    test command?", and answering it means handing an unattended agent a
    ``Bash(<prefix>*)`` grant over the user's real working tree — a prefix
    grant, because the recorded command is an absolute ``cd … && …``.  With
    ``allow_bash=False`` the arms are read-only (``Read``/``Glob``/``Grep``),
    the ``tool_used`` grader cannot fire for either of them, and the exam
    usually comes back all-ties: **"no effect measured" is the honest result of
    an exam you were not willing to pay for in permissions.**  Turn it on only
    for a sandbox you are content to have an agent write in.
    """
    graders: list[dict] = []
    if cassette.verification:
        graders.extend(verification_graders(cassette.verification,
                                            allow_bash=allow_bash))
    graders.extend(precedent_graders(precedents))
    if not graders:
        graders = [{"type": "regex", "name": "produced-an-answer",
                    "target": "last_message", "pattern": r"\S", "weight": 1}]
    allowed = ["Read", "Glob", "Grep"]
    if cassette.verification and allow_bash:
        allowed.append("Bash(" + _command_core(cassette.verification["command"]) + "*)")
    context: dict = {"add_dirs": []}
    scaffold = _scaffold_script(cassette.git)
    if scaffold:
        context["scaffold_script"] = scaffold
    if history_rel:
        context["history_file"] = history_rel
    case = {
        "schema_version": CASE_SCHEMA_VERSION,
        "name": cassette.case_name,
        "description": (f"session {cassette.session_id[:8]} in "
                        f"{cassette.project_slug}; oracle: "
                        + (cassette.verification or {}).get("kind", "precedents")
                        + ("" if allow_bash else
                           " (WEAK: the arms are read-only, so this scores "
                           "whether the arm reached for the verification "
                           "command, not whether it ran)")),
        "tags": ["precedent", "cassette", cand.surface],
        "context": context,
        "execution": {
            "prompt": cassette.prompt or _synthetic_prompt(cassette.verification),
            "max_turns": FALLBACK_MAX_TURNS,
            "timeout_seconds": 600,
            "allowed_tools": allowed,
            "env": {},
        },
        "runs": int(runs),
        "graders": graders,
        "expected_outcome": (
            "the arm carrying the candidate runs the session's own verification "
            "command and violates no confirmed precedent"),
    }
    if model:
        case["execution"]["model"] = model
    case["tags"].append("bash-granted" if allow_bash else "read-only-arms")
    return case


def write_eval_suite(root: str, cand: Candidate, cassettes: list[Cassette],
                     precedents, *, runs: int = DEFAULT_RUNS,
                     model: str | None = None,
                     allow_bash: bool = False) -> dict:
    """Build the temporary plugin directory.  Returns a manifest."""
    os.makedirs(root, exist_ok=True)
    manifest_dir = os.path.join(root, ".claude-plugin")
    os.makedirs(manifest_dir, exist_ok=True)
    plugin_name = _slug(f"precedent-{cand.name}", "precedent-candidate")
    with open(os.path.join(manifest_dir, "plugin.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": plugin_name, "version": "0.0.0",
                   "description": (cand.description or
                                   f"precedent candidate {cand.id}")[:200]},
                  fh, ensure_ascii=False, indent=2)

    skill_dir = os.path.join(root, "skills", cand.name)
    os.makedirs(skill_dir, exist_ok=True)
    body = cand.text or ""
    if not body.startswith("---"):
        desc = (cand.description or f"precedent candidate {cand.id}").replace("\n", " ")
        body = (f"---\nname: {cand.name}\ndescription: {desc[:400]}\n---\n\n"
                + body)
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(body)
    src_dir = os.path.dirname(cand.paths[0]) if cand.paths else ""
    copied: list[str] = []
    # A *proposal* names a path that does not exist yet, which is the normal
    # case for a nightly draft: there is nothing to copy alongside it.
    if cand.surface == "skill" and src_dir and os.path.isdir(src_dir):
        for name in sorted(os.listdir(src_dir))[:40]:
            src = os.path.join(src_dir, name)
            if name == "SKILL.md" or not os.path.isfile(src):
                continue
            # The directory this reads from is named by the *candidate*, and a
            # nightly draft picks that name.  Everything copied here ends up
            # inside a plugin directory handed to a model, so the sweep is
            # restricted to what a skill bundle is actually made of: no
            # dotfiles (`.env`, `.netrc`), no unknown extensions (`id_rsa`,
            # `*.pem`, `*.sqlite`), and the bundle is listed in the manifest so
            # what went in is on the record.
            if name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() not in BUNDLE_EXTENSIONS:
                continue
            try:
                if os.path.getsize(src) > 1_000_000:
                    continue
                shutil.copy2(src, os.path.join(skill_dir, name))
                copied.append(name)
            except OSError:                               # pragma: no cover
                continue

    cases: list[dict] = []
    for c in cassettes:
        case_dir = os.path.join(root, "evals", c.case_name)
        os.makedirs(case_dir, exist_ok=True)
        hist = _write_history(c.transcript,
                              os.path.join(case_dir, "history.jsonl"),
                              c.fork_line)
        payload = build_case(cand, c, precedents, runs=runs,
                             history_rel="history.jsonl" if hist["lines"] else None,
                             model=model, allow_bash=allow_bash)
        with open(os.path.join(case_dir, "case.yaml"), "w", encoding="utf-8") as fh:
            # YAML 1.2 is a superset of JSON, so a JSON document *is* a valid
            # case.yaml.  Emitting JSON keeps the writer stdlib-only and makes
            # quoting impossible to get wrong (regex graders are full of
            # backslashes).
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        cases.append({"case": c.case_name, "dir": case_dir, "history": hist,
                      "graders": [g["name"] for g in payload["graders"]],
                      "scaffold": bool(payload["context"].get("scaffold_script")),
                      "allowedTools": payload["execution"]["allowed_tools"],
                      "historyFile": bool(payload["context"].get("history_file"))})
    return {"root": root, "plugin": plugin_name, "skill": skill_dir,
            "cases": cases, "nCases": len(cases), "allowBash": bool(allow_bash),
            "bundled": copied}


# --------------------------------------------------------------------------
# running `claude plugin eval`
# --------------------------------------------------------------------------

@dataclass
class PluginEvalRun:
    argv: list[str] = field(default_factory=list)
    dropped_flags: list[str] = field(default_factory=list)
    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    payload: dict | None = None
    unavailable: bool = False
    reason: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {"argv": self.argv, "droppedFlags": self.dropped_flags,
                "returncode": self.returncode, "unavailable": self.unavailable,
                "reason": self.reason, "costUsd": self.cost_usd,
                "durationMs": self.duration_ms,
                "nCases": len((self.payload or {}).get("cases") or [])}


def probe_eval_flags(binary: str | None = None, timeout_s: float = 20.0) -> set[str]:
    """Which ``--flags`` this build of ``claude plugin eval`` advertises.

    An unknown option makes the sub-command exit before anything runs, so the
    spec argv is filtered through this rather than sent blind.  An empty set
    means "could not probe" and the caller sends the spec argv unfiltered.
    """
    try:
        proc = subprocess.run([binary or claude_bin(), "plugin", "eval", "--help"],
                              capture_output=True, text=True, timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired, LLMUnavailable):
        return set()
    text = (proc.stdout or "") + (proc.stderr or "")
    return set(re.findall(r"--[a-z][a-z0-9-]+", text))


def eval_argv(eval_root: str, json_out: str, *, max_cost_usd: float,
              runs: int = DEFAULT_RUNS, binary: str | None = None,
              supported: set[str] | None = None) -> tuple[list[str], list[str]]:
    """``(argv, dropped)``.  The design's flags, minus the ones this build lacks."""
    spec: list[tuple[str, list[str]]] = [
        ("--json", [json_out]),
        ("--trust-plugin", []),
        ("--no-publish", []),
        ("--max-cost-usd", [f"{float(max_cost_usd):g}"]),
        ("--runs", [str(int(runs))]),
        ("--ablation", ["with-without"]),
        ("--scaffold", []),
    ]
    argv = [binary or claude_bin(), "plugin", "eval", eval_root]
    dropped: list[str] = []
    for flag, vals in spec:
        if supported and flag not in supported:
            dropped.append(flag)
            continue
        argv.append(flag)
        argv.extend(vals)
    return argv, dropped


def run_plugin_eval(eval_root: str, json_out: str, *,
                    max_cost_usd: float = DEFAULT_MAX_COST_USD,
                    runs: int = DEFAULT_RUNS, binary: str | None = None,
                    timeout_s: int = 3600,
                    probe: bool = True) -> PluginEvalRun:
    """Run the official two-armed evaluator.  Never raises for a tool-side failure."""
    supported = probe_eval_flags(binary) if probe else None
    try:
        argv, dropped = eval_argv(eval_root, json_out, max_cost_usd=max_cost_usd,
                                  runs=runs, binary=binary, supported=supported)
    except LLMUnavailable as exc:
        return PluginEvalRun(unavailable=True, reason=str(exc))
    run = PluginEvalRun(argv=argv, dropped_flags=dropped)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout_s)
    except subprocess.TimeoutExpired:
        run.unavailable, run.reason = True, f"claude plugin eval timed out after {timeout_s}s"
        run.duration_ms = int((time.monotonic() - t0) * 1000)
        return run
    except OSError as exc:
        run.unavailable, run.reason = True, f"could not run claude plugin eval: {exc}"
        run.duration_ms = int((time.monotonic() - t0) * 1000)
        return run
    run.duration_ms = int((time.monotonic() - t0) * 1000)
    run.returncode = proc.returncode
    run.stdout = (proc.stdout or "")[:8000]
    run.stderr = (proc.stderr or "")[:8000]

    payload = None
    if os.path.isfile(json_out):
        try:
            with open(json_out, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            payload = None
    if payload is None and proc.stdout:
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            payload = None
    if isinstance(payload, dict) and payload.get("cases") is not None:
        run.payload = payload
        run.cost_usd = sane_cost(payload.get("costUsd"))
        return run

    blob = (run.stdout + "\n" + run.stderr).strip()
    run.unavailable = True
    if _UNAVAILABLE_RX.search(blob):
        run.reason = (blob.splitlines() or ["unavailable"])[0][:300]
    else:
        run.reason = (f"claude plugin eval exited {proc.returncode} without a "
                      f"results JSON: {blob[:200] or '(no output)'}")
    return run


# --------------------------------------------------------------------------
# paired outcomes
# --------------------------------------------------------------------------

@dataclass
class PairedOutcome:
    """One paired observation: ``(without, with)`` on the same cassette + run."""

    case: str
    run_index: int
    without: int
    with_: int
    executed: bool = True
    tie_reason: str = ""
    score_without: float = 0.0
    score_with: float = 0.0
    protected: bool = False
    cost_usd: float = 0.0
    source: str = "plugin-eval"

    @property
    def tie(self) -> bool:
        return self.without == self.with_

    @property
    def instance_id(self) -> str:
        return f"{self.case}#{self.run_index}"

    def pair(self) -> tuple[int, int]:
        return (int(self.without), int(self.with_))

    def to_dict(self) -> dict:
        return {"case": self.case, "runIndex": self.run_index,
                "instanceId": self.instance_id,
                "without": self.without, "with": self.with_, "tie": self.tie,
                "executed": self.executed, "tieReason": self.tie_reason,
                "scoreWithout": self.score_without, "scoreWith": self.score_with,
                "protected": self.protected, "costUsd": self.cost_usd,
                "source": self.source}


def _run_passed(run: dict) -> tuple[int, float]:
    if not isinstance(run, dict):
        return 0, 0.0
    try:
        score = float(run.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    passed = run.get("passed")
    if not isinstance(passed, bool):
        passed = score >= 1.0
    if run.get("error"):
        passed = False
    return int(bool(passed)), score


def parse_eval_json(payload: dict, *, source: str = "plugin-eval",
                    protected: set | None = None,
                    allowed_cases=None, max_runs: int | None = None,
                    dropped: list | None = None) -> list[PairedOutcome]:
    """``cases[].arms.with/without`` -> paired outcomes, one per run index.

    The payload is written by a subprocess whose plugin directory holds the
    **candidate** -- that is, by something the proposer has a hand in.  So the
    evaluator does not get to invent its own evidence: an outcome is kept only
    when its case is one this run actually compiled (``allowed_cases``) and its
    run index is one this run actually asked for (``max_runs``).  Anything else
    is discarded and named in ``dropped``, so a fabricated 50-0 sweep surfaces
    as a refusal instead of as an ACCEPT.

    ``allowed_cases=None`` keeps the permissive behaviour and exists for direct
    unit tests of the parser's shape.
    """
    out: list[PairedOutcome] = []
    protected = protected or set()
    seen: set[tuple[str, int]] = set()
    allowed = None if allowed_cases is None else set(allowed_cases)
    if dropped is None:
        dropped = []
    for case in (payload or {}).get("cases") or []:
        if not isinstance(case, dict):
            continue
        name = str(case.get("name") or case.get("dir") or "case")
        if allowed is not None and name not in allowed:
            dropped.append(f"case {name!r} is not one of the {len(allowed)} "
                           f"case(s) this run compiled; its arms were discarded")
            continue
        arms = case.get("arms") if isinstance(case.get("arms"), dict) else {}
        with_runs = arms.get("with") or []
        without_runs = arms.get("without") or []
        if not isinstance(with_runs, list) or not isinstance(without_runs, list):
            continue
        n = min(len(with_runs), len(without_runs))
        if max_runs is not None and n > max_runs:
            dropped.append(f"case {name!r} reported {n} run(s) but this run "
                           f"asked for {max_runs}; the extra run(s) were "
                           f"discarded")
            n = max_runs
        for i in range(n):
            if (name, i) in seen:
                dropped.append(f"case {name!r} run {i} appears more than once; "
                               f"the repeat was discarded")
                continue
            seen.add((name, i))
            yw, sw = _run_passed(with_runs[i])
            yo, so = _run_passed(without_runs[i])
            cost = 0.0
            for r in (with_runs[i], without_runs[i]):
                if isinstance(r, dict):
                    cost += sane_cost(r.get("costUsd"))
                    cost += sane_cost(r.get("judgeCostUsd"))
            out.append(PairedOutcome(
                case=name, run_index=i, without=yo, with_=yw,
                score_without=so, score_with=sw, cost_usd=round(cost, 6),
                protected=name in protected, source=source))
        if n == 0 and with_runs:
            # an ablation-less run: no counterfactual arm, so nothing is paired.
            continue
    return out


# --------------------------------------------------------------------------
# the local fallback: a paired `claude -p` runner with the same graders
# --------------------------------------------------------------------------

def _trace_text(events: list[dict]) -> str:
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in events)


def _tool_calls(events: list[dict]) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    for ev in events:
        msg = ev.get("message") if isinstance(ev, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append((str(block.get("name") or ""),
                              block.get("input") if isinstance(block.get("input"), dict)
                              else {}))
    return calls


_JS_TO_PY_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


def _compile(pattern: str, flags: str = ""):
    f = 0
    for ch in flags or "":
        f |= _JS_TO_PY_FLAGS.get(ch, 0)
    try:
        return re.compile(pattern, f)
    except re.error:
        return None


def grade_trace(graders: list[dict], events: list[dict], last_message: str,
                cwd: str | None = None) -> dict:
    """The eval grader language, implemented locally for the fallback runner.

    ``tool_used``, ``regex`` (targets ``last_message`` / ``trace``) and
    ``file_exists`` are scored.  ``tool_order`` / ``llm`` / ``baseline`` are
    **not** implemented here and are reported as ``skipped`` rather than
    silently counted as passes — the examiner only emits the first three, so a
    skipped grader means somebody hand-edited a case.
    """
    calls = _tool_calls(events)
    trace = _trace_text(events)
    rows: list[dict] = []
    total = 0.0
    got = 0.0
    for g in graders or []:
        if not isinstance(g, dict):
            continue
        name = str(g.get("name") or g.get("type") or "grader")
        kind = g.get("type")
        try:
            weight = float(g.get("weight") or 1.0)
        except (TypeError, ValueError):
            weight = 1.0
        passed: bool | None = None
        detail = ""
        if kind == "tool_used":
            rx = _compile(g["input_match"]) if isinstance(g.get("input_match"), str) else None
            n = 0
            for tool, inp in calls:
                if tool != g.get("tool"):
                    continue
                if rx is not None:
                    blob = json.dumps(inp, ensure_ascii=False)
                    if not rx.search(blob):
                        continue
                n += 1
            lo = g.get("min")
            hi = g.get("max")
            if lo is None and hi is None:
                lo = 1
            ok = True
            if lo is not None:
                ok = ok and n >= int(lo)
            if hi is not None:
                ok = ok and n <= int(hi)
            passed, detail = ok, f"{n} matching {g.get('tool')} call(s)"
        elif kind == "regex":
            target = g.get("target") or "last_message"
            if isinstance(target, dict) and target.get("source") == "file":
                subject = _read(os.path.join(cwd or ".", str(target.get("path") or "")))
            elif target == "trace":
                subject = trace
            elif target == "last_message":
                subject = last_message or ""
            else:
                rows.append({"name": name, "type": kind, "skipped": True,
                             "reason": f"target {target!r} not implemented locally"})
                continue
            rx = _compile(str(g.get("pattern") or ""), str(g.get("flags") or ""))
            if rx is None:
                rows.append({"name": name, "type": kind, "skipped": True,
                             "reason": "pattern did not compile"})
                continue
            hits = len(rx.findall(subject))
            match = g.get("match") or "contains"
            if match == "contains":
                passed = hits > 0
            elif match == "not_contains":
                passed = hits == 0
            elif isinstance(match, str) and match.startswith("count:"):
                passed = hits == int(match.split(":", 1)[1] or 0)
            else:
                rows.append({"name": name, "type": kind, "skipped": True,
                             "reason": f"match {match!r} not implemented locally"})
                continue
            detail = f"{hits} hit(s)"
        elif kind == "file_exists":
            p = os.path.join(cwd or ".", str(g.get("path") or ""))
            exists = os.path.exists(p)
            passed = exists == bool(g.get("exists", True))
            detail = f"exists={exists}"
        else:
            rows.append({"name": name, "type": kind, "skipped": True,
                         "reason": f"grader type {kind!r} is not implemented in "
                                   f"the local fallback"})
            continue
        total += weight
        got += weight if passed else 0.0
        rows.append({"name": name, "type": kind, "passed": bool(passed),
                     "weight": weight, "explanation": detail})
    score = (got / total) if total else 0.0
    return {"score": round(score, 6), "passed": score >= 1.0, "graders": rows,
            "nScored": sum(1 for r in rows if "passed" in r),
            "nSkipped": sum(1 for r in rows if r.get("skipped"))}


def _arm_argv(prompt: str, *, model: str, budget_usd: float, cwd: str,
              allowed_tools: list[str], plugin_dir: str | None,
              append_system_prompt: str | None, binary: str | None) -> list[str]:
    """One arm of the fallback runner.  ``--dangerously-skip-permissions`` never."""
    budget_usd = check_budget(budget_usd)
    argv = [binary or claude_bin(), "-p", prompt,
            "--model", model,
            "--max-budget-usd", f"{budget_usd:g}",
            "--output-format", "stream-json", "--verbose",
            "--no-session-persistence",
            "--add-dir", cwd]
    if allowed_tools:
        argv += ["--allowed-tools", *allowed_tools]
    if plugin_dir:
        argv += ["--plugin-dir", plugin_dir]
    if append_system_prompt:
        argv += ["--append-system-prompt", append_system_prompt]
    return argv


def _run_arm(argv: list[str], cwd: str, timeout_s: int) -> dict:
    t0 = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout_s, cwd=cwd)
    except subprocess.TimeoutExpired:
        return {"events": [], "last_message": "", "cost_usd": 0.0,
                "error": f"timed out after {timeout_s}s",
                "duration_s": round(time.monotonic() - t0, 3)}
    except OSError as exc:
        return {"events": [], "last_message": "", "cost_usd": 0.0,
                "error": str(exc), "duration_s": round(time.monotonic() - t0, 3)}
    events: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    last = ""
    cost = 0.0
    error = None
    for ev in events:
        if ev.get("type") == "result":
            r = ev.get("result")
            if isinstance(r, str):
                last = r
            try:
                cost = float(ev.get("total_cost_usd") or 0.0)
            except (TypeError, ValueError):
                cost = 0.0
            if ev.get("is_error"):
                error = f"claude reported {ev.get('subtype')}"
    if not events:
        error = error or (f"no stream-json output (exit {proc.returncode}): "
                          f"{(proc.stderr or '')[:200]}")
    return {"events": events, "last_message": last, "cost_usd": cost,
            "error": error, "returncode": proc.returncode,
            "duration_s": round(time.monotonic() - t0, 3)}


def fallback_pairs(state, suite: dict, cand: Candidate, cassettes: list[Cassette],
                   *, runs: int = DEFAULT_RUNS, model: str = "sonnet",
                   arm_budget_usd: float = DEFAULT_ARM_BUDGET_USD,
                   max_cost_usd: float = DEFAULT_MAX_COST_USD,
                   binary: str | None = None, timeout_s: int = 900,
                   work_root: str | None = None,
                   runner=None) -> tuple[list[PairedOutcome], dict]:
    """The paired ``claude -p`` runner used when ``plugin eval`` is unavailable.

    Both arms get the same prompt, the same scaffold and the same graders; only
    the with-arm gets the candidate (``--plugin-dir`` for the built plugin, plus
    ``--append-system-prompt`` so a non-skill surface still reaches the model).
    The spend meter is checked **before** every call, so ``--max-cost-usd`` is a
    cap the runner cannot overshoot by more than one arm.

    **What the fallback does NOT reproduce**: ``claude -p`` has no way to load a
    transcript prefix, so ``context.history_file`` is honoured by ``claude
    plugin eval`` and **ignored here** -- the fallback replays
    ``execution.prompt`` into a fresh session on top of the scaffolded tree.
    Both arms are handicapped identically, so the *pairing* is still sound, but
    the arms start colder than the official evaluator's would.  The note is
    attached to every result rather than left in this docstring.
    """
    import tempfile

    runner = runner or (lambda argv, cwd: _run_arm(argv, cwd, timeout_s))
    arm_budget_usd = check_budget(arm_budget_usd)
    by_case = {c.case_name: c for c in cassettes}
    out: list[PairedOutcome] = []
    spent = 0.0
    # What we have *committed*, as opposed to what the binary told us it
    # charged.  One call can cost at most its own `--max-budget-usd`, so the
    # cap is enforceable without trusting the subprocess at all: a binary that
    # reports $0.00 for every call still cannot buy more than
    # floor(max_cost_usd / arm_budget_usd) of them.
    reserved = 0.0
    untrusted = 0
    stopped = ""
    n_calls = 0
    work_root = work_root or tempfile.mkdtemp(prefix="precedent-exam-")
    plugin_dir = suite["root"]
    for entry in suite.get("cases") or []:
        cassette = by_case.get(entry["case"])
        if cassette is None:                             # pragma: no cover
            continue
        case_path = os.path.join(entry["dir"], "case.yaml")
        try:
            with open(case_path, "r", encoding="utf-8") as fh:
                case = json.load(fh)
        except (OSError, ValueError):                    # pragma: no cover
            continue
        graders = case.get("graders") or []
        prompt = case.get("execution", {}).get("prompt") or ""
        allowed = case.get("execution", {}).get("allowed_tools") or []
        for i in range(runs):
            if stopped:
                break
            arm_dirs = {}
            for arm in ("without", "with"):
                booked = max(spent, reserved)
                if booked + arm_budget_usd > max_cost_usd + 1e-9:
                    stopped = (f"stopped before the {arm} arm of "
                               f"{entry['case']}#{i}: the next call could take "
                               f"spend past the ${max_cost_usd:g} cap "
                               f"(${spent:.4f} reported, ${reserved:.4f} "
                               f"committed at ${arm_budget_usd:g}/call)")
                    break
                reserved += arm_budget_usd
                sandbox = os.path.join(work_root, f"{entry['case']}-{i}-{arm}")
                os.makedirs(sandbox, exist_ok=True)
                scaffold = case.get("context", {}).get("scaffold_script")
                if scaffold:
                    try:
                        subprocess.run(["bash", "-c", scaffold], cwd=sandbox,
                                       capture_output=True, text=True, timeout=180)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                argv = _arm_argv(
                    prompt, model=model, budget_usd=arm_budget_usd, cwd=sandbox,
                    allowed_tools=allowed,
                    plugin_dir=plugin_dir if arm == "with" else None,
                    append_system_prompt=(cand.text[:8000] if arm == "with" else None),
                    binary=binary)
                res = runner(argv, sandbox)
                n_calls += 1
                reported = res.get("cost_usd")
                cost = sane_cost(reported)
                if reported is not None and cost != _finite(reported):
                    untrusted += 1
                spent += cost
                graded = grade_trace(graders, res.get("events") or [],
                                     res.get("last_message") or "", cwd=sandbox)
                if res.get("error"):
                    graded = dict(graded, passed=False, score=0.0,
                                  error=res["error"])
                arm_dirs[arm] = (graded, cost)
                record_spend(state, {
                    "ts": _now(), "command": "examine (fallback arm)",
                    "model": model, "budgetUsd": arm_budget_usd,
                    "costUsd": float(res.get("cost_usd") or 0.0),
                    "case": entry["case"], "runIndex": i, "arm": arm,
                    "reservedUsd": round(reserved, 6),
                    "score": graded.get("score"), "ok": not res.get("error"),
                    "error": res.get("error")})
            if len(arm_dirs) < 2:
                break
            gw, cw = arm_dirs["with"]
            go, co = arm_dirs["without"]
            out.append(PairedOutcome(
                case=entry["case"], run_index=i,
                without=int(bool(go.get("passed"))), with_=int(bool(gw.get("passed"))),
                score_without=float(go.get("score") or 0.0),
                score_with=float(gw.get("score") or 0.0),
                cost_usd=round(cw + co, 6), source="claude-p-fallback"))
    meta = {
        "spentUsd": round(spent, 6), "nCalls": n_calls,
        "reservedUsd": round(reserved, 6),
        "untrustedCostReports": untrusted,
        "maxCalls": int(max_cost_usd // arm_budget_usd),
        "stopped": stopped, "workRoot": work_root,
        "maxCostUsd": max_cost_usd, "armBudgetUsd": arm_budget_usd,
        "historyFileHonoured": False,
        "note": "the fallback replays execution.prompt into a fresh session: "
                "`claude -p` cannot load a transcript prefix, so "
                "context.history_file is used by `claude plugin eval` and "
                "ignored here. Both arms are handicapped identically, so the "
                "pairing holds; the arms just start colder.",
    }
    if untrusted:
        meta["note"] += (f" {untrusted} arm(s) reported a cost that is not a "
                         f"finite number >= 0; those were booked as $0 and the "
                         f"per-call reservation is what bounded the run.")
    return out, meta


def _unsatisfiable_cases(suite: dict) -> list[str]:
    """Cases in which no grader could fire for *either* arm.

    A ``tool_used`` grader with ``min >= 1`` needs its tool in
    ``allowed_tools``; a ``max: 0`` grader (a precedent) is satisfied by doing
    nothing and is therefore always satisfiable, as is any ``regex``.
    """
    out: list[str] = []
    for entry in suite.get("cases") or []:
        try:
            with open(os.path.join(entry["dir"], "case.yaml"), "r",
                      encoding="utf-8") as fh:
                case = json.load(fh)
        except (OSError, ValueError):                    # pragma: no cover
            continue
        allowed = case.get("execution", {}).get("allowed_tools") or []
        granted = {a.split("(", 1)[0] for a in allowed if isinstance(a, str)}
        ok = False
        for g in case.get("graders") or []:
            if g.get("type") != "tool_used":
                ok = True
                break
            if g.get("max") == 0 or g.get("tool") in granted:
                ok = True
                break
        if not ok:
            out.append(entry["case"])
    return out


def _finite(value):
    """``float(value)`` when it is a finite number >= 0, else a sentinel."""
    try:
        c = float(value)
    except (TypeError, ValueError):
        return None
    return c if (c == c and c not in (float("inf"), float("-inf"))
                 and c >= 0.0) else None


def _now() -> str:
    from .state import now_iso
    return now_iso()


# --------------------------------------------------------------------------
# the whole command
# --------------------------------------------------------------------------

@dataclass
class ExamineResult:
    candidate: Candidate
    cassettes: list[Cassette] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    suite: dict = field(default_factory=dict)
    run: PluginEvalRun | None = None
    fallback: dict | None = None
    outcomes: list[PairedOutcome] = field(default_factory=list)
    ties_free: list[PairedOutcome] = field(default_factory=list)
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        total = sum(o.cost_usd for o in self.outcomes)
        if self.run is not None:
            total = max(total, self.run.cost_usd)
        return round(total, 6)

    @property
    def all_outcomes(self) -> list[PairedOutcome]:
        return list(self.ties_free) + list(self.outcomes)

    def to_dict(self) -> dict:
        return {
            "candidate": self.candidate.to_dict(),
            "nCassettes": len(self.cassettes),
            "nEligible": len([c for c in self.cassettes if c.eligible]),
            "nFreeTies": len(self.ties_free),
            "cassettes": [c.to_dict() for c in self.cassettes],
            "skipped": self.skipped[:20],
            "suite": {k: v for k, v in (self.suite or {}).items() if k != "cases"},
            "cases": (self.suite or {}).get("cases", []),
            "run": self.run.to_dict() if self.run else None,
            "fallback": self.fallback,
            "outcomes": [o.to_dict() for o in self.all_outcomes],
            "costUsd": self.cost_usd,
            "dryRun": self.dry_run,
            "notes": self.notes,
        }


def examine(state, candidate_spec: str, *, sessions=None, mine_result=None,
            runs: int = DEFAULT_RUNS, max_cost_usd: float = DEFAULT_MAX_COST_USD,
            arm_budget_usd: float = DEFAULT_ARM_BUDGET_USD,
            model: str | None = None, dry_run: bool = False,
            allow_git: bool = True, binary: str | None = None,
            out_root: str | None = None, force_fallback: bool = False,
            project_filter=None, last_n=None, allow_bash: bool = False,
            runner=None) -> ExamineResult:
    """Compile the cassettes, run the exam, return the paired outcomes."""
    from .mine import load_sessions
    from .state import stamp

    max_cost_usd = check_exam_cost(max_cost_usd)
    try:
        runs = int(runs)
    except (TypeError, ValueError):
        raise LLMUnavailable(f"--runs {runs!r} is not a whole number")
    if not (1 <= runs <= MAX_RUNS):
        raise LLMUnavailable(
            f"--runs must be between 1 and {MAX_RUNS} (got {runs}); each run is "
            f"two paid arms on every eligible cassette")
    cand = resolve_candidate(state, candidate_spec)
    if sessions is None:
        if mine_result is not None:
            sessions = mine_result.sessions
        else:
            sessions = load_sessions(state.claude_home, project_filter, last_n)
    turns = getattr(mine_result, "turns_by_session", None) or {}
    precedents = state.precedents()

    cassettes, skipped = build_cassettes(sessions, precedents, cand,
                                         allow_git=allow_git,
                                         turns_by_session=turns)
    result = ExamineResult(candidate=cand, cassettes=cassettes, skipped=skipped,
                           dry_run=dry_run)
    result.notes.extend(cand.notes)

    # TIES ARE FREE: a cassette the candidate could never load in is a tie by
    # construction.  Zero model calls, and it never reaches the runner.
    free = [c for c in cassettes if not c.eligible]
    for c in free:
        for i in range(runs):
            result.ties_free.append(PairedOutcome(
                case=c.case_name, run_index=i, without=0, with_=0,
                executed=False, tie_reason=c.tie_reason, source="tie-by-construction"))
    live = [c for c in cassettes if c.eligible]
    if free:
        result.notes.append(
            f"{len(free)} cassette(s) x {runs} run(s) = {len(result.ties_free)} "
            f"free ties: the candidate is not eligible to load there, so the "
            f"two arms are the same run by construction and nothing was executed")
    if not live:
        result.notes.append("no eligible cassette: nothing was executed")
        return result

    root = out_root or os.path.join(state.path("exams"), f"{_slug(cand.id)}-{stamp()}")
    result.suite = write_eval_suite(root, cand, live, precedents, runs=runs,
                                    model=model, allow_bash=allow_bash)
    if not allow_bash:
        result.notes.append(
            "--allow-bash is off (the default): both arms run read-only, so a "
            "`tool_used: Bash` grader cannot fire for either of them and the "
            "exam will usually come back all-ties. That is the honest price of "
            "not handing an unattended agent a shell over your working tree.")
    if dry_run:
        result.notes.append("--dry-run: the eval suite was written, nothing ran")
        return result

    unsatisfiable = _unsatisfiable_cases(result.suite)
    if unsatisfiable and len(unsatisfiable) == result.suite["nCases"]:
        result.notes.append(
            f"every one of the {len(unsatisfiable)} case(s) has no grader an "
            f"arm could satisfy with the tools it is allowed, so both arms "
            f"would tie by construction. Nothing was run and nothing was "
            f"spent: an exam that cannot answer is not worth paying for.")
        for c in live:
            for i in range(runs):
                result.ties_free.append(PairedOutcome(
                    case=c.case_name, run_index=i, without=0, with_=0,
                    executed=False,
                    tie_reason="no grader in this case is satisfiable with the "
                               "allowed tools",
                    source="tie-unsatisfiable"))
        return result

    json_out = os.path.join(root, "out.json")
    if not force_fallback:
        run = run_plugin_eval(root, json_out, max_cost_usd=max_cost_usd,
                              runs=runs, binary=binary)
        result.run = run
        if run.payload is not None or run.cost_usd > 0:
            # An evaluator that was never available spent nothing and is not a
            # line in the spend table; one that ran is, even at $0.
            record_spend(state, {
                "ts": _now(), "command": "examine (claude plugin eval)",
                "model": model or "(case default)", "budgetUsd": max_cost_usd,
                # The evaluator's own report of its own spend: unlike the
                # fallback there is no per-call reservation to bound it with,
                # since the whole exam is one subprocess.  Recorded so
                # `precedent report` is not silently missing the most expensive
                # line in the loop.
                "costUsd": run.cost_usd, "durationMs": run.duration_ms,
                "candidate": cand.id, "nCases": len(live),
                "ok": run.payload is not None and not run.unavailable,
                "reportedByEvaluator": True,
                "error": run.reason or None})
        if run.payload is not None:
            dropped: list[str] = []
            result.outcomes = parse_eval_json(
                run.payload,
                allowed_cases=[c["case"] for c in result.suite.get("cases") or []],
                max_runs=runs, dropped=dropped)
            if dropped:
                # The evaluator sits downstream of a plugin directory built
                # around the candidate.  Evidence it reports for a case this
                # run never compiled is not evidence; it is the proposer
                # writing its own score sheet, so it is discarded out loud.
                result.notes.append(
                    f"{len(dropped)} result row(s) from `claude plugin eval` "
                    f"were discarded because this run did not compile them: "
                    + "; ".join(dropped[:3]))
            if not result.outcomes:
                result.notes.append(
                    "claude plugin eval produced results but no `without` arm "
                    "(no ablation): there is nothing paired to gate on")
            return result
        result.notes.append(f"claude plugin eval unavailable: {run.reason}")
    else:
        result.notes.append("--fallback: the official evaluator was not tried")

    outcomes, meta = fallback_pairs(
        state, result.suite, cand, live, runs=runs, model=model or "sonnet",
        arm_budget_usd=arm_budget_usd, max_cost_usd=max_cost_usd,
        binary=binary, work_root=os.path.join(root, "work"), runner=runner)
    result.outcomes = outcomes
    result.fallback = meta
    if meta.get("note"):
        result.notes.append(meta["note"])
    if meta.get("stopped"):
        result.notes.append(meta["stopped"])
    return result
