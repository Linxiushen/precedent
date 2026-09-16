# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Rendering: the Chinese first screen, the mine report, the digest.

The first screen is deliberately ten lines.  It is the only thing a new user
reads, and every line is a count that can be checked by hand with ``grep`` —
there is no score, no grade and no advice in it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from receipts.transcripts import parse_ts

from . import __version__
from .hooks import render_status as render_hook_status

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


def _artifact_mix(scan_result) -> str:
    counts: dict[str, int] = {}
    for row in scan_result.funnel:
        counts[row.kind] = counts.get(row.kind, 0) + 1
    if not counts:
        return "0 个"
    parts = [f"{_KIND_ZH.get(k, k)} {v}" for k, v in
             sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return f"{sum(counts.values())} 个（{' / '.join(parts)}）"


def render_headline(scan_result, state, n_precedents: int = 0,
                    ledger_len: int = 0, ledger_ok: bool = True,
                    now: datetime | None = None) -> str:
    """The ten-line Chinese first screen printed by ``precedent init``."""
    now = now or datetime.now(timezone.utc)
    rows = scan_result.funnel
    never = [r for r in rows if r.never_cited and r.exists]
    trunc = [r for r in rows if r.n_sessions_truncated]
    stale = [r for r in rows if not r.exists]
    dupes = [r for r in rows if r.near_duplicates]
    citable = [r for r in rows if r.citable and r.exists]
    pct = f"{100.0 * len(never) / len(citable):.0f}%" if citable else "n/a"
    uw = unattended_writes(scan_result, now=now)

    L = [
        f"precedent {__version__} — 首屏（只读，零模型调用）",
        "─" * 62,
        f" 1. Claude home     : {scan_result.claude_home}",
        f" 2. 状态目录        : {state.root}",
        f" 3. 会话            : 扫描 {scan_result.sessions_scanned} / 发现 "
        f"{scan_result.sessions_found} 个",
        f" 4. 学习工件        : {_artifact_mix(scan_result)}",
        f" 5. 从未被引用      : {len(never)} 个（可引用工件的 {pct}）",
        f" 6. 曾被截断        : {len(trunc)} 个工件在 ≥1 个会话里只加载了一部分",
        f" 7. 失效索引/缺文件 : {len(stale)} 条",
        f" 8. 近重复工件      : {len(dupes)} 个",
        f" 9. 无人值守写入    : 最近 {uw['days']} 天 {uw['total']} 次"
        f"（子代理 {uw['sidechain']} 次，Bash 绕过记忆工具 {uw['bash_bypass']} 次）",
        f"10. 已强制执行的先例: {n_precedents} 条"
        + ("  → 下一步：precedent mine" if n_precedents == 0 else ""),
        "─" * 62,
        f"账本 {state.ledger_path}（哈希链，{ledger_len} 条记录，校验 "
        f"{'ok' if ledger_ok else '失败'}）",
    ]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# mine
# --------------------------------------------------------------------------

def _check_line(check: dict) -> str:
    if not check or not check.get("template"):
        return f"  建议检查: （无法自动编译）{(check or {}).get('note', '')}"
    bits = [f"  建议检查: {check['template']}"]
    for k in ("x", "y", "path", "field", "value", "flag", "preset", "regex"):
        if check.get(k):
            bits.append(f"{k}={check[k]}")
    bits.append(f"→ {check.get('tool')} {check.get('action')}")
    return " ".join(bits)


_VERDICT_MARK = {"PASS": "✅", "FAIL": "❌", "INSUFFICIENT": "⚠️"}


def _auto_lines(auto: dict | None) -> list[str]:
    """The auto-compiled candidate and its gate verdict, per topic."""
    if not auto:
        return []
    if not auto.get("compiled"):
        return [f"  - 自动编译: 未编译 — {auto.get('note', '')}"]
    rule = auto.get("rule") or {}
    gate = auto.get("gate") or {}
    verdict = gate.get("verdict", "?")
    L = [f"  - 自动编译: `{rule.get('id')}` {rule.get('tool')} "
         f"{rule.get('action')} [{rule.get('match')}] "
         f"模板 {rule.get('template')} "
         f"→ 出生门 **{verdict}** {_VERDICT_MARK.get(verdict, '')}"]
    L.append(f"    匹配器: "
             f"{json.dumps(rule.get('matchers'), ensure_ascii=False)[:160]}")
    L.append(f"    证据  : {gate.get('counts', '')}")
    for f in (gate.get("failures") or [])[:2]:
        L.append(f"    ✗ {f}")
    return L


def render_mine_markdown(result, top: int | None = None) -> str:
    """The ``precedent mine`` report."""
    ordered = result.ordered_topics()
    shown = ordered if top is None else ordered[:top]
    autos = [t.auto for t in result.topics if t.auto]
    n_compiled = len([a for a in autos if a.get("compiled")])
    n_pass = len([a for a in autos if (a.get("gate") or {}).get("verdict") == "PASS"])
    n_insuf = len([a for a in autos
                   if (a.get("gate") or {}).get("verdict") == "INSUFFICIENT"])
    L: list[str] = []
    L.append("# precedent mine — 纠正挖掘 v1")
    L.append("")
    L.append(f"Claude home: `{result.claude_home}`  ·  生成于 {result.generated_at}"
             f"  ·  中文分词 `{result.segmenter}`")
    if result.project_filter or result.last_n:
        L.append(f"过滤: project=`{result.project_filter}` last={result.last_n}")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 会话 | {result.n_sessions} |")
    L.append(f"| 人类轮次（已剔除 skill/命令展开 {result.n_skipped_expansions} 条） "
             f"| {result.n_human_turns} |")
    L.append(f"| 检出纠正 | {result.n_corrections}（{result.pct_corrections}%） |")
    L.append(f"| 被问句过滤掉的句子 | {result.n_questions_filtered} |")
    L.append(f"| 撤销/回滚动作 | {len(result.reverts)} |")
    L.append(f"| 主题 | {len(result.topics)} |")
    L.append(f"| **重复主题**（≥2 个会话） | {len(result.repeated_topics)} |")
    L.append(f"| 已写进 CLAUDE.md/记忆、却仍在发生（written but violated） "
             f"| {len(result.written_but_violated)} |")
    L.append("")
    if autos:
        L.append(f"**自动编译：{len(result.topics)} 个主题，{n_compiled} 个编译成功，"
                 f"{n_pass} 个通过出生门（PASS）**"
                 + (f"，{n_insuf} 个证据不足（INSUFFICIENT）" if n_insuf else "")
                 + f"，{n_compiled - n_pass - n_insuf} 个 FAIL。")
        L.append("")
    if result.n_corrections:
        L.append(f"你纠正了 agent **{result.n_corrections}** 次，"
                 f"其中 **{len(result.repeated_topics)}** 个主题重复出现；"
                 f"**{len(result.written_but_violated)}** 个主题已经写在 "
                 f"CLAUDE.md / 记忆里、但仍然发生。")
        L.append("")

    L.append("## 主题")
    L.append("")
    if not shown:
        L.append("（没有检出纠正。这可能是真的，也可能是模式没覆盖到你的说法——"
                 "见下面的 limitations。）")
    for i, t in enumerate(ordered):
        if t not in shown:
            continue
        conf = max((c.confidence for c in t.members), default=0.0)
        L.append(f"### T{i + 1} · `{t.id}` — {t.label}")
        L.append("")
        L.append(f"- 次数 **{t.count}** · 会话 **{len(t.sessions)}** · "
                 f"重复 {'是' if t.repeated else '否'} · 最高置信度 {conf} · "
                 f"t0 {t.t0}")
        if t.written_in:
            names = ", ".join(w["artifact"] for w in t.written_in[:3])
            L.append(f"- ⚠️ **written but violated**：已出现在 {names}"
                     f"（命中 token: {', '.join(t.written_in[0]['matchedTokens'][:4])}）")
        L.append("- 原话：")
        for c in t.members[:4]:
            date = (c.turn.ts or "")[:10]
            L.append(f"  - “{c.quote()}” — `{c.turn.locator()}` "
                     f"({c.turn.session_id[:8]}, {date}, 置信度 {c.confidence}, "
                     f"信号 {'/'.join(c.signals) or '—'}, "
                     f"模式 {'/'.join(p for p, _ in c.patterns)})")
            for r in c.reverts[:2]:
                L.append(f"    ↩ 撤销动作 `{r.command[:70]}` "
                         f"({r.to_dict()['locator']})")
        tools = t.preceding_tools()
        if tools:
            L.append("- 被纠正前的动作（violating action）：")
            for tool in tools[:4]:
                L.append(f"  - `{tool['tool']}` — {tool['summary'] or '(no summary)'} "
                         f"[{tool['sessionId'][:8]}:{tool['lineNo']}]")
        else:
            L.append("- 被纠正前的动作：（无——这些纠正不紧跟任何工具调用）")
        from .compile import suggest_check
        L.append(_check_line(suggest_check(t)))
        L.extend(_auto_lines(t.auto))
        L.append(f"  `precedent compile {t.id}`")
        L.append("")

    if result.reverts:
        L.append("## 撤销/回滚（agent 编辑被 git revert/checkout/restore 掉）")
        L.append("")
        for r in result.reverts[:10]:
            d = r.to_dict()
            L.append(f"- `{d['command'][:90]}` — {d['locator']} "
                     f"({d['sessionId'][:8]}) "
                     f"{'↩ 撤销了 agent 写过的 ' + ', '.join(d['revertedPaths'][:2]) if d['undoesAgentEdit'] else '（未匹配到 agent 的写入）'}")
        L.append("")

    L.append("## Limitations")
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


def render_alarms(alarm_rows: list[dict]) -> list[str]:
    L: list[str] = []
    if not alarm_rows:
        L.append("（无告警：没有超期待办、没有钩子异常、没有安装漂移。"
                 "通过时静默——这一行就是全部输出。）")
        return L
    L.append(f"**{len(alarm_rows)} 条告警。饿死绝不静默。**")
    L.append("")
    L.append("| 级别 | 代码 | 说明 | 下一步 |")
    L.append("|---|---|---|---|")
    for a in alarm_rows:
        L.append(f"| {a['level']} | `{a['code']}` | {a['message']} | "
                 f"`{a['action']}` |")
    return L


def render_funnel(fun: dict) -> list[str]:
    stages = fun["stages"]
    L = ["| 阶段 | 数量 | 含义 |", "|---|---|---|"]
    L.append(f"| ① proposed | {stages['proposed']} | 规则候选 "
             f"{len(fun['proposed']['rules'])} + 治理树写入候选 "
             f"{len(fun['proposed']['writes'])} + 提案（examiner / 夜间改进器）"
             f"{len(fun['proposed'].get('proposals') or [])} |")
    L.append(f"| ② accepted | {stages['accepted']} | 过了门并被你确认的先例 "
             f"{len(fun['accepted']['rules'])} + 你接受的写入 "
             f"{len(fun['accepted']['writes'])} + 你确认的提案 "
             f"{len(fun['accepted'].get('proposals') or [])}"
             f"（确认提案只记录决定，precedent 不会替你写文件） |")
    L.append(f"| ③ activated | {stages['activated']} | 现在真的在 settings.json "
             f"的 PreToolUse matcher 覆盖范围里 |")
    L.append(f"| ④ attributed | {stages['attributed']} | 钩子日志里真的触发过的规则 |")
    L.append("")
    L.append(f"钩子调用 {fun['hookCalls']} 次，其中触发 {fun['hookFires']} 次"
             f"（{_pct(fun['hookFires'], fun['hookCalls'])}）。"
             f"所有权守卫：{'开' if fun['ownershipGuard'] else '关'}。")
    if fun["firesByRule"]:
        L.append("")
        L.append("| 规则 | 触发次数 |")
        L.append("|---|---|")
        for rid, n in sorted(fun["firesByRule"].items(), key=lambda kv: -kv[1])[:10]:
            L.append(f"| `{rid}` | {n} |")
    gap = stages["accepted"] - stages["activated"]
    if gap > 0:
        L.append("")
        L.append(f"⚠️ {gap} 条已接受但未激活——这就是全行业的 "
                 f"“written but never loaded”，本机 60 个工件里 41 个从未被引用的同一格。")
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


def render_daily(rows: list[dict]) -> list[str]:
    if not rows:
        return ["（这段时间没有任何活动记录。）"]
    L = ["| 日期 | 新候选 | 确认 | 拒绝 | 推迟 | 钩子调用 | 触发 | 异常 | 会话 | $ |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['day']} | {r['proposed']} | {r['confirmed']} | "
                 f"{r['rejected']} | {r['snoozed']} | {r['calls']} | {r['fires']} | "
                 f"{r['errors']} | {r['sessions']} | {r['usd']:.4f} |")
    return L


def render_digest(state, scan_result, mine_result, precedents: list[dict],
                  ledger_records: list, now: datetime | None = None,
                  settings_path: str | None = None, live_summary: dict | None = None,
                  days: int = 7) -> str:
    """``precedent report`` — the daily digest: alarms, funnel, docket, spend."""
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
    L.append("# precedent report")
    L.append("")
    L.append(f"生成于 {now.astimezone(timezone.utc).isoformat()} · "
             f"state `{state.root}` · claude home `{state.claude_home}`")
    L.append("")
    L.append("## 0. 告警（STARVATION）")
    L.append("")
    L.extend(render_alarms(alarm_rows))
    L.append("")
    L.append("## 1. 首屏")
    L.append("")
    L.append("```")
    L.append(render_headline(scan_result, state, n_precedents=len(precedents),
                             ledger_len=len(ledger_records), now=now).rstrip("\n"))
    L.append("```")
    L.append("")
    L.append("## 2. 漏斗：proposed → accepted → activated → attributed")
    L.append("")
    L.extend(render_funnel(fun))
    L.append("")
    L.append("## 3. 待办（docket）")
    L.append("")
    pend = [e for e in entries if e["status"] == "pending"]
    snoozed = [e for e in entries if e["status"] == "snoozed"]
    L.append(f"- 待办 {len(pend)} 条（规则 "
             f"{len([e for e in pend if e['kind'] == 'rule'])} / 写入 "
             f"{len([e for e in pend if e['kind'] == 'write'])}），"
             f"已推迟 {len(snoozed)} 条")
    if pend:
        L.append("")
        L.append("| id | 类型 | 年龄 | 证据 |")
        L.append("|---|---|---|---|")
        for e in pend[:10]:
            L.append(f"| `{e['id']}` | {e['kind']} | "
                     f"{'?' if e.get('ageDays') is None else str(e['ageDays']) + ' 天'} | "
                     f"{(e['evidence'][0] if e['evidence'] else '')[:70]} |")
        L.append("")
        L.append("`precedent docket --batch` 看全部；"
                 "`precedent docket confirm|reject|snooze <id>`。")
    L.append("")
    L.append("## 4. 治理树所有权")
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
    L.append("## 5. 加载收据（live 优先）")
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
    L.append("## 6. 纠正挖掘")
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
        for t in mine_result.ordered_topics()[:12]:
            L.append(f"| `{t.id}` | {t.count} | {len(t.sessions)} | "
                     f"{'是' if t.repeated else '否'} | "
                     f"{'是' if t.written_in else '否'} | {t.label} |")
    L.append("")
    L.append("## 7. 已确认的先例（enforced）")
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
    L.append("## 8. 支出表")
    L.append("")
    L.extend(render_spend(meter))
    L.append("")
    L.append("## 9. 最近 %d 天" % days)
    L.append("")
    L.extend(render_daily(daily_rows(state, entries, days=days, now=now)))
    L.append("")
    L.append("## 10. 账本尾部（哈希链）")
    L.append("")
    if not ledger_records:
        L.append("（空）")
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
    L.append("## 11. 安装状态")
    L.append("")
    L.append("```")
    L.append(render_hook_status(hstatus).rstrip("\n"))
    L.append("```")
    L.append("")
    L.append("## 12. 诚实的限制")
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
