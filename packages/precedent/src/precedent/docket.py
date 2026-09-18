# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""THE DOCKET — one queue, with the evidence attached, that never piles up silently.

Two kinds of thing wait for a human here:

* **rule candidates** — a topic the miner compiled into a DSL v1 rule, with its
  TEMPORAL BIRTH GATE counts;
* **write candidates** — a governed write the ownership hook recorded, with the
  pre-image sha256 and a ``+N −M lines`` diff summary.

Three verbs: ``confirm`` (a rule becomes ``active`` in ``precedents.json``; a
write is accepted), ``reject`` (with the reason, and rejected rule drafts feed
the next LLM prompt as negative examples), ``snooze`` (14 days — and the day it
comes back it is *older*, not gone).

The rule that makes this a docket rather than a queue: **passing is silent,
blocking is loud, starvation is never silent.**  ``precedent report`` raises a
STARVATION alarm for anything pending more than 7 days, because the production
failure this design is copied away from is write_approval accumulating 241
staged writes over eight weeks with nobody told (hermes#105770).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from acceptor import Certificate

from . import SCHEMA_VERSION, __version__
from .ownership import read_write_candidates, summarise_candidates
from .proposals import read_proposals
from .rules import RuleError, validate_rule
from . import i18n
from .i18n import t
from .state import now_iso

__all__ = [
    "SNOOZE_DAYS", "STARVATION_DAYS", "build_entries", "confirm", "confirm_rule",
    "read_decisions", "reject", "render_batch", "render_docket", "retire_rule",
    "snooze", "starving",
]

SNOOZE_DAYS = 14
STARVATION_DAYS = 7

_VERDICT_MARK = {"PASS": "✅", "FAIL": "❌", "INSUFFICIENT": "⚠️"}


def _parse(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(
            timezone.utc)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------

def read_decisions(state) -> dict:
    doc = state.read_json(state.docket_path, default=None)
    if not isinstance(doc, dict) or not isinstance(doc.get("decisions"), dict):
        return {"schemaVersion": SCHEMA_VERSION, "decisions": {}}
    return doc


def _record(state, entry_id: str, status: str, reason: str | None = None,
            until: str | None = None, now: datetime | None = None) -> dict:
    doc = read_decisions(state)
    rec = {"status": status, "at": now_iso(now), "by": "user"}
    if reason:
        rec["reason"] = reason
    if until:
        rec["until"] = until
    doc["decisions"][entry_id] = rec
    doc["schemaVersion"] = SCHEMA_VERSION
    doc["updatedAt"] = now_iso(now)
    state.write_json(state.docket_path, doc)
    return rec


# --------------------------------------------------------------------------
# entries
# --------------------------------------------------------------------------

def _effective_status(raw: dict | None, now: datetime) -> tuple[str, str | None]:
    if not isinstance(raw, dict):
        return "pending", None
    status = raw.get("status") or "pending"
    until = raw.get("until")
    if status == "snoozed":
        u = _parse(until)
        if u is not None and u <= now:
            return "pending", until          # the snooze expired: back on the list
    return status, until


def _rule_entry(cand: dict, decisions: dict, now: datetime) -> dict:
    gate = cand.get("birth") or {}
    status, until = _effective_status(decisions.get(cand.get("id")), now)
    created = cand.get("compiledAt") or cand.get("createdAt")
    age = (now - _parse(created)).days if _parse(created) else None
    evidence = []
    if gate:
        evidence.append(t("docket.gate", verdict=gate.get("verdict", "?"))
                        + f" {_VERDICT_MARK.get(gate.get('verdict'), '')} — "
                        + f"{gate.get('counts', 'n/a')}")
        for k in ("t0", "hit", "quietAfter", "before"):
            if gate.get(k):
                evidence.append(f"{k}: {gate[k]}")
        for f in (gate.get("failures") or [])[:3]:
            evidence.append(f"✗ {f}")
    if cand.get("quote"):
        evidence.append(t("docket.quote", date=cand.get("quoteDate", "?"),
                          quote=cand["quote"]))
    if cand.get("topics"):
        evidence.append("topics: " + ", ".join(cand["topics"]))
    if cand.get("origin"):
        evidence.append(f"origin: {cand['origin']}")
    return {
        "id": cand.get("id"), "kind": "rule", "status": status,
        "snoozedUntil": until, "created": created, "ageDays": age,
        "title": (cand.get("message") or "")[:100],
        "gateVerdict": gate.get("verdict"), "gateCounts": gate.get("counts"),
        "evidence": evidence,
        "diff": json.dumps({k: v for k, v in cand.items()
                            if k in ("tool", "action", "match", "matchers",
                                     "scope", "cwd_glob", "message")},
                           ensure_ascii=False, indent=2),
        "raw": cand,
    }


def _write_entry(rec: dict, decisions: dict, now: datetime) -> dict:
    status, until = _effective_status(decisions.get(rec.get("id")), now)
    if status == "pending" and rec.get("status") == "blocked":
        # A confirmed precedent already denied this call, so the write never
        # happened and there is nothing left for a human to decide.  It stays
        # in the stream as evidence; it does not stand in the queue.
        status = "blocked"
    created = rec.get("ts")
    age = (now - _parse(created)).days if _parse(created) else None
    snap = rec.get("snapshot") or {}
    diff = rec.get("diff") or {}
    evidence = [
        t("docket.write.evidence", agent=rec.get("agent"), tool=rec.get("tool"),
          how=rec.get("how"), governed=rec.get("governed")),
        f"owner: {rec.get('owner')} — {rec.get('ownerWhy')}",
        f"hook decision: {rec.get('decision')}",
        f"snapshot: {(snap.get('sha256') or '(none)')[:16]} "
        f"({snap.get('bytes', 0)} bytes, existed={snap.get('existed')})",
    ]
    if rec.get("blockedBy"):
        evidence.append(f"blocked before it ran by precedent {rec['blockedBy']}")
    if rec.get("agentId"):
        evidence.append(f"agent_id: {rec['agentId']}")
    if rec.get("session"):
        evidence.append(f"session: {rec['session']}")
    return {
        "id": rec.get("id"), "kind": "write", "status": status,
        "snoozedUntil": until, "created": created, "ageDays": age,
        "title": f"{rec.get('tool')} → {rec.get('path')}",
        "path": rec.get("path"), "agent": rec.get("agent"),
        "gateVerdict": None, "gateCounts": None,
        "evidence": evidence,
        "diff": diff.get("summary", ""),
        "raw": rec,
    }


def _proposal_entry(prop: dict, decisions: dict, now: datetime) -> dict:
    """An examiner / improver proposal: a bounded edit with its gate verdict.

    A proposal is **never applied by precedent**.  Confirming it records the
    human decision and prints the command that would apply it; the bytes stay
    the user's to write.  That is why the entry carries the full content hash
    and the declared paths -- so "what exactly am I agreeing to" has an answer
    that does not require trusting this tool.
    """
    status, until = _effective_status(decisions.get(prop.get("id")), now)
    created = prop.get("ts")
    age = (now - _parse(created)).days if _parse(created) else None
    decision = prop.get("decision") or "HOLD"
    gate = prop.get("gate") or {}
    evidence = [
        f"surface: {prop.get('surface')} · 来源 {prop.get('kind')} "
        f"· 判定 **{decision}**",
        f"declared_paths: " + ", ".join(prop.get("declaredPaths") or ["(none)"]),
        f"hypothesis: {(prop.get('hypothesis') or '(none)')[:200]}",
        f"expected_effect: {(prop.get('expectedEffect') or '(none)')[:200]}",
        f"reason: {(prop.get('reason') or '')[:240]}",
        f"content_hash: {(prop.get('contentHash') or '')[:16]}",
    ]
    if gate:
        if gate.get("verdict"):
            evidence.append(t("docket.gate", verdict=gate.get("verdict"))
                            + f" — {gate.get('counts', gate.get('note', 'n/a'))}")
        if gate.get("state"):
            evidence.append(
                f"配对门控 {gate.get('state')} wealth={gate.get('wealth')} "
                f"pairs={gate.get('n_pairs')} 不一致={gate.get('n_informative')} "
                f"(平局 {gate.get('n_ties')})")
    floor = prop.get("floor") or {}
    if floor:
        evidence.append(f"TaskFloor {floor.get('verdict')} — {floor.get('reason')}")
    if prop.get("certificateHash"):
        evidence.append(f"证书 {prop['certificateHash'][:16]} 已入账本")
    evidence.append("precedent 不会替你写入这条改动；confirm 只是记录你的决定")
    return {
        "id": prop.get("id"), "kind": "proposal", "status": status,
        "snoozedUntil": until, "created": created, "ageDays": age,
        "title": f"{prop.get('surface')} → "
                 f"{', '.join(prop.get('declaredPaths') or [])[:80]}"
                 or (prop.get("hypothesis") or "")[:80],
        "gateVerdict": decision,
        "gateCounts": gate.get("counts") or gate.get("state"),
        "evidence": evidence,
        "diff": (prop.get("content") or "")[:4000],
        "raw": prop,
    }


def build_entries(state, now: datetime | None = None,
                  include_decided: bool = False) -> list[dict]:
    """Every docket entry, newest first, with its evidence attached."""
    now = now or datetime.now(timezone.utc)
    decisions = read_decisions(state)["decisions"]
    confirmed = {r.get("id") for r in state.precedents()}
    out: list[dict] = []
    for cand in state.candidates():
        e = _rule_entry(cand, decisions, now)
        if e["id"] in confirmed and e["status"] == "pending":
            e["status"] = "confirmed"
        out.append(e)
    for rec in read_write_candidates(state, apply_decisions=False):
        out.append(_write_entry(rec, decisions, now))
    for prop in read_proposals(state):
        out.append(_proposal_entry(prop, decisions, now))
    if not include_decided:
        out = [e for e in out if e["status"] in ("pending", "snoozed")]
    out.sort(key=lambda e: (e.get("created") or ""), reverse=True)
    return out


def starving(entries: list[dict], days: int = STARVATION_DAYS) -> list[dict]:
    return [e for e in entries
            if e["status"] == "pending" and (e.get("ageDays") or 0) >= days]


# --------------------------------------------------------------------------
# the three verbs
# --------------------------------------------------------------------------

def _behaviour_key(rule: dict) -> str:
    """What this rule *does*, ignoring how it was worded or when it was built.

    Two rules with the same tool, action, scope and matcher set will interrupt
    exactly the same calls, whatever their ids or messages say.  Matching on
    this rather than on ``id`` is what makes the rejection memory survive a
    re-compile that reworded the message or re-derived a different id.
    """
    matchers = sorted(
        json.dumps(m, sort_keys=True, ensure_ascii=False)
        for m in (rule.get("matchers") or []) if isinstance(m, dict))
    return json.dumps({
        "tool": rule.get("tool"), "hook": rule.get("hook"),
        "action": rule.get("action"), "match": rule.get("match"),
        "scope": rule.get("scope"), "cwd_glob": rule.get("cwd_glob"),
        "matchers": matchers,
    }, sort_keys=True, ensure_ascii=False)


def prior_rejection(state, rule: dict) -> dict | None:
    """The most recent time this rule was thrown out, or ``None``.

    Step (5) of the loop is "re-evolve", and README:37 promises that rejected
    drafts come back as negative examples.  They did -- to the *proposer*.
    The *acceptor* never looked.  A rule the user retired by hand could be
    re-compiled, pass the birth gate again (it had passed the first time; that
    is why it was enforced), and `confirm` would walk it straight back to
    ``active`` with rc=0 and not one word about the rejection.  Verified on a
    synthetic state built entirely by the shipped CLI.

    A gate that cannot remember being overruled is not an acceptor, it is a
    filter, and the thing the user is most likely to re-propose is the thing
    they already threw out once.

    Matched by id first, then by behaviour -- see :func:`_behaviour_key`.
    """
    try:
        rows = [json.loads(line) for line in
                open(state.path("rejected.jsonl"), encoding="utf-8")
                if line.strip()]
    except (OSError, ValueError):
        return None
    want_id = rule.get("id")
    want_key = _behaviour_key(rule)
    for row in reversed(rows):                       # newest first
        body = row.get("draft")
        if body is None:
            body = row.get("rule")                   # rows written before 2026-09-18
        if not isinstance(body, dict):
            continue
        if (want_id and body.get("id") == want_id) or _behaviour_key(body) == want_key:
            return row
    return None


def confirm_rule(state, rule_id: str, force: bool = False,
                 now: datetime | None = None) -> tuple[int, list[str]]:
    """A compiled candidate becomes an ``active`` precedent.  Returns (rc, lines).

    ``precedents.json`` is the one file the hook trusts, so nothing reaches it
    without passing the *current* schema and the *current* regex linter — a
    candidate written by an older build (or edited by hand) is re-validated
    here.  ``--force`` overrides the birth-gate **verdict**, never the schema.
    """
    cands = state.candidates()
    match = [c for c in cands
             if c.get("id") == rule_id or c.get("topic") == rule_id
             or rule_id in (c.get("topics") or [])]
    if not match:
        lines = [f"precedent: no compiled candidate {rule_id!r}; "
                 f"run `precedent compile <topic-id>` first."]
        if cands:
            lines.append("  candidates: " + ", ".join(c.get("id", "?") for c in cands))
        return 2, lines
    rule = dict(match[-1])
    gate = rule.get("birth") or {}
    try:
        clean = validate_rule(rule, require_id=True)
    except RuleError as exc:
        return 2, [f"precedent: candidate {rule.get('id')!r} is not a valid DSL v1 "
                   f"rule and will not be confirmed: {exc}",
                   "  re-run `precedent compile` for this topic to rebuild it."]
    for key in ("birth", "topics", "compiledAt", "origin", "template"):
        if rule.get(key) is not None and key not in clean:
            clean[key] = rule[key]
    rule = clean

    if gate.get("verdict") != "PASS" and not force:
        return 1, [f"precedent: {rule['id']} did not PASS the temporal birth gate "
                   f"({gate.get('verdict')}: {gate.get('counts', 'n/a')}). "
                   f"Mechanical rejection overrides everything (PROCTOR); pass "
                   f"--force only if you know why."]

    prior = prior_rejection(state, rule)
    if prior is not None and not force:
        when = str(prior.get("at") or prior.get("ts") or "?")[:10]
        how = {"retire": "retired after being enforced",
               "docket": "rejected in the docket"}.get(prior.get("source"),
                                                       str(prior.get("source") or "rejected"))
        return 1, [
            f"precedent: {rule['id']} was already thrown out once and is being "
            f"proposed again — refusing.",
            f"  {when}: {how}",
            f"  reason given: {str(prior.get('reason') or '(none recorded)')[:160]}",
            f"  The birth gate only looks at the record; it cannot see that you "
            f"already decided this. If the reason no longer holds — a narrower "
            f"scope, a different matcher — re-compile it as a *different* rule "
            f"rather than the same one, or pass --force to overrule yourself.",
            f"  ({state.rejected_path})",
        ]

    rule["status"] = "active"
    rule["confirmedBy"] = "user"
    rule["confirmedAt"] = now_iso(now)
    if gate.get("verdict") != "PASS":
        rule["forcedPastGate"] = gate.get("verdict")
    rules = [r for r in state.precedents() if r.get("id") != rule["id"]]
    rules.append(rule)
    state.write_precedents(rules, now=now)

    state.ledger().append(Certificate(
        candidate_id=rule["id"], round=2, algorithm="temporal-birth-gate/v1",
        decision="ACCEPT", alpha_spent=0.0, cumulative_alpha=0.0,
        metrics=gate, evaluator_id=f"precedent/{__version__}",
        verification_rung="execution", closure="human_in",
        note=f"confirmed by the user: {rule.get('message', '')[:120]}"
             + (" [--force past the gate]" if force and
                gate.get("verdict") != "PASS" else "")))
    _record(state, rule["id"], "confirmed", now=now)
    return 0, [
        f"precedent: confirmed {rule['id']} → {state.precedents_path}",
        f"  {rule['tool']}  {rule['action']}  [{rule.get('match', 'all')}] "
        f"{json.dumps(rule.get('matchers'), ensure_ascii=False)}",
        "  " + t("docket.gate", verdict=gate.get("verdict"))
        + f" — {gate.get('counts', 'n/a')}",
        "  下一步: precedent hooks install claude-code   (默认 dry-run)",
    ]


def retire_rule(state, rule_id: str, reason: str | None = None,
                now: datetime | None = None) -> tuple[int, list[str]]:
    """An **active** precedent stops being enforced.  Returns (rc, lines).

    Rejecting a rule that is still only a *candidate* is enough to keep it out
    of ``precedents.json``.  Rejecting one that has already been confirmed is
    not: the hook reads ``precedents.json`` and enforces every entry whose
    ``status`` is exactly ``"active"``, so a rule the user has just thrown out
    would go on denying their tool calls until someone noticed.  Retiring is
    the verb that closes that gap — and it *retires*, never deletes, because
    the funnel, the ledger and the next ``compile --llm`` prompt all need to
    know the rule existed and why it stopped.
    """
    now = now or datetime.now(timezone.utc)
    rules = state.precedents()
    match = [r for r in rules if r.get("id") == rule_id]
    if not match:
        return 2, [f"precedent: no precedent {rule_id!r} in {state.precedents_path}",
                   "  (a compiled candidate that was never confirmed is "
                   "`precedent docket reject <id>`)"]
    rule = match[-1]
    if rule.get("status") != "active":
        return 0, [f"precedent: {rule_id} is already {rule.get('status')!r} — "
                   f"nothing to do (idempotent)."]
    rule["status"] = "retired"
    rule["retiredAt"] = now_iso(now)
    rule["retiredReason"] = reason or "(none given)"
    state.write_precedents(rules, now=now)
    _certify(state, rule_id, "REJECT",
             {"kind": "rule", "was": "active", "reason": rule["retiredReason"],
              "gate": (rule.get("birth") or {}).get("verdict")},
             note=f"user retired an enforced rule: {rule['retiredReason'][:120]}",
             now=now)
    state.ledger().append_event(rule_id, "retired",
                                {"reason": rule["retiredReason"],
                                 "message": rule.get("message", "")[:120]})
    _record(state, rule_id, "rejected", reason=reason, now=now)
    _append_rejected(state, {
        "id": rule_id, "at": now_iso(now),
        "reason": reason or "user retired an enforced rule",
        # `draft`, not `rule`: every reader of rejected.jsonl asks for `draft`.
        "draft": {k: v for k, v in rule.items() if k != "birth"},
        "source": "retire"})
    return 0, [
        f"precedent: retired {rule_id} — it is no longer enforced",
        f"  {rule.get('message', '')[:100]}",
        f"  the hook only ever reads status == \"active\"; re-run "
        f"`precedent hooks install claude-code` to drop its matcher from "
        f"settings.json too (the rule itself is already inert).",
        f"  它会作为反例进入下一次 `compile --llm` 的提示词 ({state.rejected_path})",
    ]


def _find(state, entry_id: str, now: datetime) -> dict | None:
    for e in build_entries(state, now=now, include_decided=True):
        if e["id"] == entry_id:
            return e
    return None


def confirm(state, entry_id: str, force: bool = False,
            now: datetime | None = None) -> tuple[int, list[str]]:
    now = now or datetime.now(timezone.utc)
    entry = _find(state, entry_id, now)
    if entry is None:
        return 2, [f"precedent: no docket entry {entry_id!r} "
                   f"(`precedent docket` lists them)"]
    if entry["kind"] == "rule":
        return confirm_rule(state, entry_id, force=force, now=now)
    if entry["kind"] == "proposal":
        return _confirm_proposal(state, entry, force=force, now=now)
    rec = entry["raw"]
    _record(state, entry_id, "confirmed", now=now)
    _certify(state, entry_id, "ACCEPT", {
        "kind": "write", "path": rec.get("path"), "agent": rec.get("agent"),
        "tool": rec.get("tool"), "diff": (rec.get("diff") or {}).get("summary"),
        "snapshot": (rec.get("snapshot") or {}).get("sha256")},
        note=f"user accepted the write to {rec.get('path')}", now=now)
    return 0, [f"precedent: accepted write {entry_id} — {rec.get('path')}",
               f"  {(rec.get('diff') or {}).get('summary', '')}",
               f"  这次接受不改所有权。要让同类写入以后直接放行："
               f"precedent own {rec.get('path')} --agent"]


def reject(state, entry_id: str, reason: str | None = None,
           now: datetime | None = None) -> tuple[int, list[str]]:
    now = now or datetime.now(timezone.utc)
    # A rule that is already being enforced needs more than a docket note: the
    # hook reads precedents.json, not the docket, so rejecting it has to retire
    # it as well or the user goes on being denied by a rule they just threw out.
    if any(r.get("id") == entry_id and r.get("status") == "active"
           for r in state.precedents()):
        return retire_rule(state, entry_id, reason=reason, now=now)
    entry = _find(state, entry_id, now)
    if entry is None:
        return 2, [f"precedent: no docket entry {entry_id!r}"]
    _record(state, entry_id, "rejected", reason=reason, now=now)
    raw = entry["raw"]
    if entry["kind"] == "rule":
        _certify(state, entry_id, "REJECT", {
            "kind": "rule", "reason": reason or "(none given)",
            "gate": (raw.get("birth") or {}).get("verdict")},
            note=f"user rejected the rule: {reason or '(no reason given)'}",
            now=now)
        _append_rejected(state, {
            "id": entry_id, "at": now_iso(now), "reason": reason or "user rejected",
            "draft": {k: v for k, v in raw.items() if k != "birth"},
            "source": "docket"})
        return 0, [f"precedent: rejected rule {entry_id}",
                   f"  它会作为反例进入下一次 `compile --llm` 的提示词 "
                   f"({state.rejected_path})"]
    if entry["kind"] == "proposal":
        _certify(state, entry_id, "REJECT", {
            "kind": "proposal", "surface": raw.get("surface"),
            "declaredPaths": raw.get("declaredPaths"),
            "contentHash": raw.get("contentHash"),
            "reason": reason or "(none given)"},
            note=f"user rejected the proposal: {reason or '(no reason given)'}",
            now=now)
        _append_rejected(state, {
            "id": entry_id, "at": now_iso(now),
            "reason": reason or "user rejected",
            "draft": {k: v for k, v in raw.items()
                      if k in ("surface", "declaredPaths", "hypothesis",
                               "expectedEffect", "rule")},
            "cluster": raw.get("clusterId"), "source": "docket"})
        return 0, [f"precedent: rejected proposal {entry_id}",
                   f"  它会作为反例进入下一次 `precedent improve` 的提示词 "
                   f"({state.rejected_path})"]
    snap = raw.get("snapshot") or {}
    _certify(state, entry_id, "REJECT", {
        "kind": "write", "path": raw.get("path"),
        "reason": reason or "(none given)", "snapshot": snap.get("sha256")},
        note=f"user rejected the write to {raw.get('path')}", now=now)
    lines = [f"precedent: rejected write {entry_id} — {raw.get('path')}"]
    if snap.get("blob"):
        lines.append(f"  写入前的快照在 {snap['blob']}")
        lines.append(f"  还原: precedent undo --session {raw.get('session')} "
                     f"（先 dry-run），或手动比对上面的 blob")
    else:
        lines.append("  没有快照（文件当时不存在或过大）— 无法自动还原")
    return 0, lines


def snooze(state, entry_id: str, days: int = SNOOZE_DAYS,
           now: datetime | None = None) -> tuple[int, list[str]]:
    now = now or datetime.now(timezone.utc)
    entry = _find(state, entry_id, now)
    if entry is None:
        return 2, [f"precedent: no docket entry {entry_id!r}"]
    until = (now + timedelta(days=days)).astimezone(timezone.utc).isoformat()
    _record(state, entry_id, "snoozed", until=until, now=now)
    _certify(state, entry_id, "HOLD",
             {"until": until, "days": days, "kind": entry["kind"]},
             note=f"snoozed for {days} days — it comes back older, not gone",
             now=now)
    return 0, [f"precedent: snoozed {entry_id} until {until[:10]} ({days} 天)",
               "  到期后它会重新出现在 docket 里，并且**更老**——不会悄悄消失。"]




def _confirm_proposal(state, entry: dict, force: bool = False,
                      now: datetime | None = None) -> tuple[int, list[str]]:
    """Record that the user accepted a proposal.  **Never writes the edit.**

    The one thing this function must not do is apply the change: an acceptance
    layer that also writes is a proposer with a rubber stamp.  What it does is
    record the decision, chain a certificate, and print the diff's identity so
    the user can apply it themselves and check they got the same bytes.
    """
    raw = entry["raw"]
    decision = raw.get("decision") or "HOLD"
    if decision in ("REJECT", "BLOCKED", "NSF") and not force:
        return 1, [
            f"precedent: {entry['id']} was decided **{decision}** by the gate "
            f"({(raw.get('reason') or '')[:160]}).",
            "  Mechanical rejection overrides everything (PROCTOR); pass "
            "--force only if you know why."]
    _record(state, entry["id"], "confirmed", now=now)
    _certify(state, entry["id"], "ACCEPT", {
        "kind": "proposal", "surface": raw.get("surface"),
        "declaredPaths": raw.get("declaredPaths"),
        "contentHash": raw.get("contentHash"),
        "gateDecision": decision, "applied": False,
        "forced": bool(force and decision in ("REJECT", "BLOCKED", "NSF"))},
        note=f"user accepted the proposal for {raw.get('declaredPaths')}; "
             f"precedent did not write it",
        now=now)
    paths = raw.get("declaredPaths") or []
    lines = [f"precedent: accepted proposal {entry['id']} "
             f"({raw.get('surface')} → {', '.join(paths) or '(no path)'})",
             f"  门控判定 {decision} — {(raw.get('reason') or '')[:160]}",
             f"  content_hash {(raw.get('contentHash') or '')[:16]}",
             "",
             "  **precedent 没有、也不会替你写这个文件。**下一步由你自己动手："]
    if paths:
        lines.append(f"    precedent snapshot            # 先留一个内容寻址快照")
        lines.append(f"    $EDITOR {paths[0]}            # 然后按 docket 里的正文改")
    lines.append("  改完再跑 `precedent report` 看漏斗有没有从②走到③。")
    return 0, lines


def _certify(state, candidate_id: str, decision: str, metrics: dict,
             note: str = "", now: datetime | None = None):
    """One hash-chained certificate per docket decision.

    ``acceptor``'s ``Event`` vocabulary is deliberately tiny (activated /
    attributed / reverted / retired), so a *decision* is a Certificate — which
    is the right shape anyway: it carries the evidence it was made on.
    """
    return state.ledger().append(Certificate(
        candidate_id=candidate_id, round=1, algorithm="docket/v1",
        decision=decision, alpha_spent=0.0, cumulative_alpha=0.0,
        metrics=metrics, evaluator_id=f"precedent/{__version__}",
        verification_rung="execution", closure="human_in", note=note,
        ts=now_iso(now)))


def _append_rejected(state, row: dict) -> None:
    state.ensure()
    with open(state.rejected_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def render_docket(state, entries: list[dict], now: datetime | None = None,
                  show_diff: bool = True) -> str:
    now = now or datetime.now(timezone.utc)
    pend = [e for e in entries if e["status"] == "pending"]
    snoozed = [e for e in entries if e["status"] == "snoozed"]
    starved = starving(entries)
    lang = i18n.get_lang()
    L = [t("docket.title", lang, n=len(pend))
         + (t("docket.title.snoozed", lang, n=len(snoozed)) if snoozed else ""), ""]
    L.append(t("docket.generated", lang,
               ts=now.astimezone(timezone.utc).isoformat(), state=state.root))
    if starved:
        L.append("")
        L.append(t("docket.starvation", lang, n=len(starved),
                   days=STARVATION_DAYS,
                   ids=", ".join(e["id"] for e in starved[:6])))
    L.append("")
    if not entries:
        L.append(t("docket.empty", lang))
        return "\n".join(L) + "\n"
    for e in entries:
        age = ("" if e.get("ageDays") is None
               else t("docket.age", lang, n=e["ageDays"]))
        head = f"## `{e['id']}` · {e['kind']} · {e['status']}"
        if e["status"] == "snoozed" and e.get("snoozedUntil"):
            head += f" → {e['snoozedUntil'][:10]}"
        L.append(head)
        L.append("")
        L.append(f"- {e['title']}")
        L.append(t("docket.raised", lang, ts=e.get("created"), age=age))
        for ev in e["evidence"]:
            L.append(f"- {ev}")
        if show_diff and e.get("diff"):
            L.append("")
            if e["kind"] == "rule":
                L.append("```json")
                L.append(e["diff"])
                L.append("```")
            elif e["kind"] == "proposal":
                L.append("```")
                L.append(e["diff"][:2000])
                L.append("```")
            else:
                L.append(f"  diff: {e['diff']}")
        L.append("")
        L.append(t("docket.actions", lang, id=e["id"], days=SNOOZE_DAYS))
        L.append("")
    return "\n".join(L) + "\n"


def render_batch(state, entries: list[dict], now: datetime | None = None) -> str:
    """``--batch``: one digest, one screen, grouped — for a daily push."""
    now = now or datetime.now(timezone.utc)
    pend = [e for e in entries if e["status"] == "pending"]
    rules = [e for e in pend if e["kind"] == "rule"]
    writes = [e for e in pend if e["kind"] == "write"]
    props = [e for e in pend if e["kind"] == "proposal"]
    sm = summarise_candidates([e["raw"] for e in writes])
    starved = starving(entries)
    L = ["# precedent docket — batch digest", ""]
    lang = i18n.get_lang()
    L.append(t("docket.batch.counts", lang,
               ts=now.astimezone(timezone.utc).isoformat(), n=len(pend),
               rules=len(rules), writes=len(writes), props=len(props))
             + (t("docket.batch.starved", lang, n=len(starved),
                  days=STARVATION_DAYS) if starved else ""))
    L.append("")
    if rules:
        L.append(t("docket.batch.rules", lang))
        L.append("")
        L.append(t("docket.batch.rules.head", lang))
        L.append("|---|---|---|---|")
        for e in rules:
            L.append(f"| `{e['id']}` | {e.get('gateVerdict', '?')} "
                     f"{_VERDICT_MARK.get(e.get('gateVerdict'), '')} | "
                     f"{(e.get('gateCounts') or 'n/a')} | {e['title'][:60]} |")
        L.append("")
    if writes:
        L.append(t("docket.batch.writes", lang))
        L.append("")
        L.append(t("docket.batch.writes.n", lang, total=sm["total"],
                   sub=sm["subagentWrites"], asks=sm["asks"]))
        L.append(t("docket.batch.writes.dist", lang,
                   dist=json.dumps(sm["byGoverned"], ensure_ascii=False)))
        L.append("")
        L.append(t("docket.batch.writes.head", lang))
        L.append("|---|---|---|---|---|")
        for e in writes[:20]:
            r = e["raw"]
            L.append(f"| `{e['id']}` | {r.get('agent')} | "
                     f"`{(r.get('path') or '')[-52:]}` | "
                     f"{(r.get('diff') or {}).get('summary', '')} | "
                     f"{r.get('owner')} |")
        L.append("")
    if props:
        L.append(t("docket.batch.props", lang))
        L.append("")
        L.append(t("docket.batch.props.head", lang))
        L.append("|---|---|---|---|---|")
        for e in props[:20]:
            r = e["raw"]
            L.append(f"| `{e['id']}` | {r.get('surface')} | "
                     f"{r.get('decision')} | "
                     f"{(e.get('gateCounts') or (r.get('reason') or ''))[:60]} | "
                     f"`{', '.join(r.get('declaredPaths') or [])[-40:]}` |")
        L.append("")
    if not pend:
        L.append(t("docket.batch.none", lang))
        L.append("")
    L.append(t("docket.batch.footer", lang))
    L.append("")
    return "\n".join(L) + "\n"
