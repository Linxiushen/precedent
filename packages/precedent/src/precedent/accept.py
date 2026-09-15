# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""② ACCEPT — paired outcomes in, a hash-chained certificate out.

This is the wiring between :mod:`precedent.examine` (which produces paired
outcomes) and :mod:`acceptor` (which decides).  Nothing here re-implements the
statistics; the whole point is that the decision core is a library that is
*used*, not forked.

The pipeline::

    paired outcomes            (case, run) -> (y_without, y_with)
      |
      +-- ties by construction  ------------------------------------+
      |     cases the candidate could not load in: fed as ties,     |
      |     never executed, zero model calls                        |
      |                                                             v
      +-- protected cases -> acceptor.ProtectedCorpus (TaskFloor)   |
      |     k-of-n confirmation on tasks the incumbent passes k/k   |
      |                                                             |
      +-- everything else -> acceptor.PairedBinaryGate              |
            betting="mixture", harm martingale at harm_alpha        |
                                                                    |
    acceptor.SpendSchedule  ----- one CTHS round per examine run ---+
                                                                    |
                                                                    v
                          Certificate (ACCEPT / HOLD / REJECT / NSF / BLOCKED)
                                  -> <state>/ledger.jsonl  (hash-chained)
                                  -> <state>/proposals.jsonl -> the DOCKET

**The floor and the gate consume disjoint streams.**  ``acceptor``'s own
documentation is explicit that feeding the protected corpus to the gate makes
the harm martingale fire ~100 % of the time on an equal-quality candidate — the
protected corpus is *by construction* the set of instances the incumbent
passes.  So a case that is already protected (from **earlier** runs recorded in
``<state>/protected.json``, never from this run's own baseline arm) goes to the
floor and is excluded from the gate.  On the first examine of a machine nothing
is protected yet, the floor is ``INCOMPLETE`` — which is *not* a pass — and the
certificate says so.

**Nothing is ever applied.**  An ``ACCEPT`` is a certificate and a docket entry.
The bytes of the candidate are written by the user, by hand, after they read it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from acceptor import (Certificate, InstanceSampler, PairedBinaryGate,
                      ProtectedCorpus, SpendSchedule, content_hash)

from . import __version__
from .proposals import append_proposal
from .state import now_iso

__all__ = [
    "DEFAULT_ALPHA0",
    "anti_rewind_problems",
    "DEFAULT_BETTING",
    "DEFAULT_HARM_ALPHA",
    "AcceptanceResult",
    "accept_outcomes",
    "load_protected",
    "load_schedule",
    "protected_case_ids",
    "render_acceptance",
    "save_protected",
    "save_schedule",
    "verify_acceptance",
]

#: The family-wise error budget the CTHS schedule hands out over an unbounded
#: number of candidates (SEA, arXiv 2607.00871; ``Z = 3.3877``).
DEFAULT_ALPHA0 = 0.05
#: The harm martingale is deliberately *looser* than the superiority test: a
#: regression must be cheap to prove, a promotion expensive (方案 §L3).
DEFAULT_HARM_ALPHA = 0.10
DEFAULT_BETTING = "mixture"
#: Below this many discordant pairs an ACCEPT is refused however high the
#: wealth: an anytime-valid stop on two hand-picked instances is still a
#: decision resting on two instances.
MIN_INFORMATIVE = 3
#: A case joins the protected corpus once the incumbent has passed it k/k.
K_STABILITY = 2
K_CONFIRM = 2


# --------------------------------------------------------------------------
# persistent state
# --------------------------------------------------------------------------

def load_schedule(state) -> SpendSchedule:
    """The run-level error-budget schedule (one CTHS round per examine run)."""
    raw = state.read_json(state.path("schedule.json"), default=None)
    if isinstance(raw, dict):
        try:
            return SpendSchedule.from_dict(raw)
        except (ValueError, KeyError):
            pass
    return SpendSchedule(delta0=DEFAULT_ALPHA0)


def save_schedule(state, sched: SpendSchedule) -> str:
    return state.write_json(state.path("schedule.json"), sched.to_dict())


def load_protected(state) -> ProtectedCorpus:
    raw = state.read_json(state.path("protected.json"), default=None)
    if isinstance(raw, dict):
        try:
            return ProtectedCorpus.from_dict(raw)
        except (ValueError, KeyError):
            pass
    return ProtectedCorpus(k_stability=K_STABILITY, k_confirm=K_CONFIRM,
                           n_confirm=K_CONFIRM, min_protected=1)


def save_protected(state, corpus: ProtectedCorpus) -> str:
    return state.write_json(state.path("protected.json"), corpus.to_dict())


def protected_case_ids(corpus: ProtectedCorpus) -> set:
    """Cases the incumbent has passed k/k in **earlier** runs."""
    return set(corpus.protected_tasks())


# --------------------------------------------------------------------------
# the decision
# --------------------------------------------------------------------------

@dataclass
class AcceptanceResult:
    candidate_id: str
    content_hash: str
    decision: str = "HOLD"
    reason: str = ""
    gate: dict = field(default_factory=dict)
    floor: dict = field(default_factory=dict)
    schedule: dict = field(default_factory=dict)
    certificate: dict | None = None
    n_pairs: int = 0
    n_ties: int = 0
    n_free_ties: int = 0
    n_informative: int = 0
    n_floor_pairs: int = 0
    alpha: float = 0.0
    proposal_id: str | None = None
    order: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "candidateId": self.candidate_id, "contentHash": self.content_hash,
            "decision": self.decision, "reason": self.reason,
            "alpha": self.alpha, "gate": self.gate, "floor": self.floor,
            "schedule": self.schedule, "order": self.order,
            "nPairs": self.n_pairs, "nTies": self.n_ties,
            "nFreeTies": self.n_free_ties, "nInformative": self.n_informative,
            "nFloorPairs": self.n_floor_pairs,
            "certificate": self.certificate, "proposalId": self.proposal_id,
            "notes": self.notes,
        }


def _next_round(state, candidate_id: str) -> int:
    """Rounds must advance per candidate (``acceptor.CandidateMonitor``).

    Docket decisions for the same id already occupy rounds 1-2, so acceptance
    certificates start at 10 and count up.
    """
    n = 0
    try:
        for rec in state.ledger().records():
            d = rec.as_dict() if hasattr(rec, "as_dict") else rec
            if (d.get("candidate_id") == candidate_id
                    and str(d.get("algorithm", "")).startswith("PairedBinaryGate")):
                n += 1
    except Exception:                                    # pragma: no cover - defensive
        return 10
    return 10 + n


def anti_rewind_problems(state, *, candidate_id: str, content_hash_: str,
                         summary: dict, round_no: int, alpha_spent: float,
                         cumulative_alpha: float, next_round: int) -> list[str]:
    """What ``acceptor.CandidateMonitor`` would say about this certificate.

    Run **before** the certificate is chained, so a rewound or re-badged
    candidate is refused rather than certified-and-then-complained-about.  The
    certificate built here is a throwaway probe: it never touches the gate
    (``Certificate.from_gate`` has a side effect, ``mark_certified``) and it is
    never appended.

    The attacks this catches in practice are the two cheap ones: re-examining
    the same candidate against a *smaller* set of cassettes until the arms
    happen to agree (``n_pairs`` / ``n_informative`` going backwards), and
    re-proposing byte-identical content under a fresh id to buy a fresh alpha.
    """
    from acceptor import CandidateMonitor, LedgerViolation
    mon = CandidateMonitor()
    try:
        records = list(state.ledger().records())
    except Exception:                                    # pragma: no cover
        return []
    for rec in records:
        d = rec.as_dict() if hasattr(rec, "as_dict") else rec
        if str(d.get("algorithm", "")).startswith("PairedBinaryGate"):
            mon.record(d)
    probe = Certificate(
        candidate_id=candidate_id, round=next_round,
        algorithm=f"PairedBinaryGate/{summary.get('betting')}",
        decision="HOLD", alpha_spent=alpha_spent,
        cumulative_alpha=cumulative_alpha,
        evidence_hash=str(summary.get("evidence_hash") or ""),
        instance_set_hash=str(summary.get("instance_set_hash") or ""),
        n_pairs=int(summary.get("n_pairs", 0) or 0),
        n_informative=int(summary.get("n_informative", 0) or 0),
        content_hash=content_hash_, schedule_round=round_no)
    try:
        mon.validate(probe)
    except LedgerViolation as exc:
        return [str(exc)]
    return []


def accept_outcomes(state, candidate, outcomes, *, alpha0: float = DEFAULT_ALPHA0,
                    harm_alpha: float = DEFAULT_HARM_ALPHA,
                    betting: str = DEFAULT_BETTING,
                    require_floor: bool = False,
                    evidence: dict | None = None,
                    surface: str = "", declared_paths=None,
                    hypothesis: str = "", expected_effect: str = "",
                    now=None, persist: bool = True) -> AcceptanceResult:
    """Paired outcomes -> gate + floor + schedule -> certificate -> docket.

    ``candidate`` is anything with ``.id`` and ``.content_hash`` (an
    :class:`precedent.examine.Candidate`), or a ``(id, content_hash)`` pair.
    """
    cid = getattr(candidate, "id", None) or (candidate[0] if isinstance(candidate, (list, tuple)) else str(candidate))
    chash = getattr(candidate, "content_hash", None) or (
        candidate[1] if isinstance(candidate, (list, tuple)) and len(candidate) > 1 else "")
    if not chash:
        chash = content_hash(str(cid))
    surface = surface or getattr(candidate, "surface", "") or ""
    declared_paths = list(declared_paths or getattr(candidate, "paths", []) or [])

    outcomes = list(outcomes)
    free = [o for o in outcomes if not o.executed]
    executed = [o for o in outcomes if o.executed]

    corpus = load_protected(state)
    already_protected = protected_case_ids(corpus)

    floor_rows = [o for o in executed if o.case in already_protected]
    gate_rows = [o for o in executed if o.case not in already_protected]
    # Free ties never cost anything and never move wealth, but they *are* part
    # of the record: they go to the gate as ties so n_pairs is the truth.
    gate_rows = free + gate_rows

    res = AcceptanceResult(candidate_id=str(cid), content_hash=chash,
                           n_free_ties=len(free), n_floor_pairs=len(floor_rows))

    # ---- the error budget: reserve, then commit only if evidence was seen --
    sched = load_schedule(state)
    prior_round = sched.round_of(chash)
    if prior_round is not None:
        alpha = sched.next_alpha(prior_round)
        round_no = prior_round
        res.notes.append(
            f"this content hash already holds schedule round {prior_round}; "
            f"re-examining it reuses that level instead of buying a fresh one "
            f"(a fresh alpha per re-proposal is how K=100 reaches 90 % false ACCEPT)")
    else:
        alpha = sched.reserve()
        round_no = sched.rounds_spent + 1
    res.alpha = alpha

    # ---- the gate ------------------------------------------------------
    gate = PairedBinaryGate(alpha=alpha, betting=betting,
                            max_pairs=max(1, len(gate_rows)),
                            harm_alpha=harm_alpha,
                            min_informative=MIN_INFORMATIVE)
    order = None
    # One instance is one paired observation.  Two rows claiming the same
    # `case#run` would be fed to the wealth process twice (and drawn twice by
    # the sampler), which is a free doubling of evidence, so the first one wins
    # and the repeat is dropped.
    deduped: list = []
    duplicates: list[str] = []
    _seen_ids: set[str] = set()
    for o in gate_rows:
        if o.instance_id in _seen_ids:
            duplicates.append(o.instance_id)
            continue
        _seen_ids.add(o.instance_id)
        deduped.append(o)
    if duplicates:
        res.notes.append(
            f"{len(duplicates)} duplicate paired observation(s) were dropped "
            f"before the gate saw them ({', '.join(duplicates[:3])}): one "
            f"case#run is one observation")
    gate_rows = deduped
    if gate_rows:
        pool = [o.instance_id for o in gate_rows]
        sampler = InstanceSampler(pool, salt=chash, reuse_window=0)
        order = sampler.draw(chash, len(pool))
        gate.bind_instances(order)
        by_id = {o.instance_id: o for o in gate_rows}
        for iid in order.instance_ids:
            gate.update(by_id[iid].pair(), instance_id=iid)
        res.order = order.as_dict()

    # ---- the floor -----------------------------------------------------
    corpus.reset_candidate()
    for o in floor_rows:
        corpus.record_candidate(o.case, bool(o.with_))
    verdict = corpus.check()
    res.floor = verdict.as_dict()

    # this run's baseline arm builds tomorrow's protected corpus
    for o in executed:
        corpus.record_incumbent(o.case, bool(o.without))

    summary = gate.summary()
    # `summary()` does not carry these two; a certificate that does not say
    # which gate and which harm level produced it is not auditable.
    summary["kind"] = "PairedBinaryGate"
    summary["harm_alpha"] = harm_alpha
    res.gate = summary
    res.n_pairs = int(summary.get("n_pairs", 0))
    res.n_informative = int(summary.get("n_informative", 0))
    res.n_ties = int(summary.get("n_ties", 0))

    # ---- decision precedence: mechanical first (PROCTOR) ---------------
    state_name = str(summary.get("state") or "CONTINUE")
    if verdict.verdict == "BLOCKED":
        decision = "BLOCKED"
        reason = (f"TaskFloor: {verdict.reason} "
                  f"({', '.join(verdict.confirmed_flips[:4])})")
    elif state_name == "ACCEPT":
        decision, reason = "ACCEPT", (
            f"wealth {summary.get('wealth', 0):.3g} >= 1/alpha "
            f"{1 / alpha:.3g} on {res.n_informative} discordant pair(s)")
        if verdict.verdict != "PASS":
            if require_floor:
                decision = "HOLD"
                reason = (f"gate says ACCEPT but the TaskFloor is "
                          f"{verdict.verdict}: {verdict.reason} "
                          f"(--require-floor)")
            else:
                res.notes.append(
                    f"the TaskFloor is {verdict.verdict} ({verdict.reason}): "
                    f"this ACCEPT is evidence of improvement, NOT a "
                    f"no-regression guarantee")
    elif state_name in ("REJECT", "NSF"):
        decision = state_name
        reason = str(summary.get("reason") or state_name)
    else:
        decision = "HOLD"
        reason = (f"gate is still CONTINUE: wealth {summary.get('wealth', 0):.3g} "
                  f"< 1/alpha {1 / alpha:.3g} after {res.n_informative} "
                  f"discordant pair(s) of {res.n_pairs}")
    res.decision, res.reason = decision, reason
    if res.n_informative == 0:
        res.notes.append(
            "zero discordant pairs: the two arms agreed everywhere they ran. "
            "'No effect' is the honest answer and it cost no alpha.")

    # ---- spend the round only if evidence was actually examined --------
    if prior_round is None:
        if res.n_informative == 0:
            sched.release()
            res.notes.append("schedule round released (no evidence was examined "
                             "at that level), so the budget is untouched")
            round_no = 0
            res.alpha = alpha
        else:
            round_no = sched.commit(chash)
    res.schedule = dict(sched.to_dict(), roundUsed=round_no,
                        spent=sched.spent(), remaining=sched.remaining_budget())

    # ---- anti-rewind: mechanical, and it runs BEFORE the certificate ----
    next_round = _next_round(state, str(cid))
    cumulative = max(sched.spent(), alpha if round_no else 0.0)
    rewind = anti_rewind_problems(
        state, candidate_id=str(cid), content_hash_=chash, summary=summary,
        round_no=round_no, alpha_spent=(alpha if round_no else 0.0),
        cumulative_alpha=cumulative, next_round=next_round)
    if rewind:
        res.notes.extend(rewind)
        if res.decision == "ACCEPT":
            # PROCTOR: a mechanical check outranks the gate, never the reverse.
            # An ACCEPT built on a rewound wealth process is exactly the free
            # alpha the anti-rewind fields exist to deny.
            res.decision = "HOLD"
            res.reason = (f"the gate said ACCEPT, but the ledger's anti-rewind "
                          f"check refuses it: {rewind[0]}")
            decision, reason = res.decision, res.reason

    # ---- the certificate ------------------------------------------------
    cert_dict = None
    if persist:
        save_protected(state, corpus)
        save_schedule(state, sched)
        cert = Certificate.from_gate(
            candidate_id=str(cid), round=next_round, gate=gate,
            decision=decision, alpha_spent=(alpha if round_no else 0.0),
            cumulative_alpha=cumulative,
            metrics={"gate": summary, "floor": res.floor,
                     "schedule": {"round": round_no, "alpha": alpha,
                                  "delta0": sched.delta0,
                                  "remaining": sched.remaining_budget()},
                     "pairs": {"total": res.n_pairs,
                               "freeTies": res.n_free_ties,
                               "floorPairs": res.n_floor_pairs,
                               "informative": res.n_informative},
                     "evidence": evidence or {},
                     "antiRewind": rewind,
                     "surface": surface, "declaredPaths": declared_paths},
            content_hash=chash, schedule_round=round_no,
            instance_ids=list(order.instance_ids)[:200] if order else [],
            evaluator_id=f"precedent/{__version__}",
            verification_rung="execution", closure="human_in",
            note=f"{decision}: {reason}"[:400], ts=now_iso(now))
        # `append` returns the *stored* certificate, with prev_hash/hash filled
        # in; the unstored one has neither, and a certificate without its hash
        # is not a receipt.
        cert_dict = state.ledger().append(cert).as_dict()
    res.certificate = cert_dict

    # ---- the docket (never auto-apply) ---------------------------------
    if persist:
        res.proposal_id = append_proposal(state, {
            "ts": now_iso(now), "kind": "examined", "surface": surface,
            "candidateId": str(cid), "contentHash": chash,
            "declaredPaths": declared_paths,
            "hypothesis": hypothesis or getattr(candidate, "description", "") or "",
            "expectedEffect": expected_effect,
            "decision": decision, "reason": reason,
            "gate": {k: summary.get(k) for k in
                     ("state", "wealth", "n_pairs", "n_informative", "n_wins",
                      "n_losses", "n_ties", "alpha", "p_value", "harm_wealth")},
            "floor": res.floor,
            "schedule": {"round": round_no, "alpha": alpha},
            "evidence": evidence or {},
            "certificateHash": (cert_dict or {}).get("hash"),
            "antiRewind": rewind,
            "applied": False,
            "note": "precedent never applies a proposal; this is a decision "
                    "record and a docket entry",
        })
    return res


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------

def verify_acceptance(state) -> dict:
    """Replay the gate certificates through ``acceptor.CandidateMonitor``.

    ``precedent`` keeps lifecycle events, docket decisions and gate
    certificates in **one** chain, so the ledger itself runs non-strict.  The
    anti-rewind fields (``evidence_hash``, ``prev_evidence_hash``, ``n_pairs``,
    ``schedule_round``) are still written on every gate certificate, which is
    what lets this function check them after the fact.
    """
    from acceptor import CandidateMonitor, LedgerViolation
    mon = CandidateMonitor()
    n = 0
    problems: list[str] = []
    try:
        records = state.ledger().records()
    except Exception as exc:                             # pragma: no cover
        return {"ok": False, "checked": 0, "problems": [str(exc)]}
    for rec in records:
        d = rec.as_dict() if hasattr(rec, "as_dict") else rec
        if not str(d.get("algorithm", "")).startswith("PairedBinaryGate"):
            continue
        n += 1
        cert = Certificate.from_dict(d)
        try:
            mon.validate(cert)
        except LedgerViolation as exc:
            problems.append(f"{cert.candidate_id} round {cert.round}: {exc}")
        mon.record(d)
    return {"ok": not problems, "checked": n, "problems": problems}


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

_MARK = {"ACCEPT": "✅", "REJECT": "❌", "BLOCKED": "⛔", "NSF": "🪙", "HOLD": "⏸"}


def render_acceptance(res: AcceptanceResult, examine_result=None) -> str:
    g = res.gate or {}
    L = [f"# precedent examine — {res.candidate_id}", ""]
    if examine_result is not None:
        cand = examine_result.candidate
        L.append(f"候选: `{cand.id}`  surface=`{cand.surface}`  "
                 f"content_hash=`{res.content_hash[:16]}`")
        if cand.paths:
            L.append(f"路径: " + ", ".join(f"`{p}`" for p in cand.paths[:4]))
        n_elig = len([c for c in examine_result.cassettes if c.eligible])
        L.append(f"磁带: {len(examine_result.cassettes)} 个（可执行 {n_elig}，"
                 f"按构造平局 {len(examine_result.cassettes) - n_elig}）")
        for c in examine_result.cassettes[:8]:
            v = (c.verification or {}).get("command", "(precedents only)")
            git = "git ✅" if c.git else "git —"
            hist = f"history<{c.fork_line}"
            flag = "eligible" if c.eligible else f"TIE: {c.tie_reason[:60]}"
            L.append(f"  - `{c.case_name}` {git} {hist} · `{v[:60]}` · {flag}")
        if examine_result.skipped:
            L.append(f"  跳过 {len(examine_result.skipped)} 个会话："
                     f"{examine_result.skipped[0].get('reason', '')}"
                     f"{' …' if len(examine_result.skipped) > 1 else ''}")
        if examine_result.run is not None:
            r = examine_result.run
            L.append("")
            L.append(f"`claude plugin eval`: "
                     + ("不可用 — " + r.reason if r.unavailable else
                        f"exit {r.returncode}, {len((r.payload or {}).get('cases') or [])} case"))
            if r.dropped_flags:
                L.append(f"  本机 claude 不认识的参数（已丢弃并如实记录）: "
                         f"{', '.join(r.dropped_flags)}")
        if examine_result.fallback:
            f = examine_result.fallback
            L.append(f"回退配对 `claude -p`: {f.get('nCalls')} 次调用，"
                     f"${f.get('spentUsd', 0):.4f} / 上限 ${f.get('maxCostUsd')}")
            if f.get("stopped"):
                L.append(f"  **{f['stopped']}**")
    L.append("")
    L.append(f"## 判定 {_MARK.get(res.decision, '')} **{res.decision}**")
    L.append("")
    L.append(f"- 原因: {res.reason}")
    L.append(f"- 配对: {res.n_pairs} 对（平局 {res.n_ties}，其中按构造免费 "
             f"{res.n_free_ties}；不一致 {res.n_informative}）")
    if g:
        L.append(f"- 门控: {g.get('kind', 'PairedBinaryGate')}/{g.get('betting')} "
                 f"wealth={g.get('wealth', 0):.4g} 阈值=1/α={1 / res.alpha:.4g} "
                 f"p={g.get('p_value', 1):.3g}")
        if g.get("harm_wealth") is not None:
            L.append(f"- 伤害鞅: wealth={g['harm_wealth']:.4g} "
                     f"(α_harm={g.get('harm_alpha')})")
    else:
        L.append("- 门控: 未运行（没有配对结果可喂给它）")
    fl = res.floor or {}
    if fl:
        L.append(f"- TaskFloor: **{fl.get('verdict')}** — {fl.get('reason')} "
                 f"(受保护 {fl.get('n_protected', 0)}，本次判定 "
                 f"{res.n_floor_pairs} 对)")
    sc = res.schedule or {}
    if sc:
        L.append(f"- 误差预算: round {sc.get('roundUsed')} α={res.alpha:.3g}，"
                 f"已花 {sc.get('spent', 0):.4g} / δ0={sc.get('delta0')}，"
                 f"剩余 {sc.get('remaining', 0):.4g}")
    else:
        L.append(f"- 误差预算: 未支出（α 只在真的检验了证据时才扣，δ0={DEFAULT_ALPHA0}）")
    if res.certificate:
        L.append(f"- 证书: `{(res.certificate.get('hash') or '')[:16]}` → ledger")
    if res.proposal_id:
        L.append(f"- docket: `{res.proposal_id}` — **不会自动应用**；"
                 f"`precedent docket confirm {res.proposal_id}` 只是记录你的决定")
    for n in res.notes:
        L.append(f"- ⚠ {n}")
    L.append("")
    return "\n".join(L) + "\n"
