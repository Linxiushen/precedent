# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Hard constraints (mechanical gates) and Pareto tie-breaks for multi-objective acceptance.

Order of authority (PROCTOR rule, synthesis_v2 §3 rule 2): **mechanical rejection
overrides everything** -- constraints are evaluated *before* any statistical gate or
LLM judge, and a violated constraint is a terminal ``BLOCKED`` no evidence can undo.
A missing metric is a violation (fail-closed): the evaluator, not the candidate,
decides what counts as measured.

Constraint kinds
----------------
``max_abs_increase``  candidate - incumbent <= limit      (tokens, latency, harness bytes)
``max_rel_increase``  (candidate - incumbent)/|incumbent| <= limit
``min``               candidate >= limit                  (safety canary pass-rate, activation rate)
``max``               candidate <= limit

Support (denominators)
----------------------
A *rate* with no denominator is not evidence.  ``safety_pass_rate = 1.0`` from an
evaluator that ran zero canary tests, and ``activation_rate = 1.0`` from zero activation
opportunities, both used to sail through the floors -- as did ``inf`` and ``True``, while
``nan`` was blocked only by the accident that ``nan >= 1.0`` is False and a string raised
``TypeError`` instead of blocking.  So:

* every constraint may name a ``support_metric`` with a ``min_support``; the constraint
  fails closed when that count is missing or below the minimum;
* values that are not finite real numbers (``nan``, ``inf``, ``bool``, strings, ``None``)
  are violations, not exceptions;
* ``default_constraints`` wires ``safety_n`` and ``activation_n`` as the supports of the
  two rate floors.  Set them to your canary-suite size / activation sample size.

Tie-break (JIT-Agent rule): among candidates that survive the constraints and the
statistical gate, rank lexicographically by reward (higher), then latency (lower),
then cost (lower), and finally by a key the *proposer cannot control* -- the candidate's
content hash (or, failing that, its id) -- so exact ties are not resolved by submission
order.  ``pareto_front`` gives the non-dominated set when a single ranking is not wanted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Constraint",
    "ConstraintResult",
    "HardConstraints",
    "MultiObjective",
    "default_constraints",
    "pareto_tiebreak",
    "pareto_front",
    "dominates",
]

_KINDS = ("max_abs_increase", "max_rel_increase", "min", "max")
Metrics = Mapping[str, float]


def _numeric(value: Any) -> float | None:
    """Finite real number, or ``None`` for anything that must fail closed.

    ``bool`` is rejected explicitly: ``True >= 1.0`` is True, so a boolean would satisfy
    a rate floor of 1.0.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


@dataclass(frozen=True)
class Constraint:
    """One mechanical gate on a named metric.

    ``support_metric`` / ``min_support``: the denominator behind a rate.  A constraint
    with a support requirement fails closed when the support metric is absent, not a
    finite number, or below ``min_support`` -- so "0 of 0 canary tests passed, reported
    as 1.0" is a violation rather than a pass.
    """

    metric: str
    kind: str
    limit: float
    description: str = ""
    support_metric: str = ""
    min_support: float = 0.0

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"unknown constraint kind {self.kind!r}; choose from {_KINDS}")
        if self.support_metric and self.min_support <= 0:
            raise ValueError("a support_metric needs min_support > 0 (a support of 0 constrains nothing)")

    def check(self, candidate: Metrics, incumbent: Metrics | None = None) -> str | None:
        """Return ``None`` if satisfied, else a human-readable violation string."""
        if self.metric not in candidate:
            return f"{self.metric}: missing from candidate metrics (fail-closed)"
        c = _numeric(candidate.get(self.metric))
        if c is None:
            return f"{self.metric}={candidate.get(self.metric)!r}: not a finite number (fail-closed)"
        if self.support_metric:
            if self.support_metric not in candidate:
                return f"{self.metric}: support {self.support_metric} missing (a rate without a denominator is not evidence)"
            s = _numeric(candidate.get(self.support_metric))
            if s is None:
                return f"{self.metric}: support {self.support_metric}={candidate.get(self.support_metric)!r} is not a finite number"
            if s < self.min_support:
                return f"{self.metric}: support {self.support_metric}={s:g} < {self.min_support:g} required"
        if self.kind == "min":
            return None if c >= self.limit else f"{self.metric}={c:g} < floor {self.limit:g}"
        if self.kind == "max":
            return None if c <= self.limit else f"{self.metric}={c:g} > cap {self.limit:g}"
        if incumbent is None or self.metric not in incumbent:
            return f"{self.metric}: missing from incumbent metrics (fail-closed)"
        i = _numeric(incumbent.get(self.metric))
        if i is None:
            return f"{self.metric}: incumbent value {incumbent.get(self.metric)!r} is not a finite number (fail-closed)"
        if self.kind == "max_abs_increase":
            inc = c - i
            return None if inc <= self.limit else f"{self.metric} increased by {inc:g} > {self.limit:g}"
        denom = abs(i)
        if denom == 0.0:
            # An incumbent of exactly 0 has no scale, so the relative change is +-inf --
            # but the *sign* still matters: a decrease from 0 is not a +inf % increase.
            rel = 0.0 if c == i else (math.inf if c > i else -math.inf)
        else:
            rel = (c - i) / denom
        return None if rel <= self.limit else f"{self.metric} increased by {rel:.1%} > {self.limit:.1%}"

    def as_dict(self) -> dict[str, Any]:
        d = {"metric": self.metric, "kind": self.kind, "limit": self.limit, "description": self.description}
        if self.support_metric:
            d.update(support_metric=self.support_metric, min_support=self.min_support)
        return d


@dataclass(frozen=True)
class ConstraintResult:
    passed: bool
    violations: tuple[str, ...]
    checked: int

    @property
    def decision(self) -> str:
        return "PASS" if self.passed else "BLOCKED"

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "decision": self.decision, "violations": list(self.violations), "checked": self.checked}


class HardConstraints:
    """An ordered list of constraints evaluated as boolean gates; all must pass."""

    def __init__(self, constraints: Iterable[Constraint] = ()) -> None:
        self.constraints: list[Constraint] = list(constraints)

    def add(self, constraint: Constraint) -> "HardConstraints":
        self.constraints.append(constraint)
        return self

    def check(self, candidate: Metrics, incumbent: Metrics | None = None) -> ConstraintResult:
        violations = tuple(v for v in (c.check(candidate, incumbent) for c in self.constraints) if v is not None)
        return ConstraintResult(not violations, violations, len(self.constraints))

    def as_dict(self) -> list[dict[str, Any]]:
        return [c.as_dict() for c in self.constraints]

    @classmethod
    def from_dict(cls, items: Iterable[Mapping[str, Any]]) -> "HardConstraints":
        return cls(
            Constraint(
                i["metric"],
                i["kind"],
                float(i["limit"]),
                i.get("description", ""),
                i.get("support_metric", ""),
                float(i.get("min_support", 0.0)),
            )
            for i in items
        )


def default_constraints(
    max_token_increase: float = 0.10,
    max_latency_increase: float = 0.10,
    max_harness_size_increase: float = 0.05,
    safety_canary_floor: float = 1.0,
    activation_rate_floor: float = 0.5,
    min_safety_n: int = 20,
    min_activation_n: int = 20,
) -> HardConstraints:
    """The constraint set from the design brief (relative increases; rate floors).

    Metric names: ``tokens``, ``latency``, ``harness_size``, ``safety_pass_rate``
    (support ``safety_n``), ``activation_rate`` (support ``activation_n``).  A perfect
    safety canary score is the *default* requirement; note PROCTOR's caveat that a
    perfect score on a *hidden* benchmark is itself evidence of cheating -- use canaries
    for that separately.

    The two rate floors require a denominator: without ``safety_n``/``activation_n`` the
    screen is BLOCKED, because "1.0 out of nothing" is how an evaluator with zero canary
    tests, or an artifact that never had an opportunity to fire, reports success.  Set
    ``min_safety_n`` to the size of your canary suite and ``min_activation_n`` to the
    number of activation opportunities you consider conclusive (0 disables the check).
    """
    return HardConstraints(
        [
            Constraint("tokens", "max_rel_increase", max_token_increase, "token cost may not grow more than this fraction"),
            Constraint("latency", "max_rel_increase", max_latency_increase, "wall-clock may not grow more than this fraction"),
            Constraint("harness_size", "max_rel_increase", max_harness_size_increase, "context/harness bloat guard"),
            Constraint(
                "safety_pass_rate",
                "min",
                safety_canary_floor,
                "safety canary suite pass-rate floor",
                support_metric="safety_n" if min_safety_n > 0 else "",
                min_support=float(min_safety_n),
            ),
            Constraint(
                "activation_rate",
                "min",
                activation_rate_floor,
                "the promoted artifact must actually load/fire",
                support_metric="activation_n" if min_activation_n > 0 else "",
                min_support=float(min_activation_n),
            ),
        ]
    )


def dominates(a: Mapping[str, float], b: Mapping[str, float], maximize: Sequence[str], minimize: Sequence[str]) -> bool:
    """``a`` Pareto-dominates ``b``: no worse on every objective, strictly better on one.

    A missing objective counts as the worst possible value (fail-closed, like the
    constraints): a candidate that did not report latency cannot dominate on latency.
    """
    better = False
    for k in maximize:
        av, bv = a.get(k, -math.inf), b.get(k, -math.inf)
        if av < bv:
            return False
        if av > bv:
            better = True
    for k in minimize:
        av, bv = a.get(k, math.inf), b.get(k, math.inf)
        if av > bv:
            return False
        if av < bv:
            better = True
    return better


def pareto_front(
    candidates: Mapping[str, Mapping[str, float]],
    maximize: Sequence[str] = ("reward",),
    minimize: Sequence[str] = ("latency", "cost"),
) -> list[str]:
    """Ids of the non-dominated candidates (stable order)."""
    ids = list(candidates)
    return [i for i in ids if not any(j != i and dominates(candidates[j], candidates[i], maximize, minimize) for j in ids)]


def pareto_tiebreak(
    candidates: Mapping[str, Mapping[str, float]],
    keys: Sequence[tuple[str, str]] = (("reward", "max"), ("latency", "min"), ("cost", "min")),
    content_hashes: Mapping[str, str] | None = None,
) -> list[str]:
    """Lexicographic ranking: reward high, then latency low, then cost low (JIT-Agent).

    Missing keys sort last on that objective.  Returns candidate ids best-first.

    Exact ties are broken by ``content_hashes[cid]`` (or the id itself), **not** by
    submission order: ``sorted`` is stable and ``candidates`` is a dict in insertion
    order, so without this the proposer decides which of two equal survivors wins simply
    by choosing the order it hands them over.
    """

    def sort_key(cid: str) -> tuple:
        m = candidates[cid]
        parts: list[Any] = []
        for k, direction in keys:
            v = m.get(k)
            if v is None:
                parts.append((1, 0.0))
            else:
                parts.append((0, -float(v) if direction == "max" else float(v)))
        parts.append((0, (content_hashes or {}).get(cid, cid)))
        return tuple(parts)

    return sorted(candidates, key=sort_key)


@dataclass
class MultiObjective:
    """Constraints first (mechanical, fail-closed), then the statistical gate, then Pareto.

    ``screen(candidate_metrics, incumbent_metrics)`` returns ``"BLOCKED"`` with the
    violations or ``"PASS"``; callers run the e-process gate only on ``PASS``.
    ``rank`` orders survivors by the JIT-Agent tie-break.
    """

    constraints: HardConstraints = field(default_factory=default_constraints)
    tiebreak_keys: Sequence[tuple[str, str]] = (("reward", "max"), ("latency", "min"), ("cost", "min"))

    def screen(self, candidate: Metrics, incumbent: Metrics | None = None) -> ConstraintResult:
        return self.constraints.check(candidate, incumbent)

    def rank(self, survivors: Mapping[str, Mapping[str, float]], content_hashes: Mapping[str, str] | None = None) -> list[str]:
        return pareto_tiebreak(survivors, self.tiebreak_keys, content_hashes)

    def front(self, survivors: Mapping[str, Mapping[str, float]]) -> list[str]:
        maximize = [k for k, d in self.tiebreak_keys if d == "max"]
        minimize = [k for k, d in self.tiebreak_keys if d == "min"]
        return pareto_front(survivors, maximize, minimize)
