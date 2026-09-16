# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""RULE DSL v1 — the JSON a precedent compiles to, and the bounded matcher.

A rule is a small JSON object.  It is the only thing the PreToolUse hook reads,
so it has to be (a) expressive enough for the corrections people actually make,
(b) cheap enough to evaluate in well under 300 ms, and (c) impossible to turn
into a denial-of-service against the agent it is supposed to protect::

    {"schemaVersion": 1,
     "id": "p-1a2b3c4d",
     "hook": "PreToolUse",
     "tool": "Agent|Workflow",              # one of TOOLS, "|"-joined, or "*"
     "match": "any",                        # "all" (default) | "any"
     "matchers": [
       {"type": "input_field_missing", "field": "model"},
       {"type": "input_field_equals", "field": "model", "value": "opus",
        "negate": true}],
     "action": "deny",                      # deny | ask | log
     "scope": "project",                    # project | global
     "cwd_glob": "/Users/me/proj/**",       # project scope only
     "message": "Agent/Workflow 必须带 model=opus — 你 2026-09-15 说：改用 Opus 5",
     "quote": "后续代码修改，以及收据工具改用 Opus 5",
     "quoteDate": "2026-09-15"}

Four matcher types, all on a **named input field** of ``tool_input``:

``input_regex``          the field's value matches ``regex``
``input_regex_absent``   the field's value does **not** match ``regex``, *or*
                         the field is missing — the "every call must say X"
                         shape, where silence is as much a violation as the
                         wrong value
``input_field_missing``  the field is absent / null / empty  (Agent without a
                         ``model``, Bash without a ``description``, …)
``input_field_equals``   the field equals ``value`` (``negate`` flips it; a
                         *missing* field never satisfies an ``equals``, and
                         never satisfies a negated one either — use
                         ``input_field_missing`` for that, explicitly)

``input_regex_absent`` inverts the *answer*, never the *failure mode*: when a
pattern has been quarantined the matcher does not fire, exactly as
``input_regex`` does not fire.  Inverting a "we could not evaluate this" into a
denial would turn one slow regex into a gate that blocks every call.

Templates (:func:`build_rule`) are the supported ways to get one:

======================  =======================================================
``dont_use``            "don't use X"            -> regex on ``command``
``use_x_not_y``         "use X not Y"            -> regex on ``command`` for Y
``dont_touch_path``     "don't touch P"          -> regex on ``file_path``
``require_field``       "Agent calls must set model=opus"
``require_regex``       "Workflow scripts must contain model:'opus'"
``forbid_flag``         "never pass --force to git push"
``ask_before``          "ask me first" -> action=ask (rm -rf, curl|sh, push -f)
======================  =======================================================

Regex safety
------------
A rule can come from an LLM (``precedent compile --llm``), so every pattern is
treated as hostile input:

* **length caps** — the pattern is at most :data:`MAX_REGEX_CHARS`, the subject
  is truncated to :data:`MAX_SUBJECT_CHARS` before matching;
* **a static linter** (:func:`lint_regex`) rejects nested quantifiers
  (``(a+)+``), huge bounded repeats (``a{1,5000}``) and backreference-plus-
  quantifier shapes — the classic catastrophic-backtracking families;
* **a bounded matcher** (:func:`bounded_search`) times every match and
  **quarantines** a pattern that blows its budget: from then on that pattern is
  skipped (treated as *no match* — fail-open) for the rest of the process and
  the event is recorded in :data:`SLOW_EVENTS` / ``hooklog.jsonl``.

Honest limitation: CPython's ``re`` holds the GIL and ignores signals for the
duration of a match, so a *hard* mid-match deadline is not available to a
stdlib-only tool.  Running the match on a worker thread does not help — the
worker keeps the GIL and starves the timer.  The bound is therefore
**reject-then-quarantine**: the linter refuses the known catastrophic families
before a pattern is ever stored, the subject is truncated so even a quadratic
pattern is bounded by ``MAX_SUBJECT_CHARS``, and one overrun disables the
pattern instead of repeating.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import time

__all__ = [
    "ACTIONS",
    "MAX_REGEX_CHARS",
    "MAX_RISKY_QUANTIFIERS",
    "MAX_SUBJECT_CHARS",
    "MATCHER_TYPES",
    "RuleError",
    "SCOPES",
    "TEMPLATES",
    "TOOLS",
    "TOOL_FIELDS",
    "bounded_search",
    "build_rule",
    "search_evaluated",
    "quarantined",
    "reset_quarantine",
    "default_field",
    "field_value",
    "lint_regex",
    "normalise_rule",
    "rule_fires",
    "rule_id",
    "scope_matches",
    "validate_rule",
]

SCHEMA_VERSION = 1

#: Every tool a rule may bind to.  ``"*"`` matches any tool.
TOOLS = ("Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "Agent",
         "Workflow", "WebFetch", "Skill", "*")

ACTIONS = ("deny", "ask", "log")
SCOPES = ("project", "global")
MATCHER_TYPES = ("input_regex", "input_regex_absent", "input_field_missing",
                 "input_field_equals")

TEMPLATES = ("dont_use", "use_x_not_y", "dont_touch_path", "require_field",
             "require_regex", "forbid_flag", "ask_before")

#: The named input fields each tool carries, most characteristic first.  The
#: first entry is the default field a regex matcher binds to.
TOOL_FIELDS: dict[str, tuple[str, ...]] = {
    "Bash": ("command", "description"),
    "Edit": ("file_path", "old_string", "new_string"),
    "Write": ("file_path", "content"),
    "MultiEdit": ("file_path",),
    "NotebookEdit": ("notebook_path", "file_path", "new_source"),
    "Agent": ("prompt", "description", "model", "agent_type", "subagent_type"),
    "Workflow": ("script", "scriptPath", "args", "model"),
    "WebFetch": ("url", "prompt"),
    "Skill": ("skill", "args", "command"),
    "*": ("command", "file_path", "prompt", "url", "skill"),
}


class RuleError(ValueError):
    """The rule is not valid DSL v1."""


# --------------------------------------------------------------------------
# 1. regex safety: caps, a static linter, a bounded matcher
# --------------------------------------------------------------------------

MAX_REGEX_CHARS = 400
MAX_SUBJECT_CHARS = 4096
MAX_REPEAT = 200
#: a bounded repeat above this many characters is treated as "big enough to
#: pump" when the linter looks for adjacent quantifiers
RISKY_REPEAT = 20
#: a pattern with more repeats than this is refused outright: every extra
#: unbounded quantifier is another degree of backtracking, and no correction
#: any user has ever made needs five of them
MAX_RISKY_QUANTIFIERS = 4
DEFAULT_BUDGET_MS = 50.0

#: patterns that blew their budget at least once; they take the threaded path
#: from then on.  (module-level: the hook process is short-lived, the CLI run
#: is one command.)
_SLOW: set[str] = set()
#: ``[{pattern, ms, subject_chars, timed_out}]`` — what `report` shows and what
#: the hook appends to ``hooklog.jsonl``.
SLOW_EVENTS: list[dict] = []

_GROUP_OPEN = re.compile(r"\((?!\?[:=!<])")
_BIG_REPEAT = re.compile(r"\{\s*(\d+)\s*(?:,\s*(\d*)\s*)?\}")


def _strip_classes(pattern: str) -> str:
    """Blank out ``[...]`` bodies and escaped chars so the linter sees structure."""
    out: list[str] = []
    i, n, in_class = 0, len(pattern), False
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n:
            out.append("__")
            i += 2
            continue
        if in_class:
            out.append("_" if c != "]" else "]")
            if c == "]":
                in_class = False
            i += 1
            continue
        if c == "[":
            in_class = True
            out.append("[")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _quantified_groups(pattern: str):
    """Yield ``(body, quantifier)`` for every ``(...)`` that carries a quantifier."""
    stack: list[int] = []
    n = len(pattern)
    for i, c in enumerate(pattern):
        if c == "(":
            stack.append(i)
        elif c == ")" and stack:
            start = stack.pop()
            j = i + 1
            if j < n and pattern[j] in "*+{":
                quant = pattern[j:]
                yield pattern[start + 1:i], quant


#: A small, representative alphabet.  Two quantified atoms "overlap" when some
#: probe character satisfies both -- that is exactly the condition under which
#: ``X*Y*`` can split the same input in many ways and backtrack.
_PROBE_ALPHABET = "aZ0 \t\n-_/.=|\\'\";:*+?()[]{}$#&%@~^!,\u4e2d"
_ALL_PROBES = frozenset(_PROBE_ALPHABET)
_REPEAT_BODY = re.compile(r"\{\s*(\d*)\s*(,?)\s*(\d*)\s*\}")
#: zero-width escapes are not atoms: they cannot be quantified or pumped
_ZERO_WIDTH_ESCAPES = frozenset("bBAZz")


def _atom_first_set(atom: str) -> frozenset:
    """Which probe characters this single-character atom can match.

    An atom we cannot compile on its own (a backreference, a group) is treated
    as matching everything, because over-rejecting a weird pattern is cheap and
    under-rejecting one costs a wedged agent.
    """
    if atom is None:
        return _ALL_PROBES
    try:
        rx = re.compile(atom)
    except re.error:
        return _ALL_PROBES
    try:
        return frozenset(ch for ch in _PROBE_ALPHABET if rx.fullmatch(ch))
    except re.error:                                     # pragma: no cover
        return _ALL_PROBES


def _parse_quantifier(pattern: str, i: int) -> tuple[str, int]:
    """``("none" | "safe" | "risky", index after the quantifier)``.

    "risky" means unbounded (``*``, ``+``, ``{n,}``) or a bounded repeat with a
    ceiling above :data:`RISKY_REPEAT` -- i.e. big enough to be pumped.
    """
    if i >= len(pattern):
        return "none", i
    c = pattern[i]
    if c in "*+":
        j = i + 1
        if j < len(pattern) and pattern[j] in "?+":
            j += 1
        return "risky", j
    if c == "?":
        j = i + 1
        if j < len(pattern) and pattern[j] in "?+":
            j += 1
        return "safe", j
    if c == "{":
        m = _REPEAT_BODY.match(pattern, i)
        if not m or (not m.group(1) and not m.group(3)):
            return "none", i                 # a literal "{" -- not a quantifier
        j = m.end()
        if j < len(pattern) and pattern[j] in "?+":
            j += 1
        if m.group(2) and not m.group(3):
            return "risky", j                # {n,} is unbounded
        top = int(m.group(3) or m.group(1) or 0)
        return ("risky" if top > RISKY_REPEAT else "safe"), j
    return "none", i


def _adjacent_quantifier_problems(pattern: str) -> list[str]:
    r"""Reject ``a*a*a*b`` / ``.*.*.*=`` / ``\s*\s*=`` and their relatives.

    The nested-quantifier check catches ``(a+)+``; this one catches the other,
    more common half of the catastrophic-backtracking families: two or more
    *adjacent* quantified atoms whose character sets overlap, so that the same
    run of input can be divided between them in combinatorially many ways.
    ``a*b*`` is fine (disjoint), ``a*a*`` is not.  A run is broken by any
    unquantified atom, by ``|`` and by a group boundary.
    """
    problems: list[str] = []
    n = len(pattern)
    i = 0
    stack: list[list[tuple[str, frozenset]]] = []
    run: list[tuple[str, frozenset]] = []
    n_risky = 0

    def overlap(atom: str, first: frozenset) -> None:
        for prev, pfirst in run:
            if first & pfirst:
                problems.append(
                    f"adjacent quantified atoms {prev}/{atom} match the same "
                    f"characters -- the same input can be split between them in "
                    f"combinatorially many ways (catastrophic backtracking). "
                    f"Put a literal, or a bounded repeat, between them, or drop "
                    f"the redundant one: a search does not need a trailing .*")
                return

    while i < n:
        c = pattern[i]
        atom: str | None = None
        if c == "\\":
            esc = pattern[i:i + 2]
            i += 2
            atom = None if esc[1:] in _ZERO_WIDTH_ESCAPES else esc
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] == "^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" else 1
            atom = pattern[i:j + 1]
            i = j + 1
        elif c == "(":
            stack.append(run)
            run = []
            i += 1
            continue
        elif c == ")":
            i += 1
            kind, i = _parse_quantifier(pattern, i)
            run = stack.pop() if stack else []
            if kind == "risky":
                n_risky += 1
                overlap("(group)", _ALL_PROBES)
                run = run + [("(group)", _ALL_PROBES)]
            else:
                run = []
            continue
        elif c == "|":
            run = []
            i += 1
            continue
        elif c in "^$":
            i += 1
            continue
        else:
            atom = c
            i += 1
        if atom is None:
            continue
        kind, i = _parse_quantifier(pattern, i)
        if kind == "risky":
            n_risky += 1
            first = _atom_first_set(atom)
            overlap(atom, first)
            run = run + [(atom, first)]
        else:
            run = []
        if problems:
            break
    if not problems and n_risky > MAX_RISKY_QUANTIFIERS:
        problems.append(
            f"{n_risky} repeats in one pattern (cap {MAX_RISKY_QUANTIFIERS}); "
            f"each one is another degree of backtracking")
    return problems


def lint_regex(pattern: str) -> list[str]:
    r"""Reasons ``pattern`` is unsafe.  Empty list = safe enough to store.

    This is a *structural* filter, not a decision procedure: it rejects the
    catastrophic-backtracking families that actually show up -- nested
    quantifiers (``(a+)+``), quantified alternation of overlapping branches
    (``(a|ab)*``), **adjacent quantifiers over overlapping character sets**
    (``a*a*a*b``, ``.*.*.*=``, ``\s*\s*=``), giant bounded repeats, and more
    than :data:`MAX_RISKY_QUANTIFIERS` repeats in one pattern -- and it is
    paired with a wall-clock bound at match time.

    The adjacency family is the one that matters most in this tool: it is the
    easiest shape for a drafting model to emit by accident, and because CPython
    holds the GIL for the whole of ``re.search`` the match-time budget can only
    notice an overrun *after* it returns.  On one of these it never returns, so
    the linter is the only real defence and it runs before the pattern is
    stored.
    """
    problems: list[str] = []
    if not isinstance(pattern, str) or not pattern:
        return ["regex is empty"]
    if len(pattern) > MAX_REGEX_CHARS:
        problems.append(f"regex is {len(pattern)} chars, cap is {MAX_REGEX_CHARS}")
    try:
        re.compile(pattern)
    except re.error as exc:
        return problems + [f"regex does not compile: {exc}"]

    skeleton = _strip_classes(pattern)
    for m in _BIG_REPEAT.finditer(skeleton):
        lo = int(m.group(1) or 0)
        hi = int(m.group(2)) if (m.group(2) or "").isdigit() else lo
        if max(lo, hi) > MAX_REPEAT:
            problems.append(f"bounded repeat {m.group(0)} exceeds {MAX_REPEAT}")
    for body, quant in _quantified_groups(skeleton):
        inner = _strip_classes(body)
        if re.search(r"[*+]|\{\s*\d+\s*,", inner):
            problems.append(
                f"nested quantifier: a group containing a repeat is itself "
                f"repeated -- ({body[:40]}){quant[:3]}")
            break
        if "|" in inner and quant[:1] in "*+":
            problems.append(
                f"quantified alternation ({body[:40]}){quant[:1]} can backtrack "
                "exponentially on a near miss")
            break
    if re.search(r"\\[1-9]", pattern) and re.search(r"[*+]", skeleton):
        problems.append("backreference combined with an unbounded repeat")
    problems.extend(_adjacent_quantifier_problems(pattern))
    return problems


def search_evaluated(rx: re.Pattern, subject: str,
                     budget_ms: float = DEFAULT_BUDGET_MS) -> tuple:
    """``(match, evaluated)`` — the search, and whether it actually happened.

    ``evaluated`` is ``False`` when the pattern was skipped (quarantined, or the
    subject is not usable text).  It exists because ``None`` is ambiguous the
    moment a matcher *inverts* the answer: for :data:`input_regex` "no match"
    and "not evaluated" both mean *do not fire*, but for
    ``input_regex_absent`` "no match" means *fire* — so without this flag one
    quarantined pattern would flip from a gate that misses into a gate that
    denies everything.  Callers must fail open on ``evaluated is False``.

    **Truncation is the third way not to know.**  The subject is capped at
    :data:`MAX_SUBJECT_CHARS`, so "no match" in a capped subject means "no match
    in the first 4 KB", which is not the same claim.  For ``input_regex`` that
    only loses a hit (fail open, as designed); for ``input_regex_absent`` it
    *invents* a violation and denies a compliant call.  Measured, not imagined:
    a real ``Workflow`` call carrying ``model: 'opus'`` at offset 5,778 of a
    19,739-character script was flagged by exactly this path.  A match found
    inside the cap is still definitive — truncation only makes the *negative*
    unsafe — so only that case reports ``evaluated=False``.
    """
    if not isinstance(subject, str) or not subject:
        return None, False
    full_len = len(subject)
    subject = subject[:MAX_SUBJECT_CHARS]
    truncated = full_len > len(subject)
    key = rx.pattern
    if key in _SLOW:
        SLOW_EVENTS.append({"pattern": key[:120], "ms": None,
                            "subjectChars": len(subject), "quarantined": True})
        return None, False
    t0 = time.monotonic()
    out = rx.search(subject)
    ms = (time.monotonic() - t0) * 1000
    if ms > budget_ms:
        _SLOW.add(key)
        SLOW_EVENTS.append({"pattern": key[:120], "ms": round(ms, 3),
                            "subjectChars": len(subject), "quarantined": False})
    if out is None and truncated:
        SLOW_EVENTS.append({"pattern": key[:120], "ms": None,
                            "subjectChars": len(subject), "truncated": full_len,
                            "quarantined": False})
        return None, False
    return out, True


def bounded_search(rx: re.Pattern, subject: str, budget_ms: float = DEFAULT_BUDGET_MS):
    """``rx.search(subject)`` with a length cap and a time budget.

    Returns the match, or ``None`` — including when the pattern has been
    quarantined, because a gate that hangs the agent is worse than a gate that
    misses (fail-open).  Every overrun is appended to :data:`SLOW_EVENTS` and
    disables that pattern for the rest of the process.
    """
    return search_evaluated(rx, subject, budget_ms)[0]


def quarantined() -> list[str]:
    """Patterns disabled after blowing their time budget."""
    return sorted(_SLOW)


def reset_quarantine() -> None:
    """Test hook: forget the quarantine list."""
    _SLOW.clear()
    SLOW_EVENTS.clear()


# --------------------------------------------------------------------------
# 2. fields
# --------------------------------------------------------------------------

def default_field(tool: str) -> str:
    for t in str(tool or "").split("|"):
        fields = TOOL_FIELDS.get(t.strip())
        if fields:
            return fields[0]
    return "command"


def field_value(tool_input, field: str) -> str | None:
    """The field's value as a string, or ``None`` when it is absent/empty.

    Non-string values are rendered as compact JSON so a regex can still be
    written against them (``args`` on ``Workflow``, for instance).
    """
    if not isinstance(tool_input, dict):
        return None
    if field not in tool_input:
        return None
    v = tool_input[field]
    if v is None:
        return None
    if isinstance(v, str):
        return v or None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    try:
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):                      # pragma: no cover
        return str(v)


def tool_matches(rule_tool: str, tool_name: str) -> bool:
    """``"Edit|Write"`` matches both; ``"*"`` matches anything named."""
    tools = {t.strip() for t in str(rule_tool or "").split("|") if t.strip()}
    if not tools or not tool_name:
        return False
    return "*" in tools or tool_name in tools


def scope_matches(rule: dict, cwd: str | None) -> bool:
    """``global`` always matches; ``project`` matches its ``cwd_glob``."""
    if (rule.get("scope") or "project") == "global":
        return True
    glob = rule.get("cwd_glob")
    if not glob:
        return True                       # a project rule with no glob is not scoped
    if not cwd:
        return False
    cwd = os.path.normpath(cwd)
    pats = glob if isinstance(glob, list) else [glob]
    for pat in pats:
        p = os.path.normpath(os.path.expanduser(str(pat)))
        if fnmatch.fnmatch(cwd, p) or fnmatch.fnmatch(cwd, p.rstrip("/*") + "/*") \
                or cwd == p.rstrip("/*"):
            return True
    return False


# --------------------------------------------------------------------------
# 3. validation
# --------------------------------------------------------------------------

def _check_matcher(m, i: int) -> dict:
    if not isinstance(m, dict):
        raise RuleError(f"matchers[{i}] is not an object")
    mtype = m.get("type")
    if mtype not in MATCHER_TYPES:
        raise RuleError(f"matchers[{i}].type must be one of {MATCHER_TYPES}, "
                        f"got {mtype!r}")
    field = m.get("field")
    if not isinstance(field, str) or not field.strip():
        raise RuleError(f"matchers[{i}].field must be a non-empty field name")
    if len(field) > 64 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", field):
        raise RuleError(f"matchers[{i}].field {field!r} is not an input field name")
    out = {"type": mtype, "field": field}
    if mtype in ("input_regex", "input_regex_absent"):
        rx = m.get("regex")
        if not isinstance(rx, str) or not rx:
            raise RuleError(f"matchers[{i}].regex must be a non-empty string")
        problems = lint_regex(rx)
        if problems:
            raise RuleError(f"matchers[{i}].regex rejected: {'; '.join(problems)}")
        out["regex"] = rx
    elif mtype == "input_field_equals":
        if "value" not in m:
            raise RuleError(f"matchers[{i}] needs a 'value'")
        v = m["value"]
        if not isinstance(v, (str, int, float, bool)):
            raise RuleError(f"matchers[{i}].value must be a scalar")
        out["value"] = v
        out["negate"] = bool(m.get("negate", False))
        out["case_sensitive"] = bool(m.get("case_sensitive", False))
    return out


def normalise_rule(rule: dict) -> dict:
    """Accept a v0 rule (top-level ``input_regex``) as v1 with one matcher."""
    if not isinstance(rule, dict):
        raise RuleError("rule is not an object")
    out = dict(rule)
    if not out.get("matchers") and out.get("input_regex"):
        out["matchers"] = [{"type": "input_regex",
                            "field": out.get("field") or default_field(out.get("tool", "")),
                            "regex": out["input_regex"]}]
    return out


def validate_rule(rule: dict, *, require_id: bool = False) -> dict:
    """Return a normalised copy, or raise :class:`RuleError`.

    Everything not in the schema is dropped: an LLM draft does not get to smuggle
    ``"status": "active"`` or a pre-cooked ``"birth"`` block past the gate.
    """
    r = normalise_rule(rule)
    tool = r.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise RuleError("rule needs a 'tool'")
    parts = [t.strip() for t in tool.split("|") if t.strip()]
    bad = [t for t in parts if t not in TOOLS]
    if bad or not parts:
        raise RuleError(f"tool {tool!r}: unknown tool(s) {bad or ['(empty)']}; "
                        f"choose from {TOOLS}")
    action = r.get("action", "deny")
    if action not in ACTIONS:
        raise RuleError(f"action must be one of {ACTIONS}, got {action!r}")
    scope = r.get("scope", "project")
    if scope not in SCOPES:
        raise RuleError(f"scope must be one of {SCOPES}, got {scope!r}")
    hook = r.get("hook", "PreToolUse")
    if hook != "PreToolUse":
        raise RuleError(f"hook must be 'PreToolUse' in v1, got {hook!r}")
    matchers = r.get("matchers")
    if not isinstance(matchers, list) or not matchers:
        raise RuleError("rule needs a non-empty 'matchers' array")
    if len(matchers) > 8:
        raise RuleError(f"at most 8 matchers, got {len(matchers)}")
    checked = [_check_matcher(m, i) for i, m in enumerate(matchers)]
    mode = r.get("match", "all")
    if mode not in ("all", "any"):
        raise RuleError(f"match must be 'all' or 'any', got {mode!r}")
    message = r.get("message")
    if not isinstance(message, str) or not message.strip():
        raise RuleError("rule needs a human-readable 'message' (it is what the "
                        "agent is shown when the rule fires)")
    if len(message) > 1000:
        raise RuleError("message is too long (cap 1000 chars)")
    cwd_glob = r.get("cwd_glob")
    if cwd_glob is not None and not isinstance(cwd_glob, (str, list)):
        raise RuleError("cwd_glob must be a string or a list of strings")

    clean = {
        "schemaVersion": SCHEMA_VERSION,
        "hook": "PreToolUse",
        "tool": "|".join(parts),
        "match": mode,
        "matchers": checked,
        "action": action,
        "scope": scope,
        "message": message.strip(),
    }
    if cwd_glob:
        clean["cwd_glob"] = cwd_glob
    for key in ("quote", "quoteDate", "template", "topic", "topics", "x", "y",
                "path", "field", "value", "flag", "regex", "origin", "rationale"):
        if r.get(key) not in (None, ""):
            clean[key] = r[key]
    rid = r.get("id")
    if require_id and not rid:
        raise RuleError("rule needs an 'id'")
    clean["id"] = rid if isinstance(rid, str) and rid.startswith("p-") else rule_id(clean)
    return clean


def rule_id(rule: dict) -> str:
    """The id is a hash of everything that changes what the rule *does*.

    ``cwd_glob`` is in there: the same matchers scoped to a different directory
    are a different rule, and sharing an id would let one overwrite the other's
    gate evidence in ``candidates.json``.
    """
    core = {k: rule.get(k) for k in ("tool", "match", "matchers", "action",
                                     "scope", "cwd_glob")}
    return "p-" + hashlib.sha256(
        json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:8]


# --------------------------------------------------------------------------
# 4. evaluation
# --------------------------------------------------------------------------

def _matcher_fires(m: dict, tool_input, budget_ms: float) -> bool:
    field = m.get("field", "")
    value = field_value(tool_input, field)
    mtype = m.get("type")
    if mtype == "input_field_missing":
        return value is None
    if mtype == "input_field_equals":
        want = m.get("value")
        if value is None:
            return False                # missing is 'missing', never '!= x'
        if isinstance(want, bool):
            got = value.lower() in ("true", "1", "yes")
            eq = got is want
        elif m.get("case_sensitive"):
            eq = value == str(want)
        else:
            eq = value.lower() == str(want).lower()
        return (not eq) if m.get("negate") else eq
    if mtype in ("input_regex", "input_regex_absent"):
        absent = mtype == "input_regex_absent"
        if value is None:
            # a field that is not there cannot match: that is a violation of
            # "must match", and a non-event for "must not contain"
            return absent
        try:
            rx = re.compile(m["regex"])
        except (KeyError, re.error):
            return False
        hit, evaluated = search_evaluated(rx, value, budget_ms)
        if not evaluated:
            return False                # unknown -> fail open, both directions
        return (hit is None) if absent else (hit is not None)
    return False


def rule_fires(rule: dict, tool_name: str, tool_input, cwd: str | None = None,
               budget_ms: float = DEFAULT_BUDGET_MS) -> bool:
    """Does ``rule`` match this tool call?  Never raises."""
    try:
        if not tool_matches(rule.get("tool", ""), tool_name):
            return False
        if not scope_matches(rule, cwd):
            return False
        matchers = rule.get("matchers")
        if not matchers and rule.get("input_regex"):
            rule = normalise_rule(rule)
            matchers = rule.get("matchers")
        if not isinstance(matchers, list) or not matchers:
            return False
        usable = [m for m in matchers if isinstance(m, dict)]
        if not usable:
            # ``all([])`` is True, which would make a malformed rule fire on
            # *everything*.  The hook script refuses this shape; so does this.
            return False
        results = [_matcher_fires(m, tool_input, budget_ms) for m in usable]
        return any(results) if rule.get("match") == "any" else all(results)
    except Exception:                                    # pragma: no cover - defensive
        return False


# --------------------------------------------------------------------------
# 5. templates
# --------------------------------------------------------------------------

def _boundary(token: str) -> str:
    """Escape ``token``, adding ASCII word boundaries only where they mean something."""
    esc = re.escape(token)
    left = r"(?<![A-Za-z0-9_])" if token[:1].isalnum() and token[:1].isascii() else ""
    right = r"(?![A-Za-z0-9_])" if token[-1:].isalnum() and token[-1:].isascii() else ""
    return left + esc + right


#: ask_before ships with the three shapes everyone means by "ask me first".
ASK_BEFORE_PRESETS: dict[str, tuple[str, str]] = {
    "git_push_force": (
        r"(?i)git\s+push\b[^\n]{0,200}?(?:--force(?!-with-lease)|(?<![A-Za-z0-9_])-f(?![A-Za-z0-9_]))",
        "git push --force 先问我一句"),
    "rm_rf": (
        r"(?i)(?<![A-Za-z0-9_.-])rm\b[^\n]{0,60}?\s-[A-Za-z]{0,6}[rR]",
        "rm -rf 先问我一句"),
    "curl_pipe_sh": (
        r"(?i)(?:curl|wget)\b[^\n|]{0,200}\|[^\n]{0,40}?(?:ba)?sh\b",
        "curl | sh 先问我一句"),
}

_DANGEROUS_PATH_PREFIXES = ("/", "~")


def build_rule(topic_id: str | list[str] | None, template: str, *,
               x: str | None = None, y: str | None = None, path: str | None = None,
               field: str | None = None, value: str | None = None,
               flag: str | None = None, preset: str | None = None,
               command: str | None = None, regex: str | None = None,
               tool: str | None = None, action: str | None = None,
               message: str | None = None, scope: str = "project",
               cwd_glob: str | None = None,
               quote: str | None = None, quote_date: str | None = None) -> dict:
    """Build one DSL v1 rule from a template.  Raises :class:`RuleError`."""
    if template not in TEMPLATES:
        raise RuleError(f"unknown template {template!r}; choose one of {TEMPLATES}")
    matchers: list[dict] = []
    mode = "all"

    if template == "dont_use":
        if not x:
            raise RuleError("template 'dont_use' needs --x (the thing not to use)")
        rtool = tool or "Bash"
        matchers = [{"type": "input_regex", "field": field or default_field(rtool),
                     "regex": "(?i)" + _boundary(x)}]
        msg = message or f"不要使用 {x}"
        act = action or "deny"
    elif template == "use_x_not_y":
        if not (x and y):
            raise RuleError("template 'use_x_not_y' needs --x (use this) and "
                            "--y (not this)")
        rtool = tool or "Bash"
        matchers = [{"type": "input_regex", "field": field or default_field(rtool),
                     "regex": "(?i)" + _boundary(y)}]
        msg = message or f"用 {x}，不要用 {y}"
        act = action or "deny"
    elif template == "dont_touch_path":
        if not path:
            raise RuleError("template 'dont_touch_path' needs --path")
        rtool = tool or "Edit|Write|MultiEdit|NotebookEdit"
        alts = [path]
        if path.startswith("~"):
            alts.append(os.path.expanduser(path))
        rx = "(?i)" + "|".join(re.escape(a) for a in dict.fromkeys(alts))
        matchers = [{"type": "input_regex", "field": field or "file_path",
                     "regex": rx}]
        msg = message or f"不要修改 {path}"
        act = action or "deny"
    elif template == "require_field":
        if not field:
            raise RuleError("template 'require_field' needs --field (e.g. model)")
        rtool = tool or "Agent|Workflow"
        matchers = [{"type": "input_field_missing", "field": field}]
        if value is not None and value != "":
            matchers.append({"type": "input_field_equals", "field": field,
                             "value": value, "negate": True})
        mode = "any"
        want = f"{field}={value}" if value else field
        msg = message or f"{rtool} 调用必须带 {want}"
        act = action or "deny"
    elif template == "require_regex":
        if not field:
            raise RuleError("template 'require_regex' needs --field (the input "
                            "field that must match, e.g. script)")
        if not regex:
            raise RuleError("template 'require_regex' needs --regex (the pattern "
                            "the field must match)")
        problems = lint_regex(regex)
        if problems:
            raise RuleError(f"--regex rejected: {'; '.join(problems)}")
        rtool = tool or "Agent|Workflow"
        # One matcher, not two: `input_regex_absent` already treats a missing
        # field as a violation, so pairing it with `input_field_missing` under
        # match=any would only add a second way to say the same thing -- and a
        # second way to get the mode wrong.
        matchers = [{"type": "input_regex_absent", "field": field, "regex": regex}]
        msg = message or f"{field} must match {regex}"
        act = action or "deny"
    elif template == "forbid_flag":
        if not flag:
            raise RuleError("template 'forbid_flag' needs --flag (e.g. --force)")
        rtool = tool or "Bash"
        fld = field or default_field(rtool)
        flag_rx = "(?i)(?<![A-Za-z0-9_-])" + re.escape(flag) + r"(?![A-Za-z0-9_-])"
        matchers = [{"type": "input_regex", "field": fld, "regex": flag_rx}]
        if command:
            matchers.insert(0, {"type": "input_regex", "field": fld,
                                "regex": "(?i)" + _boundary(command)})
        msg = message or (f"不要给 {command} 加 {flag}" if command
                          else f"不要使用 {flag}")
        act = action or "deny"
    else:  # ask_before
        rtool = tool or "Bash"
        fld = field or default_field(rtool)
        if preset:
            if preset not in ASK_BEFORE_PRESETS:
                raise RuleError(f"unknown ask_before preset {preset!r}; "
                                f"choose from {tuple(ASK_BEFORE_PRESETS)}")
            rx, default_msg = ASK_BEFORE_PRESETS[preset]
        elif x:
            rx, default_msg = "(?i)" + _boundary(x), f"{x} 先问我一句"
        else:
            raise RuleError("template 'ask_before' needs --preset "
                            f"({'/'.join(ASK_BEFORE_PRESETS)}) or --x")
        matchers = [{"type": "input_regex", "field": fld, "regex": rx}]
        msg = message or default_msg
        act = action or "ask"

    if quote:
        msg = f"{msg} — 你在 {quote_date or '之前的会话'} 说：{quote}"
    draft = {
        "hook": "PreToolUse", "tool": rtool, "match": mode, "matchers": matchers,
        "action": act, "scope": scope, "message": msg, "template": template,
        "quote": quote, "quoteDate": quote_date,
    }
    if cwd_glob:
        draft["cwd_glob"] = cwd_glob
    for k, v in (("x", x), ("y", y), ("path", path), ("field", field),
                 ("value", value), ("flag", flag), ("regex", regex)):
        if v:
            draft[k] = v
    rule = validate_rule(draft)
    if topic_id:
        rule["topic"] = topic_id if isinstance(topic_id, str) else topic_id[0]
        if isinstance(topic_id, list):
            rule["topics"] = list(topic_id)
    return rule
