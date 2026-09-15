# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Hash-chained certificate ledger and loop-health funnel.

Every gate decision is written as an immutable *certificate* (SEA's "certificate"
vocabulary, arXiv 2607.00871) to an append-only JSONL file.  Each record carries
``prev_hash`` (the previous record's hash, ``"0" * 64`` for genesis) and
``hash = sha256(canonical JSON of the record without "hash")``; ``verify_chain`` recomputes
the chain so any edit, deletion or reordering of an *interior* line is detected.

What the chain does and does not detect
---------------------------------------
A ledger is a receipt, not a security boundary.  Concretely, without an external anchor:

* editing or reordering a line in the middle of the file **is** detected;
* **truncating the tail is not** -- a 5-record chain cut to 3 records verifies as
  "ok (3 records)";
* **rewriting the last record and recomputing its hash is not** either.

Both holes close with an anchor: keep ``ledger.head`` somewhere the writer cannot reach
(a second store, a signed daily email, the next day's first certificate) and pass it as
``verify_chain(records, expected_head=...)``.  The module docstring used to claim "any
edit, deletion or reordering of a line is detected"; that was false for the last line.

Anti-rewind / anti-re-proposal checks (``strict=True``, the default)
--------------------------------------------------------------------
Anytime validity covers optional *stopping*, not rewinding.  An adapter that snapshots
gate state before each batch and restores it whenever wealth fell accepts ~100 % of null
candidates, and nothing in a plain append-only log notices.  So ``Ledger.append``
enforces, per ``candidate_id``:

* ``round`` strictly increasing, ``n_pairs`` and ``n_informative`` non-decreasing;
* ``evidence_hash`` never returning to a value already superseded (that is a rewind);
* ``alpha_spent`` constant (a candidate is tested at one level; a second level is a
  second hypothesis and needs its own schedule draw);
* at most one terminal decision, except a later ``REJECT`` (the post-acceptance harm
  canary) after an ``ACCEPT``;

and, across candidates, that one ``content_hash`` is not re-proposed under a fresh
``candidate_id``.  Content hashing is a *floor* an LLM evades by renaming a variable --
the summable spend schedule is the only real bound -- but it makes the cheap attack
mechanical to catch.  ``strict=False`` restores the old permissive behaviour for
replaying historical logs.

Certificate fields
------------------
candidate_id, round, algorithm, decision (ACCEPT | HOLD | REJECT | NSF | BLOCKED),
alpha_spent, cumulative_alpha, metrics, evidence_refs, evaluator_id,
verification_rung (formal | execution | learned_judge | intrinsic -- the RSI survey's
verification hierarchy, arXiv 2607.07663), closure (human_in | human_on | closed),
note, ts, prev_hash, hash, and the audit fields content_hash, instance_set_hash,
evidence_hash, n_pairs, n_informative, schedule_round, instance_ids.

``HOLD`` is the ledger name for the gate's ``CONTINUE``: evidence is still accumulating
(the e-process is anytime-valid, so the candidate can be resumed on another day).
``BLOCKED`` is a mechanical rejection by hard constraints (PROCTOR: overrides all).

Loop-health funnel
------------------
proposed -> accepted -> activated -> attributed, computed from certificates plus
lifecycle *events* (``append_event(candidate_id, "activated" | "attributed", payload)``)
in the same chain.  Alarms (synthesis_v1 §B, Hermes #95976: a loop silently dead for
weeks): ``no_accepts`` (0 ACCEPT over the last N decisions), ``accepted_never_activated``
(promoted artifacts that never fired, listed **per candidate** -- one activation used to
silence the alarm for every accepted candidate), ``zero_lift_with_cost`` (median
attributed lift ~ 0 while cost > 0 -- a mean lets one large claim hide many zeroes),
``hold_backlog`` (starving pending queue).  An ``activated`` event must carry an explicit
``payload["activated"] = True``; a bare ``{}`` used to count as activation evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Mapping

__all__ = [
    "Certificate",
    "Event",
    "Ledger",
    "FunnelSummary",
    "CandidateMonitor",
    "LedgerViolation",
    "LedgerCorrupt",
    "ConcurrentWrite",
    "verify_chain",
    "funnel",
    "DECISIONS",
    "RUNGS",
    "CLOSURES",
    "GENESIS_HASH",
]


class LedgerViolation(ValueError):
    """An append that would corrupt the audit trail (rewind, re-proposal, level change)."""


class LedgerCorrupt(ValueError):
    """The ledger file cannot be parsed (e.g. a torn trailing line from a killed process)."""


class ConcurrentWrite(LedgerViolation):
    """The file head moved since this Ledger object read it: another writer is active."""

DECISIONS = ("ACCEPT", "HOLD", "REJECT", "NSF", "BLOCKED")
RUNGS = ("formal", "execution", "learned_judge", "intrinsic")
CLOSURES = ("human_in", "human_on", "closed")
EVENTS = ("activated", "attributed", "reverted", "retired")
GENESIS_HASH = "0" * 64


def _canonical(d: Mapping[str, Any]) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def record_hash(d: Mapping[str, Any]) -> str:
    """sha256 over the canonical JSON of the record with ``hash`` removed."""
    body = {k: v for k, v in d.items() if k != "hash"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Certificate:
    """Immutable per-decision record (see module docstring)."""

    candidate_id: str
    round: int
    algorithm: str
    decision: str
    alpha_spent: float
    cumulative_alpha: float
    metrics: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    evaluator_id: str = ""
    verification_rung: str = "execution"
    closure: str = "human_on"
    note: str = ""
    ts: str = ""
    prev_hash: str = ""
    hash: str = ""
    type: str = "certificate"
    # ---- audit fields (default-empty so older call sites keep working) ----
    content_hash: str = ""  # identity of the candidate's content (instances.content_hash)
    instance_set_hash: str = ""  # committed evaluation order (instances.InstanceOrder)
    evidence_hash: str = ""  # gate.summary()["evidence_hash"]: the fed payoff chain
    prev_evidence_hash: str = ""  # the gate's evidence_hash when the LAST certificate was written
    n_pairs: int = 0  # gate.n_pairs at this certificate
    n_informative: int = 0  # discordant pairs / informative deltas
    schedule_round: int = 0  # 1-based SpendSchedule round this alpha was drawn at
    instance_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}, got {self.decision!r}")
        if self.verification_rung not in RUNGS:
            raise ValueError(f"verification_rung must be one of {RUNGS}, got {self.verification_rung!r}")
        if self.closure not in CLOSURES:
            raise ValueError(f"closure must be one of {CLOSURES}, got {self.closure!r}")
        if not (0.0 <= self.alpha_spent <= 1.0) or self.cumulative_alpha < 0.0:
            raise ValueError("alpha_spent must lie in [0, 1] and cumulative_alpha >= 0")
        if self.cumulative_alpha + 1e-12 < self.alpha_spent:
            raise ValueError(
                f"cumulative_alpha {self.cumulative_alpha} < alpha_spent {self.alpha_spent}: "
                f"the running total cannot be smaller than this candidate's own level"
            )
        if self.n_pairs < 0 or self.n_informative < 0 or self.schedule_round < 0:
            raise ValueError("n_pairs, n_informative and schedule_round must be >= 0")
        if self.n_informative > self.n_pairs:
            raise ValueError(f"n_informative {self.n_informative} > n_pairs {self.n_pairs}")

    @classmethod
    def from_gate(cls, candidate_id: str, round: int, gate: Any, **kw: Any) -> "Certificate":
        """Build a certificate from a gate's ``summary()``, filling the audit fields.

        Keeps ``evidence_hash``/``n_pairs`` in step with the wealth process, which is what
        lets :class:`Ledger` detect a rewound gate.
        """
        s = gate.summary()
        decision = {"CONTINUE": "HOLD"}.get(s["state"], s["state"])
        # Side effect by design: the gate records that it was certified here, so the next
        # certificate must continue from this point (see _PairedGateBase.mark_certified).
        prev_evidence = gate.mark_certified()
        kw.setdefault("algorithm", f"{type(gate).__name__}/{s['betting']}")
        kw.setdefault("metrics", s)
        return cls(
            candidate_id=candidate_id,
            round=round,
            decision=kw.pop("decision", decision),
            alpha_spent=kw.pop("alpha_spent", s["alpha"]),
            cumulative_alpha=kw.pop("cumulative_alpha", s["alpha"]),
            evidence_hash=s.get("evidence_hash", ""),
            prev_evidence_hash=prev_evidence,
            instance_set_hash=s.get("instance_set_hash", ""),
            n_pairs=int(s.get("n_pairs", 0)),
            n_informative=int(s.get("n_informative", 0)),
            **kw,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Certificate":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass(frozen=True)
class Event:
    """Lifecycle event chained into the same ledger (activation / attribution / revert)."""

    candidate_id: str
    event: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: str = ""
    prev_hash: str = ""
    hash: str = ""
    type: str = "event"

    def __post_init__(self) -> None:
        if self.event not in EVENTS:
            raise ValueError(f"event must be one of {EVENTS}, got {self.event!r}")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Event":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


Record = Certificate | Event


def _parse(d: Mapping[str, Any]) -> Record:
    return Event.from_dict(d) if d.get("type") == "event" else Certificate.from_dict(d)


def verify_chain(records: Iterable[Mapping[str, Any]], expected_head: str | None = None) -> tuple[bool, int | None, str]:
    """Verify prev_hash linkage and hash integrity of raw record dicts.

    Returns ``(ok, index_of_first_bad_record_or_None, reason)``.

    ``expected_head`` is the anchor: pass the head hash you stored *outside* this file
    and tail truncation / last-record rewriting -- neither of which the chain alone can
    see -- becomes detectable.  A record dict carrying ``_corrupt`` (produced by
    ``Ledger`` for an unparseable line) is reported rather than raised.
    """
    prev = GENESIS_HASH
    n = -1
    for n, d in enumerate(records):
        if d.get("_corrupt"):
            return False, n, f"record {n}: unparseable line ({d.get('_error', 'invalid JSON')})"
        if d.get("prev_hash") != prev:
            return False, n, f"record {n}: prev_hash mismatch (chain broken or record removed/reordered)"
        if record_hash(d) != d.get("hash"):
            return False, n, f"record {n}: hash mismatch (record modified)"
        prev = d["hash"]
    if expected_head is not None and prev != expected_head:
        return (
            False,
            n if n >= 0 else None,
            f"head {prev[:12]}... != anchored head {expected_head[:12]}... "
            f"(records appended, or the tail was truncated/rewritten)",
        )
    return True, None, f"ok ({n + 1} records)"


@dataclass(frozen=True)
class FunnelSummary:
    proposed: int
    accepted: int
    activated: int
    attributed: int
    held: int
    rejected: int
    nsf: int
    blocked: int
    attributed_lift_mean: float | None
    attributed_cost_total: float
    alarms: tuple[str, ...]
    window: int
    accepted_unactivated_ids: tuple[str, ...] = ()
    attributed_lift_median: float | None = None
    content_hash_duplicates: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else 0.5 * (s[m - 1] + s[m])


def funnel(
    records: Iterable[Mapping[str, Any]],
    window: int = 20,
    lift_eps: float = 0.01,
    max_hold_backlog: int = 10,
) -> FunnelSummary:
    """proposed -> accepted -> activated -> attributed with alarms (see module docstring).

    ``proposed`` counts distinct candidate ids with at least one certificate; the
    other stages count distinct candidates whose *latest* certificate has that
    decision (accepted) or that have an ``activated`` / ``attributed`` event.
    ``window`` is the number of most recent *terminal* decisions inspected for the
    ``no_accepts`` alarm.
    """
    latest: dict[str, str] = {}
    order: list[str] = []
    terminal_seq: list[str] = []
    activated: set[str] = set()
    attributed: set[str] = set()
    lifts: list[float] = []
    per_candidate_lift: dict[str, float] = {}
    content_hashes: dict[str, set[str]] = {}
    cost = 0.0
    for d in records:
        if d.get("type") == "event":
            cid = d["candidate_id"]
            p = d.get("payload") or {}
            # An explicit True is required: an empty payload is not activation evidence.
            if d["event"] == "activated" and p.get("activated") is True:
                activated.add(cid)
            elif d["event"] == "attributed":
                attributed.add(cid)
                if "lift" in p:
                    lifts.append(float(p["lift"]))
                    per_candidate_lift[cid] = float(p["lift"])
                cost += float(p.get("cost", 0.0))
            continue
        cid = d["candidate_id"]
        if cid not in latest:
            order.append(cid)
        latest[cid] = d["decision"]
        if d.get("content_hash"):
            content_hashes.setdefault(d["content_hash"], set()).add(cid)
        if d["decision"] != "HOLD":
            terminal_seq.append(d["decision"])

    counts = {k: sum(1 for v in latest.values() if v == k) for k in DECISIONS}
    accepted_ids = {c for c, v in latest.items() if v == "ACCEPT"}
    unactivated = tuple(sorted(accepted_ids - activated))
    duplicates = tuple(sorted(h for h, ids in content_hashes.items() if len(ids) > 1))
    alarms: list[str] = []
    recent = terminal_seq[-window:]
    if len(recent) >= window and "ACCEPT" not in recent:
        alarms.append(f"no_accepts: 0 ACCEPT in the last {window} terminal decisions (loop may be dead or starving)")
    if unactivated:
        # Per candidate, not set-level: one activation event used to clear this alarm for
        # every accepted candidate in the ledger.
        shown = ", ".join(unactivated[:5]) + (" ..." if len(unactivated) > 5 else "")
        alarms.append(
            f"accepted_never_activated: {len(unactivated)} of {len(accepted_ids)} promoted artifacts have no "
            f"activation evidence ({shown}) (is it loaded?)"
        )
    lift_mean = sum(lifts) / len(lifts) if lifts else None
    lift_median = _median(list(per_candidate_lift.values()) or lifts)
    # Median, not mean: one large claimed lift hides many zero-lift attributions.
    if lift_median is not None and abs(lift_median) <= lift_eps and cost > 0.0:
        alarms.append(f"zero_lift_with_cost: median attributed lift {lift_median:+.3f} ~ 0 while cost {cost:g} > 0")
    if counts["HOLD"] > max_hold_backlog:
        alarms.append(f"hold_backlog: {counts['HOLD']} candidates pending > {max_hold_backlog} (gate starving?)")
    if duplicates:
        alarms.append(
            f"content_hash_duplicates: {len(duplicates)} candidate content hash(es) proposed under more than one id "
            f"(re-proposal to buy extra alpha?)"
        )
    return FunnelSummary(
        proposed=len(order),
        accepted=counts["ACCEPT"],
        activated=len(accepted_ids & activated) if accepted_ids else len(activated),
        attributed=len(attributed),
        held=counts["HOLD"],
        rejected=counts["REJECT"],
        nsf=counts["NSF"],
        blocked=counts["BLOCKED"],
        attributed_lift_mean=lift_mean,
        attributed_cost_total=cost,
        alarms=tuple(alarms),
        window=window,
        accepted_unactivated_ids=unactivated,
        attributed_lift_median=lift_median,
        content_hash_duplicates=duplicates,
    )


@dataclass
class _CandidateState:
    """What the ledger remembers about one candidate, for the monotonicity checks."""

    round: int = 0
    n_pairs: int = 0
    n_informative: int = 0
    alpha_spent: float | None = None
    evidence_hash: str = ""
    seen_evidence: set[str] = field(default_factory=set)
    terminal: str = ""
    content_hash: str = ""


class CandidateMonitor:
    """The per-candidate anti-rewind rules, usable without a file.

    ``Ledger`` runs this on every append; the adversarial bench runs it to measure what
    the rules buy.  ``validate`` raises ``LedgerViolation``; ``record`` advances the
    remembered state; ``accept`` does both.
    """

    def __init__(self, schedule_delta0: float | None = None) -> None:
        self._candidates: dict[str, _CandidateState] = {}
        self._content_owner: dict[str, str] = {}
        self._round_owner: dict[int, str] = {}
        self._cumulative_alpha = 0.0
        # When given, alpha_spent must equal the CTHS level for the certificate's
        # schedule_round, so a candidate cannot quietly test itself at a richer level
        # than the schedule handed out.
        self.schedule_delta0 = schedule_delta0

    def record(self, d: Mapping[str, Any]) -> None:
        st = self._candidates.setdefault(d["candidate_id"], _CandidateState())
        st.round = max(st.round, int(d.get("round", 0)))
        st.n_pairs = max(st.n_pairs, int(d.get("n_pairs", 0)))
        st.n_informative = max(st.n_informative, int(d.get("n_informative", 0)))
        if float(d.get("alpha_spent", 0.0)) > 0.0:
            st.alpha_spent = float(d["alpha_spent"])
        ev = str(d.get("evidence_hash", ""))
        if ev:
            st.seen_evidence.add(ev)
            st.evidence_hash = ev
        if d.get("decision") in ("ACCEPT", "REJECT", "NSF", "BLOCKED"):
            st.terminal = str(d["decision"])
        ch = str(d.get("content_hash", ""))
        if ch:
            st.content_hash = ch
            self._content_owner.setdefault(ch, d["candidate_id"])
        rnd = int(d.get("schedule_round", 0))
        if rnd > 0:
            self._round_owner.setdefault(rnd, d["candidate_id"])
        self._cumulative_alpha = max(self._cumulative_alpha, float(d.get("cumulative_alpha", 0.0)))

    def validate(self, c: "Certificate") -> None:
        """Refuse appends that would make the audit trail lie (see module docstring)."""
        st = self._candidates.get(c.candidate_id)
        if c.content_hash:
            owner = self._content_owner.get(c.content_hash)
            if owner is not None and owner != c.candidate_id:
                raise LedgerViolation(
                    f"content_hash {c.content_hash[:12]}... was already proposed as {owner!r}; re-proposing identical "
                    f"content under a fresh candidate_id buys a fresh alpha (P(>=1 false ACCEPT) 8 % at K=5, 90 % at "
                    f"K=100 with a flat alpha).  Resume {owner!r}'s gate state instead, or draw a new schedule round."
                )
        if c.cumulative_alpha + 1e-12 < self._cumulative_alpha:
            raise LedgerViolation(
                f"{c.candidate_id}: cumulative_alpha went backwards ({c.cumulative_alpha} < "
                f"{self._cumulative_alpha}); spent alpha is not recoverable"
            )
        if c.schedule_round > 0:
            owner = self._round_owner.get(c.schedule_round)
            if owner is not None and owner != c.candidate_id:
                raise LedgerViolation(
                    f"schedule_round {c.schedule_round} was already used by {owner!r}: one round funds one hypothesis"
                )
            if self.schedule_delta0 is not None:
                from .schedule import SpendSchedule

                expected = SpendSchedule(delta0=self.schedule_delta0).next_alpha(c.schedule_round)
                if abs(c.alpha_spent - expected) > 1e-12:
                    raise LedgerViolation(
                        f"{c.candidate_id}: alpha_spent {c.alpha_spent} != schedule level {expected} for round "
                        f"{c.schedule_round} (delta0={self.schedule_delta0})"
                    )
        if st is None:
            return
        if c.round <= st.round:
            raise LedgerViolation(f"{c.candidate_id}: round {c.round} <= last recorded round {st.round} (rounds must advance)")
        if c.n_pairs < st.n_pairs:
            raise LedgerViolation(
                f"{c.candidate_id}: n_pairs went backwards ({c.n_pairs} < {st.n_pairs}) -- the wealth process was "
                f"rewound.  Anytime validity covers stopping, not restoring an earlier snapshot."
            )
        if c.n_informative < st.n_informative:
            raise LedgerViolation(
                f"{c.candidate_id}: n_informative went backwards ({c.n_informative} < {st.n_informative}) -- rewound gate state"
            )
        if st.evidence_hash and c.prev_evidence_hash and c.prev_evidence_hash != st.evidence_hash:
            raise LedgerViolation(
                f"{c.candidate_id}: this certificate continues evidence {c.prev_evidence_hash[:12]}... but the last "
                f"one ended at {st.evidence_hash[:12]}... -- the gate was restored from an earlier snapshot "
                f"(anytime validity covers stopping, not rewinding)"
            )
        if c.evidence_hash and c.evidence_hash in st.seen_evidence and c.evidence_hash != st.evidence_hash:
            raise LedgerViolation(
                f"{c.candidate_id}: evidence_hash {c.evidence_hash[:12]}... was already superseded -- the gate was "
                f"restored from an earlier snapshot"
            )
        if st.evidence_hash and c.evidence_hash and c.evidence_hash == st.evidence_hash and c.n_pairs > st.n_pairs:
            raise LedgerViolation(f"{c.candidate_id}: n_pairs advanced without new evidence (evidence_hash unchanged)")
        if st.alpha_spent is not None and c.alpha_spent > 0.0 and abs(c.alpha_spent - st.alpha_spent) > 1e-12:
            raise LedgerViolation(
                f"{c.candidate_id}: alpha_spent changed from {st.alpha_spent} to {c.alpha_spent}; one candidate is "
                f"tested at one level.  A second level is a second hypothesis and needs its own schedule draw."
            )
        if st.terminal and not (st.terminal == "ACCEPT" and c.decision == "REJECT"):
            raise LedgerViolation(
                f"{c.candidate_id}: already terminal ({st.terminal}); only a later REJECT (the post-acceptance harm "
                f"canary) may follow an ACCEPT"
            )

    def accept(self, c: "Certificate") -> None:
        self.validate(c)
        self.record(c.as_dict())


class Ledger:
    """Append-only JSONL ledger with a sha256 hash chain.

    ``append(cert)`` fills ``ts`` (if empty), ``prev_hash`` and ``hash`` and returns the
    finalised immutable record; the caller's object is never mutated.

    Parameters
    ----------
    strict : run the anti-rewind / anti-re-proposal checks described in the module
        docstring on every append (default).  Turn it off only to replay historical logs.
    schedule_delta0 : when given, ``alpha_spent`` must equal the CTHS level for the
        certificate's ``schedule_round`` -- the only way the ledger can tell that the
        level a candidate was tested at is the level the schedule handed out.
    verify : verify the whole chain when opening (default); a broken or torn file then
        refuses appends until the caller has dealt with it.

    **One writer per path.**  The head hash is cached in memory, so two live ``Ledger``
    objects on the same file would interleave and silently break the chain; every append
    re-reads the file's last line and raises ``ConcurrentWrite`` if it moved.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        strict: bool = True,
        verify: bool = True,
        schedule_delta0: float | None = None,
    ) -> None:
        self.path = os.fspath(path)
        self.strict = bool(strict)
        self._head = GENESIS_HASH
        self._count = 0
        self._monitor = CandidateMonitor(schedule_delta0=schedule_delta0)
        self.corrupt_at: int | None = None
        if os.path.exists(self.path):
            for i, d in enumerate(self._iter_raw()):
                if d.get("_corrupt"):
                    self.corrupt_at = i
                    break
                self._head = d.get("hash", self._head)
                self._count += 1
                if d.get("type") != "event":
                    self._track(d)
        if verify and os.path.exists(self.path):
            ok, idx, msg = self.verify_chain()
            self._opened_intact = ok
            self._open_reason = msg
        else:
            self._opened_intact = True
            self._open_reason = "not verified on open"

    # ---- io ------------------------------------------------------------------------
    def _iter_raw(self) -> Iterator[dict[str, Any]]:
        """Yield raw records; an unparseable line yields a ``{"_corrupt": True}`` marker.

        A process killed mid-append leaves a partial trailing line.  Raising
        ``JSONDecodeError`` from here would make the ledger impossible to reopen *or*
        diagnose through the API, so the damage is reported as data instead.
        """
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    yield {"_corrupt": True, "_error": str(exc), "_line": line[:80]}

    def _file_head(self) -> str:
        """Hash on the file's last non-empty line (``GENESIS_HASH`` for an empty file)."""
        if not os.path.exists(self.path):
            return GENESIS_HASH
        last = ""
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if not last:
            return GENESIS_HASH
        try:
            return str(json.loads(last).get("hash", ""))
        except json.JSONDecodeError as exc:
            raise LedgerCorrupt(f"{self.path}: trailing line is not valid JSON ({exc})") from None

    def _append_raw(self, d: dict[str, Any]) -> dict[str, Any]:
        if self.corrupt_at is not None:
            raise LedgerCorrupt(
                f"{self.path}: record {self.corrupt_at} is unparseable (torn write?); "
                f"repair or truncate the file before appending"
            )
        file_head = self._file_head()
        if file_head != self._head:
            raise ConcurrentWrite(
                f"{self.path}: head moved from {self._head[:12]}... to {file_head[:12]}... since this Ledger was "
                f"opened -- another writer is appending.  One writer per ledger path; reopen to continue."
            )
        d = dict(d)
        d["prev_hash"] = self._head
        d["hash"] = ""
        d["hash"] = record_hash(d)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(_canonical(d) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._head = d["hash"]
        self._count += 1
        return d

    @property
    def head(self) -> str:
        """Current head hash -- **anchor this externally**; see the module docstring."""
        return self._head

    def __len__(self) -> int:
        return self._count

    # ---- append-time validation ----------------------------------------------------
    def _track(self, d: Mapping[str, Any]) -> None:
        self._monitor.record(d)

    def _validate(self, c: Certificate) -> None:
        self._monitor.validate(c)

    # ---- writes --------------------------------------------------------------------
    def append(self, cert: Certificate) -> Certificate:
        if self.strict:
            self._validate(cert)
        c = replace(cert, ts=cert.ts or _now(), prev_hash="", hash="")
        d = self._append_raw(c.as_dict())
        self._track(d)
        return Certificate.from_dict(d)

    def append_event(self, candidate_id: str, event: str, payload: Mapping[str, Any] | None = None, ts: str = "") -> Event:
        e = Event(candidate_id=candidate_id, event=event, payload=dict(payload or {}), ts=ts or _now())
        return Event.from_dict(self._append_raw(e.as_dict()))

    # ---- reads ---------------------------------------------------------------------
    def records(self) -> list[Record]:
        return [_parse(d) for d in self._iter_raw() if not d.get("_corrupt")]

    def certificates(self, candidate_id: str | None = None) -> list[Certificate]:
        return [r for r in self.records() if isinstance(r, Certificate) and (candidate_id is None or r.candidate_id == candidate_id)]

    def find(self, content_hash: str) -> list[Certificate]:
        """Certificates whose candidate content hashes to ``content_hash`` (dedup lookup)."""
        return [c for c in self.certificates() if c.content_hash == content_hash]

    def verify_chain(self, expected_head: str | None = None) -> tuple[bool, int | None, str]:
        return verify_chain(self._iter_raw(), expected_head=expected_head)

    def funnel(self, window: int = 20, **kw: Any) -> FunnelSummary:
        return funnel((d for d in self._iter_raw() if not d.get("_corrupt")), window=window, **kw)

    def cumulative_alpha(self) -> float:
        """Total alpha at risk: the **maximum** ``alpha_spent`` per candidate, summed.

        Not the *latest*: a candidate whose third certificate reported ``alpha_spent=0.0``
        used to erase its own 0.05 from the total.  Alpha, once risked, is spent.
        """
        per_candidate: dict[str, float] = {}
        for d in self._iter_raw():
            if d.get("_corrupt") or d.get("type") == "event":
                continue
            cid = d["candidate_id"]
            per_candidate[cid] = max(per_candidate.get(cid, 0.0), float(d["alpha_spent"]))
        return sum(per_candidate.values())

    def exposure(self) -> dict[str, Any]:
        """Instance re-use across candidates, and per-candidate stale fraction.

        Populated from ``Certificate.instance_ids``; empty when certificates do not carry
        them (in which case the ledger simply cannot see dev-set re-use, and says so via
        ``instances_recorded=False``).
        """
        uses: dict[str, set[str]] = {}
        per_candidate: dict[str, list[str]] = {}
        for c in self.certificates():
            if not c.instance_ids:
                continue
            per_candidate.setdefault(c.candidate_id, list(c.instance_ids))
            for iid in c.instance_ids:
                uses.setdefault(iid, set()).add(c.candidate_id)
        stale = {}
        for cid, ids in per_candidate.items():
            others = sum(1 for i in ids if len(uses.get(i, set()) - {cid}) > 0)
            stale[cid] = others / len(ids) if ids else 0.0
        return {
            "instances_recorded": bool(uses),
            "n_instances": len(uses),
            "instance_uses": {i: len(c) for i, c in sorted(uses.items())},
            "max_instance_uses": max((len(c) for c in uses.values()), default=0),
            "stale_fraction": dict(sorted(stale.items())),
        }
