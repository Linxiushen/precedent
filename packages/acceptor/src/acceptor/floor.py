# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Per-task "never-regress" floor with stability-gated membership and k-of-n confirmation.

The problem
-----------
A naive floor -- "block the candidate if any previously-passing task now fails" -- is
itself a multiple-testing procedure with no error control.  For a task the incumbent
passes with probability ``p`` and a candidate of *identical* quality, a single repeat
flips pass->fail with probability ``p (1 - p)``; over ``N`` tasks the floor fires with
probability ``1 - (1 - p(1-p))^N``, e.g. 94 % for N = 10, p = 0.6 (synthesis_v1 §G,
research/tools/gate_power_sim.py: 93-100 % false-block at k = 1).

Two remedies, both implemented here (synthesis_v2 §3, rule 4):

1. **Membership requires observed stability.**  A task enters the protected corpus only
   after the incumbent passed ``k_stability`` of ``k_stability`` repeats.  Under
   Bernoulli(p) this admits a task with probability ``p^k``, which selects for tasks
   that are actually deterministic (real test-verified coding tasks are far more
   deterministic than Bernoulli(0.6); the baseline self-replay measures this for free).
2. **A flip counts only after k-of-n confirmation.**  A candidate is charged with a
   regression on a protected task only when it failed at least ``k_confirm`` of the
   **first** ``n_confirm`` attempts on that task.

   The confirmation window is *frozen* at the first ``n_confirm`` outcomes, and that is
   load-bearing.  If the rule were "``k_confirm`` fails among however many runs the
   adapter chose to do", the closed form below (which assumes exactly ``n_confirm``
   attempts) would understate the real false-block rate without bound: measured at
   N = 20, p = 0.6, k = (2, 2, 2), the closed form says 0.695 while re-running each task
   5 times blocks 0.996 of zero-lift candidates, and P(>= 2 fails of 5 | p = 0.75) = 0.37
   against a reported 0.156.  Extra runs are still counted and reported
   (``cand_runs``/``extra_runs``), they just cannot manufacture a flip.

3. **Silence is not a pass.**  ``check()`` returns a tri-state verdict: ``BLOCKED``,
   ``PASS``, or ``INCOMPLETE``.  An empty corpus, a candidate never run on the protected
   tasks, or protected tasks with failures that have not reached ``n_confirm`` attempts
   all return ``INCOMPLETE`` -- previously every one of those returned ``blocked=False``,
   so a proposer or adapter that simply skipped the protected tasks (or ran each exactly
   once with ``n_confirm = 2``) could never be blocked.  **Only ``PASS`` clears the
   floor**; treat ``INCOMPLETE`` as "run more evaluations", never as a green light.

Closed-form false-block rate (zero true lift, independent Bernoulli tasks)
-------------------------------------------------------------------------
    P(block) = 1 - prod_i ( 1 - a_i * b_i ),
    a_i = p_i^k_stability                      (membership; = 1 if membership is given),
    b_i = P( Bin(n_confirm, 1 - p_i) >= k_confirm )   (confirmed flip).

With ``k_stability = k_confirm = n_confirm = k`` this is exactly the simulation in
gate_power_sim.py: k = 1 -> 93-100 %, k = 2 -> 44-91 %, k = 3 -> 13-43 % for N in
{10, 20, 40}, p = 0.6.

Retention vs adaptation
-----------------------
EvoHarnessBench separates backward transfer (BWT: mean change on the protected /
previously-solved tasks) from forward transfer (FWT: mean change on the target tasks
the edit was aimed at).  "Additive-only cannot regress" is false, and a single
aggregate hides the trade-off, so ``transfer_report`` reports them separately.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "ProtectedCorpus",
    "TaskFloor",
    "FloorVerdict",
    "TransferReport",
    "false_block_rate",
    "transfer_report",
    "binomial_tail",
]


def binomial_tail(n: int, k: int, p: float) -> float:
    """``P(Bin(n, p) >= k)`` exactly (stdlib only)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.comb(n, j) * p**j * (1.0 - p) ** (n - j) for j in range(k, n + 1))


def false_block_rate(
    pass_probs: Iterable[float],
    k_stability: int = 1,
    k_confirm: int = 1,
    n_confirm: int | None = None,
    membership_given: bool = False,
) -> float:
    """Probability that a zero-lift candidate is blocked by the floor (closed form).

    ``pass_probs`` are the per-task incumbent pass probabilities (independent Bernoulli
    tasks).  ``membership_given=True`` conditions on the tasks already being in the
    corpus (drops the ``p^k`` membership factor).  See module docstring.
    """
    n_confirm = k_confirm if n_confirm is None else n_confirm
    if k_confirm < 1 or n_confirm < k_confirm or k_stability < 1:
        raise ValueError("need k_stability >= 1 and 1 <= k_confirm <= n_confirm")
    log_survive = 0.0
    for p in pass_probs:
        if not (0.0 <= p <= 1.0):
            raise ValueError("pass probabilities must lie in [0, 1]")
        a = 1.0 if membership_given else p**k_stability
        b = binomial_tail(n_confirm, k_confirm, 1.0 - p)
        x = 1.0 - a * b
        if x <= 0.0:
            return 1.0
        log_survive += math.log(x)
    return 1.0 - math.exp(log_survive)


@dataclass(frozen=True)
class FloorVerdict:
    """Result of ``ProtectedCorpus.check()``.

    ``verdict`` is the field to gate on: ``"PASS"`` (the candidate was evaluated to
    completion on every protected task and no flip was confirmed), ``"BLOCKED"`` (a flip
    was confirmed) or ``"INCOMPLETE"`` (not enough evidence to say either way).
    ``blocked`` is kept for backwards compatibility and means exactly ``verdict ==
    "BLOCKED"`` -- gating on ``not blocked`` treats INCOMPLETE as a pass, which is the
    vacuous-floor bug this field exists to avoid.
    """

    blocked: bool
    confirmed_flips: tuple[str, ...]
    pending_flips: tuple[str, ...]  # protected tasks with failures but not yet k-of-n
    n_protected: int
    n_evaluated: int  # protected tasks the candidate has been run on at least once
    verdict: str = "PASS"
    n_complete: int = 0  # protected tasks with >= n_confirm candidate runs
    unevaluated: tuple[str, ...] = ()  # protected tasks with < n_confirm candidate runs
    min_protected: int = 0
    reason: str = ""

    @property
    def passed(self) -> bool:
        """True only for ``PASS`` -- the one condition that clears the floor."""
        return self.verdict == "PASS"

    @property
    def complete(self) -> bool:
        return self.n_protected > 0 and self.n_complete == self.n_protected

    @property
    def coverage(self) -> float:
        """Fraction of protected tasks run to ``n_confirm`` attempts (0.0 for an empty corpus)."""
        return self.n_complete / self.n_protected if self.n_protected else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "blocked": self.blocked,
            "reason": self.reason,
            "confirmed_flips": list(self.confirmed_flips),
            "pending_flips": list(self.pending_flips),
            "unevaluated": list(self.unevaluated),
            "n_protected": self.n_protected,
            "n_evaluated": self.n_evaluated,
            "n_complete": self.n_complete,
            "coverage": self.coverage,
            "complete": self.complete,
            "min_protected": self.min_protected,
        }


@dataclass
class _TaskRecord:
    inc_runs: int = 0
    inc_passes: int = 0
    cand_runs: int = 0
    cand_fails: int = 0
    inc_recent: list[int] = field(default_factory=list)  # last k_stability incumbent outcomes
    cand_window: list[int] = field(default_factory=list)  # FIRST n_confirm candidate outcomes


class ProtectedCorpus:
    """Stability-gated protected task set with k-of-n regression confirmation.

    Parameters
    ----------
    k_stability : incumbent must have passed the last ``k_stability`` repeats (k of k)
        for a task to be protected.
    k_confirm, n_confirm : a candidate flip on a protected task is confirmed when it
        failed >= ``k_confirm`` of the **first** ``n_confirm`` attempts.  ``n_confirm``
        defaults to ``k_confirm`` (i.e. "fails k of k").
    min_protected : below this many protected tasks the verdict is ``INCOMPLETE`` -- a
        floor over an empty (or nearly empty) corpus protects nothing.

    Usage: feed incumbent outcomes with ``record_incumbent``, candidate outcomes with
    ``record_candidate`` (one candidate per corpus instance, or ``reset_candidate()``
    between candidates), then ``check()`` and gate on ``verdict == "PASS"``.
    State is JSON-serialisable.

    The protected corpus is *not* an input to the e-process gate: it is by construction
    the set of instances the incumbent passes, and feeding it to a gate makes the harm
    martingale fire ~100 % of the time on an equal-quality candidate.  See the
    ``eprocess`` module docstring.
    """

    def __init__(
        self, k_stability: int = 2, k_confirm: int = 2, n_confirm: int | None = None, min_protected: int = 1
    ) -> None:
        n_confirm = k_confirm if n_confirm is None else n_confirm
        if k_stability < 1 or k_confirm < 1 or n_confirm < k_confirm:
            raise ValueError("need k_stability >= 1 and 1 <= k_confirm <= n_confirm")
        if min_protected < 0:
            raise ValueError("min_protected must be >= 0")
        self.k_stability = int(k_stability)
        self.k_confirm = int(k_confirm)
        self.n_confirm = int(n_confirm)
        self.min_protected = int(min_protected)
        self._tasks: dict[str, _TaskRecord] = {}

    # ---- recording -----------------------------------------------------------------
    def _rec(self, task_id: str) -> _TaskRecord:
        return self._tasks.setdefault(str(task_id), _TaskRecord())

    def record_incumbent(self, task_id: str, passed: bool) -> None:
        r = self._rec(task_id)
        r.inc_runs += 1
        r.inc_passes += int(bool(passed))
        r.inc_recent.append(int(bool(passed)))
        if len(r.inc_recent) > self.k_stability:
            del r.inc_recent[: len(r.inc_recent) - self.k_stability]

    def record_candidate(self, task_id: str, passed: bool) -> None:
        """Record one candidate attempt.

        Only the first ``n_confirm`` attempts per task can confirm a flip (see the module
        docstring: an unbounded window invalidates the closed-form false-block rate).
        Later attempts are still counted in ``cand_runs`` and reported as ``extra_runs``.
        """
        r = self._rec(task_id)
        r.cand_runs += 1
        r.cand_fails += int(not passed)
        if len(r.cand_window) < self.n_confirm:
            r.cand_window.append(int(bool(passed)))

    def reset_candidate(self) -> None:
        for r in self._tasks.values():
            r.cand_runs = 0
            r.cand_fails = 0
            r.cand_window.clear()

    def extra_runs(self) -> dict[str, int]:
        """Per task: candidate attempts beyond the frozen confirmation window."""
        return {t: r.cand_runs - len(r.cand_window) for t, r in sorted(self._tasks.items()) if r.cand_runs > len(r.cand_window)}

    # ---- queries -------------------------------------------------------------------
    def is_protected(self, task_id: str) -> bool:
        r = self._tasks.get(str(task_id))
        return r is not None and len(r.inc_recent) >= self.k_stability and all(r.inc_recent)

    def protected_tasks(self) -> list[str]:
        return sorted(t for t in self._tasks if self.is_protected(t))

    def estimated_pass_prob(self, task_id: str) -> float | None:
        """Laplace-smoothed incumbent pass rate (for ``expected_false_block``)."""
        r = self._tasks.get(str(task_id))
        if r is None or r.inc_runs == 0:
            return None
        return (r.inc_passes + 1.0) / (r.inc_runs + 2.0)

    def check(self, min_protected: int | None = None) -> FloorVerdict:
        """Tri-state floor verdict; gate on ``verdict == "PASS"``, never on ``not blocked``."""
        min_protected = self.min_protected if min_protected is None else int(min_protected)
        confirmed: list[str] = []
        pending: list[str] = []
        unevaluated: list[str] = []
        prot = self.protected_tasks()
        evaluated = 0
        complete = 0
        for t in prot:
            r = self._tasks[t]
            if r.cand_runs:
                evaluated += 1
            window_fails = sum(1 for x in r.cand_window if not x)
            window_done = len(r.cand_window) >= self.n_confirm
            if window_done:
                complete += 1
            else:
                unevaluated.append(t)
            if window_fails >= self.k_confirm and window_done:
                confirmed.append(t)
            elif window_fails > 0:
                pending.append(t)
        if confirmed:
            verdict, reason = "BLOCKED", f"{len(confirmed)} confirmed regression(s) on protected tasks"
        elif len(prot) < min_protected:
            verdict, reason = "INCOMPLETE", f"only {len(prot)} protected tasks < min_protected={min_protected}"
        elif unevaluated:
            verdict, reason = (
                "INCOMPLETE",
                f"{len(unevaluated)} protected task(s) have fewer than n_confirm={self.n_confirm} candidate runs",
            )
        else:
            verdict, reason = "PASS", f"{len(prot)} protected tasks evaluated to completion, no confirmed flip"
        return FloorVerdict(
            bool(confirmed),
            tuple(confirmed),
            tuple(pending),
            len(prot),
            evaluated,
            verdict=verdict,
            n_complete=complete,
            unevaluated=tuple(unevaluated),
            min_protected=min_protected,
            reason=reason,
        )

    def pass_prob_evidence(self) -> dict[str, str]:
        """Per protected task: ``"explicit"`` is impossible here, so ``"prior_only"`` vs ``"measured"``.

        A task enters the corpus on ``k_stability`` of ``k_stability`` incumbent passes, so
        its Laplace-smoothed rate is ``(k+1)/(k+2)`` *by construction* -- 0.75 for k = 2
        whatever the task's true pass rate is.  Only incumbent runs **beyond** the
        membership window carry information.
        """
        out = {}
        for t in self.protected_tasks():
            r = self._tasks[t]
            out[t] = "measured" if r.inc_runs > self.k_stability else "prior_only"
        return out

    def expected_false_block(
        self,
        pass_probs: Mapping[str, float] | None = None,
        membership_given: bool = True,
        require_evidence: bool = False,
    ) -> float:
        """Closed-form false-block rate for the *current* protected set.

        Uses ``pass_probs`` if given, else Laplace-smoothed incumbent pass rates.  With
        ``membership_given=True`` (default) it is the rate conditional on the tasks
        already being protected -- what a user pays per candidate for the chosen k.

        **The default estimate is a prior, not a measurement.**  Membership requires
        ``k_stability`` of ``k_stability`` incumbent passes, so the smoothed rate of a
        task with no runs beyond that window is ``(k+1)/(k+2)`` regardless of its true
        pass rate, and the returned number is then a function of ``k`` alone.  Pass
        explicit ``pass_probs`` from an independent incumbent replay, or set
        ``require_evidence=True`` to raise instead of returning a prior-shaped number.
        ``pass_prob_evidence()`` says which tasks are prior-only.
        """
        probs = []
        prior_only = []
        for t in self.protected_tasks():
            explicit = (pass_probs or {}).get(t)
            if explicit is None and self._tasks[t].inc_runs <= self.k_stability:
                prior_only.append(t)
            p = explicit if explicit is not None else self.estimated_pass_prob(t)
            if p is not None:
                probs.append(p)
        if require_evidence and prior_only:
            raise ValueError(
                f"no independent incumbent evidence for {len(prior_only)} protected task(s) "
                f"(e.g. {prior_only[:3]}): their pass probability is the ({self.k_stability}+1)/"
                f"({self.k_stability}+2) prior, so the result would be a function of k alone. "
                f"Pass explicit pass_probs or replay the incumbent beyond the membership window."
            )
        return false_block_rate(probs, self.k_stability, self.k_confirm, self.n_confirm, membership_given=membership_given)

    # ---- serialisation -------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "ProtectedCorpus",
            "k_stability": self.k_stability,
            "k_confirm": self.k_confirm,
            "n_confirm": self.n_confirm,
            "min_protected": self.min_protected,
            "tasks": {
                t: {
                    "inc_runs": r.inc_runs,
                    "inc_passes": r.inc_passes,
                    "cand_runs": r.cand_runs,
                    "cand_fails": r.cand_fails,
                    "inc_recent": list(r.inc_recent),
                    "cand_window": list(r.cand_window),
                }
                for t, r in sorted(self._tasks.items())
            },
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProtectedCorpus":
        if d.get("kind") not in (None, "ProtectedCorpus"):
            raise ValueError(f"cannot restore ProtectedCorpus from a {d.get('kind')!r} record")
        c = cls(d["k_stability"], d["k_confirm"], d.get("n_confirm"), min_protected=int(d.get("min_protected", 1)))
        for t, r in d.get("tasks", {}).items():
            c._tasks[t] = _TaskRecord(
                inc_runs=int(r["inc_runs"]),
                inc_passes=int(r["inc_passes"]),
                cand_runs=int(r["cand_runs"]),
                cand_fails=int(r["cand_fails"]),
                inc_recent=[int(x) for x in r["inc_recent"]],
                # Legacy records (no frozen window) reconstruct the most conservative
                # window consistent with the counts: fails first, capped at n_confirm.
                cand_window=[int(x) for x in r["cand_window"]]
                if "cand_window" in r
                else ([0] * min(int(r["cand_fails"]), c.n_confirm) + [1] * max(0, min(int(r["cand_runs"]), c.n_confirm) - int(r["cand_fails"])))[: c.n_confirm],
            )
        return c


#: Alias: the floor *is* the protected corpus plus its confirmation rule.
TaskFloor = ProtectedCorpus


@dataclass(frozen=True)
class TransferReport:
    """Retention (BWT) and adaptation (FWT) reported separately (EvoHarnessBench)."""

    bwt: float | None  # mean (candidate - incumbent) over protected tasks
    fwt: float | None  # mean (candidate - incumbent) over target tasks
    n_protected: int
    n_target: int
    protected_regressions: int  # protected tasks where candidate < incumbent
    target_improvements: int  # target tasks where candidate > incumbent

    def as_dict(self) -> dict[str, Any]:
        return {
            "bwt": self.bwt,
            "fwt": self.fwt,
            "n_protected": self.n_protected,
            "n_target": self.n_target,
            "protected_regressions": self.protected_regressions,
            "target_improvements": self.target_improvements,
        }


def transfer_report(
    protected: Sequence[tuple[float, float]],
    target: Sequence[tuple[float, float]],
) -> TransferReport:
    """``protected`` / ``target`` are per-task ``(incumbent_score, candidate_score)`` pairs."""

    def _mean_delta(pairs: Sequence[tuple[float, float]]) -> float | None:
        return sum(c - i for i, c in pairs) / len(pairs) if pairs else None

    return TransferReport(
        bwt=_mean_delta(protected),
        fwt=_mean_delta(target),
        n_protected=len(protected),
        n_target=len(target),
        protected_regressions=sum(1 for i, c in protected if c < i),
        target_improvements=sum(1 for i, c in target if c > i),
    )
