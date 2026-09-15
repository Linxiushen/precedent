# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Compiler + TEMPORAL BIRTH GATE (v1).

The v0 gate asked "does this rule fire on corrected sessions and stay quiet on
uncorrected ones?"  That question is *anachronistic*: it judges a rule against
behaviour from before the user ever stated the policy, so an agent that was
corrected once and then complied for the rest of the week looks like a rule
that misfires.

**A correction expresses a policy from the moment it was made.**  So the v1
gate is temporal.  For a candidate rule ``R`` compiled from topic ``T`` whose
earliest correction is at ``t0``:

(a) **HIT** — ``R`` must fire on the violating action recorded immediately
    before at least one correction in ``T`` (the ``tool_use`` preceding the
    correcting human turn).  A rule that does not fire on the thing the user
    was objecting to is not a compilation of that correction.

(b) **QUIET-AFTER** — among the tool calls *after* ``t0`` where ``R`` was
    eligible (its tool was used), a fire that is followed, within the same
    session, by another correction from ``T`` in the next
    :data:`FOLLOW_UP_TURNS` human turns is a **true positive**: the user
    objected again, so the rule was right.  Every other fire is a **false
    fire**, tolerated only up to ``false_fires / eligible_after <= epsilon``
    (default 2 %).  The counts are always printed raw — with six eligible calls
    a percentage is theatre.

(c) **pre-t0 behaviour is reported, not counted.**  What the agent did before
    being told is evidence about the world, not about the rule.

Verdict: ``PASS`` / ``FAIL`` / ``INSUFFICIENT`` (fewer than
:data:`MIN_ELIGIBLE_AFTER` eligible actions after ``t0`` — there is simply not
enough post-policy behaviour to judge).  The whole evidence block is stored on
the candidate, so ``precedent confirm`` and the ledger show exactly what was
counted.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from receipts.transcripts import parse_ts

from .mine import MAX_PRECEDING_TOOLS
from .rules import (ASK_BEFORE_PRESETS, TEMPLATES, RuleError, build_rule,
                    default_field, normalise_rule, rule_fires, scope_matches,
                    tool_matches, validate_rule)

__all__ = [
    "CompileError",
    "DEFAULT_EPSILON",
    "FOLLOW_UP_TURNS",
    "MIN_ELIGIBLE_AFTER",
    "TEMPLATES",
    "TemporalGate",
    "auto_compile",
    "build_rule",
    "compile_topics",
    "extract",
    "run_temporal_gate",
    "suggest_check",
    "tool_matches",
]

#: ``CompileError`` is the CLI-facing name for a rejected rule.
CompileError = RuleError

DEFAULT_EPSILON = 0.02
FOLLOW_UP_TURNS = 3
MIN_ELIGIBLE_AFTER = 3


# --------------------------------------------------------------------------
# extraction — deterministic, no model call
# --------------------------------------------------------------------------

_STOP_EDGE = "\\s，,。.；;、）)（(\"'`！!？?：:"
_STOP_EDGE_PATH = "\\s，,。；;、）)（(\"'`！!？?：:"

_USE_X_NOT_Y = [
    re.compile(r"用\s*([^" + _STOP_EDGE + r"]{1,24}?)\s*[，,]?\s*(?:而)?(?:不要用|不用|别用|不要再用)\s*"
               r"([^" + _STOP_EDGE + r"]{1,24})"),
    re.compile(r"(?:不要用|不用|别用|不要再用)\s*([^" + _STOP_EDGE + r"]{1,24})\s*[，,]?\s*"
               r"(?:改用|换成|改成|用)\s*([^" + _STOP_EDGE + r"]{1,24})"),
    re.compile(r"\buse\s+([\w./+\-]{1,32})\s+(?:not|instead\s+of|rather\s+than)\s+([\w./+\-]{1,32})",
               re.IGNORECASE),
    re.compile(r"\b([\w./+\-]{1,32})\s+instead\s+of\s+([\w./+\-]{1,32})", re.IGNORECASE),
    re.compile(r"\b(?:stop|don['’]?t|do not|never|quit)\s+us(?:e|ing)\s+([\w./+\-]{1,32})"
               r"[^\n]{0,60}?\buse\s+([\w./+\-]{1,32})", re.IGNORECASE),
]
#: patterns whose group 1 is Y and group 2 is X (the others are X, Y)
_USE_X_NOT_Y_SWAPPED = {1, 4}

_DONT_USE = [
    re.compile(r"(?:不要|别|不用|禁止|不许|停止)\s*(?:再)?\s*(?:用|使用|调用|执行|跑|开)\s*"
               r"([^" + _STOP_EDGE + r"]{1,32})"),
    re.compile(r"\b(?:don['’]?t|do not|never|stop|quit)\s+(?:use|using|run|running|call|calling|"
               r"invoke|invoking|touch)\s+([\w./+\-]{1,32})", re.IGNORECASE),
]

_PATHY = re.compile(r"[/\\]|\.md$|\.json$|\.py$|^~|CLAUDE|MEMORY|\.claude")

_DONT_TOUCH = [
    re.compile(r"(?:不要|别|不用|禁止|不许)\s*(?:再)?\s*(?:动|碰|改|修改|编辑|写|覆盖|删除|删)\s*"
               r"([^" + _STOP_EDGE_PATH + r"]{1,160})"),
    re.compile(r"\b(?:don['’]?t|do not|never|stop)\s+(?:touch|edit|modify|change|write\s+to|"
               r"overwrite|delete|rm)\s+([^\s\"'`]{1,160})", re.IGNORECASE),
]

#: "改用 Opus 5" / "switch to sonnet" -> every Agent/Workflow call must say so.
_USE_MODEL = re.compile(
    r"(?:改用|换成|改成|切换到|用)\s*(opus|sonnet|haiku)|"
    r"\b(?:switch\s+to|use)\s+(opus|sonnet|haiku)\b", re.IGNORECASE)

_FORBID_FLAG = re.compile(
    r"(?:不要|别|不用|禁止|不许|never|don['’]?t|do not|stop)\s*(?:再)?\s*"
    r"(?:用|使用|加|带|加上|传|pass|use|add)?\s*(--[A-Za-z][A-Za-z0-9\-]{1,30})",
    re.IGNORECASE)

_ASK_BEFORE_HINT = re.compile(
    r"先问|问我|问一下|确认一下|征求|ask\s+me|ask\s+first|confirm\s+with\s+me|"
    r"check\s+with\s+me", re.IGNORECASE)
_ASK_BEFORE_SUBJECT = [
    ("git_push_force", re.compile(r"git\s+push[^\n]{0,40}(?:--force|-f\b)|强推|force\s*push",
                                  re.IGNORECASE)),
    ("rm_rf", re.compile(r"rm\s+-[A-Za-z]{0,4}[rRfF]|删库|rm\s*-rf", re.IGNORECASE)),
    ("curl_pipe_sh", re.compile(r"curl[^\n|]{0,60}\|\s*(?:ba)?sh|wget[^\n|]{0,60}\|\s*(?:ba)?sh",
                                re.IGNORECASE)),
]


def extract(texts: list[str]) -> dict:
    """First template that matches, scanning the topic's quotes in order.

    Deterministic: no ranking, no model.  ``{"template": None, "note": …}`` when
    nothing matched — which, on real Chinese transcripts, is common, and is why
    ``--template`` with explicit arguments and ``--llm`` both exist.
    """
    for text in texts:
        for i, rx in enumerate(_USE_X_NOT_Y):
            m = rx.search(text)
            if m:
                a, b = m.group(1).strip(), m.group(2).strip()
                x, y = (b, a) if i in _USE_X_NOT_Y_SWAPPED else (a, b)
                if x and y and x != y:
                    return {"template": "use_x_not_y", "x": x, "y": y, "from": text}
    for text in texts:
        m = _USE_MODEL.search(text)
        if m:
            model = (m.group(1) or m.group(2) or "").lower()
            if model:
                return {"template": "require_field", "field": "model", "value": model,
                        "tool": "Agent|Workflow", "from": text}
    for text in texts:
        if _ASK_BEFORE_HINT.search(text):
            for preset, rx in _ASK_BEFORE_SUBJECT:
                if rx.search(text):
                    return {"template": "ask_before", "preset": preset, "from": text}
    for text in texts:
        for rx in _DONT_TOUCH:
            m = rx.search(text)
            if m:
                p = m.group(1).strip().strip("。.，,；;")
                if p and _PATHY.search(p):
                    return {"template": "dont_touch_path", "path": p, "from": text}
    for text in texts:
        m = _FORBID_FLAG.search(text)
        if m:
            return {"template": "forbid_flag", "flag": m.group(1), "from": text}
    for text in texts:
        for rx in _DONT_USE:
            m = rx.search(text)
            if m:
                x = m.group(1).strip()
                if x:
                    return {"template": "dont_use", "x": x, "from": text}
    return {"template": None,
            "note": "no deterministic template matched these quotes; pass "
                    "--template with its arguments, or try --llm"}


_EXTRACT_KEYS = ("x", "y", "path", "field", "value", "flag", "preset", "command",
                 "tool")


def suggest_check(topic) -> dict:
    """What ``precedent mine`` prints under a topic as its suggested check."""
    try:
        got = extract([c.quote(200) for c in topic.members])
    except Exception:                                   # pragma: no cover - defensive
        return {"template": None, "note": "extraction failed"}
    if not got.get("template"):
        return got
    kwargs = {k: got.get(k) for k in _EXTRACT_KEYS if got.get(k)}
    try:
        rule = build_rule(topic.id, got["template"], **kwargs)
    except RuleError as exc:                             # pragma: no cover - defensive
        return {"template": got["template"], "note": str(exc)}
    out = {"template": got["template"], "tool": rule["tool"],
           "action": rule["action"], "match": rule["match"],
           "matchers": rule["matchers"],
           "command": f"precedent compile {topic.id}"}
    out.update(kwargs)
    return out


# --------------------------------------------------------------------------
# the temporal birth gate
# --------------------------------------------------------------------------

@dataclass
class TemporalGate:
    """Evidence for one candidate rule.  Every number here is a raw count."""

    rule: dict
    epsilon: float = DEFAULT_EPSILON
    follow_up_turns: int = FOLLOW_UP_TURNS
    min_eligible_after: int = MIN_ELIGIBLE_AFTER
    topic_ids: list[str] = field(default_factory=list)
    t0: str | None = None
    corrected_sessions: list[str] = field(default_factory=list)
    n_corrections: int = 0
    # (a) hit
    n_violating_actions: int = 0
    n_corrections_with_hit: int = 0
    preceding_tools_window: int = 0
    hits: list[dict] = field(default_factory=list)
    # (b) quiet-after
    eligible_after: int = 0
    true_positives: list[dict] = field(default_factory=list)
    false_fires: list[dict] = field(default_factory=list)
    # (c) pre-t0, reported only
    eligible_before: int = 0
    fires_before: int = 0
    unordered: int = 0
    out_of_scope: int = 0
    n_tool_calls_scanned: int = 0
    eligible_after_sessions: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def hit(self) -> bool:
        return bool(self.hits)

    @property
    def n_false_fires(self) -> int:
        return len(self.false_fires)

    @property
    def n_true_positives(self) -> int:
        return len(self.true_positives)

    @property
    def fires_after(self) -> int:
        return self.n_false_fires + self.n_true_positives

    @property
    def false_fire_rate(self) -> float:
        if not self.eligible_after:
            return 0.0
        return self.n_false_fires / self.eligible_after

    @property
    def verdict(self) -> str:
        if not self.hit:
            return "FAIL"
        if self.eligible_after < self.min_eligible_after:
            return "INSUFFICIENT"
        if self.false_fire_rate > self.epsilon:
            return "FAIL"
        return "PASS"

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    @property
    def failures(self) -> list[str]:
        out: list[str] = []
        if not self.hit:
            out.append(
                f"(a) HIT: the rule does not fire on any of the "
                f"{self.n_violating_actions} violating action(s) recorded "
                f"immediately before the {self.n_corrections} correction(s) in "
                f"this topic — it is not a compilation of this correction")
        if self.hit and self.eligible_after < self.min_eligible_after:
            out.append(
                f"(b) INSUFFICIENT: only {self.eligible_after} eligible action(s) "
                f"after t0={self.t0}; the gate needs >= {self.min_eligible_after} "
                f"to say anything about quiet-after")
        if self.false_fire_rate > self.epsilon:
            out.append(
                f"(b) QUIET-AFTER: {self.n_false_fires}/{self.eligible_after} "
                f"= {self.false_fire_rate * 100:.1f}% of post-t0 eligible actions "
                f"are tolerated (unpunished) fires > epsilon "
                f"{self.epsilon * 100:.0f}%")
        return out

    @property
    def n_false_fire_sessions(self) -> int:
        return len({f["sessionId"] for f in self.false_fires})

    def counts(self) -> str:
        return (f"hit {len(self.hits)}/{self.n_violating_actions} actions "
                f"({self.n_corrections_with_hit}/{self.n_corrections} corrections) · "
                f"after-t0 false {self.n_false_fires}/{self.eligible_after} "
                f"(true {self.n_true_positives}, in "
                f"{self.n_false_fire_sessions}/{self.eligible_after_sessions} "
                f"sessions) · "
                f"pre-t0 {self.fires_before}/{self.eligible_before} (not counted)")

    def to_dict(self) -> dict:
        return {
            "gate": "temporal-birth-gate/v1",
            "verdict": self.verdict,
            "counts": self.counts(),
            "t0": self.t0,
            "topics": self.topic_ids,
            "epsilon": self.epsilon,
            "followUpTurns": self.follow_up_turns,
            "minEligibleAfter": self.min_eligible_after,
            "nCorrections": self.n_corrections,
            "correctedSessions": self.corrected_sessions,
            "hit": self.hit,
            "nViolatingActions": self.n_violating_actions,
            "nCorrectionsWithHit": self.n_corrections_with_hit,
            # (a) looks at the last N tool calls before the correcting turn, not
            # only the single last one: a Read immediately before the objection
            # would otherwise sink every rule.  The denominator above is
            # actions, not corrections — both are reported so neither can be
            # misread as the other.
            "precedingToolsWindow": self.preceding_tools_window,
            "nHits": len(self.hits),
            "hitExamples": self.hits[:5],
            "eligibleAfter": self.eligible_after,
            "firesAfter": self.fires_after,
            "truePositives": self.n_true_positives,
            "falseFires": self.n_false_fires,
            "falseFireRate": round(self.false_fire_rate, 4),
            # reported, not a criterion: epsilon is measured per *action*, so a
            # large corpus can hide a rule that would still interrupt you N
            # times across M sessions.  Read the absolute numbers.
            "falseFireSessions": len({f["sessionId"] for f in self.false_fires}),
            "eligibleAfterSessions": self.eligible_after_sessions,
            "falseFireExamples": self.false_fires[:5],
            "truePositiveExamples": self.true_positives[:5],
            "preT0": {"eligible": self.eligible_before, "fires": self.fires_before,
                      "counted": False},
            "unorderedActions": self.unordered,
            "outOfScopeActions": self.out_of_scope,
            "nToolCallsScanned": self.n_tool_calls_scanned,
            "failures": self.failures,
            "notes": self.notes,
        }


def _locator(tc) -> str:
    return f"{os.path.basename(tc.source)}:{tc.line_no}"


def run_temporal_gate(rule: dict, topics, mine_result, *,
                      epsilon: float = DEFAULT_EPSILON,
                      follow_up_turns: int = FOLLOW_UP_TURNS,
                      min_eligible_after: int = MIN_ELIGIBLE_AFTER) -> TemporalGate:
    """Replay ``rule`` against the transcripts, in time order, around ``t0``."""
    rule = normalise_rule(rule)
    topic_list = list(topics)
    corrections = [c for t in topic_list for c in t.members]
    if not corrections:
        raise CompileError("the topic has no corrections to gate against")

    stamps = sorted(c.turn.ts for c in corrections if c.turn.ts)
    t0 = stamps[0] if stamps else None
    t0_dt = parse_ts(t0)
    gate = TemporalGate(
        rule=rule, epsilon=epsilon, follow_up_turns=follow_up_turns,
        min_eligible_after=min_eligible_after,
        topic_ids=[t.id for t in topic_list], t0=t0,
        corrected_sessions=sorted({c.turn.session_id for c in corrections}),
        n_corrections=len(corrections))
    if t0_dt is None:
        gate.notes.append("no correction in this topic carries a timestamp; "
                          "the temporal gate cannot be run")
        return gate

    cwd_by_session = {s.session_id: s.cwd for s in mine_result.sessions}

    # ---- (a) HIT on the violating actions ------------------------------
    gate.preceding_tools_window = MAX_PRECEDING_TOOLS
    for c in corrections:
        cwd = cwd_by_session.get(c.turn.session_id)
        hit_here = False
        for tool in c.turn.preceding_tools:
            gate.n_violating_actions += 1
            if rule_fires(rule, tool.get("tool", ""), tool.get("input") or {}, cwd):
                hit_here = True
                gate.hits.append({
                    "sessionId": c.turn.session_id,
                    "tool": tool.get("tool"),
                    "locator": f"{os.path.basename(c.turn.transcript)}:{tool.get('lineNo')}",
                    "summary": tool.get("summary", "")[:120],
                    "ts": tool.get("ts"),
                    "correctionLocator": c.turn.locator(),
                    "correctionQuote": c.quote(),
                })
        if hit_here:
            gate.n_corrections_with_hit += 1

    # ---- (b)/(c) every recorded action, partitioned at t0 ---------------
    # A confirming turn is identified by *which turn it is* (transcript + line),
    # never by its timestamp: two human turns in one session can carry the same
    # stamp, and matching on the stamp would let an unrelated turn confirm a
    # fire -- laundering a tolerated false fire into a true positive and
    # turning a FAIL into a PASS.  The gate must only ever get stricter by
    # accident, never looser.
    corr_keys_by_session: dict[str, set] = {}
    for c in corrections:
        if parse_ts(c.turn.ts) is not None:
            corr_keys_by_session.setdefault(c.turn.session_id, set()).add(
                (c.turn.transcript, c.turn.line_no))

    for sess in mine_result.sessions:
        cwd = sess.cwd
        eligible_here = 0
        turns = mine_result.turns_by_session.get(sess.session_id) or []
        turn_stamps = [(parse_ts(t.ts), t) for t in turns]
        turn_stamps = [(d, t) for d, t in turn_stamps if d is not None]
        turn_stamps.sort(key=lambda p: p[0])
        corr_here = corr_keys_by_session.get(sess.session_id, set())

        in_scope = scope_matches(rule, cwd)
        for tc in sess.tool_calls:
            if not tool_matches(rule.get("tool", ""), tc.name):
                continue
            gate.n_tool_calls_scanned += 1
            if not in_scope:
                # a project-scoped rule is not *eligible* where it cannot fire;
                # counting those calls as quiet would flatter it
                gate.out_of_scope += 1
                continue
            dt = parse_ts(tc.ts)
            fired = rule_fires(rule, tc.name, tc.input, cwd)
            if dt is None:
                gate.unordered += 1
                continue
            if dt <= t0_dt:
                gate.eligible_before += 1
                if fired:
                    gate.fires_before += 1
                continue
            gate.eligible_after += 1
            eligible_here += 1
            if not fired:
                continue
            following = [t for d, t in turn_stamps if d > dt][:follow_up_turns]
            confirmed = None
            for t in following:
                if (t.transcript, t.line_no) in corr_here:
                    confirmed = t
                    break
            row = {
                "sessionId": sess.session_id,
                "tool": tc.name,
                "locator": _locator(tc),
                "ts": tc.ts,
                "value": (tc.input.get("command") or tc.input.get("file_path")
                          or tc.input.get("prompt") or "")[:120]
                if isinstance(tc.input, dict) else "",
            }
            if confirmed is not None:
                row["confirmedBy"] = confirmed.locator()
                row["confirmedQuote"] = confirmed.quote(80)
                gate.true_positives.append(row)
            else:
                gate.false_fires.append(row)
        if eligible_here:
            gate.eligible_after_sessions += 1
    return gate


# --------------------------------------------------------------------------
# topic -> rule -> gate
# --------------------------------------------------------------------------

def compile_topics(topics, mine_result, *, template: str | None = None,
                   epsilon: float = DEFAULT_EPSILON,
                   follow_up_turns: int = FOLLOW_UP_TURNS,
                   min_eligible_after: int = MIN_ELIGIBLE_AFTER,
                   **kwargs) -> tuple[dict, TemporalGate]:
    """One or more topics -> one DSL v1 rule -> the temporal birth gate.

    ``kwargs`` are the template arguments (``x``, ``y``, ``path``, ``field``,
    ``value``, ``flag``, ``preset``, ``command``, ``tool``, ``action``,
    ``message``, ``scope``, ``cwd_glob``).  Anything left ``None`` is filled in
    from the deterministic extractor.
    """
    topic_list = list(topics)
    if not topic_list:
        raise CompileError("no topics to compile")
    quotes = [c.quote(200) for t in topic_list for c in t.members]
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    if template is None:
        got = extract(quotes)
        if not got.get("template"):
            raise CompileError(got.get("note", "no template matched"))
        template = got["template"]
        for key in _EXTRACT_KEYS:
            if got.get(key) and key not in kwargs:
                kwargs[key] = got[key]

    members = [c for t in topic_list for c in t.members]
    members.sort(key=lambda c: (c.turn.ts or "", c.turn.line_no))
    first = members[0]
    kwargs.setdefault("quote", first.quote())
    kwargs.setdefault("quote_date", (first.turn.ts or "")[:10])
    rule = build_rule([t.id for t in topic_list], template, **kwargs)
    gate = run_temporal_gate(rule, topic_list, mine_result, epsilon=epsilon,
                             follow_up_turns=follow_up_turns,
                             min_eligible_after=min_eligible_after)
    return rule, gate


def auto_compile(mine_result, *, epsilon: float = DEFAULT_EPSILON,
                 follow_up_turns: int = FOLLOW_UP_TURNS,
                 min_eligible_after: int = MIN_ELIGIBLE_AFTER,
                 topics=None) -> list[dict]:
    """Try to compile and gate **every** topic.  Never raises.

    The result is attached to each topic as ``topic.auto`` and summarised by
    ``precedent mine`` as "N topics, K compiled, J PASS".
    """
    out: list[dict] = []
    for topic in (topics if topics is not None else mine_result.topics):
        row: dict = {"topic": topic.id}
        try:
            rule, gate = compile_topics([topic], mine_result, epsilon=epsilon,
                                        follow_up_turns=follow_up_turns,
                                        min_eligible_after=min_eligible_after)
        except CompileError as exc:
            row["compiled"] = False
            row["note"] = str(exc)
        except Exception as exc:                        # pragma: no cover - defensive
            row["compiled"] = False
            row["note"] = f"{type(exc).__name__}: {exc}"
        else:
            row["compiled"] = True
            row["rule"] = rule
            row["gate"] = gate.to_dict()
        topic.auto = row
        out.append(row)
    return out
