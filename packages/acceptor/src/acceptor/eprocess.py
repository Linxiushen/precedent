# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Anytime-valid paired acceptance gates built on test (super)martingales.

Statistical background
----------------------
A *test supermartingale* for a null hypothesis H0 is a nonnegative process
``M_0 = 1, M_1, M_2, ...`` adapted to the data filtration ``F_t`` with

    E[M_{t+1} | F_t] <= M_t     under every P in H0.

Ville's inequality (Ville 1939) then gives, for any alpha in (0, 1],

    P( sup_t M_t >= 1/alpha ) <= alpha     under H0,

so the rule "commit the first time M_t >= 1/alpha" has type-I error at most alpha
*at any data-dependent stopping time*, including stopping after every single pair
and resuming days later.  ``M_t`` is an e-process ("e-value"); ``min(1, 1/max_s<=t M_s)``
is an anytime-valid p-value.  This is the machinery behind PACE's paired commit gate
(arXiv 2606.08106), SEA's harness-edit certificates (arXiv 2607.00871), Howard,
Ramdas, McAuliffe & Sekhon's confidence sequences (Ann. Stat. 2021) and
Waudby-Smith & Ramdas's betting confidence intervals (JRSS-B 2024).

Testing by betting
------------------
Every gate here is a *betting* martingale: starting from wealth 1, on each paired
observation we wager a *predictable* fraction ``lambda_t in [0, lambda_max]`` (chosen
from data strictly before t) on the candidate being better:

    M_t = M_{t-1} * (1 + lambda_t * g_t),

where ``g_t`` is a centred payoff with ``E[g_t | F_{t-1}] <= 0`` under H0 and a known
worst-case ``g_min < 0``.  Nonnegativity needs ``lambda_t <= 1/|g_min|`` (the factor is
then >= 0 even on the worst outcome); the supermartingale inequality follows from
``E[1 + lambda_t g_t | F_{t-1}] = 1 + lambda_t E[g_t | F_{t-1}] <= 1``.  Validity does
not depend on *how* ``lambda_t`` is chosen, only on predictability and the range;
the choice only affects power.  Three choices are offered:

* ``"fixed"``  -- a constant fraction (PACE's default 0.5).
* ``"mixture"`` -- an equal-weight average of fixed-fraction martingales over a grid;
  an average of test supermartingales is a test supermartingale (the mixture method
  of Robbins / Howard et al.), and it is never much worse than the best grid point.
* ``"ons"`` / ``"agrapa"`` -- adaptive fractions (Online Newton Step of Cutkosky &
  Orabona 2018 as used by Waudby-Smith & Ramdas 2024; approximate GRAPA, ibid.),
  each clipped to ``[0, cap]`` with ``cap < 1/|g_min|``.

Two gates are provided:

``PairedBinaryGate``
    Paired 0/1 outcomes (incumbent, candidate) on identical instances.  Ties carry no
    information about *which* is better and are discarded (McNemar's argument).  On a
    discordant pair let ``w = 1`` if the candidate was right and the incumbent wrong.
    H0: P(w = 1 | discordant, F_{t-1}) <= 1/2, i.e. the candidate is not better.
    Payoff ``g = 2w - 1 in {-1, +1}``, so ``lambda_max = 1``.

``PairedBoundedGate``
    Paired continuous differences ``d = score_cand - score_inc in [-1, 1]`` (normalised
    score, step or token deltas).  H0: E[d_t | F_{t-1}] <= mu0 (default mu0 = 0, i.e.
    the candidate's mean delta is not positive).  Payoff ``g = d - mu0`` with
    ``g_min = -1 - mu0``, so ``lambda_max = 1/(1 + mu0)`` -- the Waudby-Smith & Ramdas
    bet for a bounded mean, shifted to [-1, 1].

Both optionally run a second martingale for the *harm* null

    H0': E[d_t | F_{t-1}] >= harm_mu0      (the candidate is not worse than harm_mu0),

with payoff ``harm_mu0 - d`` and ``lambda_max' = 1/(1 - harm_mu0)``; crossing
``1/harm_alpha`` flags a regression early and moves the gate to ``REJECT`` (reason
``harm_detected``), even after an earlier ``ACCEPT`` -- this is the post-acceptance
canary.  The two tests carry separate error budgets (alpha and harm_alpha); their union
is bounded by the sum.

``harm_mu0`` defaults to **0** and is deliberately *not* tied to the superiority margin
``mu0``.  Anchoring the harm test at ``mu0`` (as this module did before) makes H0'
"E[d] >= mu0", so a candidate exactly as good as the incumbent is in the *alternative*
of the harm test and gets flagged: measured harm rates for an E[d] = 0 stream at n = 200
were 2.1 % at mu0 = 0, 48.4 % at mu0 = 0.1 and 98.8 % at mu0 = 0.2.  The two nulls are
now independent statements: "not better by mu0" and "not worse than harm_mu0".

What the guarantee requires of the *data stream*
------------------------------------------------
Predictable betting is enforced in code; the rest is a property of how instances are
chosen, and no gate can check it for you.  For ``E[g_t | F_{t-1}] <= 0`` to hold:

* **Instances must be selected independently of either arm's outcome on them.**  Feeding
  only instances the incumbent failed makes the candidate win by construction (ACCEPT
  100 % for an equal-quality candidate in simulation); feeding only instances the
  incumbent passed makes the harm martingale fire 100 %.  In particular, **never feed
  the protected corpus** (``floor.ProtectedCorpus``, which is by definition the set of
  instances the incumbent passes) to a gate: the floor and the gate consume disjoint
  streams.
* **The order must be fixed before the outcomes are known.**  Sorting a fixed set of 40
  pairs wins-first raises false ACCEPT from ~2 % to 66-89 %.  ``bind_instances()`` plus
  ``instances.InstanceSampler`` make the acceptor own that order and turn a violation
  into a ``ValueError``; ``evidence_hash`` chains every fed payoff so a certificate pins
  the exact sequence that produced the wealth.
* **The wealth process may be stopped, but never rewound.**  Anytime validity covers
  optional *stopping*, not restoring an earlier snapshot after a losing batch
  (~100 % false ACCEPT in simulation).  ``evidence_hash`` and ``n_pairs`` are in
  ``summary()`` precisely so ``ledger.Ledger`` can refuse a certificate whose evidence
  went backwards.
* **Instances should be fresh with respect to the proposer.**  Pre-selecting the best of
  K candidates on a fixed dev set gives 31-33 % false ACCEPT at K = 20 versus 1.4 % with
  fresh instances.  ``InstanceSampler`` rotates and reports exposure; ``min_informative``
  refuses to decide on a handful of hand-picked instances.

``PairedBinaryGate.summary()`` reports ``selection_suspected`` when the incumbent passed
either all or none of >= 10 fed instances -- the fingerprint of a filtered stream.  It is
a heuristic: a genuinely uniform slice of instances looks the same.

Decision states
---------------
``CONTINUE``  -- keep collecting pairs.
``ACCEPT``    -- wealth reached ``1/alpha``.
``REJECT``    -- the evaluation budget (``max_pairs``) was exhausted without acceptance,
                 or the harm martingale fired.
``NSF``       -- "no sufficient funds": even if every remaining budgeted pair were a
                 maximal win the wealth could not reach ``1/alpha`` (futility stop).
                 Stopping early can only lower the crossing probability, so this
                 keeps the alpha guarantee.

All state is plain JSON (``to_dict``/``from_dict``); wealth round-trips exactly because
Python serialises floats with shortest-round-trip repr.  Everything is deterministic --
the gates draw no randomness.  Wealth is clamped at ``WEALTH_CAP`` = 1e300 so a long
post-acceptance canary cannot overflow to ``inf`` (and then to ``NaN`` on the next loss,
which would make the certificate unserialisable).  Clamping is safe: ``min(M_t, C)`` is a
nonnegative supermartingale whenever ``M_t`` is, because ``x -> min(x, C)`` is concave
and nondecreasing, so ``E[min(M_{t+1}, C) | F_t] <= min(E[M_{t+1} | F_t], C) <= min(M_t, C)``.
It can only make the gate more conservative.
"""

from __future__ import annotations

import hashlib
import json
import math
from enum import Enum
from typing import Any, Iterable, Sequence

__all__ = [
    "GateState",
    "PairedBinaryGate",
    "PairedBoundedGate",
    "BETTING_STRATEGIES",
    "OrderViolation",
    "WEALTH_CAP",
    "load_gate",
]

BETTING_STRATEGIES = ("fixed", "mixture", "ons", "agrapa")
#: Wealth ceiling; see the module docstring for why clamping preserves validity.
WEALTH_CAP = 1e300
#: Starting value of the evidence chain (no pairs fed yet).
GENESIS_EVIDENCE = "0" * 64
#: Serialisation format version of gate state.  1 = pre-harm_mu0 (harm anchored at mu0).
STATE_VERSION = 2
_SUPPORTED_STATE_VERSIONS = (1, 2)
_UNSET: Any = object()


class OrderViolation(ValueError):
    """Raised when a pair is fed for an instance that is not next in the committed order.

    This is an alpha-inflation attempt (or an adapter bug), not a statistical outcome:
    the guarantee is void for a stream the proposer ordered, so the gate refuses the
    datum instead of quietly pricing it in.
    """

# ONS step constant from Cutkosky & Orabona (2018), used verbatim by
# Waudby-Smith & Ramdas (2024, Section 3.2).
_ONS_STEP = 2.0 / (2.0 - math.log(3.0))
# aGRAPA prior weight (pseudo-observations at lam0); affects power only, never validity.
_PRIOR_N = 4.0


class GateState(str, Enum):
    """Decision state of a gate after the latest update (see module docstring)."""

    CONTINUE = "CONTINUE"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    NSF = "NSF"

    @property
    def terminal(self) -> bool:
        return self is not GateState.CONTINUE


class _Bettor:
    """A betting wealth process ``W_t = prod_i (1 + lambda_i g_i)`` with predictable
    ``lambda_i in [0, cap]`` for payoffs ``g in [g_min, g_max]``, ``g_min < 0 < g_max``.

    Under the null ``E[g_t | F_{t-1}] <= 0`` this is a nonnegative supermartingale with
    ``W_0 = 1`` provided ``cap <= 1/|g_min|`` (enforced in ``__init__``).
    """

    def __init__(
        self,
        strategy: str,
        g_min: float,
        g_max: float,
        lam: float = 0.5,
        grid: Sequence[float] | None = None,
        cap: float | None = None,
        lam0: float | None = None,
    ) -> None:
        if strategy not in BETTING_STRATEGIES:
            raise ValueError(f"unknown betting strategy {strategy!r}; choose from {BETTING_STRATEGIES}")
        if not (g_min < 0.0 < g_max):
            raise ValueError("payoff range must straddle zero (g_min < 0 < g_max)")
        self.strategy = strategy
        self.g_min = float(g_min)
        self.g_max = float(g_max)
        self.lam_max = 1.0 / abs(self.g_min)  # nonnegativity bound

        if strategy == "fixed":
            self._check_lambda(lam, allow_equal=True)
            self.lam = float(lam)
            self.grid: list[float] = []
        elif strategy == "mixture":
            grid = list(grid) if grid is not None else []
            if not grid:
                raise ValueError("mixture strategy needs a non-empty lambda grid")
            for g in grid:
                self._check_lambda(g, allow_equal=True)
            self.grid = [float(g) for g in grid]
            self.lam = 0.0
        else:  # ons / agrapa
            cap = 0.5 * self.lam_max if cap is None else float(cap)
            self._check_lambda(cap, allow_equal=False)
            self.cap = cap
            # Warm start: the first bet is a constant (hence predictable), so any value in
            # [0, cap] is valid.  Starting at 0 (as in the textbook ONS) costs ~10 points of
            # power at n = 40 in the bench; cap/2 is the PACE default when cap = 1.
            lam0 = 0.5 * cap if lam0 is None else float(lam0)
            if not (0.0 <= lam0 <= cap):
                raise ValueError("lam0 must lie in [0, cap]")
            self.lam0 = lam0
            self.lam = 0.0
            self.grid = []

        # state
        self.n = 0
        self.wealth = 1.0
        self.max_wealth = 1.0
        self.components: list[float] = [1.0] * len(self.grid) if strategy == "mixture" else []
        # ONS state: current bet and curvature accumulator A_t = 1 + sum z_i^2
        self.ons_lambda = getattr(self, "lam0", 0.0)
        self.ons_A = 1.0
        # aGRAPA state: running sums of g and g^2 (the prior enters as PRIOR_N pseudo-observations)
        self.sum_g = 0.0
        self.sum_g2 = 0.0

    def _check_lambda(self, lam: float, *, allow_equal: bool) -> None:
        lam = float(lam)
        hi_ok = lam <= self.lam_max if allow_equal else lam < self.lam_max
        if not (0.0 <= lam and hi_ok):
            bound = "<=" if allow_equal else "<"
            raise ValueError(
                f"betting fraction {lam} outside the valid range 0 <= lambda {bound} {self.lam_max:.6g} "
                f"(= 1/|g_min|); larger fractions can make wealth negative and break the e-process"
            )

    # ---- betting -------------------------------------------------------------------
    def _current_lambda(self) -> float:
        """Predictable fraction for the *next* payoff (depends on the past only)."""
        if self.strategy == "fixed":
            return self.lam
        if self.strategy == "ons":
            return self.ons_lambda
        if self.strategy == "agrapa":
            # approximate GRAPA (Waudby-Smith & Ramdas 2024): lambda ~ mean(g)/mean(g^2),
            # shrunk towards lam0 by PRIOR_N pseudo-observations.  Any clipped predictable
            # value is valid; the choice only affects power.
            lam = (self.sum_g + _PRIOR_N * self.lam0) / (self.sum_g2 + _PRIOR_N)
            return min(max(lam, 0.0), self.cap)
        return 0.0  # mixture handled per component

    def step(self, g: float) -> None:
        if not (self.g_min - 1e-12 <= g <= self.g_max + 1e-12):
            raise ValueError(f"payoff {g} outside [{self.g_min}, {self.g_max}]")
        g = min(max(g, self.g_min), self.g_max)
        if self.strategy == "mixture":
            # Each component is clamped separately: min(W, C) of a nonnegative
            # supermartingale is one, and an average of supermartingales is one.
            self.components = [min(w * (1.0 + lam * g), WEALTH_CAP) for w, lam in zip(self.components, self.grid)]
            self.wealth = sum(self.components) / len(self.components)
        else:
            lam = self._current_lambda()
            self.wealth = min(self.wealth * (1.0 + lam * g), WEALTH_CAP)
            if self.strategy == "ons":
                # gradient of log(1 + lam g) w.r.t. lam, Newton-style step, clipped to [0, cap]
                z = g / (1.0 + lam * g)
                self.ons_A += z * z
                nxt = lam + _ONS_STEP * z / self.ons_A
                self.ons_lambda = min(max(nxt, 0.0), self.cap)
            elif self.strategy == "agrapa":
                self.sum_g += g
                self.sum_g2 += g * g
        self.n += 1
        if self.wealth > self.max_wealth:
            self.max_wealth = min(self.wealth, WEALTH_CAP)

    def max_wealth_after(self, r: int) -> float:
        """Upper bound on wealth after ``r`` further steps (every payoff = g_max)."""
        if r <= 0:
            return self.wealth
        try:
            if self.strategy == "mixture":
                return sum(w * (1.0 + lam * self.g_max) ** r for w, lam in zip(self.components, self.grid)) / len(self.grid)
            lam = self.lam if self.strategy == "fixed" else self.cap
            return self.wealth * (1.0 + lam * self.g_max) ** r
        except OverflowError:  # a huge budget cannot make the futility stop fire
            return math.inf

    # ---- serialisation -------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "strategy": self.strategy,
            "g_min": self.g_min,
            "g_max": self.g_max,
            "n": self.n,
            "wealth": self.wealth,
            "max_wealth": self.max_wealth,
        }
        if self.strategy == "fixed":
            d["lam"] = self.lam
        elif self.strategy == "mixture":
            d["grid"] = self.grid
            d["components"] = self.components
        else:
            d["cap"] = self.cap
            d["lam0"] = self.lam0
            d["ons_lambda"] = self.ons_lambda
            d["ons_A"] = self.ons_A
            d["sum_g"] = self.sum_g
            d["sum_g2"] = self.sum_g2
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "_Bettor":
        b = cls(
            d["strategy"],
            d["g_min"],
            d["g_max"],
            lam=d.get("lam", 0.5),
            grid=d.get("grid"),
            cap=d.get("cap"),
            lam0=d.get("lam0"),
        )
        b.n = int(d["n"])
        b.wealth = float(d["wealth"])
        b.max_wealth = float(d["max_wealth"])
        if b.strategy == "mixture":
            b.components = [float(x) for x in d["components"]]
        elif b.strategy in ("ons", "agrapa"):
            b.ons_lambda = float(d["ons_lambda"])
            b.ons_A = float(d["ons_A"])
            b.sum_g = float(d["sum_g"])
            b.sum_g2 = float(d["sum_g2"])
        return b


def _resolve_betting_params(
    betting: str,
    lam: Any,
    grid: Sequence[float] | None,
    cap: float | None,
    lam0: float | None,
    default_lam: float = 0.5,
) -> tuple[float, Sequence[float] | None, float | None, float | None]:
    """Reject parameters the chosen strategy does not use, instead of ignoring them.

    ``PairedBinaryGate(betting="fixed", cap=0.1, grid=(0.9,))`` used to construct a gate
    betting 0.5 flat and silently drop the rest, so a mis-specified gate looked healthy.
    """
    used = {"fixed": {"lam"}, "mixture": {"grid"}, "ons": {"cap", "lam0"}, "agrapa": {"cap", "lam0"}}[betting]
    given = {"lam": lam is not _UNSET, "grid": grid is not None, "cap": cap is not None, "lam0": lam0 is not None}
    extra = sorted(k for k, was_given in given.items() if was_given and k not in used)
    if extra:
        raise ValueError(
            f"betting={betting!r} does not use {', '.join(extra)} (it uses {', '.join(sorted(used))}); "
            f"silently ignoring them would hide a mis-specified gate"
        )
    return (default_lam if lam is _UNSET else float(lam)), grid, cap, lam0


def _betting_kwargs(betting: str, bettor: dict[str, Any]) -> dict[str, Any]:
    """The subset of (lam, grid, cap, lam0) a strategy uses, read back from gate state."""
    if betting == "fixed":
        return {"lam": bettor.get("lam", 0.5)}
    if betting == "mixture":
        return {"grid": bettor.get("grid")}
    return {"cap": bettor.get("cap"), "lam0": bettor.get("lam0")}


class _PairedGateBase:
    """Shared machinery: alpha, budget, decision bookkeeping, harm martingale, JSON."""

    _kind = "base"

    def __init__(
        self,
        alpha: float,
        betting: str,
        lam: float,
        grid: Sequence[float] | None,
        cap: float | None,
        max_pairs: int | None,
        harm_alpha: float | None,
        g_min: float,
        g_max: float,
        lam0: float | None = None,
        harm_mu0: float = 0.0,
        min_informative: int = 0,
        min_pairs: int = 0,
    ) -> None:
        if not (0.0 < alpha < 1.0):
            raise ValueError("alpha must lie in (0, 1)")
        if max_pairs is not None and max_pairs < 1:
            raise ValueError("max_pairs must be >= 1 or None")
        if harm_alpha is not None and not (0.0 < harm_alpha < 1.0):
            raise ValueError("harm_alpha must lie in (0, 1) or be None")
        if not (-1.0 < float(harm_mu0) < 1.0):
            raise ValueError("harm_mu0 must lie strictly inside (-1, 1)")
        if min_informative < 0 or min_pairs < 0:
            raise ValueError("min_informative and min_pairs must be >= 0")
        self.alpha = float(alpha)
        self.betting = betting
        self.max_pairs = max_pairs
        self.harm_alpha = harm_alpha
        self.harm_mu0 = float(harm_mu0)
        self.min_informative = int(min_informative)
        self.min_pairs = int(min_pairs)
        self._bettor = _Bettor(betting, g_min, g_max, lam=lam, grid=grid, cap=cap, lam0=lam0)
        # The harm martingale tests H0': E[d] >= harm_mu0 with payoff (harm_mu0 - d), whose
        # range is [harm_mu0 - 1, harm_mu0 + 1]; lambda_max' = 1/(1 - harm_mu0).  We bet the
        # same *fraction of the admissible range* on both sides, so one lam/grid/cap setting
        # is valid for both.
        self._harm = None
        if harm_alpha:
            hg_min, hg_max = self.harm_mu0 - 1.0, self.harm_mu0 + 1.0
            scale = (1.0 / abs(hg_min)) / (1.0 / abs(g_min))
            hgrid = None if grid is None else [x * scale for x in grid]
            hcap = None if cap is None else cap * scale
            hlam0 = None if lam0 is None else lam0 * scale
            self._harm = _Bettor(betting, hg_min, hg_max, lam=lam * scale, grid=hgrid, cap=hcap, lam0=hlam0)
        self.n_pairs = 0  # every pair fed, including ties
        self.state = GateState.CONTINUE
        self.reason = ""
        self.decided_at: int | None = None
        self.accepted_at: int | None = None  # first crossing; never overwritten by the canary
        self.wealth_at_decision: float | None = None
        self.harm_detected = False
        self.harm_detected_at: int | None = None
        # Evidence chain: sha256 over (instance_id, payoff) in the order actually fed.
        # Two gate states with the same evidence_hash saw the same data in the same order;
        # a state whose evidence_hash is not an extension of the last certified one has
        # been rewound.  See ledger.Ledger for the enforcement.
        self.evidence_hash = GENESIS_EVIDENCE
        # The evidence hash as of the last certificate written from this gate.  A gate
        # restored from an older snapshot carries an older value here, which is what lets
        # the ledger notice a rewind that happens to replay the same number of pairs.
        self.last_certified_evidence = GENESIS_EVIDENCE
        self.instance_set_hash = ""
        self._order: tuple[str, ...] | None = None
        self._cursor = 0
        # Selection diagnostic: incumbent outcomes observed in the fed stream.  Only the
        # binary gate records these -- "the incumbent passed" is not defined for a
        # continuous delta, and a one-signed delta stream is normal for a real improvement.
        self.n_inc_obs = 0
        self.n_inc_pass = 0

    # ---- instance-order commitment ---------------------------------------------------
    def bind_instances(self, order: Any) -> "_PairedGateBase":
        """Commit the evaluation order (an ``InstanceOrder`` or a sequence of ids).

        Once bound, ``update(pair, instance_id=...)`` must supply exactly the next id in
        the committed order; anything else raises ``OrderViolation``.  Bind *before* the
        first pair and derive the order from the candidate's content hash (see
        ``instances.InstanceSampler``) so the proposer cannot choose it after seeing
        outcomes.
        """
        if self.n_pairs:
            raise ValueError("bind_instances() must be called before any pair is fed")
        ids = getattr(order, "instance_ids", None)
        if ids is None:
            ids = tuple(str(i) for i in order)
            from .instances import set_hash

            self.instance_set_hash = set_hash(ids)
        else:
            ids = tuple(str(i) for i in ids)
            self.instance_set_hash = getattr(order, "instance_set_hash", "")
        if len(set(ids)) != len(ids):
            raise ValueError("committed instance order contains duplicates")
        self._order = ids
        self._cursor = 0
        return self

    @property
    def bound(self) -> bool:
        return self._order is not None

    @property
    def next_instance(self) -> str | None:
        if self._order is None or self._cursor >= len(self._order):
            return None
        return self._order[self._cursor]

    def _check_instance(self, instance_id: str | None) -> str:
        if self._order is None:
            return "" if instance_id is None else str(instance_id)
        if instance_id is None:
            raise OrderViolation("gate is bound to a committed instance order; pass instance_id=")
        if self._cursor >= len(self._order):
            raise OrderViolation(f"committed order of {len(self._order)} instances is exhausted")
        expected = self._order[self._cursor]
        if str(instance_id) != expected:
            raise OrderViolation(
                f"instance {instance_id!r} is not next in the committed order (expected {expected!r}); "
                f"re-ordering the stream inflates alpha (66-89 % false ACCEPT in simulation)"
            )
        self._cursor += 1
        return expected

    # ---- accessors -----------------------------------------------------------------
    @property
    def wealth(self) -> float:
        """Current wealth ``M_t`` (an e-value against H0)."""
        return self._bettor.wealth

    @property
    def e_value(self) -> float:
        return self._bettor.wealth

    @property
    def max_wealth(self) -> float:
        return self._bettor.max_wealth

    @property
    def p_value(self) -> float:
        """Anytime-valid p-value ``min(1, 1/max_{s<=t} M_s)`` (Ville)."""
        return min(1.0, 1.0 / self._bettor.max_wealth)

    @property
    def threshold(self) -> float:
        return 1.0 / self.alpha

    @property
    def harm_wealth(self) -> float | None:
        return self._harm.wealth if self._harm is not None else None

    @property
    def n_informative(self) -> int:
        """Number of pairs that moved the wealth process (discordant pairs / deltas)."""
        return self._bettor.n

    @property
    def remaining_budget(self) -> int | None:
        return None if self.max_pairs is None else max(0, self.max_pairs - self.n_pairs)

    # ---- decision logic ------------------------------------------------------------
    @property
    def _accept_conditions_met(self) -> bool:
        """Wealth crossed *and* the decision rests on enough evidence.

        Requiring ``n_informative >= min_informative`` only ever *delays* the stop, and
        ``{exists t: M_t >= 1/alpha and n_t >= m} subset {exists t: M_t >= 1/alpha}``, so
        Ville's bound still applies -- this is a valid way to refuse a decision resting
        on 8 hand-picked instances.  It limits *fragility*, not overfitting: a proposer
        with enough hand-picked instances can still satisfy it.
        """
        return (
            self._bettor.wealth >= self.threshold
            and self.n_informative >= self.min_informative
            and self.n_pairs >= self.min_pairs
        )

    def _decide(self) -> GateState:
        # Harm detection has priority and may override an earlier ACCEPT (canary).
        if self._harm is not None and not self.harm_detected and self._harm.wealth >= 1.0 / self.harm_alpha:
            self.harm_detected = True
            self.harm_detected_at = self.n_pairs
            self.state = GateState.REJECT
            # The original acceptance round is kept in ``accepted_at``; ``decided_at``
            # tracks the round of the state currently in force.
            self.reason = "harm_detected" if self.accepted_at is None else "harm_detected_after_decision"
            self.decided_at = self.n_pairs
            return self.state
        if self.state.terminal:
            return self.state
        if self._accept_conditions_met:
            self.state, self.reason, self.decided_at = GateState.ACCEPT, "wealth_reached_threshold", self.n_pairs
            self.accepted_at = self.n_pairs
            self.wealth_at_decision = self._bettor.wealth
        elif self.max_pairs is not None and self.n_pairs >= self.max_pairs:
            self.state, self.reason, self.decided_at = GateState.REJECT, "budget_exhausted", self.n_pairs
        elif self.max_pairs is not None and self._unreachable_within_budget():
            self.state, self.reason, self.decided_at = GateState.NSF, "threshold_unreachable_within_budget", self.n_pairs
        return self.state

    def _unreachable_within_budget(self) -> bool:
        remaining = self.max_pairs - self.n_pairs  # type: ignore[operator]
        if self._bettor.max_wealth_after(remaining) < self.threshold:
            return True
        # min_informative can also become unreachable: every remaining pair informative
        # still leaves fewer than the required number.
        return self.n_informative + remaining < self.min_informative

    def _chain(self, instance_id: str, g: float | None) -> None:
        h = hashlib.sha256()
        h.update(self.evidence_hash.encode("utf-8"))
        h.update(b"\x00")
        h.update(instance_id.encode("utf-8"))
        h.update(b"\x00")
        h.update(("tie" if g is None else repr(float(g))).encode("utf-8"))
        self.evidence_hash = h.hexdigest()

    def _feed(self, g: float | None, instance_id: str = "", g_harm: float | None = None) -> GateState:
        """Feed one centred payoff (``None`` = uninformative tie); return the new state.

        Terminal states are sticky: the improvement decision is the *first* crossing
        (that is the event Ville's inequality bounds).  Pairs fed afterwards keep both
        martingales running -- the harm martingale in particular acts as a
        post-acceptance canary and can still move an ``ACCEPT`` to ``REJECT``.
        """
        self.n_pairs += 1
        self._chain(instance_id, g)
        if g is not None:
            self._bettor.step(g)
        if self._harm is not None and g_harm is not None:
            self._harm.step(g_harm)
        return self._decide()

    def update_many(self, pairs: Iterable[Any], instance_ids: Iterable[str] | None = None) -> GateState:
        """Feed a sequence of pairs; returns the final state (does not stop early).

        ``instance_ids`` is required when the gate is bound to a committed order.
        """
        st = self.state
        if instance_ids is None:
            for p in pairs:
                st = self.update(p)
            return st
        for p, iid in zip(pairs, instance_ids):
            st = self.update(p, instance_id=iid)
        return st

    def update(self, pair: Any, instance_id: str | None = None) -> GateState:  # pragma: no cover - abstract
        raise NotImplementedError

    # ---- serialisation -------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self._kind,
            "version": STATE_VERSION,
            "alpha": self.alpha,
            "betting": self.betting,
            "max_pairs": self.max_pairs,
            "harm_alpha": self.harm_alpha,
            "harm_mu0": self.harm_mu0,
            "min_informative": self.min_informative,
            "min_pairs": self.min_pairs,
            "n_pairs": self.n_pairs,
            "state": self.state.value,
            "reason": self.reason,
            "decided_at": self.decided_at,
            "accepted_at": self.accepted_at,
            "wealth_at_decision": self.wealth_at_decision,
            "harm_detected": self.harm_detected,
            "harm_detected_at": self.harm_detected_at,
            "evidence_hash": self.evidence_hash,
            "last_certified_evidence": self.last_certified_evidence,
            "instance_set_hash": self.instance_set_hash,
            "order": list(self._order) if self._order is not None else None,
            "cursor": self._cursor,
            "n_inc_obs": self.n_inc_obs,
            "n_inc_pass": self.n_inc_pass,
            "bettor": self._bettor.to_dict(),
            "harm": self._harm.to_dict() if self._harm is not None else None,
        }

    @staticmethod
    def _check_version(d: dict[str, Any]) -> int:
        v = int(d.get("version", 1))
        if v not in _SUPPORTED_STATE_VERSIONS:
            raise ValueError(f"gate state version {v} is not supported (this build reads {_SUPPORTED_STATE_VERSIONS})")
        return v

    def _restore(self, d: dict[str, Any]) -> None:
        if d.get("kind") != self._kind:
            raise ValueError(f"cannot restore {self._kind} from a {d.get('kind')!r} record")
        self._check_version(d)
        self.n_pairs = int(d["n_pairs"])
        self.state = GateState(d["state"])
        self.reason = str(d.get("reason", ""))
        self.decided_at = d.get("decided_at")
        self.accepted_at = d.get("accepted_at", self.decided_at if self.state is GateState.ACCEPT else None)
        self.wealth_at_decision = d.get("wealth_at_decision")
        self.harm_detected = bool(d.get("harm_detected", False))
        self.harm_detected_at = d.get("harm_detected_at", self.decided_at if self.harm_detected else None)
        self.evidence_hash = str(d.get("evidence_hash", GENESIS_EVIDENCE))
        self.last_certified_evidence = str(d.get("last_certified_evidence", GENESIS_EVIDENCE))
        self.instance_set_hash = str(d.get("instance_set_hash", ""))
        order = d.get("order")
        self._order = tuple(str(i) for i in order) if order else None
        self._cursor = int(d.get("cursor", 0))
        self.n_inc_obs = int(d.get("n_inc_obs", 0))
        self.n_inc_pass = int(d.get("n_inc_pass", 0))
        self._bettor = _Bettor.from_dict(d["bettor"])
        self._harm = _Bettor.from_dict(d["harm"]) if d.get("harm") else None

    def to_json(self, **kw: Any) -> str:
        kw.setdefault("sort_keys", True)
        return json.dumps(self.to_dict(), **kw)

    @classmethod
    def from_json(cls, s: str):
        return cls.from_dict(json.loads(s))

    def mark_certified(self) -> str:
        """Record that a certificate is being written now; return the *previous* mark.

        ``ledger.Certificate.from_gate`` calls this and stores the returned value as
        ``prev_evidence_hash``, so the ledger can check that each certificate continues
        the evidence the last one ended on.  Rewinding to an old snapshot restores an old
        mark, and the mismatch is then a ``LedgerViolation`` instead of free alpha.
        """
        prev = self.last_certified_evidence
        self.last_certified_evidence = self.evidence_hash
        return prev

    @property
    def selection_suspected(self) -> bool:
        """The incumbent passed all, or none, of >= 10 fed instances.

        That is the fingerprint of a stream filtered on the incumbent's cached outcome
        ("show me it fixes the failures", "run the regression corpus through the gate"),
        under which the conditional null is false and the e-value means nothing.  It is a
        heuristic, not a proof: a genuinely hard or genuinely easy slice looks the same.
        """
        if self.n_inc_obs < 10:
            return False
        return self.n_inc_pass == 0 or self.n_inc_pass == self.n_inc_obs

    def summary(self) -> dict[str, Any]:
        """Compact, JSON-friendly snapshot for certificates and logs."""
        return {
            "state": self.state.value,
            "reason": self.reason,
            "alpha": self.alpha,
            "betting": self.betting,
            "n_pairs": self.n_pairs,
            "n_informative": self.n_informative,
            "wealth": self.wealth,
            "max_wealth": self.max_wealth,
            "p_value": self.p_value,
            "harm_wealth": self.harm_wealth,
            "harm_detected": self.harm_detected,
            "harm_detected_at": self.harm_detected_at,
            "accepted_at": self.accepted_at,
            "wealth_at_decision": self.wealth_at_decision,
            "remaining_budget": self.remaining_budget,
            "evidence_hash": self.evidence_hash,
            "last_certified_evidence": self.last_certified_evidence,
            "instance_set_hash": self.instance_set_hash,
            "selection_suspected": self.selection_suspected,
        }


class PairedBinaryGate(_PairedGateBase):
    """PACE-style testing-by-betting on paired binary outcomes.

    H0 (no improvement): for every discordant pair, conditional on the past,
    ``P(candidate right & incumbent wrong) <= P(incumbent right & candidate wrong)``,
    i.e. ``q_t := P(w_t = 1 | discordant, F_{t-1}) <= 1/2``.

    Test martingale: ``M_t = prod (1 + lambda_i (2 w_i - 1))`` over discordant pairs with
    predictable ``lambda_i in [0, 1]``; ``E[1 + lambda (2w - 1) | F] = 1 + lambda (2q - 1) <= 1``
    under H0 and the factor is in ``[1 - lambda, 1 + lambda] >= 0``.  Ties (both right or
    both wrong) are discarded: they are ancillary for the comparison, exactly as in
    McNemar's test (PACE arXiv 2606.08106, Sec. 3).

    Parameters
    ----------
    alpha : per-candidate type-I error; commit when wealth >= 1/alpha (Ville).
    betting : ``"fixed"`` (PACE default lambda = 0.5, factor 1.5 / 0.5),
        ``"mixture"`` (grid; default (0.2, 0.4, 0.6, 0.8) which equals the
        ``q in {0.6, 0.7, 0.8, 0.9}`` likelihood-ratio grid of research/tools/gate_power_sim.py
        via ``lambda = 2q - 1``), ``"ons"`` or ``"agrapa"`` (adaptive, clipped to ``[0, cap]``,
        default cap 0.5, warm-started at ``lam0`` = cap/2).  Parameters the chosen strategy
        does not use are rejected rather than silently ignored.

        Measured power (``python -m acceptor.bench --regime planted --k 0 --seeds 2000``,
        n = 40, SE ~ 1.1 pt) -- fixed / mixture / ons / agrapa:
        ``--p-inc 0.5 --lift 0.3`` -> 72.5 / 65.8 / 54.1 / 59.8 %;
        ``--p-inc 0.7 --lift 0.2`` -> 43.5 / 43.2 / 29.5 / 33.1 %.
    max_pairs : evaluation budget in pairs (ties included); ``None`` = unbounded.
    harm_alpha : if given, run the second martingale for H0' ``E[2w - 1] >= harm_mu0``
        and move to ``REJECT(harm_detected)`` when it reaches ``1/harm_alpha``.
    harm_mu0 : harm margin (default 0: "not worse at all").
    min_informative, min_pairs : refuse to ACCEPT before this many discordant pairs /
        pairs have been seen.  Delaying a stop never inflates alpha (see
        ``_accept_conditions_met``); it stops a decision resting on a handful of
        hand-picked instances.

    Example
    -------
    >>> g = PairedBinaryGate(alpha=0.05, betting="mixture", max_pairs=40)
    >>> for pair in [(0, 1)] * 12:
    ...     st = g.update(pair)
    >>> st.value, round(g.wealth, 2) >= 20
    ('ACCEPT', True)
    """

    _kind = "PairedBinaryGate"
    DEFAULT_GRID = (0.2, 0.4, 0.6, 0.8)

    def __init__(
        self,
        alpha: float = 0.05,
        betting: str = "fixed",
        lam: float = _UNSET,
        grid: Sequence[float] | None = None,
        cap: float | None = None,
        max_pairs: int | None = None,
        harm_alpha: float | None = None,
        lam0: float | None = None,
        harm_mu0: float = 0.0,
        min_informative: int = 0,
        min_pairs: int = 0,
    ) -> None:
        if betting not in BETTING_STRATEGIES:
            raise ValueError(f"unknown betting strategy {betting!r}; choose from {BETTING_STRATEGIES}")
        lam, grid, cap, lam0 = _resolve_betting_params(betting, lam, grid, cap, lam0)
        if betting == "mixture" and grid is None:
            grid = self.DEFAULT_GRID
        super().__init__(
            alpha,
            betting,
            lam,
            grid,
            cap,
            max_pairs,
            harm_alpha,
            g_min=-1.0,
            g_max=1.0,
            lam0=lam0,
            harm_mu0=harm_mu0,
            min_informative=min_informative,
            min_pairs=min_pairs,
        )
        self.n_wins = 0  # candidate right, incumbent wrong
        self.n_losses = 0  # incumbent right, candidate wrong
        self.n_ties = 0

    @property
    def n_discordant(self) -> int:
        return self.n_wins + self.n_losses

    def update(self, pair: Sequence[int], instance_id: str | None = None) -> GateState:
        """Feed one paired outcome ``(y_incumbent, y_candidate)`` with entries in {0, 1}.

        ``instance_id`` names the instance the pair came from; it is required (and checked
        against the committed order) once ``bind_instances`` has been called, and is
        chained into ``evidence_hash`` either way.
        """
        y_inc, y_cand = pair
        if y_inc not in (0, 1, False, True) or y_cand not in (0, 1, False, True):
            raise ValueError("binary gate expects outcomes in {0, 1}")
        iid = self._check_instance(instance_id)
        y_inc, y_cand = int(y_inc), int(y_cand)
        self.n_inc_obs += 1
        self.n_inc_pass += y_inc
        if y_inc == y_cand:
            self.n_ties += 1
            return self._feed(None, iid, None)
        if y_cand > y_inc:
            self.n_wins += 1
            return self._feed(1.0, iid, self.harm_mu0 - 1.0)
        self.n_losses += 1
        return self._feed(-1.0, iid, self.harm_mu0 + 1.0)

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update({"n_wins": self.n_wins, "n_losses": self.n_losses, "n_ties": self.n_ties})
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PairedBinaryGate":
        cls._check_version(d)
        b = d["bettor"]
        g = cls(
            alpha=d["alpha"],
            betting=d["betting"],
            max_pairs=d.get("max_pairs"),
            harm_alpha=d.get("harm_alpha"),
            harm_mu0=d.get("harm_mu0", 0.0),
            min_informative=int(d.get("min_informative", 0)),
            min_pairs=int(d.get("min_pairs", 0)),
            **_betting_kwargs(d["betting"], b),
        )
        g._restore(d)
        g.n_wins, g.n_losses, g.n_ties = int(d["n_wins"]), int(d["n_losses"]), int(d["n_ties"])
        return g

    def summary(self) -> dict[str, Any]:
        s = super().summary()
        s.update({"n_wins": self.n_wins, "n_losses": self.n_losses, "n_ties": self.n_ties, "n_discordant": self.n_discordant})
        return s


class PairedBoundedGate(_PairedGateBase):
    """Betting martingale for the mean of paired bounded differences in [-1, 1].

    H0 (no improvement): ``E[d_t | F_{t-1}] <= mu0`` for every t, where
    ``d_t = score_cand - score_inc in [-1, 1]`` (default ``mu0 = 0``).

    Test martingale (Waudby-Smith & Ramdas 2024, the capital process ``K_t(m)`` of their
    betting-based bounded-mean confidence sequences, shifted from [0, 1] to [-1, 1]):
    ``M_t = prod (1 + lambda_i (d_i - mu0))`` with predictable ``lambda_i in [0, 1/(1 + mu0)]``.
    The worst payoff is ``-1 - mu0`` so the factor is nonnegative on that range, and
    ``E[1 + lambda (d - mu0) | F] <= 1`` under H0.  ``mu0`` must lie in (-1, 1).

    Ties (``d = 0``) are *not* discarded: with ``mu0 = 0`` they leave wealth unchanged
    (informative "no difference"), with ``mu0 > 0`` they count against the candidate.

    ``betting``: ``"fixed"`` (default lambda 0.5), ``"mixture"`` (default grid
    (0.05, 0.1, 0.2, 0.4, 0.8) scaled by ``1/(1 + mu0)``), ``"ons"``, ``"agrapa"``
    (cap default ``0.5/(1 + mu0)``).

    ``harm_alpha`` runs a *second, independently anchored* test of
    H0' ``E[d_t | F_{t-1}] >= harm_mu0`` (payoff ``harm_mu0 - d``,
    ``lambda < 1/(1 - harm_mu0)``).  ``harm_mu0`` defaults to 0 and is **not** tied to
    ``mu0``: with the harm test anchored at a superiority margin ``mu0 > 0``, "harm"
    would mean "not better by mu0" and an equal-quality candidate would be flagged as
    harmful 48 % of the time at mu0 = 0.1 and 99 % at mu0 = 0.2.  Set ``harm_mu0`` to a
    small negative number (e.g. -0.02) to allow a tolerated regression band.
    """

    _kind = "PairedBoundedGate"
    DEFAULT_GRID = (0.05, 0.1, 0.2, 0.4, 0.8)

    def __init__(
        self,
        alpha: float = 0.05,
        betting: str = "fixed",
        lam: float = _UNSET,
        grid: Sequence[float] | None = None,
        cap: float | None = None,
        max_pairs: int | None = None,
        harm_alpha: float | None = None,
        mu0: float = 0.0,
        lam0: float | None = None,
        harm_mu0: float = 0.0,
        min_informative: int = 0,
        min_pairs: int = 0,
    ) -> None:
        if betting not in BETTING_STRATEGIES:
            raise ValueError(f"unknown betting strategy {betting!r}; choose from {BETTING_STRATEGIES}")
        if not (-1.0 < mu0 < 1.0):
            raise ValueError("mu0 must lie strictly inside (-1, 1)")
        self.mu0 = float(mu0)
        lam, grid, cap, lam0 = _resolve_betting_params(betting, lam, grid, cap, lam0, default_lam=0.5)
        if betting == "mixture" and grid is None:
            grid = tuple(g / (1.0 + self.mu0) for g in self.DEFAULT_GRID)
        super().__init__(
            alpha,
            betting,
            lam,
            grid,
            cap,
            max_pairs,
            harm_alpha,
            g_min=-1.0 - self.mu0,
            g_max=1.0 - self.mu0,
            lam0=lam0,
            harm_mu0=harm_mu0,
            min_informative=min_informative,
            min_pairs=min_pairs,
        )
        self.sum_delta = 0.0

    @property
    def mean_delta(self) -> float:
        return self.sum_delta / self.n_pairs if self.n_pairs else 0.0

    def update(self, pair: float | Sequence[float], instance_id: str | None = None) -> GateState:
        """Feed one paired observation.

        ``pair`` is either the difference ``d in [-1, 1]`` directly, or a 2-tuple
        ``(score_incumbent, score_candidate)`` with both scores in [0, 1], in which case
        ``d = score_candidate - score_incumbent``.  ``instance_id`` is required once
        ``bind_instances`` has been called.
        """
        if isinstance(pair, (tuple, list)):
            s_inc, s_cand = float(pair[0]), float(pair[1])
            for s in (s_inc, s_cand):
                if not (0.0 <= s <= 1.0):
                    raise ValueError("scores must lie in [0, 1]")
            d = s_cand - s_inc
        else:
            d = float(pair)
        if not (-1.0 - 1e-9 <= d <= 1.0 + 1e-9):
            raise ValueError(f"delta {d} outside [-1, 1]; normalise the metric first")
        iid = self._check_instance(instance_id)
        d = min(max(d, -1.0), 1.0)
        self.sum_delta += d
        return self._feed(d - self.mu0, iid, self.harm_mu0 - d)

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update({"mu0": self.mu0, "sum_delta": self.sum_delta})
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PairedBoundedGate":
        version = cls._check_version(d)
        # Version 1 anchored the harm test at mu0; keep restored v1 states faithful.
        harm_mu0 = d.get("harm_mu0", d.get("mu0", 0.0) if version == 1 else 0.0)
        b = d["bettor"]
        g = cls(
            alpha=d["alpha"],
            betting=d["betting"],
            max_pairs=d.get("max_pairs"),
            harm_alpha=d.get("harm_alpha"),
            mu0=d.get("mu0", 0.0),
            harm_mu0=harm_mu0,
            min_informative=int(d.get("min_informative", 0)),
            min_pairs=int(d.get("min_pairs", 0)),
            **_betting_kwargs(d["betting"], b),
        )
        g._restore(d)
        g.sum_delta = float(d.get("sum_delta", 0.0))
        return g

    def summary(self) -> dict[str, Any]:
        s = super().summary()
        s.update({"mu0": self.mu0, "harm_mu0": self.harm_mu0, "mean_delta": self.mean_delta})
        return s


def load_gate(state: dict[str, Any] | str) -> _PairedGateBase:
    """Restore whichever gate a JSON blob came from, dispatching on ``kind``.

    Callers holding a persisted blob no longer have to know the class up front::

        gate = load_gate(open("gate.json").read())
    """
    d = json.loads(state) if isinstance(state, str) else state
    kinds = {"PairedBinaryGate": PairedBinaryGate, "PairedBoundedGate": PairedBoundedGate}
    cls = kinds.get(d.get("kind"))
    if cls is None:
        raise ValueError(f"unknown gate kind {d.get('kind')!r}; expected one of {sorted(kinds)}")
    return cls.from_dict(d)
