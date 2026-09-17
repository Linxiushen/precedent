# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Rendering: the first screen, the mine report, the digest.

The first screen is deliberately ten lines.  It is the only thing a new user
reads, and every line is a count that can be checked by hand with ``grep`` —
there is no score, no grade and no advice in it.

Every *frame* string here comes from :mod:`precedent.i18n` (English by default,
Chinese with ``--lang zh``).  The *evidence* — your quotes, paths, rule bodies,
matchers, tool names — is reproduced byte for byte in both languages, because a
translated quote is no longer a quote.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from receipts.transcripts import parse_ts

from . import __version__, i18n
from .hooks import render_status as render_hook_status
from .i18n import t

__all__ = ["render_alarms", "render_daily", "render_digest", "render_funnel",
           "render_headline", "render_mine_markdown", "render_spend",
           "unattended_writes"]

_KIND_ZH = {
    "memory": "memory",
    "memory_index": "MEMORY.md",
    "claude_md": "CLAUDE.md",
    "rule": "rule",
    "skill": "skill",
}


def unattended_writes(scan_result, now: datetime | None = None, days: int = 7) -> dict:
    """Agent writes to learned state in the last ``days``.

    "Unattended" here means *written by the agent, not typed by the user* —
    which is every mutation in the change feed, since the feed only records
    tool calls.  The sub-count that matters operationally is the sidechain one:
    a subagent writing to the learned state is the 02:17 background-fork
    failure mode from the design doc.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    total = 0
    sidechain = 0
    bash = 0
    by_kind: dict[str, int] = {}
    for m in scan_result.mutations:
        ts = parse_ts(m.ts)
        if ts is None or ts < cutoff:
            continue
        total += 1
        if m.is_sidechain:
            sidechain += 1
        if m.tool == "Bash":
            bash += 1
        by_kind[m.artifact_kind] = by_kind.get(m.artifact_kind, 0) + 1
    return {"days": days, "total": total, "sidechain": sidechain,
            "bash_bypass": bash, "byKind": by_kind}


def _artifact_mix(scan_result, lang: str | None = None) -> str:
    counts: dict[str, int] = {}
    for row in scan_result.funnel:
        counts[row.kind] = counts.get(row.kind, 0) + 1
    if not counts:
        return t("init.value.artifacts_none", lang)
    parts = [f"{_KIND_ZH.get(k, k)} {v}" for k, v in
             sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return t("init.value.artifacts", lang, n=sum(counts.values()),
             mix=" / ".join(parts))


def render_headline(scan_result, state, n_precedents: int = 0,
                    ledger_len: int = 0, ledger_ok: bool = True,
                    now: datetime | None = None, lang: str | None = None) -> str:
    """The ten-line first screen printed by ``precedent init``."""
    now = now or datetime.now(timezone.utc)
    lang = lang or i18n.get_lang()
    rows = scan_result.funnel
    never = [r for r in rows if r.never_cited and r.exists]
    trunc = [r for r in rows if r.n_sessions_truncated]
    stale = [r for r in rows if not r.exists]
    dupes = [r for r in rows if r.near_duplicates]
    citable = [r for r in rows if r.citable and r.exists]
    pct = f"{100.0 * len(never) / len(citable):.0f}%" if citable else "n/a"
    uw = unattended_writes(scan_result, now=now)

    lines = [
        ("home", scan_result.claude_home),
        ("state", state.root),
        ("sessions", t("init.value.sessions", lang,
                       scanned=scan_result.sessions_scanned,
                       found=scan_result.sessions_found)),
        ("artifacts", _artifact_mix(scan_result, lang)),
        ("never_cited", t("init.value.never_cited", lang, n=len(never), pct=pct)),
        ("truncated", t("init.value.truncated", lang, n=len(trunc),
                        artifact_w=i18n.plural(len(trunc), "artifact", lang))),
        ("stale", t("init.value.stale", lang, n=len(stale))),
        ("duplicates", t("init.value.duplicates", lang, n=len(dupes))),
        ("unattended", t("init.value.unattended", lang, days=uw["days"],
                         n=uw["total"], sub=uw["sidechain"],
                         bash=uw["bash_bypass"])),
        ("enforced", t("init.value.enforced", lang, n=n_precedents)
         + (t("init.next", lang) if n_precedents == 0 else "")),
    ]
    # CJK labels are two columns wide, so the ruler is display width, not len()
    width = max(i18n.display_width(t(f"init.label.{k}", lang)) for k, _ in lines)

    L = [t("init.title", lang, version=__version__), "─" * 62]
    for i, (key, value) in enumerate(lines, start=1):
        L.append(f"{i:2d}. {i18n.pad(t('init.label.' + key, lang), width)}: {value}")
    L.append("─" * 62)
    L.append(t("init.ledger", lang, path=state.ledger_path, n=ledger_len,
               record_w=i18n.plural(ledger_len, "record", lang),
               ok=t("init.ok", lang) if ledger_ok else t("init.failed", lang)))
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# mine
# --------------------------------------------------------------------------

def _check_line(check: dict, lang: str | None = None) -> str:
    if not check or not check.get("template"):
        return t("mine.topic.uncompilable", lang, note=(check or {}).get("note", ""))
    bits = [f"{t('mine.topic.suggested', lang)} {check['template']}"]
    for k in ("x", "y", "path", "field", "value", "flag", "preset", "regex"):
        if check.get(k):
            bits.append(f"{k}={check[k]}")
    bits.append(f"\u2192 {check.get('tool')} {check.get('action')}")
    return " ".join(bits)


_VERDICT_MARK = {"PASS": "\u2705", "FAIL": "\u274c", "INSUFFICIENT": "\u26a0\ufe0f"}


def _auto_lines(auto: dict | None, lang: str | None = None) -> list[str]:
    """The auto-compiled candidate and its gate verdict, per topic."""
    if not auto:
        return []
    if not auto.get("compiled"):
        return [t("mine.topic.auto_none", lang, note=auto.get("note", ""))]
    rule = auto.get("rule") or {}
    gate = auto.get("gate") or {}
    verdict = gate.get("verdict", "?")
    L = [t("mine.topic.auto", lang, id=rule.get("id"), tool=rule.get("tool"),
           action=rule.get("action"), match=rule.get("match"),
           template=rule.get("template"), verdict=verdict,
           mark=_VERDICT_MARK.get(verdict, ""))]
    L.append(t("mine.topic.auto_matchers", lang,
               matchers=json.dumps(rule.get("matchers"), ensure_ascii=False)[:160]))
    L.append(t("mine.topic.auto_evidence", lang, counts=gate.get("counts", "")))
    for f in (gate.get("failures") or [])[:2]:
        L.append(f"    \u2717 {f}")
    return L


def render_mine_markdown(result, top: int | None = None,
                         lang: str | None = None) -> str:
    """The ``precedent mine`` report.  Frame translated, quotes verbatim."""
    lang = lang or i18n.get_lang()
    ordered = result.ordered_topics()
    shown = ordered if top is None else ordered[:top]
    autos = [t_.auto for t_ in result.topics if t_.auto]
    n_compiled = len([a for a in autos if a.get("compiled")])
    n_pass = len([a for a in autos if (a.get("gate") or {}).get("verdict") == "PASS"])
    n_insuf = len([a for a in autos
                   if (a.get("gate") or {}).get("verdict") == "INSUFFICIENT"])
    yes, no = t("mine.yes", lang), t("mine.no", lang)
    L: list[str] = []
    L.append(t("mine.title", lang))
    L.append("")
    L.append(t("mine.meta", lang, home=result.claude_home, ts=result.generated_at,
               seg=result.segmenter))
    if result.project_filter or result.last_n:
        L.append(t("mine.filter", lang, project=result.project_filter,
                   last=result.last_n))
    L.append("")
    L.append(t("mine.table.head", lang))
    L.append("|---|---|")
    L.append(f"| {t('mine.metric.sessions', lang)} | {result.n_sessions} |")
    L.append(f"| {t('mine.metric.turns', lang, n=result.n_skipped_expansions)} "
             f"| {result.n_human_turns} |")
    L.append("| " + t("mine.metric.corrections", lang) + " | "
             + f"{result.n_corrections} ({result.pct_corrections}%) |")
    L.append(f"| {t('mine.metric.questions', lang)} | {result.n_questions_filtered} |")
    L.append(f"| {t('mine.metric.reverts', lang)} | {len(result.reverts)} |")
    L.append(f"| {t('mine.metric.topics', lang)} | {len(result.topics)} |")
    L.append(f"| {t('mine.metric.repeated', lang)} | {len(result.repeated_topics)} |")
    L.append(f"| {t('mine.metric.written_violated', lang)} "
             f"| {len(result.written_but_violated)} |")
    L.append("")
    if autos:
        L.append(t("mine.autocompile", lang, topics=len(result.topics),
                   compiled=n_compiled, passed=n_pass)
                 + (t("mine.autocompile.insufficient", lang, n=n_insuf)
                    if n_insuf else "")
                 + t("mine.autocompile.failed", lang,
                     n=n_compiled - n_pass - n_insuf))
        L.append("")
    if result.n_corrections:
        L.append(t("mine.summary", lang, corrections=result.n_corrections,
                   repeated=len(result.repeated_topics),
                   written=len(result.written_but_violated)))
        L.append("")

    L.append(t("mine.section.topics", lang))
    L.append("")
    if not shown:
        L.append(t("mine.no_topics", lang))
    for i, topic in enumerate(ordered):
        if topic not in shown:
            continue
        conf = max((c.confidence for c in topic.members), default=0.0)
        L.append(f"### T{i + 1} \u00b7 `{topic.id}` \u2014 {topic.label}")
        L.append("")
        L.append(t("mine.topic.stats", lang, count=topic.count,
                   sessions=len(topic.sessions),
                   repeated=yes if topic.repeated else no, conf=conf, t0=topic.t0))
        if topic.written_in:
            names = ", ".join(w["artifact"] for w in topic.written_in[:3])
            L.append(t("mine.topic.written_in", lang, names=names,
                       tokens=", ".join(topic.written_in[0]["matchedTokens"][:4])))
        L.append(t("mine.topic.quotes", lang))
        for c in topic.members[:4]:
            date = (c.turn.ts or "")[:10]
            L.append(f"  - \u201c{c.quote()}\u201d \u2014 `{c.turn.locator()}` "
                     f"({c.turn.session_id[:8]}, {date}, "
                     + t("mine.topic.quote_meta", lang, conf=c.confidence,
                         signals="/".join(c.signals) or "\u2014",
                         patterns="/".join(pat for pat, _ in c.patterns))
                     + ")")
            for r in c.reverts[:2]:
                L.append(f"    {t('mine.topic.revert', lang)} `{r.command[:70]}` "
                         f"({r.to_dict()['locator']})")
        tools = topic.preceding_tools()
        if tools:
            L.append(t("mine.topic.violating", lang))
            for tool in tools[:4]:
                L.append(f"  - `{tool['tool']}` \u2014 "
                         f"{tool['summary'] or '(no summary)'} "
                         f"[{tool['sessionId'][:8]}:{tool['lineNo']}]")
        else:
            L.append(t("mine.topic.no_violating", lang))
        from .compile import suggest_check
        L.append(_check_line(suggest_check(topic), lang))
        L.extend(_auto_lines(topic.auto, lang))
        L.append(f"  `precedent compile {topic.id}`")
        L.append("")

    if result.reverts:
        L.append(t("mine.section.reverts", lang))
        L.append("")
        for r in result.reverts[:10]:
            d = r.to_dict()
            tail = (t("mine.revert.undid", lang) + ", ".join(d["revertedPaths"][:2])
                    if d["undoesAgentEdit"] else t("mine.revert.unmatched", lang))
            L.append(f"- `{d['command'][:90]}` \u2014 {d['locator']} "
                     f"({d['sessionId'][:8]}) {tail}")
        L.append("")

    L.append(t("mine.section.limitations", lang))
    L.append("")
    from .mine import limitations
    for lim in limitations(result):
        L.append(f"- {lim}")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# digest — the daily audit screen (step 4 of the loop)
# --------------------------------------------------------------------------

def _pct(n, d):
    return f"{100.0 * n / d:.0f}%" if d else "n/a"


def render_alarms(alarm_rows: list[dict], lang: str | None = None) -> list[str]:
    L: list[str] = []
    if not alarm_rows:
        L.append(t("report.alarms.none", lang))
        return L
    L.append(t("report.alarms.count", lang, n=len(alarm_rows)))
    L.append("")
    L.append(t("report.alarms.head", lang))
    L.append("|---|---|---|---|")
    for a in alarm_rows:
        L.append(f"| {a['level']} | `{a['code']}` | {a['message']} | "
                 f"`{a['action']}` |")
    return L


def render_funnel(fun: dict, lang: str | None = None) -> list[str]:
    stages = fun["stages"]
    L = [t("report.funnel.head", lang), "|---|---|---|"]
    L.append(f"| \u2460 proposed | {stages['proposed']} | "
             + t("report.funnel.proposed", lang,
                 rules=len(fun["proposed"]["rules"]),
                 writes=len(fun["proposed"]["writes"]),
                 proposals=len(fun["proposed"].get("proposals") or [])) + " |")
    L.append(f"| \u2461 accepted | {stages['accepted']} | "
             + t("report.funnel.accepted", lang,
                 rules=len(fun["accepted"]["rules"]),
                 writes=len(fun["accepted"]["writes"]),
                 proposals=len(fun["accepted"].get("proposals") or [])) + " |")
    L.append(f"| \u2462 activated | {stages['activated']} | "
             + t("report.funnel.activated", lang) + " |")
    L.append(f"| \u2463 attributed | {stages['attributed']} | "
             + t("report.funnel.attributed", lang) + " |")
    L.append("")
    L.append(t("report.funnel.calls", lang, calls=fun["hookCalls"],
               fires=fun["hookFires"],
               pct=_pct(fun["hookFires"], fun["hookCalls"]),
               guard=t("report.on" if fun["ownershipGuard"] else "report.off", lang)))
    if fun["firesByRule"]:
        L.append("")
        L.append(t("report.funnel.rules_head", lang))
        L.append("|---|---|")
        for rid, n in sorted(fun["firesByRule"].items(), key=lambda kv: -kv[1])[:10]:
            L.append(f"| `{rid}` | {n} |")
    gap = stages["accepted"] - stages["activated"]
    if gap > 0:
        L.append("")
        L.append(t("report.funnel.gap", lang, n=gap))
    return L


def render_spend(meter: dict) -> list[str]:
    ours = meter["ours"]
    L = [f"- 我们自己的 `claude -p`：**${ours['usd']:.4f}**，{ours['calls']} 次调用"
         f"（精确值，来自 spend.jsonl；每次都带 --max-budget-usd）"]
    for cmd, c in sorted(ours["byCommand"].items()):
        L.append(f"  - `{cmd}`: {c['calls']} 次 / ${c['usd']:.4f}")
    t = meter.get("transcripts")
    if t:
        tok = t["tokens"]
        L.append(f"- 会话转录里的 token（不是我们花的，是这些会话本身的用量）："
                 f"输入 {tok['input_tokens']:,} / 输出 {tok['output_tokens']:,} / "
                 f"缓存读 {tok['cache_read_input_tokens']:,} / "
                 f"缓存写 {tok['cache_creation_input_tokens']:,}")
        L.append(f"  - 按 {t['pricesAsOf']} 的官方 list price 折算 **约 "
                 f"${t['usdEstimate']:.2f}**（估算：订阅用量并不按 token 计费；"
                 f"缓存读按 0.1×、缓存写按 1.25× 输入价）")
        if t["byModel"]:
            L.append("")
            L.append("| 模型 | 会话 | 输入 | 输出 | 估算 $ |")
            L.append("|---|---|---|---|---|")
            for m, row in sorted(t["byModel"].items(),
                                 key=lambda kv: -kv[1]["usd"])[:8]:
                L.append(f"| `{m}` | {row['sessions']} | {row['input_tokens']:,} | "
                         f"{row['output_tokens']:,} | {row['usd']:.2f} |")
        if t["unpricedModels"]:
            L.append(f"  - 未定价的模型（只记 token，不折算）："
                     f"{', '.join(t['unpricedModels'])}")
    return L


def render_daily(rows: list[dict], lang: str | None = None) -> list[str]:
    if not rows:
        return [t("report.daily.none", lang)]
    L = [t("report.daily.head", lang),
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['day']} | {r['proposed']} | {r['confirmed']} | "
                 f"{r['rejected']} | {r['snoozed']} | {r['calls']} | {r['fires']} | "
                 f"{r['errors']} | {r['sessions']} | {r['usd']:.4f} |")
    return L


def render_digest(state, scan_result, mine_result, precedents: list[dict],
                  ledger_records: list, now: datetime | None = None,
                  settings_path: str | None = None, live_summary: dict | None = None,
                  days: int = 7, lang: str | None = None) -> str:
    """``precedent report`` — the daily digest: alarms, funnel, docket, spend.

    The section headings and the table frames are translated; the bodies below
    §5 quote the record and stay as they were written.
    """
    lang = lang or i18n.get_lang()
    from .audit import alarms, daily_rows, funnel, hook_health, spend_meter
    from .docket import build_entries
    from .hooks import status as hooks_status_fn
    from .ownership import read_owners, read_write_candidates, summarise_candidates

    now = now or datetime.now(timezone.utc)
    settings_path = settings_path or f"{state.claude_home}/settings.json"
    entries = build_entries(state, now=now, include_decided=True)
    # the installed block is built from ACTIVE rules only, so the drift check
    # has to compare against the same set or a retired rule invents drift
    active = [r for r in precedents if r.get("status", "active") == "active"]
    hstatus = hooks_status_fn(state, active, settings_path)
    health = hook_health(state, now=now)
    fun = funnel(state, entries, hstatus, health=health, now=now)
    alarm_rows = alarms(state, entries, fun, health, hstatus, now=now)
    meter = spend_meter(state, scan_result)
    writes = read_write_candidates(state)
    wsum = summarise_candidates(writes)

    L: list[str] = []
    L.append(t("report.title", lang))
    L.append("")
    L.append(t("report.meta", lang, ts=now.astimezone(timezone.utc).isoformat(),
               state=state.root, home=state.claude_home))
    L.append("")
    L.append(t("report.h0", lang))
    L.append("")
    L.extend(render_alarms(alarm_rows, lang))
    L.append("")
    L.append(t("report.h1", lang))
    L.append("")
    L.append("```")
    L.append(render_headline(scan_result, state, n_precedents=len(precedents),
                             ledger_len=len(ledger_records), now=now,
                             lang=lang).rstrip("\n"))
    L.append("```")
    L.append("")
    L.append(t("report.h2", lang))
    L.append("")
    L.extend(render_funnel(fun, lang))
    L.append("")
    L.append(t("report.h3", lang))
    L.append("")
    pend = [e for e in entries if e["status"] == "pending"]
    snoozed = [e for e in entries if e["status"] == "snoozed"]
    L.append(t("report.docket.summary", lang, pending=len(pend),
               rules=len([e for e in pend if e["kind"] == "rule"]),
               writes=len([e for e in pend if e["kind"] == "write"]),
               snoozed=len(snoozed)))
    if pend:
        L.append("")
        L.append(t("report.docket.head", lang))
        L.append("|---|---|---|---|")
        for e in pend[:10]:
            age = ("?" if e.get("ageDays") is None
                   else t("report.docket.days", lang, n=e["ageDays"]))
            L.append(f"| `{e['id']}` | {e['kind']} | {age} | "
                     f"{(e['evidence'][0] if e['evidence'] else '')[:70]} |")
        L.append("")
        L.append(t("report.docket.more", lang))
    L.append("")
    L.append(t("report.h4", lang))
    L.append("")
    owners = read_owners(state)
    L.append(f"- 已声明所有权的路径：{len(owners.get('paths', {}))} 条"
             f"（`{state.owners_path}`）")
    L.append(f"- 钩子记录的治理树写入：{wsum['total']} 次"
             f"（前台 {wsum['byAgent'].get('foreground', 0)} / "
             f"子代理 {wsum['byAgent'].get('subagent', 0)}），"
             f"其中被拦下询问 {wsum['asks']} 次")
    if wsum["byGoverned"]:
        L.append(f"- 按树分布：{json.dumps(wsum['byGoverned'], ensure_ascii=False)}")
    hot = sorted(wsum["paths"].items(), key=lambda kv: -kv[1])[:5]
    for path, n in hot:
        L.append(f"  - {n}× `{path}`")
    if not wsum["total"]:
        L.append("  （钩子还没装，或者装了之后还没有人写过治理树。"
                 "`precedent hooks install claude-code` 先看 dry-run。）")
    L.append("")
    L.append(t("report.h5", lang))
    L.append("")
    if live_summary and live_summary.get("rows"):
        L.append(f"- 实时收据：{live_summary['sessions']} 个会话有 "
                 f"InstructionsLoaded 记录，覆盖/新增 "
                 f"{live_summary['overrides']}/{live_summary['added']} 条工件记录")
        L.append(f"- SessionStart {live_summary['sessionStarts']} 次 · "
                 f"人类轮次 {live_summary['humanTurns']} 次 · "
                 f"Stop {live_summary['stops']} 次")
        for c in live_summary.get("changed", [])[:5]:
            L.append(f"  - 实时记录改写了推断结论：`{c['path']}` "
                     f"{c['was']} → **{c['now']}**（{c['session'][:8]}）")
        if live_summary.get("unmatchedSessions"):
            L.append(f"  - {len(live_summary['unmatchedSessions'])} 个会话只有实时收据、"
                     f"扫描里没有（转录还没落盘或被过滤掉了）")
    else:
        L.append("- 没有实时收据（SessionStart / InstructionsLoaded 钩子未安装，"
                 "或这些会话早于安装时间）。此时状态全部来自转录推断，"
                 "`unknown` 是诚实答案。")
    L.append("")
    L.append(t("report.h6", lang))
    L.append("")
    if mine_result is None:
        L.append("（未运行 `precedent mine`）")
    else:
        L.append(f"- 人类轮次 {mine_result.n_human_turns}，检出纠正 "
                 f"{mine_result.n_corrections}（{mine_result.pct_corrections}%）")
        L.append(f"- 主题 {len(mine_result.topics)}，其中重复主题 "
                 f"{len(mine_result.repeated_topics)}")
        L.append(f"- written but violated: {len(mine_result.written_but_violated)}")
        L.append("")
        L.append("| 主题 | 次数 | 会话 | 重复 | 已写入 | 标签 |")
        L.append("|---|---|---|---|---|---|")
        yes, no = t("mine.yes", lang), t("mine.no", lang)
        for topic in mine_result.ordered_topics()[:12]:
            L.append(f"| `{topic.id}` | {topic.count} | {len(topic.sessions)} | "
                     f"{yes if topic.repeated else no} | "
                     f"{yes if topic.written_in else no} | {topic.label} |")
    L.append("")
    L.append(t("report.h7", lang))
    L.append("")
    # `active` is what the hook enforces; a retired rule is inert but still
    # part of the record, so it is listed under the table, never inside it —
    # counting a retired rule as enforced is exactly the kind of number that
    # makes a funnel lie.
    retired = [r for r in precedents if r.get("status") not in (None, "active")]
    if not active:
        L.append("0 条生效。`precedent compile <topic-id>` → `precedent confirm <id>` → "
                 "`precedent hooks install claude-code`。")
    else:
        L.append("| id | tool | action | matchers | 出生门 | 来源纠正 | 触发 |")
        L.append("|---|---|---|---|---|---|---|")
        for r in active:
            birth = r.get("birth") or {}
            ms = r.get("matchers") or ([{"type": "input_regex",
                                         "regex": r.get("input_regex")}]
                                       if r.get("input_regex") else [])
            L.append(f"| `{r.get('id')}` | {r.get('tool')} | {r.get('action')} | "
                     f"`{json.dumps(ms, ensure_ascii=False)[:60]}` | "
                     f"{birth.get('verdict', '—')} {birth.get('counts', '')} | "
                     f"{(r.get('quote') or r.get('message') or '')[:30]} | "
                     f"{fun['firesByRule'].get(r.get('id'), 0)} |")
    if retired:
        L.append("")
        L.append(f"已退役 {len(retired)} 条（不再强制执行，保留在记录里）：")
        for r in retired:
            L.append(f"- `{r.get('id')}` {r.get('status')} — "
                     f"{(r.get('message') or '')[:60]} "
                     f"· 原因：{(r.get('retiredReason') or '—')[:60]}")
    L.append("")
    L.append(t("report.h8", lang))
    L.append("")
    L.extend(render_spend(meter))
    L.append("")
    L.append(t("report.h9", lang, days=days))
    L.append("")
    L.extend(render_daily(daily_rows(state, entries, days=days, now=now), lang))
    L.append("")
    L.append(t("report.h10", lang))
    L.append("")
    if not ledger_records:
        L.append(t("report.empty", lang))
    else:
        L.append("| ts | type | candidate | event/decision | hash |")
        L.append("|---|---|---|---|---|")
        for rec in ledger_records[-10:]:
            d = rec.as_dict()
            kind = d.get("type")
            what = d.get("event") if kind == "event" else d.get("decision")
            L.append(f"| {d.get('ts', '')} | {kind} | `{d.get('candidate_id', '')}` | "
                     f"{what} | `{str(d.get('hash', ''))[:12]}…` |")
    L.append("")
    L.append(t("report.h11", lang))
    L.append("")
    L.append("```")
    L.append(render_hook_status(hstatus).rstrip("\n"))
    L.append("```")
    L.append("")
    L.append(t("report.h12", lang))
    L.append("")
    L.append("- v1 只做确定性检查（DSL 正则 + 字段断言）；可执行检查与判据型检查不在范围内。")
    L.append("- 纠正靠表层模式识别，会漏也会误判；置信度是输出的一部分，不是装饰。")
    L.append("- 出生门用的是历史会话，不是留出集：它证明规则在纠正发生那一刻之后"
             "「该响时响、不该响时静」，不证明它在未来会话上泛化。")
    L.append("- 漏斗的 ④ attributed 只数“钩子真的触发过”，不证明 agent 因此改了行为；"
             "要的是可核对，不是归因。")
    L.append("- 支出表里只有 `claude -p` 那一行是精确的；转录 token 折算是按 list price "
             "的估算，订阅用量并不按 token 计费。")
    L.append("- 所有权默认规则是“没有 agent 创建记录、且文件存在 = 你的”。"
             "带外创建的文件因此算你的（保守方向：ask，不是 deny）。")
    L.append("- 只有 `hooks install/uninstall --apply` 会写 settings.json，"
             "且写之前先把原文件按时间戳备份到 `<state>/backups/`；其余命令对 "
             "Claude home 只读。")
    L.append("")
    for lim in scan_result.limitations[:4]:
        L.append(f"- （receipts）{lim}")
    L.append("")
    return "\n".join(L)
