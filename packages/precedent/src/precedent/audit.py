# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""AUDIT — the funnel, the starvation alarms and the spend meter.

Step ④ of the loop, and the one that fails silently everywhere else: Hermes'
learning loop ran 41 background forks and shipped 0 updates for weeks with
nobody told; write_approval accumulated 241 staged writes over eight weeks.
The rule this module implements is therefore:

    passing is silent · blocking is loud · **starvation is never silent**

Four alarms, each one a thing that has actually happened in production:

``PENDING``   a docket entry older than 7 days — the 241-staged-writes failure.
``HOOK``      hook errors, quarantined regexes, an unreadable ``precedents.json``
              — enforcement is fail-open by design, so an outage is *quiet*.
``NO_FIRES``  active rules, installed for ≥14 days, and never once fired: either
              the agent reformed or the rule does not match reality.
``DRIFT``     what is installed is not what this build would install.

And the funnel: **proposed → accepted → activated → attributed**.  The last
column is the one the literature says everybody skips — "written but never
loaded", 41 of 60 artifacts on this machine — so here it is counted from hook
fires, not from intentions.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from .docket import STARVATION_DAYS, starving
from .llm import total_spend
from .ownership import GUARD_RULE_ID

__all__ = [
    "PRICES_USD_PER_MTOK", "alarms", "daily_rows", "funnel", "hook_health",
    "spend_meter", "transcript_spend",
]

#: First-party Anthropic list prices, USD per million tokens, as of the
#: 2026-06-24 model table.  Cache reads are ~0.1x input and cache writes ~1.25x
#: input (5-minute TTL).  These are **list** prices applied to transcript token
#: counts: the number below is an estimate of what those sessions would cost at
#: list rate, not a bill.  Subscription usage is not billed per token at all,
#: which is exactly why the number is labelled an estimate everywhere it is
#: printed.
PRICES_USD_PER_MTOK = {
    "opus": (5.00, 25.00),
    "sonnet-5": (2.00, 10.00),
    "sonnet-4-6": (3.00, 15.00),
    "sonnet": (2.00, 10.00),
    "haiku": (1.00, 5.00),
    "fable": (10.00, 50.00),
    "mythos": (10.00, 50.00),
}
CACHE_READ_FACTOR = 0.1
CACHE_WRITE_FACTOR = 1.25
PRICES_AS_OF = "2026-06-24"

NO_FIRE_DAYS = 14


def _parse(ts):
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(
            timezone.utc)
    except ValueError:
        return None


def _read_jsonl(path: str, cap_bytes: int = 32 * 1024 * 1024) -> list[dict]:
    rows: list[dict] = []
    try:
        if os.path.getsize(path) > cap_bytes:
            with open(path, "rb") as fh:
                fh.seek(os.path.getsize(path) - cap_bytes)
                fh.readline()
                raw = fh.read().decode("utf-8", "replace")
        else:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read()
    except OSError:
        return rows
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# --------------------------------------------------------------------------
# spend
# --------------------------------------------------------------------------

def _price_for(model: str):
    m = (model or "").lower()
    for key in ("fable", "mythos", "opus", "haiku"):
        if key in m:
            return PRICES_USD_PER_MTOK[key], key
    if "sonnet" in m:
        if "4-6" in m or "4.6" in m:
            return PRICES_USD_PER_MTOK["sonnet-4-6"], "sonnet-4-6"
        return PRICES_USD_PER_MTOK["sonnet-5"], "sonnet-5"
    return None, None


def transcript_spend(scan_result) -> dict:
    """Token usage recorded in the transcripts, priced at list rate.

    The transcripts carry ``message.usage`` per assistant turn; a session is
    attributed to the model(s) it names.  Where a session names more than one
    model the tokens are attributed to the first, and that is said out loud in
    ``mixedModelSessions``.
    """
    totals = {"input_tokens": 0, "output_tokens": 0,
              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    by_model: dict[str, dict] = {}
    unpriced: set[str] = set()
    mixed = 0
    usd = 0.0
    for sr in getattr(scan_result, "session_receipts", []):
        models = list(sr.models) or ["unknown"]
        if len(models) > 1:
            mixed += 1
        model = models[0]
        row = by_model.setdefault(model, {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "sessions": 0, "usd": 0.0})
        row["sessions"] += 1
        for k in totals:
            v = int(sr.usage.get(k) or 0)
            totals[k] += v
            row[k] += v
        price, _key = _price_for(model)
        if price is None:
            unpriced.add(model)
            continue
        pin, pout = price
        cost = (row["input_tokens"] * pin
                + row["output_tokens"] * pout
                + row["cache_read_input_tokens"] * pin * CACHE_READ_FACTOR
                + row["cache_creation_input_tokens"] * pin * CACHE_WRITE_FACTOR
                ) / 1e6
        usd += cost - row["usd"]
        row["usd"] = cost
    return {"tokens": totals, "byModel": by_model, "usdEstimate": round(usd, 4),
            "unpricedModels": sorted(unpriced), "mixedModelSessions": mixed,
            "pricesAsOf": PRICES_AS_OF, "estimate": True}


def spend_meter(state, scan_result=None) -> dict:
    """Transcript usage (estimated) + our own ``claude -p`` ledger (exact)."""
    rows = _read_jsonl(state.spend_path)
    ours = {"calls": len(rows), "usd": round(total_spend(state), 6),
            "byCommand": {}, "budgetRefusals": 0, "lastTs": None}
    for r in rows:
        cmd = r.get("command") or "?"
        c = ours["byCommand"].setdefault(cmd, {"calls": 0, "usd": 0.0})
        c["calls"] += 1
        c["usd"] = round(c["usd"] + float(r.get("costUsd") or 0.0), 6)
        if r.get("ok") is False:
            ours["budgetRefusals"] += 1
        if r.get("ts"):
            ours["lastTs"] = r["ts"]
    out = {"ours": ours, "transcripts": None, "totalUsd": ours["usd"]}
    if scan_result is not None:
        t = transcript_spend(scan_result)
        out["transcripts"] = t
        out["totalUsd"] = round(ours["usd"] + t["usdEstimate"], 4)
    return out


# --------------------------------------------------------------------------
# hook health
# --------------------------------------------------------------------------

def hook_health(state, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = _read_jsonl(state.hooklog_path)
    out = {"rows": len(rows), "byEvent": {}, "errors": 0, "lastError": None,
           "fires": 0, "lastFireTs": None, "byRule": {}, "quarantined": 0,
           "unreadable": 0, "firstTs": None, "lastTs": None,
           "daysSinceFire": None, "calls": 0}
    for r in rows:
        ev = r.get("event") or "?"
        out["byEvent"][ev] = out["byEvent"].get(ev, 0) + 1
        ts = r.get("ts")
        if ts:
            out["firstTs"] = out["firstTs"] or ts
            out["lastTs"] = ts
        if ev == "error":
            out["errors"] += 1
            out["lastError"] = r
        elif ev == "regex_quarantined" or ev == "regex_slow":
            out["quarantined"] += 1
        elif ev == "precedents_unreadable":
            out["unreadable"] += 1
        elif ev in ("deny", "ask", "match"):
            out["fires"] += 1
            out["lastFireTs"] = ts or out["lastFireTs"]
            rid = r.get("rule") or "?"
            out["byRule"][rid] = out["byRule"].get(rid, 0) + 1
        if ev in ("allow", "deny", "ask", "match"):
            out["calls"] += 1
    last = _parse(out["lastFireTs"])
    if last is not None:
        out["daysSinceFire"] = (now - last).days
    return out


# --------------------------------------------------------------------------
# the funnel
# --------------------------------------------------------------------------

def funnel(state, entries: list[dict], hooks_status: dict,
           health: dict | None = None, now: datetime | None = None) -> dict:
    """proposed → accepted → activated → attributed, with the ids at each stage.

    *Activated* means a rule is covered by the PreToolUse matcher that is
    actually in ``settings.json`` right now — not that we once installed it.
    *Attributed* means the hook log recorded it firing.  Everything in between
    is where the industry's "written but never loaded" lives.
    """
    now = now or datetime.now(timezone.utc)
    health = health or hook_health(state, now=now)
    rule_cands = state.candidates()
    write_cands = [e["raw"] for e in entries if e["kind"] == "write"]
    precedents = state.precedents()
    active = [r for r in precedents if r.get("status") == "active"]

    # "installed" means our entries are in settings.json right now.  A stale
    # matcher does not un-install the suite — it just fails to activate the
    # rules it does not name, which is what the per-rule check below counts.
    installed = bool(hooks_status.get("installed"))
    covered = set(str(hooks_status.get("matcher") or "").split("|"))
    activated_ids = []
    for r in active:
        tools = [t.strip() for t in str(r.get("tool") or "").split("|") if t.strip()]
        ok = installed and (("*" in covered) or all(t in covered for t in tools))
        if ok:
            activated_ids.append(r.get("id"))
    fired_ids = [rid for rid in health["byRule"] if rid and rid != "?"]

    # Proposals (examiner / nightly improver) are the third kind of candidate.
    # Leaving them out of the funnel would hide exactly the queue that the
    # nightly loop fills, which is the failure this whole section exists for.
    from .docket import read_decisions
    from .proposals import read_proposals
    all_proposals = [p.get("id") for p in read_proposals(state) if p.get("id")]
    decisions = read_decisions(state)["decisions"]
    accepted_proposals = [pid for pid in all_proposals
                          if (decisions.get(pid) or {}).get("status") == "confirmed"]
    proposed = {"rules": [c.get("id") for c in rule_cands],
                "writes": [c.get("id") for c in write_cands],
                "proposals": all_proposals}
    accepted = {"rules": [r.get("id") for r in active],
                "writes": [e["id"] for e in entries
                           if e["kind"] == "write" and e["status"] == "confirmed"],
                "proposals": accepted_proposals}
    stages = [
        ("proposed", len(proposed["rules"]) + len(proposed["writes"])
         + len(proposed["proposals"])),
        ("accepted", len(accepted["rules"]) + len(accepted["writes"])
         + len(accepted["proposals"])),
        ("activated", len(activated_ids)),
        ("attributed", len(fired_ids)),
    ]
    return {
        "stages": dict(stages), "order": [s for s, _ in stages],
        "proposed": proposed, "accepted": accepted,
        "activated": activated_ids, "attributed": fired_ids,
        "firesByRule": dict(health["byRule"]),
        "installed": installed,
        "ownershipGuard": any(r.get("id") == GUARD_RULE_ID for r in active),
        "hookCalls": health["calls"], "hookFires": health["fires"],
    }


# --------------------------------------------------------------------------
# alarms
# --------------------------------------------------------------------------

def alarms(state, entries: list[dict], fun: dict, health: dict,
           hooks_status: dict, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    out: list[dict] = []
    starved = starving(entries)
    if starved:
        oldest = max((e.get("ageDays") or 0) for e in starved)
        out.append({
            "code": "PENDING", "level": "STARVATION",
            "message": f"{len(starved)} 条 docket 条目等待 ≥{STARVATION_DAYS} 天"
                       f"（最老 {oldest} 天）：{', '.join(e['id'] for e in starved[:5])}",
            "action": "precedent docket --batch"})
    if health["errors"] or health["unreadable"] or health["quarantined"]:
        last = (health.get("lastError") or {}).get("error", "")
        out.append({
            "code": "HOOK", "level": "STARVATION",
            "message": f"钩子日志里有 {health['errors']} 个异常、"
                       f"{health['unreadable']} 次 precedents.json 读不出、"
                       f"{health['quarantined']} 次正则隔离"
                       + (f"；最近一次：{last[:120]}" if last else ""),
            "action": f"tail {state.hooklog_path}"})
    n_active = len(fun["accepted"]["rules"])
    if n_active and not fun["installed"]:
        out.append({
            "code": "NOT_INSTALLED", "level": "STARVATION",
            "message": f"{n_active} 条已确认的先例，但 settings.json 里没有我们的钩子"
                       f"——强制执行 = 0",
            "action": "precedent hooks install claude-code   # 然后 --apply"})
    elif n_active and fun["installed"] and health["fires"] == 0:
        since = _parse((hooks_status.get("receipt") or {}).get("installedAt"))
        age = (now - since).days if since else None
        if age is None or age >= NO_FIRE_DAYS:
            out.append({
                "code": "NO_FIRES", "level": "STARVATION",
                "message": f"{n_active} 条 active 规则已安装"
                           + (f" {age} 天" if age is not None else "")
                           + f"，但 {NO_FIRE_DAYS} 天内一次都没触发"
                             f"（共 {health['calls']} 次钩子调用）——"
                             f"要么 agent 真的改了，要么规则匹配不到现实",
                "action": "precedent report --md digest.md  # 看 funnel 一节"})
    if hooks_status.get("drift"):
        out.append({
            "code": "DRIFT", "level": "STARVATION",
            "message": f"安装漂移 {len(hooks_status['drift'])} 项："
                       + "；".join(hooks_status["drift"][:3]),
            "action": "precedent hooks status"})
    return out


# --------------------------------------------------------------------------
# the daily digest
# --------------------------------------------------------------------------

def _day(ts) -> str | None:
    d = _parse(ts)
    return d.date().isoformat() if d else None


def daily_rows(state, entries: list[dict], days: int = 7,
               now: datetime | None = None) -> list[dict]:
    """One row per day: proposed / decided / hook calls / fires / spend."""
    now = now or datetime.now(timezone.utc)
    first = (now - timedelta(days=days - 1)).date()
    rows: dict[str, dict] = {}

    def bucket(day):
        if not day or day < first.isoformat():
            return None
        return rows.setdefault(day, {"day": day, "proposed": 0, "confirmed": 0,
                                     "rejected": 0, "snoozed": 0, "calls": 0,
                                     "fires": 0, "errors": 0, "usd": 0.0,
                                     "sessions": 0, "writes": 0})

    for e in entries:
        b = bucket(_day(e.get("created")))
        if b:
            b["proposed"] += 1
            if e["kind"] == "write":
                b["writes"] += 1
    from .docket import read_decisions
    for _id, d in read_decisions(state)["decisions"].items():
        b = bucket(_day(d.get("at")))
        if b and d.get("status") in ("confirmed", "rejected", "snoozed"):
            b[d["status"]] += 1
    for r in _read_jsonl(state.hooklog_path):
        b = bucket(_day(r.get("ts")))
        if not b:
            continue
        ev = r.get("event")
        if ev in ("allow", "deny", "ask", "match"):
            b["calls"] += 1
        if ev in ("deny", "ask", "match"):
            b["fires"] += 1
        if ev in ("error", "precedents_unreadable"):
            b["errors"] += 1
    for r in _read_jsonl(state.spend_path):
        b = bucket(_day(r.get("ts")))
        if b:
            b["usd"] = round(b["usd"] + float(r.get("costUsd") or 0.0), 6)
    for r in _read_jsonl(state.funnel_path):
        b = bucket(_day(r.get("ts")))
        if b and r.get("event") == "session_stop":
            b["sessions"] += 1
    return [rows[k] for k in sorted(rows)]
