# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Acceptor-owned instance sampling: who chooses *what is measured, in which order*.

Why this module exists
----------------------
Ville's inequality bounds ``P(sup_t M_t >= 1/alpha) <= alpha`` for a *fixed* filtration
in which

* the betting fraction ``lambda_t`` is predictable (``F_{t-1}``-measurable), and
* the payoff ``g_t`` is the outcome of an instance whose identity was **also** fixed
  before the outcome was known.

``eprocess`` enforces the first condition in code.  The second is a property of the
*data stream*, not of the wealth recursion, so no amount of care inside the gate can
establish it: if the proposer decides which instance is fed at step t after seeing how
that instance turns out, the conditional null ``P(w_t = 1 | discordant, F_{t-1}) <= 1/2``
is simply false and the e-value is not an e-value.  Measured on 2000 null seeds
(n = 40, p = 0.6, alpha = 0.05): honest order gives 0.9-2.1 % false ACCEPT, the same
40 pairs re-ordered wins-first give 66-89 %.

Two distinct attacks live here:

1. **Ordering.**  Feed the wins first and stop at the first crossing.  Defended by
   committing an order *before* outcomes exist (``draw()`` derives it from the
   candidate's content hash) and having the gate refuse any instance that is not the
   next one in the committed order (``PairedBinaryGate.bind_instances``).
2. **Re-use of a fixed dev set.**  Privately evaluate K candidates on the same
   instances and submit the luckiest.  The per-candidate guarantee still holds for each
   candidate *in isolation*, but the submitted one is a maximum over K, so the gate sees
   a stream selected on its own outcomes: 31-33 % false ACCEPT at K = 20 versus 1.4 %
   with fresh instances.  Defended by rotation (``reuse_window``) plus an exposure
   report, so "this candidate was tested on instances 18 earlier candidates already saw"
   is visible in the certificate instead of invisible.

Neither defence is a proof of freshness -- a proposer that has memorised the whole pool
cannot be stopped by sampling from that pool.  What this module buys is that the
precondition is *recorded* (``instance_set_hash``, ``n_fresh``, ``stale_fraction``) and
that violations of the committed order are *mechanical errors* rather than silent alpha
inflation.

Everything here is deterministic and stdlib-only: the permutation is a sha256 sort, not
``random.shuffle``, so it is reproducible from the certificate alone.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

__all__ = ["InstanceSampler", "InstanceOrder", "content_hash", "set_hash"]


def content_hash(content: Any) -> str:
    """Stable sha256 of candidate content (a string, bytes, or any JSON-able object).

    This is the identity used for de-duplication and for deriving the evaluation order.
    It is a *floor*, not a barrier: an LLM proposer can defeat content hashing by
    renaming a variable.  The summable spend schedule is the only real bound on
    re-proposal; see ``schedule.SpendSchedule``.
    """
    if isinstance(content, bytes):
        data = content
    elif isinstance(content, str):
        data = content.encode("utf-8")
    else:
        data = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def set_hash(instance_ids: Sequence[str], content_hash_: str = "") -> str:
    """Hash of an *ordered* instance list (the commitment written into the certificate)."""
    h = hashlib.sha256()
    h.update(content_hash_.encode("utf-8"))
    for i in instance_ids:
        h.update(b"\x00")
        h.update(str(i).encode("utf-8"))
    return h.hexdigest()


@dataclass(frozen=True)
class InstanceOrder:
    """A committed evaluation order for one candidate.

    ``instance_set_hash`` pins *which* instances and *in what order*; it is computed
    before any outcome exists and belongs in the certificate.
    """

    instance_ids: tuple[str, ...]
    instance_set_hash: str
    content_hash: str
    n_fresh: int  # instances not used by any previous candidate within the reuse window
    draw_index: int  # 1-based index of this draw in the sampler's history

    @property
    def n(self) -> int:
        return len(self.instance_ids)

    @property
    def stale_fraction(self) -> float:
        """Fraction of this candidate's instances that earlier candidates already saw."""
        return 0.0 if not self.instance_ids else 1.0 - self.n_fresh / len(self.instance_ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_ids": list(self.instance_ids),
            "instance_set_hash": self.instance_set_hash,
            "content_hash": self.content_hash,
            "n_fresh": self.n_fresh,
            "stale_fraction": self.stale_fraction,
            "draw_index": self.draw_index,
        }


class InstanceSampler:
    """Seeded, rotating sampler that *the acceptor* owns (never the proposer).

    Parameters
    ----------
    pool : the instance ids available for gate evaluation.  These must be disjoint from
        the protected corpus fed to ``floor.ProtectedCorpus``: the floor deliberately
        conditions on instances the incumbent passed, which is exactly the selection
        that breaks the gate's conditional null.
    salt : a secret or run-specific string mixed into the permutation, so a proposer who
        knows the pool and its own content hash still cannot predict the order.
    reuse_window : an instance drawn for candidate k is "stale" for the next
        ``reuse_window`` draws.  0 disables rotation (every draw may reuse everything).

    Usage::

        sampler = InstanceSampler(pool, salt=secret, reuse_window=5)
        order = sampler.draw(content_hash(candidate_source), n=40)   # BEFORE any eval
        gate.bind_instances(order)
        for iid in order.instance_ids:
            gate.update(evaluate(iid), instance_id=iid)

    State is JSON-serialisable (``to_dict``/``from_dict``) so rotation survives a pause.
    """

    def __init__(
        self,
        pool: Iterable[str],
        salt: str = "",
        reuse_window: int = 0,
        *,
        last_used: Mapping[str, int] | None = None,
        usage: Mapping[str, int] | None = None,
        draws: int = 0,
    ) -> None:
        self.pool: list[str] = [str(p) for p in pool]
        if len(set(self.pool)) != len(self.pool):
            raise ValueError("instance pool contains duplicate ids")
        if reuse_window < 0:
            raise ValueError("reuse_window must be >= 0")
        self.salt = str(salt)
        self.reuse_window = int(reuse_window)
        self._last_used: dict[str, int] = {str(k): int(v) for k, v in (last_used or {}).items()}
        self._usage: dict[str, int] = {str(k): int(v) for k, v in (usage or {}).items()}
        self.draws = int(draws)

    # ---- sampling --------------------------------------------------------------------
    def _key(self, content_hash_: str, instance_id: str) -> str:
        return hashlib.sha256(f"{self.salt}\x00{content_hash_}\x00{instance_id}".encode("utf-8")).hexdigest()

    def draw(self, content_hash_: str, n: int) -> InstanceOrder:
        """Commit an evaluation order of ``n`` instances for this candidate content.

        Fresh instances (not used within ``reuse_window`` draws) are preferred; if there
        are not enough, the least-recently-used stale ones fill the rest and the shortfall
        is reported as ``n_fresh`` / ``stale_fraction`` rather than hidden.
        """
        if n < 1:
            raise ValueError("n must be >= 1")
        if n > len(self.pool):
            raise ValueError(f"pool has {len(self.pool)} instances, {n} requested")
        nxt = self.draws + 1
        fresh, stale = [], []
        for iid in self.pool:
            last = self._last_used.get(iid)
            (fresh if last is None or (nxt - last) > self.reuse_window else stale).append(iid)
        fresh.sort(key=lambda i: self._key(content_hash_, i))
        stale.sort(key=lambda i: (self._last_used.get(i, 0), self._key(content_hash_, i)))
        chosen = (fresh + stale)[:n]
        n_fresh = sum(1 for i in chosen if i in set(fresh))
        # The *order* is itself derived from the content hash, so it is fixed before any
        # outcome exists and is reproducible from the certificate.
        chosen.sort(key=lambda i: self._key(content_hash_, i))
        for iid in chosen:
            self._last_used[iid] = nxt
            self._usage[iid] = self._usage.get(iid, 0) + 1
        self.draws = nxt
        ids = tuple(chosen)
        return InstanceOrder(ids, set_hash(ids, content_hash_), content_hash_, n_fresh, nxt)

    # ---- reporting -------------------------------------------------------------------
    def exposure(self) -> dict[str, Any]:
        """How many candidates each instance has been used for (overfitting pressure)."""
        counts = {i: self._usage.get(i, 0) for i in self.pool}
        used = [c for c in counts.values() if c]
        return {
            "draws": self.draws,
            "pool_size": len(self.pool),
            "n_never_used": sum(1 for c in counts.values() if c == 0),
            "max_uses": max(counts.values()) if counts else 0,
            "mean_uses": (sum(used) / len(used)) if used else 0.0,
            "usage": dict(sorted(counts.items())),
        }

    # ---- serialisation ---------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "InstanceSampler",
            "pool": list(self.pool),
            "salt": self.salt,
            "reuse_window": self.reuse_window,
            "last_used": dict(sorted(self._last_used.items())),
            "usage": dict(sorted(self._usage.items())),
            "draws": self.draws,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "InstanceSampler":
        if d.get("kind") not in (None, "InstanceSampler"):
            raise ValueError(f"cannot restore InstanceSampler from a {d.get('kind')!r} record")
        return cls(
            d["pool"],
            salt=d.get("salt", ""),
            reuse_window=int(d.get("reuse_window", 0)),
            last_used=d.get("last_used"),
            usage=d.get("usage"),
            draws=int(d.get("draws", 0)),
        )

    def to_json(self, **kw: Any) -> str:
        kw.setdefault("sort_keys", True)
        return json.dumps(self.to_dict(), **kw)

    @classmethod
    def from_json(cls, s: str) -> "InstanceSampler":
        return cls.from_dict(json.loads(s))
