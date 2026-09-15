# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Run-level error-budget schedules for an unbounded stream of candidate decisions.

Why a schedule
--------------
Each candidate is tested by an anytime-valid gate at its own level ``alpha_k``
(Ville: P(false accept of candidate k) <= alpha_k under its H0).  Over a loop that
proposes candidates forever, a union bound then controls the *family-wise* error

    P(any false accept, ever) <= sum_{k>=1} alpha_k =: delta0,

provided the ``alpha_k`` are fixed before candidate k's evidence is examined
(SEA, arXiv 2607.00871, "certificates" under a horizon-free spend schedule).  Because
the union bound is over a *countable* family, the schedule has to be summable; a
uniform ``alpha_k = alpha`` is not.

CTHS schedule (SEA)
-------------------
    delta_k = delta0 / (Z * k * ln^2(k + 1)),        Z := sum_{k>=1} 1 / (k ln^2(k+1)).

``Z`` has no closed form.  Computed here to ~1e-9 by summing 10^6 terms and adding
the Euler-Maclaurin tail ``int_{K+1/2}^inf dx / (x ln^2(x+1)) ~= 1/ln(K + 3/2)``:

    Z = 3.387735532 (natural log)     -- SEA quotes Z ~= 3.39.

Documented pitfalls (verified numerically, see ``tests/test_schedule.py``):

* The naive normaliser ``1/(2 k ln^2(k+1))`` (i.e. Z = 2) over-spends: it sums to
  ``3.3877/2 = 1.694 * delta0``, so a loop using it has FWER up to 1.69 x delta0.
* The series converges *slowly*: the first 10^6 rounds spend only 97.9 % of delta0
  (the tail beyond K is ~ 1/(Z ln K) ~ 0.0724/Z).  This is by design -- the remaining
  2.1 % is reserved for rounds beyond one million -- but it means a finite partial sum
  never reaches delta0 exactly.  ``remaining_budget`` reports the exact remainder.
* Base-2 logarithms give a *different* constant (Z_2 = 1.6276); this module uses the
  natural log throughout.

Alpha-investing (Foster & Stine 2008)
-------------------------------------
An alternative policy that *earns back* budget on each acceptance and therefore
controls the marginal false discovery rate mFDR(eta) rather than FWER:

    W_0 = alpha * eta;   test k at level alpha_k <= W_{k-1} / (1 + W_{k-1});
    candidate accepted (null rejected) -> W_k = W_{k-1} + omega    (omega <= alpha),
    candidate not accepted             -> W_k = W_{k-1} - alpha_k / (1 - alpha_k).

(The classical statement says "reject/accept the null"; the code speaks of the
*candidate*, so ``report(accepted=True)`` -- the candidate passed, the null was
rejected -- is the ``+omega`` branch.)

Use it when the loop is expected to find many real improvements and FWER would starve
it (SEA's ALG4 accepted 0 edits at ~50 tasks and was switched off; synthesis_v2 §3.4).
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["SpendSchedule", "AlphaInvesting", "cths_constant", "Z_CTHS", "Z_NAIVE"]


def cths_constant(terms: int = 1_000_000) -> float:
    """Numerically evaluate ``Z = sum_{k>=1} 1/(k ln^2(k+1))``.

    Partial sum over ``terms`` rounds plus the Euler-Maclaurin/midpoint tail
    ``1/ln(terms + 3/2)``; the neglected remainder is ``O(1/(terms ln^2 terms))``,
    below 1e-8 for ``terms >= 10^6``.
    """
    s = 0.0
    for k in range(1, terms + 1):
        lg = math.log(k + 1)
        s += 1.0 / (k * lg * lg)
    return s + 1.0 / math.log(terms + 1.5)


#: The CTHS normalising constant (natural log), Z = sum 1/(k ln^2(k+1)).
Z_CTHS = 3.387735532
#: The "naive" normaliser 1/(2 k ln^2(k+1)) uses Z = 2 and over-spends by Z_CTHS/2 = 1.694.
Z_NAIVE = 2.0


class SpendSchedule:
    """Horizon-free FWER budget ``delta_k = delta0 / (Z k ln^2(k+1))`` (SEA's CTHS).

    ``next_alpha(k)`` returns the level for candidate ``k`` (1-based) and is pure;
    ``draw()`` returns the next level and advances the internal round counter so a
    resumed loop keeps spending where it left off.  ``remaining_budget()`` is the
    exact infinite-tail remainder ``delta0 * (1 - sum_{k<=spent} 1/(Z k ln^2(k+1)))``.
    State is JSON-serialisable (``to_dict``/``from_dict``).

    Rounds are scarce and the decay is brutal -- after 1000 draws the level is 3.1e-07,
    which needs 37 net discordant wins at lambda = 0.5 and is therefore unreachable
    inside an n = 40 budget, permanently.  A burst of junk proposals that each *consume*
    a round is thus a denial-of-service on the whole loop (and the reason operators
    switch schedules off).  So do not ``draw()`` speculatively: draw only after the
    mechanical screen, content-hash dedup and support checks have passed, or use the
    two-phase form::

        a = sched.reserve()             # level for the next round, not yet spent
        if not candidate_reached_gate:
            sched.release()             # no test ran at that level: the slot is free
        else:
            sched.commit(content_hash)  # now the round is spent

    ``release()`` is sound precisely because no evidence was examined at that level.
    ``commit`` records the content hash so ``committed_hashes`` can be audited against
    the ledger's ``schedule_round`` fields.
    """

    def __init__(
        self,
        delta0: float = 0.05,
        Z: float = Z_CTHS,
        rounds_spent: int = 0,
        pending: float | None = None,
        committed_hashes: dict[str, int] | None = None,
    ) -> None:
        if not (0.0 < delta0 < 1.0):
            raise ValueError("delta0 must lie in (0, 1)")
        if Z <= 0.0:
            raise ValueError("Z must be positive")
        if Z < Z_CTHS - 1e-6:
            raise ValueError(
                f"Z={Z} < {Z_CTHS}: the schedule would sum to {Z_CTHS / Z:.3f} x delta0 and over-spend "
                f"(the naive Z=2 over-spends by {Z_CTHS / 2:.3f}x); use Z >= Z_CTHS"
            )
        self.delta0 = float(delta0)
        self.Z = float(Z)
        self.rounds_spent = int(rounds_spent)
        self._spent = sum(self.next_alpha(k) for k in range(1, self.rounds_spent + 1))
        self._pending: float | None = None if pending is None else float(pending)
        self.committed_hashes: dict[str, int] = dict(committed_hashes or {})

    def next_alpha(self, k: int) -> float:
        """Level for candidate ``k`` (1-based); pure function of ``k``."""
        if k < 1:
            raise ValueError("k is 1-based")
        lg = math.log(k + 1)
        return self.delta0 / (self.Z * k * lg * lg)

    def draw(self) -> float:
        """Advance the schedule by one candidate and return its level."""
        a = self.reserve()
        self.commit()
        return a

    # ---- two-phase draw --------------------------------------------------------------
    def reserve(self) -> float:
        """Level for the next round, held but not yet spent (idempotent while pending)."""
        if self._pending is None:
            self._pending = self.next_alpha(self.rounds_spent + 1)
        return self._pending

    def commit(self, content_hash: str = "") -> int:
        """Spend the reserved round; returns the 1-based round number."""
        a = self.reserve()
        self.rounds_spent += 1
        self._spent += a
        self._pending = None
        if content_hash:
            self.committed_hashes[content_hash] = self.rounds_spent
        return self.rounds_spent

    def release(self) -> None:
        """Give back a reserved round: valid only if no evidence was examined at it."""
        self._pending = None

    @property
    def pending(self) -> float | None:
        return self._pending

    def round_of(self, content_hash: str) -> int | None:
        """The schedule round a content hash was committed at, if any (dedup lookup)."""
        return self.committed_hashes.get(content_hash)

    def spent(self) -> float:
        return self._spent

    def remaining_budget(self) -> float:
        """``delta0 - sum_{k<=rounds_spent} delta_k`` (always > 0: the tail is infinite)."""
        return self.delta0 - self._spent

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "SpendSchedule",
            "delta0": self.delta0,
            "Z": self.Z,
            "rounds_spent": self.rounds_spent,
            "pending": self._pending,
            "committed_hashes": dict(sorted(self.committed_hashes.items())),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SpendSchedule":
        if d.get("kind") not in (None, "SpendSchedule"):
            raise ValueError(f"cannot restore SpendSchedule from a {d.get('kind')!r} record")
        return cls(
            delta0=d["delta0"],
            Z=d.get("Z", Z_CTHS),
            rounds_spent=d.get("rounds_spent", 0),
            pending=d.get("pending"),
            committed_hashes=d.get("committed_hashes"),
        )


class AlphaInvesting:
    """Foster & Stine (2008) alpha-investing: mFDR control with pay-back on acceptance.

    Parameters
    ----------
    alpha : target mFDR level.
    eta : initial wealth is ``alpha * eta`` (default 1 -> W_0 = alpha).
    payout : ``omega`` earned on each acceptance; must satisfy ``omega <= alpha``.
    spend_fraction : the investing rule ``alpha_k = spend_fraction * W_{k-1}/(1 + W_{k-1})``.
        Any rule with ``alpha_k <= W_{k-1}/(1 + W_{k-1})`` is admissible; spending the
        full amount loses *all* wealth on a non-acceptance, so a fraction < 1 keeps the
        loop alive.

    Protocol: ``a = policy.next_alpha()``; run the gate at level ``a``;
    ``policy.report(accepted)``.  ``next_alpha(k)`` also accepts an ignored ``k`` so the
    two policies share a call signature.  ``accepted`` means the *candidate* was accepted
    (the null was rejected) and earns ``+omega``; a candidate that was not accepted costs
    ``alpha_k / (1 - alpha_k)`` of wealth.

    The gate may take days, so the pending level is part of the state: ``next_alpha()``
    -> ``to_dict()`` -> ``from_dict()`` -> ``report()`` round-trips.  Calling
    ``next_alpha()`` twice without an intervening ``report()`` returns the same level
    rather than silently re-drawing (two hypotheses cannot share one alpha).

    Wealth can run out (one non-acceptance at ``spend_fraction=1.0`` empties it).
    ``next_alpha()`` then returns 0.0, which every gate constructor rejects, so callers
    must check ``exhausted`` first and record a HOLD instead of running a test --
    ``require_alpha()`` does that check for you.
    """

    def __init__(self, alpha: float = 0.05, eta: float = 1.0, payout: float | None = None, spend_fraction: float = 0.25) -> None:
        if not (0.0 < alpha < 1.0):
            raise ValueError("alpha must lie in (0, 1)")
        payout = alpha if payout is None else float(payout)
        if not (0.0 < payout <= alpha):
            raise ValueError("payout omega must satisfy 0 < omega <= alpha")
        if not (0.0 < spend_fraction <= 1.0):
            raise ValueError("spend_fraction must lie in (0, 1]")
        self.alpha = float(alpha)
        self.eta = float(eta)
        self.payout = payout
        self.spend_fraction = float(spend_fraction)
        self.wealth = self.alpha * self.eta
        self.rounds_spent = 0
        self._pending: float | None = None

    @property
    def exhausted(self) -> bool:
        """Wealth is gone: no admissible level remains (the loop must hold, not crash)."""
        return self.wealth <= 0.0 or self.spend_fraction * self.wealth / (1.0 + self.wealth) <= 0.0

    def next_alpha(self, k: int | None = None) -> float:
        """Level for the next test; call ``report`` afterwards.

        Returns the *same* pending level if called twice without a ``report`` in between.
        Returns 0.0 when the budget is exhausted -- check ``exhausted`` (or use
        ``require_alpha``) rather than handing 0.0 to a gate, which rejects it.
        """
        if self._pending is not None:
            return self._pending
        a = self.spend_fraction * self.wealth / (1.0 + self.wealth)
        self._pending = a
        return a

    def require_alpha(self, k: int | None = None) -> float:
        """``next_alpha`` that raises instead of returning an unusable 0.0."""
        a = self.next_alpha(k)
        if a <= 0.0:
            self._pending = None
            raise ValueError(
                "alpha-investing wealth is exhausted (0 budget): hold this candidate and record a HOLD "
                "certificate; wealth only recovers when an earlier candidate is accepted"
            )
        return a

    def report(self, accepted: bool) -> None:
        if self._pending is None:
            raise RuntimeError("call next_alpha() before report()")
        a = self._pending
        self._pending = None
        self.rounds_spent += 1
        if accepted:
            self.wealth += self.payout
        else:
            self.wealth -= a / (1.0 - a)
            if self.wealth < 0.0:  # numerical guard; the rule keeps it >= 0
                self.wealth = 0.0

    def remaining_budget(self) -> float:
        return self.wealth

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "AlphaInvesting",
            "alpha": self.alpha,
            "eta": self.eta,
            "payout": self.payout,
            "spend_fraction": self.spend_fraction,
            "wealth": self.wealth,
            "rounds_spent": self.rounds_spent,
            # The pending level MUST survive serialisation: the documented protocol is
            # next_alpha() -> run a gate that takes days -> report(), and without this a
            # resumed policy raises "call next_alpha() before report()".
            "pending": self._pending,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AlphaInvesting":
        if d.get("kind") not in (None, "AlphaInvesting"):
            raise ValueError(f"cannot restore AlphaInvesting from a {d.get('kind')!r} record")
        p = cls(alpha=d["alpha"], eta=d.get("eta", 1.0), payout=d.get("payout"), spend_fraction=d.get("spend_fraction", 0.25))
        p.wealth = float(d["wealth"])
        p.rounds_spent = int(d.get("rounds_spent", 0))
        pending = d.get("pending")
        p._pending = None if pending is None else float(pending)
        return p
