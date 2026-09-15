# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""LLM-ASSISTED EXTRACTION — a draftsman, never a judge.

``precedent compile <topic> --llm`` shells out to ``claude -p`` with the
correction quotes and the violating actions and asks for 1-3 DSL v1 rules.
Everything about that call is constrained, because the model is the least
trustworthy component in the loop:

* ``--max-budget-usd`` (default :data:`DEFAULT_BUDGET_USD` = 0.30 for drafts,
  0.50 elsewhere), ``--no-session-persistence``, ``--output-format json``,
  ``--model sonnet``;
* ``--safe-mode --tools "" --disable-slash-commands``: a draftsman gets no
  tools and no view of the learned state it is drafting against (that is both
  the L4 proposer/evaluator separation and, measured on this machine, a 37x
  cost reduction -- $0.33 -> $0.009 per call);
* **never** ``--dangerously-skip-permissions`` (asserted by the test suite);
* the spend is recorded in ``<state>/spend.jsonl`` whether the call helped or
  not.

And the output is treated as hostile data:

1. **schema validation** (:func:`precedent.rules.validate_rule`) — unknown
   fields are dropped, every regex is linted for catastrophic backtracking, and
   a draft cannot smuggle ``"status": "active"`` or a pre-cooked ``"birth"``
   block past the gate because those keys are not in the schema;
2. **the temporal birth gate** — a draft that does not PASS never becomes a
   candidate, no matter what the model said about it.  Mechanical rejection
   overrides LLM approval, never the reverse (PROCTOR).

Rejected drafts are appended to ``<state>/rejected.jsonl`` with the reason, and
replayed into the next prompt as **negative examples**, which is the cheapest
version of step ⑤ (re-evolve) in the loop.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from .rules import RuleError, validate_rule

__all__ = [
    "DEFAULT_BUDGET_USD",
    "DEFAULT_MODEL",
    "MAX_BUDGET_USD",
    "check_budget",
    "json_safe",
    "sane_cost",
    "LLMCall",
    "LLMUnavailable",
    "build_prompt",
    "claude_argv",
    "extract_json_rules",
    "llm_draft_rules",
    "load_negatives",
    "record_rejected",
    "record_spend",
    "run_claude",
]

DEFAULT_MODEL = "sonnet"
DEFAULT_BUDGET_USD = 0.30
#: The hard ceiling on ``--max-budget-usd`` for one call.  The budget flag is a
#: safety invariant, and a flag that can be set to ``0`` (or to a typo'd ``30``)
#: is not a cap.  Anything outside ``(0, MAX_BUDGET_USD]`` is refused before the
#: subprocess is spawned, so a bad budget costs nothing.
MAX_BUDGET_USD = 1.00
DEFAULT_TIMEOUT_S = 180
MAX_DRAFTS = 3
MAX_NEGATIVES = 5


class LLMUnavailable(RuntimeError):
    """No ``claude`` binary, or the call failed before producing output."""


def claude_bin() -> str:
    explicit = os.environ.get("PRECEDENT_CLAUDE_BIN")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if not found:
        raise LLMUnavailable(
            "no `claude` binary on PATH; --llm needs the Claude Code CLI "
            "(set PRECEDENT_CLAUDE_BIN to point at it)")
    return found


def check_budget(budget_usd) -> float:
    """The budget, or :class:`LLMUnavailable`.  Called before any subprocess."""
    try:
        b = float(budget_usd)
    except (TypeError, ValueError):
        raise LLMUnavailable(f"--llm-budget {budget_usd!r} is not a number")
    if not (b == b) or b <= 0 or b > MAX_BUDGET_USD:    # b != b catches NaN
        raise LLMUnavailable(
            f"--llm-budget must be > 0 and <= {MAX_BUDGET_USD:g} USD per call "
            f"(got {b:g}); the budget flag is the spend cap, so precedent will "
            f"not run a call that disables or inflates it")
    return b


def _is_finite_number(value) -> bool:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return False
    return c == c and c not in (float("inf"), float("-inf")) and c >= 0.0


def sane_cost(value) -> float:
    """A reported cost we are willing to put in the accounting, or ``0.0``.

    The number comes out of a subprocess we do not control, and every spend
    cap in this package is a comparison against it.  ``float("nan")`` makes
    **every** comparison false, so a binary that reports ``NaN`` (or ``inf``,
    or a negative refund, or a string) would switch off every budget check in
    ``examine`` and ``improve`` at once, and would also write a bare ``NaN``
    token into ``spend.jsonl`` that is not valid JSON for anything but Python.

    So: not a finite number >= 0 -> ``0.0``, and the caller's own worst-case
    *reservation* (one per-call cap per call actually made) is what bounds the
    run.  An absurd-but-finite value is booked as reported, not clamped: an
    over-report trips the budget check and stops the run, which is the safe
    direction, and under-booking real money is not.
    """
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    if c != c or c in (float("inf"), float("-inf")) or c < 0.0:
        return 0.0
    return c


def json_safe(obj):
    """``obj`` with every non-finite float replaced, so the line is real JSON.

    ``json.dumps`` happily emits ``NaN`` / ``Infinity``; nothing outside
    Python reads those back, and ``total_spend`` then returns ``nan`` and
    poisons every number in ``precedent report``.
    """
    if isinstance(obj, float):
        return obj if (obj == obj and obj not in (float("inf"), float("-inf"))) \
            else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


def claude_argv(prompt: str, model: str = DEFAULT_MODEL,
                budget_usd: float = DEFAULT_BUDGET_USD,
                binary: str | None = None) -> list[str]:
    """The exact argv.  Every flag here is a safety invariant, including the
    one that is absent: ``--dangerously-skip-permissions`` is never passed."""
    budget_usd = check_budget(budget_usd)
    if not isinstance(model, str) or not model.strip():
        raise LLMUnavailable("--llm-model must be a model name")
    return [
        binary or claude_bin(),
        "-p", prompt,
        "--model", model,
        "--max-budget-usd", f"{budget_usd:g}",
        "--output-format", "json",
        "--no-session-persistence",
        # A draftsman needs no tools, and it must not be able to read the
        # machine it is drafting *for*: `--safe-mode` drops CLAUDE.md, skills,
        # plugins, hooks, MCP servers and custom agents, so the proposer cannot
        # be steered by the very state it is proposing to change (and cannot
        # see precedent's own rules, which is the L4 separation).  It is also
        # what makes the call cheap: measured on this machine, the same draft
        # prompt cost $0.33 and 107 s with the default loadout and $0.009 and
        # 3.8 s with these three flags -- a 37x difference that was burning the
        # whole per-call budget before the model ever answered.
        "--safe-mode",
        "--tools", "",
        "--disable-slash-commands",
    ]


# --------------------------------------------------------------------------
# the prompt
# --------------------------------------------------------------------------

_DSL_SPEC = """\
The DSL (JSON, schemaVersion 1):

  {"tool": "<one of Bash Edit Write MultiEdit NotebookEdit Agent Workflow \
WebFetch Skill * , or several joined with |>",
   "match": "all" | "any",
   "matchers": [
      {"type": "input_regex", "field": "<input field name>", "regex": "<python re>"},
      {"type": "input_field_missing", "field": "<input field name>"},
      {"type": "input_field_equals", "field": "<name>", "value": "<scalar>", \
"negate": true|false}],
   "action": "deny" | "ask" | "log",
   "scope": "project" | "global",
   "message": "<what the agent is shown, quoting the correction and its date>"}

`field` is a key of the tool's input object: command/description for Bash,
file_path for Edit/Write/MultiEdit, notebook_path for NotebookEdit,
prompt/model/description for Agent, script/scriptPath/model for Workflow,
url for WebFetch, skill for Skill.

Rules:
* regexes must be plain, anchored on literal text, with no nested quantifiers
  ((a+)+ is rejected), no repeat bound above 200, at most 400 characters;
* prefer the narrowest matcher that catches the violating action shown below;
* "ask" for destructive-but-sometimes-right actions, "deny" for never-do-this,
  "log" when you only want it counted;
* the message must contain the user's own words and the date.
"""


def _action_lines(topics) -> list[str]:
    out: list[str] = []
    for t in topics:
        for c in t.members:
            for tool in c.turn.preceding_tools:
                inp = tool.get("input") or {}
                shown = {k: (v if isinstance(v, (str, int, float, bool)) else "…")
                         for k, v in list(inp.items())[:6]}
                for k, v in list(shown.items()):
                    if isinstance(v, str) and len(v) > 160:
                        shown[k] = v[:160] + "…"
                out.append(f"  - {tool.get('tool')}  "
                           f"{json.dumps(shown, ensure_ascii=False)}")
    return out[:12]


def build_prompt(topics, negatives: list[dict] | None = None) -> str:
    """The whole prompt: quotes, violating actions, the DSL, the negatives."""
    lines: list[str] = []
    lines.append("You are compiling a user's corrections of a coding agent into "
                 "deterministic pre-tool-use checks.")
    lines.append("")
    lines.append("## The corrections (the user's own words, with dates)")
    lines.append("")
    for t in topics:
        for c in t.members:
            date = (c.turn.ts or "")[:10]
            lines.append(f"  - [{date}] {c.quote(200)}")
    lines.append("")
    lines.append("## The violating actions recorded immediately before them")
    lines.append("")
    acts = _action_lines(topics)
    lines.extend(acts or ["  (none recorded)"])
    lines.append("")
    lines.append("## " + _DSL_SPEC)
    if negatives:
        lines.append("")
        lines.append("## Drafts that were REJECTED before — do not repeat these")
        lines.append("")
        for n in negatives[:MAX_NEGATIVES]:
            lines.append(f"  - rejected because: {n.get('reason', '?')}")
            lines.append(f"    {json.dumps(n.get('draft'), ensure_ascii=False)[:400]}")
    lines.append("")
    lines.append(f"Return ONLY a JSON array of 1 to {MAX_DRAFTS} rule objects. "
                 "No prose, no markdown fence, no explanation. A rule that does "
                 "not fire on at least one violating action above is useless: "
                 "it will be rejected by a mechanical gate you cannot see or "
                 "influence.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# the call
# --------------------------------------------------------------------------

@dataclass
class LLMCall:
    argv: list[str]
    ok: bool = False
    raw_stdout: str = ""
    stderr: str = ""
    returncode: int = -1
    text: str = ""              # the model's answer
    cost_usd: float = 0.0
    duration_ms: int = 0
    model: str = DEFAULT_MODEL
    budget_usd: float = DEFAULT_BUDGET_USD
    error: str | None = None
    #: ``claude`` answered with a cost that is not a finite number >= 0 (NaN,
    #: a string, a refund).  It is booked as ``0.0`` and the caller's own
    #: per-call reservation is what bounds the run.
    untrusted_cost: bool = False

    def to_dict(self) -> dict:
        return {"ok": self.ok, "model": self.model, "budgetUsd": self.budget_usd,
                "costUsd": self.cost_usd, "durationMs": self.duration_ms,
                "untrustedCost": self.untrusted_cost,
                "returncode": self.returncode, "error": self.error,
                "argv": [a if len(a) < 120 else a[:120] + "…" for a in self.argv]}


def run_claude(prompt: str, model: str = DEFAULT_MODEL,
               budget_usd: float = DEFAULT_BUDGET_USD,
               timeout_s: int = DEFAULT_TIMEOUT_S,
               binary: str | None = None) -> LLMCall:
    """One ``claude -p`` call.  Never raises for a model-side failure."""
    argv = claude_argv(prompt, model=model, budget_usd=budget_usd, binary=binary)
    call = LLMCall(argv=argv, model=model, budget_usd=budget_usd)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout_s)
    except subprocess.TimeoutExpired:
        call.error = f"claude -p timed out after {timeout_s}s"
        call.duration_ms = int((time.monotonic() - t0) * 1000)
        return call
    except OSError as exc:
        call.error = f"could not run claude: {exc}"
        call.duration_ms = int((time.monotonic() - t0) * 1000)
        return call
    call.duration_ms = int((time.monotonic() - t0) * 1000)
    call.returncode = proc.returncode
    call.raw_stdout = proc.stdout or ""
    call.stderr = (proc.stderr or "")[:2000]
    try:
        payload = json.loads(call.raw_stdout)
    except (ValueError, RecursionError):
        call.error = "claude -p did not return JSON (--output-format json)"
        call.text = call.raw_stdout
        return call
    if isinstance(payload, dict):
        reported = payload.get("total_cost_usd")
        if reported is None:
            reported = payload.get("cost_usd")
        call.cost_usd = sane_cost(reported)
        if reported is not None and not _is_finite_number(reported):
            # the spend ledger must still get a line, even for a garbled answer
            call.untrusted_cost = True
        result = payload.get("result")
        call.text = result if isinstance(result, str) else json.dumps(result,
                                                                      ensure_ascii=False)
        if payload.get("is_error") or payload.get("subtype") not in (None, "success"):
            call.error = f"claude reported {payload.get('subtype')}"
            return call
    else:                                                # pragma: no cover
        call.text = json.dumps(payload, ensure_ascii=False)
    call.ok = call.returncode == 0 and bool(call.text)
    if not call.ok and not call.error:
        call.error = f"claude exited {call.returncode}"
    return call


# --------------------------------------------------------------------------
# parsing + persistence
# --------------------------------------------------------------------------

def _json_slices(text: str):
    """Yield candidate JSON substrings: fenced blocks first, then bracket scans."""
    if not text:
        return
    fence = text.split("```")
    if len(fence) >= 3:
        for i in range(1, len(fence), 2):
            body = fence[i]
            if body.startswith("json"):
                body = body[4:]
            yield body.strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            yield text[start:end + 1]


def extract_json_rules(text: str) -> list[dict]:
    """The rule objects in the model's answer.  Tolerates fences and prose."""
    for chunk in _json_slices(text):
        try:
            data = json.loads(chunk)
        except (ValueError, RecursionError):
            # RecursionError is not a ValueError: 60 000 nested brackets in the
            # model's answer must be a rejected draft, not a crashed CLI.
            continue
        if isinstance(data, dict):
            if isinstance(data.get("rules"), list):
                data = data["rules"]
            else:
                data = [data]
        if isinstance(data, list):
            out = [d for d in data if isinstance(d, dict)]
            if out:
                return out[:MAX_DRAFTS]
    return []


def record_spend(state, row: dict) -> str:
    path = state.path("spend.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(json_safe(row), ensure_ascii=False,
                            allow_nan=False) + "\n")
    return path


def total_spend(state) -> float:
    path = state.path("spend.jsonl")
    total = 0.0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    total += sane_cost(json.loads(line).get("costUsd"))
                except (ValueError, AttributeError):
                    continue
    except OSError:
        return 0.0
    return round(total, 6)


def record_rejected(state, rows: list[dict]) -> str:
    path = state.path("rejected.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(json_safe(row), ensure_ascii=False,
                                allow_nan=False) + "\n")
    return path


def load_negatives(state, limit: int = MAX_NEGATIVES) -> list[dict]:
    """The most recent rejected drafts, newest first — the negative examples."""
    path = state.path("rejected.jsonl")
    rows: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return list(reversed(rows))[:limit]


# --------------------------------------------------------------------------
# the whole flow
# --------------------------------------------------------------------------

@dataclass
class LLMDraftResult:
    call: LLMCall
    drafts: list[dict] = field(default_factory=list)       # raw, as returned
    accepted: list[tuple[dict, object]] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    spend_path: str | None = None
    rejected_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "call": self.call.to_dict(),
            "nDrafts": len(self.drafts),
            "nAccepted": len(self.accepted),
            "nRejected": len(self.rejected),
            "accepted": [r for r, _g in self.accepted],
            "rejected": self.rejected,
            "costUsd": self.call.cost_usd,
        }


def llm_draft_rules(state, topics, mine_result, *, gate_runner,
                    model: str = DEFAULT_MODEL,
                    budget_usd: float = DEFAULT_BUDGET_USD,
                    timeout_s: int = DEFAULT_TIMEOUT_S,
                    binary: str | None = None,
                    now_iso_str: str | None = None) -> LLMDraftResult:
    """Draft rules with ``claude -p``, then validate and gate every one.

    ``gate_runner(rule) -> TemporalGate`` is injected so this module never
    imports the gate (and so the tests can prove the gate is unskippable).
    """
    from .state import now_iso

    negatives = load_negatives(state)
    prompt = build_prompt(list(topics), negatives)
    call = run_claude(prompt, model=model, budget_usd=budget_usd,
                      timeout_s=timeout_s, binary=binary)
    out = LLMDraftResult(call=call)
    ts = now_iso_str or now_iso()
    topic_ids = [t.id for t in topics]

    drafts = extract_json_rules(call.text) if call.ok else []
    out.drafts = drafts
    if call.ok and not drafts:
        out.rejected.append({"ts": ts, "topics": topic_ids, "reason":
                             "no JSON rule object in the model's answer",
                             "draft": (call.text or "")[:400]})

    for draft in drafts:
        raw = dict(draft)
        # an LLM does not get to choose its own id, status or birth evidence
        for key in ("id", "status", "birth", "confirmedBy", "confirmedAt"):
            raw.pop(key, None)
        raw.setdefault("topic", topic_ids[0] if topic_ids else None)
        raw["topics"] = topic_ids
        raw["origin"] = "llm"
        try:
            rule = validate_rule(raw)
        except RuleError as exc:
            out.rejected.append({"ts": ts, "topics": topic_ids,
                                 "reason": f"schema: {exc}", "draft": draft})
            continue
        try:
            gate = gate_runner(rule)
        except Exception as exc:                        # pragma: no cover - defensive
            out.rejected.append({"ts": ts, "topics": topic_ids,
                                 "reason": f"gate error: {type(exc).__name__}: {exc}",
                                 "draft": draft})
            continue
        if gate.verdict != "PASS":
            out.rejected.append({"ts": ts, "topics": topic_ids,
                                 "reason": f"temporal gate {gate.verdict}: "
                                           f"{gate.counts()}",
                                 "draft": draft, "gate": gate.to_dict()})
            continue
        rule["origin"] = "llm"
        rule["status"] = "candidate"
        out.accepted.append((rule, gate))

    if out.rejected:
        out.rejected_path = record_rejected(state, out.rejected)
    out.spend_path = record_spend(state, {
        "ts": ts, "command": "compile --llm", "model": model,
        "budgetUsd": budget_usd, "costUsd": call.cost_usd,
        "durationMs": call.duration_ms, "topics": topic_ids,
        "nDrafts": len(drafts), "nAccepted": len(out.accepted),
        "nRejected": len(out.rejected), "ok": call.ok, "error": call.error,
    })
    return out
