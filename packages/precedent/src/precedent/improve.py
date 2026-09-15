# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""⑤ THE NIGHTLY IMPROVER — failure clusters in, bounded edits out, gates in between.

``precedent improve [--budget-usd 2]`` is the third proposer (the other two are
the correction miner and Claude Code's own memory/skill writes).  It is the one
the literature is most pessimistic about — SkillsBench measured **0** benefit
from LLM-written skills against +16.2 for human-written ones, and CTA measured
+0.3pp — so everything it produces is treated as a draft by an untrusted author:

1. **Cluster failure signatures** (free, offline, no model call):
   ``tool_error`` (a tool call that came back ``is_error``), ``retry`` (the same
   tool re-run with the same command inside a short window), ``correction
   without a precedent`` (a mined topic that no confirmed rule covers) and
   ``written but violated`` (a topic whose words are already in CLAUDE.md /
   memory and which happened anyway).
2. **One bounded edit per top cluster**, drafted by ``claude -p --model sonnet``
   under ``--max-budget-usd``, never ``--dangerously-skip-permissions``.  The
   draft must declare ``hypothesis``, ``expected_effect`` and
   ``declared_paths``.
3. **diff == declared paths.**  The edit may touch exactly the files it
   declared — no more, no fewer.  A draft that edits a second file, or declares
   a path it does not edit, is rejected before any gate sees it.
4. **Lint** (deterministic, zero model cost): frontmatter parses, every
   referenced file exists, no session-specific facts (absolute home paths,
   ports, hostnames, dates, session ids) in a *global*-scope artifact, and a
   secret scan.
5. **Route to the competent gate**: a ``precedent`` draft goes to the
   TEMPORAL BIRTH GATE (it is a rule, and a rule is judged against the record);
   anything else goes to the EXAMINER when eligible cassettes exist, and is
   ``HOLD``-ed with reason ``no evidence`` when they do not.  "We cannot
   measure this" is a first-class answer here, not a silent pass.
6. **Rejected drafts go to ``rejected.jsonl``** with their reason and come back
   as negative examples in the next run's prompt.  That is step ⑤ of the loop.

Everything that survives lands in the **docket** as a proposal.  ``precedent``
never writes the edit: the bytes are the user's to apply.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from receipts.transcripts import iter_records

from .llm import (DEFAULT_BUDGET_USD, DEFAULT_MODEL, DEFAULT_TIMEOUT_S,
                  LLMUnavailable, check_budget, load_negatives, record_rejected,
                  record_spend, run_claude, sane_cost)
from .mine import content_tokens, jaccard
from .proposals import append_proposal
from .rules import RuleError, validate_rule
from .state import now_iso

__all__ = [
    "ALLOWED_SURFACES",
    "DEFAULT_TOTAL_BUDGET_USD",
    "PROTECTED_BASENAMES",
    "PROTECTED_DIR_NAMES",
    "protected_path_problems",
    "FailureCluster",
    "ImproveResult",
    "RETRY_EXEMPT_TOOLS",
    "SECRET_PATTERNS",
    "SESSION_FACT_PATTERNS",
    "build_improve_prompt",
    "cluster_failures",
    "enforce_declared_paths",
    "improve",
    "lint_draft",
    "parse_draft",
    "render_improve",
]

DEFAULT_TOTAL_BUDGET_USD = 2.00
DEFAULT_TOP_CLUSTERS = 3
MIN_CLUSTER_COUNT = 2
MAX_EXAMPLES = 4
RETRY_WINDOW = 6
#: Tools whose whole job is to be called again with the same arguments.  A
#: polling call repeated 27 times is a poll, not a retry, and on this machine
#: those four signatures crowded out every real one.  Kept as a visible
#: constant rather than buried in a heuristic, because it is a judgement call
#: about one harness's tool names.
RETRY_EXEMPT_TOOLS = frozenset({
    "TaskOutput", "BashOutput", "Monitor", "TodoWrite", "AskUserQuestion",
    "SendMessage", "KillShell",
})
MAX_CONTENT_CHARS = 20_000

#: Surfaces a nightly draft may touch.  Anything else is rejected outright —
#: the improver does not get to rewrite hooks, settings or precedent itself
#: (that is an L4/L5 event and goes through a human merge, 方案 §3.2).
ALLOWED_SURFACES = ("claude_md", "skill", "precedent")

#: Files a nightly draft may never name, whatever surface it claims to be.
#: ``surface`` is a *label the model chose*; the path is what a human would
#: actually edit, so the enforcement has to live on the path.  ``skill`` and
#: ``claude_md`` are already pinned to a basename by :func:`lint_draft`;
#: ``precedent`` is not, and without this a draft could label itself a rule and
#: put ``~/.claude/settings.json`` (or precedent's own hook script) on the
#: docket with LLM-written content for the user to paste in.
PROTECTED_BASENAMES = frozenset({
    "settings.json", "settings.local.json", "managed-settings.json",
    ".credentials.json", "config.json", "keybindings.json",
    "precedent.json", "candidates.json", "precedents.json", "ledger.jsonl",
    "schedule.json", "protected.json", "proposals.jsonl", "decisions.json",
})
#: Directory names that may not appear anywhere in a declared path.  Hooks are
#: executable and run inside Claude Code; ``.git`` and ``.ssh`` are nobody's
#: business; ``.precedent`` is the acceptance layer's own state, and a proposer
#: that can edit the evaluator is the single system bug behind Camp B's seven
#: evaluator leaks (方案 §2).
PROTECTED_DIR_NAMES = frozenset({
    "hooks", ".git", ".ssh", ".aws", ".precedent", "node_modules",
})


def protected_path_problems(paths, claude_home: str = "") -> list[str]:
    """Paths a nightly draft is never allowed to name.  Deterministic, no model."""
    problems: list[str] = []
    seen: set[str] = set()
    for raw in paths or []:
        p = _expand(str(raw))
        if p in seen:
            continue
        seen.add(p)
        base = os.path.basename(p)
        if base in PROTECTED_BASENAMES:
            problems.append(
                f"{base!r} is not a surface the nightly improver may touch "
                f"(settings, hooks and precedent's own state are L4/L5 events "
                f"and go through a human merge, not the docket)")
        parts = set(p.split(os.sep))
        hit = sorted(parts & PROTECTED_DIR_NAMES)
        if hit:
            problems.append(
                f"declared path {raw!r} lies under {hit[0]!r}, which the "
                f"nightly improver may never write to")
    return problems


# --------------------------------------------------------------------------
# 1. failure signatures
# --------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_DIGITS = re.compile(r"\d+")
_ABSPATH = re.compile(r"(?:/[\w.\-+@]+){2,}")
_HEXBLOB = re.compile(r"\b[0-9a-f]{12,}\b", re.IGNORECASE)
#: "Exit code 1" on its own says nothing; the line after it usually does.
_UNINFORMATIVE = re.compile(r"^(?:exit(?:ed)? (?:code|with) \d+|error|failed|"
                            r"command failed|non-zero exit)[:.]?$", re.IGNORECASE)


def _shorten_path(m) -> str:
    """``/a/b/c/d.py`` -> ``…/c/d.py``: enough to tell two files apart, not
    enough to carry somebody's home directory into an LLM prompt."""
    parts = [p for p in m.group(0).split("/") if p]
    return "…/" + "/".join(parts[-2:]) if len(parts) > 2 else m.group(0)


def _normalise(text: str, limit: int = 160, keep_paths: bool = False) -> str:
    """A stable signature key: one line, no numbers, no hashes.

    ``keep_paths`` shortens paths instead of erasing them.  Erasing is right
    for an *error* signature (the same ``ModuleNotFoundError`` in two repos is
    one problem) and wrong for a *retry* signature: collapsing every path to
    ``<path>`` turned 149 unrelated re-reads on this machine into a single
    meaningless cluster of "Read: <path>", which is not a failure signature at
    all.  Which file was re-read IS the signature.
    """
    t = _ANSI.sub("", str(text or ""))
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    informative = [ln for ln in lines if not _UNINFORMATIVE.match(ln)]
    # "Exit code 1" clusters 39 unrelated failures into one bucket; the line
    # underneath it is the signature.  Fall back to the bare line only when
    # there is nothing else.
    t = (informative or lines or [t.strip()])[0]
    t = _ABSPATH.sub(_shorten_path if keep_paths else "<path>", t)
    t = _HEXBLOB.sub("<hash>", t)
    t = _DIGITS.sub("N", t)
    return " ".join(t.split())[:limit]


def _result_text(block, tur) -> str:
    out = []
    c = block.get("content") if isinstance(block, dict) else None
    if isinstance(c, str):
        out.append(c)
    elif isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                out.append(b["text"])
    if isinstance(tur, str):
        out.append(tur)
    elif isinstance(tur, dict):
        for key in ("stderr", "error", "stdout", "message"):
            v = tur.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v)
    return "\n".join(out)[:4000]


@dataclass
class FailureCluster:
    kind: str
    key: str
    count: int = 0
    sessions: list[str] = field(default_factory=list)
    examples: list[dict] = field(default_factory=list)
    topic_ids: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        import hashlib
        return "fc-" + hashlib.sha256(
            f"{self.kind}\x00{self.key}".encode("utf-8")).hexdigest()[:8]

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "key": self.key,
                "count": self.count, "sessions": self.sessions[:8],
                "nSessions": len(self.sessions),
                "examples": self.examples[:MAX_EXAMPLES],
                "topics": self.topic_ids, "evidence": self.evidence}


def _tool_errors(sessions) -> dict[tuple[str, str], FailureCluster]:
    out: dict[tuple[str, str], FailureCluster] = {}
    for sess in sessions:
        by_id = {tc.id: tc for tc in sess.tool_calls if tc.id}
        try:
            records = iter_records(sess.path)
        except OSError:                                  # pragma: no cover
            continue
        for line_no, rec in records:
            if rec.get("type") != "user":
                continue
            msg = rec.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            tur = rec.get("toolUseResult")
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                if not block.get("is_error"):
                    continue
                tc = by_id.get(block.get("tool_use_id") or "")
                tool = tc.name if tc is not None else "?"
                text = _result_text(block, tur)
                key = f"{tool}: {_normalise(text)}"
                c = out.setdefault((tool, key), FailureCluster(kind="tool_error", key=key))
                c.count += 1
                if sess.session_id not in c.sessions:
                    c.sessions.append(sess.session_id)
                if len(c.examples) < MAX_EXAMPLES:
                    inp = (tc.input if tc is not None else {}) or {}
                    c.examples.append({
                        "sessionId": sess.session_id,
                        "locator": f"{os.path.basename(sess.path)}:{line_no}",
                        "tool": tool,
                        "input": {k: (str(v)[:200] if isinstance(v, (str, int, float, bool)) else "…")
                                  for k, v in list(inp.items())[:4]},
                        "error": _normalise(text, 300)})
    return out


def _retry_key(tc) -> str:
    """The identity of one tool call, for "did this exact call happen again?".

    Deliberately **not** :func:`_normalise`: that collapses to the first line,
    and a heredoc's first line is ``cd "…" && python - <<'EOF'`` for every one
    of them.  On this machine that made 85 different scripts look like 85
    retries of one call.  The key is the whole subject, whitespace-collapsed,
    with numbers and hashes erased and paths shortened.
    """
    inp = tc.input if isinstance(tc.input, dict) else {}
    # The WHOLE input, not one field: two `Edit` calls on the same file with
    # different `old_string` are two edits, not a retry, and reading only
    # `file_path` turned a 33-edit refactor into a "retry" cluster.
    parts = [f"{k}={inp[k]}" for k in sorted(inp)
             if isinstance(inp.get(k), (str, int, float, bool))]
    subject = " ".join(parts)
    if not subject.strip():
        return ""
    t = _ANSI.sub("", subject)
    t = _ABSPATH.sub(_shorten_path, t)
    t = _HEXBLOB.sub("<hash>", t)
    t = _DIGITS.sub("N", t)
    return " ".join(t.split())[:8000]


def _retries(sessions) -> dict[tuple[str, str], FailureCluster]:
    out: dict[tuple[str, str], FailureCluster] = {}
    for sess in sessions:
        calls = [tc for tc in sess.tool_calls if tc.source == sess.path]
        calls.sort(key=lambda t: t.line_no)
        for i, tc in enumerate(calls):
            if tc.name in RETRY_EXEMPT_TOOLS:
                continue
            inp = tc.input if isinstance(tc.input, dict) else {}
            # Equality is over the WHOLE normalised command, not a prefix: on
            # this machine a 120-character prefix made every
            # `cd "…" && python - <<'EOF'` heredoc look like the same call and
            # produced a 149-strong "retry" cluster that was not a retry at
            # all.  The display key is still truncated.
            full = _retry_key(tc)
            if not full:
                continue
            head = _normalise(inp.get("command") or inp.get("file_path")
                              or inp.get("pattern") or full, 120,
                              keep_paths=True)
            repeats = 0
            for other in calls[i + 1:i + 1 + RETRY_WINDOW]:
                if other.name == tc.name and _retry_key(other) == full:
                    repeats += 1
            if repeats < 1:
                continue
            key = f"{tc.name}: {head}"
            c = out.setdefault((tc.name, key), FailureCluster(kind="retry", key=key))
            c.count += repeats
            if sess.session_id not in c.sessions:
                c.sessions.append(sess.session_id)
            if len(c.examples) < MAX_EXAMPLES:
                c.examples.append({
                    "sessionId": sess.session_id,
                    "locator": f"{os.path.basename(sess.path)}:{tc.line_no}",
                    "tool": tc.name, "repeats": repeats, "command": head})
    return out


def _precedent_covers(rule: dict, topic) -> bool:
    if rule.get("topic") == topic.id or topic.id in (rule.get("topics") or []):
        return True
    text = " ".join(str(rule.get(k) or "") for k in ("message", "quote"))
    return jaccard(content_tokens(text), set(topic.top_tokens(12))) >= 0.30


def cluster_failures(sessions, mine_result=None, precedents=None, *,
                     min_count: int = MIN_CLUSTER_COUNT) -> list[FailureCluster]:
    """Every failure signature worth a bounded edit, biggest first."""
    clusters: list[FailureCluster] = []
    for c in _tool_errors(sessions).values():
        if c.count >= min_count:
            clusters.append(c)
    for c in _retries(sessions).values():
        if c.count >= min_count:
            clusters.append(c)

    active = [r for r in (precedents or []) if r.get("status") == "active"]
    for topic in getattr(mine_result, "topics", []) or []:
        covered = any(_precedent_covers(r, topic) for r in active)
        quotes = [m.quote(160) for m in topic.members][:MAX_EXAMPLES]
        if topic.written_in:
            clusters.append(FailureCluster(
                kind="written_but_violated",
                key=f"{topic.id}: {topic.label if hasattr(topic, 'label') else ''}"
                    f"{quotes[0][:100] if quotes else ''}",
                count=max(topic.count, len(topic.written_in)),
                sessions=list(topic.sessions), topic_ids=[topic.id],
                examples=[{"quote": q} for q in quotes],
                evidence={"writtenIn": topic.written_in[:4],
                          "covered": covered}))
        elif not covered and topic.count >= min_count:
            clusters.append(FailureCluster(
                kind="correction_no_precedent",
                key=f"{topic.id}: {quotes[0][:100] if quotes else ''}",
                count=topic.count, sessions=list(topic.sessions),
                topic_ids=[topic.id],
                examples=[{"quote": q} for q in quotes],
                evidence={"covered": False,
                          "topTokens": topic.top_tokens(8)}))
    clusters.sort(key=lambda c: (-c.count, -len(c.sessions), c.kind, c.key))
    return clusters


# --------------------------------------------------------------------------
# 2. the prompt
# --------------------------------------------------------------------------

_DRAFT_SPEC = """\
Return ONE JSON object, no prose and no markdown fence:

{"surface": "claude_md" | "skill" | "precedent",
 "hypothesis": "<what you believe is going wrong, in one sentence>",
 "expected_effect": "<the observable change if this edit works; name the \
signature above>",
 "declared_paths": ["<the exact file this edit touches — EXACTLY ONE>"],
 "edits": [{"path": "<the same path>", "content": "<the complete new file body>"}],
 "rule": { ... }        // ONLY when surface == "precedent"; the DSL v1 rule
}

Hard requirements — a draft that breaks one of these is thrown away by a
mechanical check you cannot see or influence:

* `declared_paths` and the paths in `edits` must be the SAME single path.
  Touching a file you did not declare is the failure this check exists for.
* a `skill` edit writes `<dir>/SKILL.md` and its body MUST start with a
  YAML frontmatter block containing `name:` and `description:`.
* every file the body references (a relative link, a `scripts/x.py`, a
  `references/y.md`) must already exist next to the declared path.
* NO session-specific facts in anything global: no absolute /Users/... or
  /home/... paths, no host:port, no IP, no dated statement, no session id.
* NO secrets of any kind.
* keep it bounded: one file, and as few lines as will do the job.
"""

_RULE_SPEC = """\
The DSL v1 rule object (surface == "precedent"):

  {"tool": "<Bash|Edit|Write|MultiEdit|NotebookEdit|Agent|Workflow|WebFetch|Skill|*>",
   "match": "all" | "any",
   "matchers": [{"type": "input_regex", "field": "command", "regex": "<python re>"}],
   "action": "deny" | "ask" | "log",
   "scope": "project" | "global",
   "message": "<what the agent is shown, quoting the user's own words and date>"}

Regexes must be plain and anchored on literal text: no nested quantifiers, no
repeat bound above 200, at most 400 characters.
"""


def build_improve_prompt(cluster: FailureCluster, *, claude_home: str,
                         negatives: list[dict] | None = None,
                         precedents=None) -> str:
    L = ["You are the nightly improver of a coding agent's own learned state.",
         "You get ONE failure signature and you propose ONE bounded edit.", ""]
    L.append(f"## The failure signature ({cluster.kind})")
    L.append("")
    L.append(f"  key        : {cluster.key}")
    L.append(f"  occurrences: {cluster.count} across {len(cluster.sessions)} session(s)")
    if cluster.evidence:
        L.append(f"  evidence   : {json.dumps(cluster.evidence, ensure_ascii=False)[:600]}")
    L.append("")
    L.append("## Examples")
    L.append("")
    for ex in cluster.examples[:MAX_EXAMPLES]:
        L.append(f"  - {json.dumps(ex, ensure_ascii=False)[:400]}")
    L.append("")
    L.append("## Where the learned state lives")
    L.append("")
    L.append(f"  claude home : {claude_home}")
    L.append(f"  skills      : {os.path.join(claude_home, 'skills', '<name>', 'SKILL.md')}")
    L.append(f"  project rules: <project>/CLAUDE.md")
    if precedents:
        L.append("")
        L.append("Rules already confirmed (do not restate one of these):")
        for r in list(precedents)[:8]:
            L.append(f"  - {r.get('id')}: {(r.get('message') or '')[:120]}")
    L.append("")
    L.append("## " + _DRAFT_SPEC)
    L.append(_RULE_SPEC)
    if negatives:
        L.append("")
        L.append("## Drafts that were REJECTED before — do not repeat these")
        L.append("")
        for n in negatives[:5]:
            L.append(f"  - rejected because: {n.get('reason', '?')}")
            L.append(f"    {json.dumps(n.get('draft'), ensure_ascii=False)[:300]}")
    L.append("")
    L.append("Answer with the JSON object and nothing else.")
    return "\n".join(L)


# --------------------------------------------------------------------------
# 3. schema + the mechanical checks
# --------------------------------------------------------------------------

def _first_json_object(text: str) -> dict | None:
    if not text:
        return None
    chunks = []
    fence = text.split("```")
    if len(fence) >= 3:
        for i in range(1, len(fence), 2):
            body = fence[i]
            if body.startswith("json"):
                body = body[4:]
            chunks.append(body.strip())
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        chunks.append(text[start:end + 1])
    for chunk in chunks:
        try:
            data = json.loads(chunk)
        except (ValueError, RecursionError):
            continue
        if isinstance(data, dict):
            return data
    return None


def parse_draft(text: str) -> tuple[dict | None, str]:
    """``(draft, error)`` — schema validation only; no judgement about content."""
    raw = _first_json_object(text)
    if raw is None:
        return None, "no JSON object in the model's answer"
    surface = raw.get("surface")
    if surface not in ALLOWED_SURFACES:
        return None, (f"surface {surface!r} is not one of {ALLOWED_SURFACES} "
                      f"(hooks, settings and precedent's own code are not a "
                      f"surface the nightly improver may touch)")
    for key in ("hypothesis", "expected_effect"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            return None, f"missing or empty {key}"
    declared = raw.get("declared_paths")
    if not isinstance(declared, list) or not declared or not all(
            isinstance(p, str) and p.strip() for p in declared):
        return None, "declared_paths must be a non-empty list of strings"
    if len(declared) != 1:
        return None, (f"a nightly edit is bounded to ONE file; "
                      f"declared_paths has {len(declared)}")
    edits = raw.get("edits")
    if not isinstance(edits, list) or not edits:
        return None, "edits must be a non-empty list"
    clean_edits = []
    for e in edits:
        if not isinstance(e, dict):
            return None, "every edit must be an object"
        p, c = e.get("path"), e.get("content")
        if not isinstance(p, str) or not p.strip():
            return None, "every edit needs a path"
        if not isinstance(c, str):
            return None, "every edit needs a string content"
        if len(c) > MAX_CONTENT_CHARS:
            return None, (f"edit content is {len(c)} chars > "
                          f"{MAX_CONTENT_CHARS}; a nightly edit is bounded")
        clean_edits.append({"path": p.strip(), "content": c})
    draft = {
        "surface": surface,
        "hypothesis": raw["hypothesis"].strip()[:1000],
        "expected_effect": raw["expected_effect"].strip()[:1000],
        "declared_paths": [p.strip() for p in declared],
        "edits": clean_edits,
    }
    if surface == "precedent":
        rule = raw.get("rule")
        if not isinstance(rule, dict):
            return None, "surface 'precedent' needs a `rule` object"
        # an LLM does not get to choose its own id, status or birth evidence
        rule = {k: v for k, v in rule.items()
                if k not in ("id", "status", "birth", "confirmedBy", "confirmedAt")}
        try:
            draft["rule"] = validate_rule(rule)
        except RuleError as exc:
            return None, f"rule schema: {exc}"
    return draft, ""


def _expand(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def enforce_declared_paths(draft: dict) -> list[str]:
    """The diff must touch exactly the declared paths — no more, no fewer."""
    declared = {_expand(p) for p in draft.get("declared_paths") or []}
    touched = {_expand(e["path"]) for e in draft.get("edits") or []}
    problems: list[str] = []
    extra = sorted(touched - declared)
    missing = sorted(declared - touched)
    if extra:
        problems.append(
            f"the diff touches {len(extra)} path(s) that were not declared: "
            + ", ".join(extra[:4]))
    if missing:
        problems.append(
            f"{len(missing)} declared path(s) are not in the diff: "
            + ", ".join(missing[:4]))
    for p in draft.get("declared_paths") or []:
        if ".." in p.split(os.sep):
            problems.append(f"declared path {p!r} contains '..'")
        if not os.path.isabs(_expand(p)):               # pragma: no cover
            problems.append(f"declared path {p!r} does not resolve to an "
                            f"absolute path")
    return problems


SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("anthropic api key", r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    ("openai-style api key", r"\bsk-[A-Za-z0-9]{20,}"),
    ("github token", r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    ("aws access key id", r"\bAKIA[0-9A-Z]{12,}"),
    ("slack token", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    ("google api key", r"\bAIza[0-9A-Za-z_\-]{30,}"),
    ("private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("bearer token", r"\bBearer\s+[A-Za-z0-9._\-]{24,}"),
    ("assigned secret", r"(?i)\b(?:api[_-]?key|secret|token|password|passwd)\b"
                       r"\s*[:=]\s*[\"']?[A-Za-z0-9/+_\-]{16,}"),
)

SESSION_FACT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("an absolute home path", r"/(?:Users|home)/[A-Za-z0-9._\-]+/"),
    ("a windows user path", r"[A-Za-z]:\\\\Users\\\\[A-Za-z0-9._\-]+"),
    ("a host:port", r"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0)\s*:\s*\d{2,5}\b"),
    ("an IPv4 address", r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ("a dated statement", r"\b20\d{2}-\d{2}-\d{2}\b"),
    ("a session id", r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                     r"[0-9a-f]{4}-[0-9a-f]{12}\b"),
    ("a process id", r"\bpid\s*[:=]?\s*\d{3,}\b"),
)

_SECRET_RX = [(name, re.compile(rx)) for name, rx in SECRET_PATTERNS]
_FACT_RX = [(name, re.compile(rx)) for name, rx in SESSION_FACT_PATTERNS]

_MD_LINK = re.compile(r"\[[^\]]{0,120}\]\(([^)\s]{1,200})\)")
_REL_REF = re.compile(r"(?<![\w/`])((?:scripts|references|assets|templates|"
                      r"examples|data)/[\w.\-/]+\.[A-Za-z0-9]{1,6})")


def _frontmatter(text: str) -> tuple[dict | None, str]:
    if not text.startswith("---"):
        return None, "the body does not start with a `---` frontmatter block"
    end = text.find("\n---", 3)
    if end < 0:
        return None, "the frontmatter block is never closed with `---`"
    fm: dict = {}
    for line in text[3:end].splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            return None, f"frontmatter line is not `key: value`: {line[:60]!r}"
        k, v = line.split(":", 1)
        fm[k.strip().lower()] = v.strip().strip("'\"")
    return fm, ""


def _is_global(path: str, claude_home: str) -> bool:
    """A user-level artifact is loaded everywhere, so it may hold no local fact."""
    p = _expand(path)
    home = _expand(claude_home)
    return p == home or p.startswith(home + os.sep)


def lint_draft(draft: dict, claude_home: str) -> list[str]:
    """The deterministic gate.  Zero model cost, and it always runs last."""
    problems: list[str] = list(protected_path_problems(
        list(draft.get("declared_paths") or [])
        + [e.get("path") for e in draft.get("edits") or []
           if isinstance(e, dict)],
        claude_home))
    for edit in draft.get("edits") or []:
        path, body = edit["path"], edit["content"]
        base = os.path.basename(path)
        if draft.get("surface") == "skill":
            if base != "SKILL.md":
                problems.append(f"a skill edit must write SKILL.md, not {base!r}")
            fm, err = _frontmatter(body)
            if err:
                problems.append(f"{base}: {err}")
            else:
                for key in ("name", "description"):
                    if not fm.get(key):
                        problems.append(f"{base}: frontmatter has no `{key}`")
        if draft.get("surface") == "claude_md" and base not in (
                "CLAUDE.md", "CLAUDE.local.md"):
            problems.append(f"a claude_md edit must write CLAUDE.md, not {base!r}")

        # referenced files must exist, relative to the declared path
        root = os.path.dirname(_expand(path))
        refs: set[str] = set()
        for m in _MD_LINK.finditer(body):
            t = m.group(1)
            if t.startswith(("http://", "https://", "#", "mailto:")):
                continue
            refs.add(t.split("#", 1)[0])
        for m in _REL_REF.finditer(body):
            refs.add(m.group(1))
        for ref in sorted(refs):
            if os.path.isabs(ref):
                problems.append(f"{base} references an absolute path {ref!r}")
                continue
            if not os.path.exists(os.path.join(root, ref)):
                problems.append(
                    f"{base} references {ref!r}, which does not exist next to "
                    f"the declared path")

        for name, rx in _SECRET_RX:
            m = rx.search(body)
            if m:
                problems.append(f"{base} contains what looks like {name} "
                                f"({m.group(0)[:8]}…)")
        scope = draft.get("scope") or ("global" if _is_global(path, claude_home)
                                       else "project")
        if scope == "global":
            for name, rx in _FACT_RX:
                m = rx.search(body)
                if m:
                    problems.append(
                        f"{base} is global scope but states {name} "
                        f"({m.group(0)[:40]!r}); a fact that is only true in "
                        f"one session must not enter a globally loaded file")
    if draft.get("surface") == "precedent":
        rule = draft.get("rule")
        if not isinstance(rule, dict):
            problems.append("surface 'precedent' has no validated rule")
    return problems


# --------------------------------------------------------------------------
# 4. the run
# --------------------------------------------------------------------------

@dataclass
class ImproveResult:
    clusters: list[FailureCluster] = field(default_factory=list)
    attempted: list[dict] = field(default_factory=list)
    accepted: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    held: list[dict] = field(default_factory=list)
    spend_usd: float = 0.0
    #: What this run *committed* (one per-call cap per call actually made,
    #: plus whatever the examiner was handed), as opposed to what ``claude``
    #: told us it charged.  The budget check compares against ``max()`` of the
    #: two, so a binary that reports $0.00 for every call still cannot buy
    #: more than ``floor(budget / per_call)`` of them.
    reserved_usd: float = 0.0
    untrusted_cost_reports: int = 0
    budget_usd: float = DEFAULT_TOTAL_BUDGET_USD
    stopped: str = ""
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"nClusters": len(self.clusters),
                "clusters": [c.to_dict() for c in self.clusters[:12]],
                "attempted": self.attempted, "accepted": self.accepted,
                "rejected": self.rejected, "held": self.held,
                "spendUsd": round(self.spend_usd, 6),
                "reservedUsd": round(self.reserved_usd, 6),
                "untrustedCostReports": self.untrusted_cost_reports,
                "budgetUsd": self.budget_usd, "stopped": self.stopped,
                "dryRun": self.dry_run, "notes": self.notes}


def improve(state, *, sessions=None, mine_result=None, top: int = DEFAULT_TOP_CLUSTERS,
            budget_usd: float = DEFAULT_TOTAL_BUDGET_USD,
            per_call_usd: float = DEFAULT_BUDGET_USD,
            model: str = DEFAULT_MODEL, dry_run: bool = False,
            binary: str | None = None, timeout_s: int = DEFAULT_TIMEOUT_S,
            examiner=None, min_count: int = MIN_CLUSTER_COUNT,
            now=None) -> ImproveResult:
    """One nightly pass.  ``examiner(draft, proposal)`` routes non-rule drafts."""
    if sessions is None:
        sessions = getattr(mine_result, "sessions", None) or []
    precedents = state.precedents()
    clusters = cluster_failures(sessions, mine_result, precedents,
                                min_count=min_count)
    res = ImproveResult(clusters=clusters, budget_usd=float(budget_usd),
                        dry_run=dry_run)
    if not clusters:
        res.notes.append("no failure signature reached the threshold "
                         f"(min_count={min_count}); nothing to propose")
        return res
    try:
        per_call_usd = check_budget(per_call_usd)
    except LLMUnavailable as exc:
        res.stopped = str(exc)
        return res
    if budget_usd <= 0:
        res.stopped = "--budget-usd must be > 0"
        return res

    ts = now_iso(now)
    negatives = load_negatives(state)
    for cluster in clusters[:top]:
        booked = max(res.spend_usd, res.reserved_usd)
        if booked + per_call_usd > budget_usd + 1e-9:
            res.stopped = (f"stopped before {cluster.id}: the next call could "
                           f"take spend past the ${budget_usd:g} budget "
                           f"(${res.spend_usd:.4f} reported, "
                           f"${res.reserved_usd:.4f} committed at "
                           f"${per_call_usd:g}/call)")
            break
        prompt = build_improve_prompt(cluster, claude_home=state.claude_home,
                                      negatives=negatives, precedents=precedents)
        if dry_run:
            res.attempted.append({"cluster": cluster.id, "kind": cluster.kind,
                                  "promptChars": len(prompt), "ran": False,
                                  "note": "--dry-run: no model call"})
            continue
        res.reserved_usd += per_call_usd
        call = run_claude(prompt, model=model, budget_usd=per_call_usd,
                          timeout_s=timeout_s, binary=binary)
        res.spend_usd += call.cost_usd
        if call.untrusted_cost:
            res.untrusted_cost_reports += 1
        record_spend(state, {
            "ts": ts, "command": "improve", "model": model,
            "budgetUsd": per_call_usd, "costUsd": call.cost_usd,
            "durationMs": call.duration_ms, "cluster": cluster.id,
            "clusterKind": cluster.kind, "ok": call.ok, "error": call.error})
        res.attempted.append({"cluster": cluster.id, "kind": cluster.kind,
                              "ran": True, "ok": call.ok,
                              "costUsd": call.cost_usd, "error": call.error})
        if not call.ok:
            _reject(state, res, cluster, None,
                    [f"claude -p failed: {call.error or call.returncode}"], ts)
            continue

        draft, err = parse_draft(call.text)
        if draft is None:
            _reject(state, res, cluster, {"raw": (call.text or "")[:600]},
                    [f"schema: {err}"], ts)
            continue
        problems = enforce_declared_paths(draft)
        problems += lint_draft(draft, state.claude_home)
        if problems:
            _reject(state, res, cluster, draft, problems, ts)
            continue
        _route(state, res, cluster, draft, ts, mine_result=mine_result,
               examiner=examiner,
               budget_remaining=max(0.0, budget_usd
                                    - max(res.spend_usd, res.reserved_usd)))
        negatives = load_negatives(state)
    if res.untrusted_cost_reports:
        res.notes.append(
            f"{res.untrusted_cost_reports} model call(s) reported a cost that "
            f"is not a finite number >= 0; those were booked as $0 and the "
            f"per-call reservation is what bounded this run")
    return res


def _reject(state, res: ImproveResult, cluster: FailureCluster,
            draft: dict | None, problems: list[str], ts: str) -> None:
    reason = "; ".join(problems)[:600]
    row = {"ts": ts, "cluster": cluster.id, "clusterKind": cluster.kind,
           "reason": reason, "draft": draft, "source": "improve"}
    record_rejected(state, [row])
    res.rejected.append({"cluster": cluster.id, "reason": reason,
                         "surface": (draft or {}).get("surface")})


def _route(state, res: ImproveResult, cluster: FailureCluster, draft: dict,
           ts: str, *, mine_result=None, examiner=None,
           budget_remaining: float = 0.0) -> None:
    """A rule goes to the temporal gate; anything else goes to the examiner."""
    from acceptor import content_hash

    body = json.dumps(draft, ensure_ascii=False, sort_keys=True)
    base = {
        "ts": ts, "kind": "improver-draft", "surface": draft["surface"],
        "clusterId": cluster.id, "clusterKind": cluster.kind,
        "declaredPaths": draft["declared_paths"],
        "hypothesis": draft["hypothesis"],
        "expectedEffect": draft["expected_effect"],
        "content": (draft["edits"][0]["content"] if draft.get("edits") else ""),
        "contentHash": content_hash(body),
        "name": os.path.basename(draft["declared_paths"][0]),
        "applied": False,
        "evidence": {"cluster": cluster.to_dict()},
    }

    if draft["surface"] == "precedent":
        verdict, gate = _temporal_gate_for(draft, cluster, mine_result)
        base["gate"] = gate
        base["decision"] = {"PASS": "HOLD"}.get(verdict, "REJECT")
        if verdict == "PASS":
            base["reason"] = ("the temporal birth gate PASSED; it is a docket "
                              "candidate, not an active rule")
            base["rule"] = draft["rule"]
            pid = append_proposal(state, base)
            res.accepted.append({"cluster": cluster.id, "proposal": pid,
                                 "surface": "precedent", "verdict": verdict,
                                 "decision": "HOLD"})
        else:
            base["reason"] = f"temporal birth gate {verdict}"
            pid = append_proposal(state, base)
            _reject(state, res, cluster, draft,
                    [f"temporal birth gate {verdict}: "
                     f"{gate.get('counts', gate.get('note', 'n/a'))}"], ts)
            res.rejected[-1]["proposal"] = pid
        return

    if examiner is None:
        base["decision"] = "HOLD"
        base["reason"] = ("no evidence: there is no eligible cassette to run "
                          "this edit against, so nothing measured it")
        pid = append_proposal(state, base)
        res.held.append({"cluster": cluster.id, "proposal": pid,
                         "surface": draft["surface"], "reason": base["reason"]})
        return

    if budget_remaining <= 0:
        # The examiner spends real money.  Running it on whatever is left of a
        # budget the drafting calls already consumed is how "--budget-usd 0.60"
        # turns into $3.00: one examine per draft, each handed the *whole*
        # budget again.  An exam we cannot pay for is HOLD, not a free pass.
        base["decision"] = "HOLD"
        base["reason"] = ("no evidence: the run's budget was exhausted before "
                          "this draft could be examined")
        pid = append_proposal(state, base)
        res.held.append({"cluster": cluster.id, "proposal": pid,
                         "surface": draft["surface"], "reason": base["reason"]})
        return

    # The examiner contract is ``examiner(draft, proposal) -> dict | None``;
    # ``budgetRemainingUsd`` is how much of this run's budget it may spend, and
    # ``costUsd`` in the answer is what it actually did spend.  The key is
    # stripped again before the proposal is ever written, so a docket entry
    # never carries a transient budget figure.
    base["budgetRemainingUsd"] = round(float(budget_remaining), 6)
    try:
        outcome = examiner(draft, base)
    except Exception as exc:                             # pragma: no cover - defensive
        base.pop("budgetRemainingUsd", None)
        base["decision"] = "HOLD"
        base["reason"] = f"examiner error: {type(exc).__name__}: {exc}"
        pid = append_proposal(state, base)
        res.held.append({"cluster": cluster.id, "proposal": pid,
                         "surface": draft["surface"], "reason": base["reason"]})
        return
    base.pop("budgetRemainingUsd", None)
    if isinstance(outcome, dict):
        # Whatever the exam cost comes out of the SAME budget as the drafting
        # calls, so the number printed at the end is the number that was spent.
        res.spend_usd += sane_cost(outcome.get("costUsd"))
        res.reserved_usd += sane_cost(outcome.get("reservedUsd"))
    if not outcome or not (isinstance(outcome, dict) and outcome.get("decision")):
        # An examiner that ran but measured nothing still reports what it
        # spent (above); what it must never do is report a verdict it does not
        # have.  No decision means no evidence, and no evidence means HOLD.
        base["decision"] = "HOLD"
        base["reason"] = ("no evidence: the examiner found no eligible cassette "
                          "for this candidate")
        pid = append_proposal(state, base)
        res.held.append({"cluster": cluster.id, "proposal": pid,
                         "surface": draft["surface"], "reason": base["reason"]})
        return
    res.accepted.append({"cluster": cluster.id, "surface": draft["surface"],
                         "decision": outcome.get("decision"),
                         "proposal": outcome.get("proposalId"),
                         "reason": outcome.get("reason"),
                         "costUsd": sane_cost(outcome.get("costUsd"))})


def _temporal_gate_for(draft: dict, cluster: FailureCluster, mine_result):
    """Run the TEMPORAL BIRTH GATE on a drafted rule.  Never raises."""
    from .compile import CompileError, run_temporal_gate
    if mine_result is None or not cluster.topic_ids:
        return "INSUFFICIENT", {
            "gate": "temporal-birth-gate/v1", "verdict": "INSUFFICIENT",
            "note": "this cluster carries no mined correction, so there is no "
                    "t0 and no violating action to replay the rule against"}
    topics = [t for t in mine_result.topics if t.id in cluster.topic_ids]
    if not topics:
        return "INSUFFICIENT", {
            "gate": "temporal-birth-gate/v1", "verdict": "INSUFFICIENT",
            "note": "the cluster's topics are not in this mine result"}
    try:
        gate = run_temporal_gate(draft["rule"], topics, mine_result)
    except CompileError as exc:
        return "FAIL", {"gate": "temporal-birth-gate/v1", "verdict": "FAIL",
                        "note": str(exc)}
    except Exception as exc:                             # pragma: no cover
        return "FAIL", {"gate": "temporal-birth-gate/v1", "verdict": "FAIL",
                        "note": f"{type(exc).__name__}: {exc}"}
    return gate.verdict, gate.to_dict()


# --------------------------------------------------------------------------
# 5. rendering
# --------------------------------------------------------------------------

def render_improve(res: ImproveResult) -> str:
    L = ["# precedent improve — 夜间改进器", ""]
    L.append(f"失败签名聚类 {len(res.clusters)} 个"
             + (f"（试了前 {len(res.attempted)} 个）" if res.attempted else "")
             + f" · 花费 ${res.spend_usd:.4f} / 预算 ${res.budget_usd:g}"
             + ("（--dry-run，零模型调用）" if res.dry_run else ""))
    L.append("")
    if res.clusters:
        L.append("| # | kind | 次数 | 会话 | 签名 |")
        L.append("|---|---|---|---|---|")
        for i, c in enumerate(res.clusters[:10], 1):
            L.append(f"| {i} | {c.kind} | {c.count} | {len(c.sessions)} | "
                     f"`{c.key[:70]}` |")
        L.append("")
    if res.accepted:
        L.append("## 过门的草稿（进入 docket，**不会自动应用**）")
        L.append("")
        for a in res.accepted:
            L.append(f"- `{a.get('proposal')}` {a.get('surface')} → "
                     f"**{a.get('decision')}** — {a.get('reason', '')[:120]}")
        L.append("")
    if res.held:
        L.append("## HOLD（没有证据）")
        L.append("")
        for h in res.held:
            L.append(f"- `{h.get('proposal')}` {h.get('surface')} — {h['reason']}")
        L.append("")
    if res.rejected:
        L.append("## 被机械检查拒绝（回灌 rejected.jsonl，下次作为反例）")
        L.append("")
        for r in res.rejected:
            L.append(f"- {r['cluster']}: {r['reason'][:200]}")
        L.append("")
    if res.stopped:
        L.append(f"**{res.stopped}**")
        L.append("")
    for n in res.notes:
        L.append(f"- ⚠ {n}")
    if res.notes:
        L.append("")
    L.append("机械拒绝永远覆盖模型的批准，反过来不行（PROCTOR）。"
             "`precedent docket` 看证据，应用与否由你自己动手。")
    L.append("")
    return "\n".join(L)
