# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""``precedent`` — the CLI.

    precedent init                       detect, create state, scan, first screen
    precedent scan                       receipts, archived into the state dir
    precedent mine                       CORRECTION MINER v0
    precedent compile <topic-id>[,<id>]  topic(s) -> DSL v1 rule -> TEMPORAL GATE
    precedent confirm <rule-id>          rule -> precedents.json
    precedent docket                     the queue: candidates + evidence
    precedent docket confirm|reject|snooze <id>
    precedent own <path> --user|--agent  who owns a governed artifact
    precedent hooks install claude-code  install the enforcement (dry-run default)
    precedent hooks uninstall claude-code remove it again (dry-run default)
    precedent hooks status claude-code   what is installed, and any drift
    precedent snapshot                   content-addressed learned-state snapshot
    precedent undo --session <id>        restore what one session wrote (dry-run)
    precedent report                     the daily digest: alarms, funnel, spend

Every command that could reach the Claude home is read-only or defaults to
``--dry-run``.  ``hooks install/uninstall --apply`` is the only command that may
write a ``settings.json``, and only after a timestamped backup under the state
dir; the user's real ``~/.claude`` additionally needs ``--i-know``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from acceptor import Certificate

from . import __version__
from .accept import (DEFAULT_ALPHA0, DEFAULT_HARM_ALPHA, AcceptanceResult,
                     accept_outcomes, render_acceptance, verify_acceptance)
from .compile import (DEFAULT_EPSILON, FOLLOW_UP_TURNS, MIN_ELIGIBLE_AFTER,
                      TEMPLATES, CompileError, auto_compile, compile_topics,
                      run_temporal_gate)
from .examine import (DEFAULT_ARM_BUDGET_USD, DEFAULT_MAX_COST_USD, DEFAULT_RUNS,
                      MAX_EXAM_COST_USD, MAX_RUNS, examine as run_examine)
from .improve import (DEFAULT_TOP_CLUSTERS, DEFAULT_TOTAL_BUDGET_USD, improve,
                      render_improve)
from .loop import cron_snippet, render_cycle, run_cycle
from .docket import (SNOOZE_DAYS, build_entries, confirm_rule, render_batch,
                     retire_rule,
                     render_docket)
from .docket import confirm as docket_confirm
from .docket import reject as docket_reject
from .docket import snooze as docket_snooze
from .hooks import SettingsUnreadable
from .hooks import install as hooks_install
from .hooks import render_plan, render_status
from .hooks import status as hooks_status
from .hooks import uninstall as hooks_uninstall
from .live import merge_live_receipts
from .ownership import (GUARD_RULE_ID, build_agent_created, guard_active, own,
                        read_owners, set_guard)
from .llm import (DEFAULT_BUDGET_USD, DEFAULT_MODEL, LLMUnavailable,
                  llm_draft_rules, total_spend)
from .rules import ASK_BEFORE_PRESETS
from .mine import DEFAULT_SIMILARITY, load_topics, mine, write_topics
from .report import render_digest, render_headline, render_mine_markdown
from .snapshot import UndoRefused, apply_undo, plan_undo, take_snapshot
from .state import (DEFAULT_CLAUDE_HOME, DEFAULT_STATE_DIR,
                    ClaudeHomeWriteRefused, StateDir, is_real_claude_home,
                    now_iso, stamp)

__all__ = ["build_parser", "main"]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--claude-home", default=None, metavar="PATH",
                   help=f"Claude home to read (default: $CLAUDE_CONFIG_DIR or "
                        f"{DEFAULT_CLAUDE_HOME}); it is never written")
    p.add_argument("--state-dir", default=None, metavar="PATH",
                   help=f"where precedent keeps its state (default: {DEFAULT_STATE_DIR})")


def _gate_args(p: argparse.ArgumentParser) -> None:
    """The three knobs of the TEMPORAL BIRTH GATE, shared by mine and compile."""
    p.add_argument("--epsilon", type=float, default=DEFAULT_EPSILON, metavar="E",
                   help=f"max tolerated-fire rate among post-t0 eligible actions "
                        f"(default {DEFAULT_EPSILON})")
    p.add_argument("--follow-up-turns", dest="follow_up_turns", type=int,
                   default=FOLLOW_UP_TURNS, metavar="N",
                   help=f"how many human turns after a fire may confirm it "
                        f"(default {FOLLOW_UP_TURNS})")
    p.add_argument("--min-eligible-after", dest="min_eligible_after", type=int,
                   default=MIN_ELIGIBLE_AFTER, metavar="N",
                   help=f"below this many post-t0 eligible actions the verdict is "
                        f"INSUFFICIENT (default {MIN_ELIGIBLE_AFTER})")


def _scan_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--project", default=None, metavar="SLUG_SUBSTRING",
                   help="only sessions whose project slug contains this substring")
    p.add_argument("--last", dest="last_n", type=int, default=None, metavar="N",
                   help="only the N most recent sessions")


def _open_state(args, create: bool = True) -> StateDir:
    return StateDir.open(args.state_dir, args.claude_home, create=create)


def _require_home(state: StateDir) -> int | None:
    if not os.path.isdir(state.claude_home):
        print(f"precedent: no such Claude home: {state.claude_home}", file=sys.stderr)
        return 2
    return None


def _run_scan(state: StateDir, project=None, last_n=None, include_subagents=True,
              live: bool = True):
    """The receipts scan, with the LIVE receipts merged in on top (live wins).

    The summary of what the merge changed is attached as ``result.live`` so
    every caller can report it instead of silently preferring one source.
    """
    from receipts.scan import scan as receipts_scan
    result = receipts_scan(state.claude_home, project_filter=project, last_n=last_n,
                           include_subagents=include_subagents)
    result.live = merge_live_receipts(result, state) if live else None
    return result


def _ledger_info(state: StateDir) -> tuple[int, bool, list]:
    try:
        ledger = state.ledger()
    except Exception as exc:                              # pragma: no cover - defensive
        print(f"precedent: ledger unreadable ({exc})", file=sys.stderr)
        return 0, False, []
    ok = ledger.verify_chain()[0] if len(ledger) else True
    return len(ledger), ok, ledger.records()


def _run_mine(state: StateDir, project=None, last_n=None,
              similarity=DEFAULT_SIMILARITY):
    return mine(state.claude_home, project_filter=project, last_n=last_n,
                similarity=similarity)


def _load_mine_for_compile(state: StateDir, args):
    """Re-run the miner with the parameters `precedent mine` last used.

    Topic ids are a function of the mined set, so compiling has to reproduce it
    exactly rather than read stale JSON.
    """
    saved = load_topics(state) or {}
    meta = saved.get("mine") if isinstance(saved.get("mine"), dict) else {}
    project = getattr(args, "project", None) or meta.get("projectFilter")
    last_n = getattr(args, "last_n", None) or meta.get("lastN")
    similarity = getattr(args, "similarity", None) or meta.get("similarity") or DEFAULT_SIMILARITY
    return _run_mine(state, project=project, last_n=last_n, similarity=float(similarity))


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_init(args) -> int:
    state = _open_state(args)
    bad = _require_home(state)
    if bad:
        return bad
    now = datetime.now(timezone.utc)
    state.write_config(now)

    ledger = state.ledger()
    if len(ledger) == 0:
        ledger.append(Certificate(
            candidate_id="precedent:init", round=0, algorithm="none",
            decision="HOLD", alpha_spent=0.0, cumulative_alpha=0.0,
            metrics={"stateDir": state.root, "claudeHome": state.claude_home},
            evaluator_id=f"precedent/{__version__}", verification_rung="execution",
            closure="human_in", note="state directory created; nothing decided yet",
            ts=now_iso(now)))

    result = _run_scan(state, project=args.project, last_n=args.last_n)
    scan_path = state.write_json(
        os.path.join(state.scans_dir, f"receipts-{stamp(now)}.json"), result.to_dict())

    n_len, ok, _records = _ledger_info(state)
    headline = render_headline(result, state, n_precedents=len(state.precedents()),
                               ledger_len=n_len, ledger_ok=ok, now=now)
    sys.stdout.write(headline)
    live = getattr(result, "live", None) or {}
    if live.get("rows"):
        print(f"（实时钩子收据: {live['rows']} 行 / {live['sessions']} 个会话，"
              f"覆盖 {live['overrides']} 条推断结论）")
    print(f"\n（receipts 扫描已存档: {scan_path}）")
    return 0


def cmd_scan(args) -> int:
    from receipts.cli import ReadOnlyViolation, _guard_output
    from receipts.report import render_markdown

    state = _open_state(args)
    bad = _require_home(state)
    if bad:
        return bad
    try:
        json_out = _guard_output(args.json_out, state.claude_home)
        md_out = _guard_output(args.md_out, state.claude_home)
    except ReadOnlyViolation as exc:
        print(f"precedent: {exc}", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    result = _run_scan(state, project=args.project, last_n=args.last_n,
                       include_subagents=not args.no_subagents)
    markdown = render_markdown(result, last_n=args.last_n)

    archived = state.write_json(
        os.path.join(state.scans_dir, f"receipts-{stamp(now)}.json"), result.to_dict())
    if json_out:
        os.makedirs(os.path.dirname(json_out) or ".", exist_ok=True)
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, ensure_ascii=False, indent=2)
        print(f"precedent: wrote {json_out}", file=sys.stderr)
    if md_out:
        os.makedirs(os.path.dirname(md_out) or ".", exist_ok=True)
        with open(md_out, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        print(f"precedent: wrote {md_out}", file=sys.stderr)
    if not args.quiet:
        sys.stdout.write(markdown)
    live = getattr(result, "live", None) or {}
    if live.get("rows"):
        print(f"precedent: merged {live['rows']} live hook receipt line(s) from "
              f"{live['sessions']} session(s) — live wins over the transcript "
              f"inference ({live['overrides']} overridden, {live['added']} added)",
              file=sys.stderr)
        for c in live.get("changed", [])[:5]:
            print(f"  {os.path.basename(c['path'])}: {c['was']} → {c['now']} "
                  f"({c['session'][:8]})", file=sys.stderr)
    print(f"precedent: archived {archived}", file=sys.stderr)
    return 0


def cmd_mine(args) -> int:
    state = _open_state(args)
    bad = _require_home(state)
    if bad:
        return bad
    result = _run_mine(state, project=args.project, last_n=args.last_n,
                       similarity=args.similarity)
    if not args.no_compile:
        auto_compile(result, epsilon=args.epsilon,
                     follow_up_turns=args.follow_up_turns,
                     min_eligible_after=args.min_eligible_after)
    write_topics(state, result)

    markdown = render_mine_markdown(result, top=args.top)
    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)) or ".",
                    exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, ensure_ascii=False, indent=2)
        print(f"precedent: wrote {args.json_out}", file=sys.stderr)
    if args.md_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.md_out)) or ".", exist_ok=True)
        with open(args.md_out, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        print(f"precedent: wrote {args.md_out}", file=sys.stderr)
    if not args.quiet:
        sys.stdout.write(markdown)
    return 0


def _template_kwargs(args) -> dict:
    return {
        "x": args.x, "y": args.y, "path": args.path, "field": args.field,
        "value": args.value, "flag": args.flag, "preset": args.preset,
        "command": args.command_text, "tool": args.tool, "action": args.action,
        "message": args.message, "scope": args.scope, "cwd_glob": args.cwd_glob,
        "regex": args.regex,
    }


def _store_candidate(state, rule: dict, gate, topics, note_extra: str = "") -> None:
    rule["birth"] = gate.to_dict()
    rule["status"] = "candidate"
    rule["compiledAt"] = now_iso()
    rule["topics"] = [t.id for t in topics]
    cands = [c for c in state.candidates() if c.get("id") != rule["id"]]
    cands.append(rule)
    state.write_candidates(cands)

    decision = {"PASS": "HOLD", "FAIL": "BLOCKED", "INSUFFICIENT": "NSF"}[gate.verdict]
    state.ledger().append(Certificate(
        candidate_id=rule["id"], round=1, algorithm="temporal-birth-gate/v1",
        decision=decision, alpha_spent=0.0, cumulative_alpha=0.0,
        metrics=gate.to_dict(), evaluator_id=f"precedent/{__version__}",
        verification_rung="execution", closure="human_in",
        note=f"topics {','.join(t.id for t in topics)}: {rule['message'][:120]}"
             + note_extra))


def cmd_compile(args) -> int:
    state = _open_state(args)
    bad = _require_home(state)
    if bad:
        return bad
    result = _load_mine_for_compile(state, args)
    try:
        topics = result.topics_for(args.topic_id)
    except KeyError as miss:
        print(f"precedent: no such topic {miss.args[0]!r}. Run `precedent mine` "
              f"and use the `t-…` id or the T<n> index.", file=sys.stderr)
        known = ", ".join(t.id for t in result.ordered_topics()[:10])
        if known:
            print(f"  known topics: {known}", file=sys.stderr)
        return 2
    if not topics:
        print("precedent: no topic given", file=sys.stderr)
        return 2

    gate_kwargs = dict(epsilon=args.epsilon, follow_up_turns=args.follow_up_turns,
                       min_eligible_after=args.min_eligible_after)

    if args.llm:
        return _compile_with_llm(state, args, result, topics, gate_kwargs)

    try:
        rule, gate = compile_topics(topics, result, template=args.template,
                                    **gate_kwargs, **_template_kwargs(args))
    except CompileError as exc:
        print(f"precedent: {exc}", file=sys.stderr)
        return 2

    _store_candidate(state, rule, gate, topics)
    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)) or ".", exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(rule, fh, ensure_ascii=False, indent=2)
        print(f"precedent: wrote {args.json_out}", file=sys.stderr)

    sys.stdout.write(_render_compile(topics, rule, gate))
    return 0 if gate.verdict == "PASS" else 1


def _compile_with_llm(state, args, result, topics, gate_kwargs) -> int:
    """`--llm`: draft with `claude -p`, then schema-validate and gate every draft."""
    def gate_runner(rule):
        return run_temporal_gate(rule, topics, result, **gate_kwargs)

    try:
        out = llm_draft_rules(state, topics, result, gate_runner=gate_runner,
                              model=args.llm_model, budget_usd=args.llm_budget,
                              timeout_s=args.llm_timeout)
    except LLMUnavailable as exc:
        print(f"precedent: {exc}", file=sys.stderr)
        return 2

    L = [f"# precedent compile {args.topic_id} --llm", ""]
    L.append(f"claude -p · model {out.call.model} · budget "
             f"${out.call.budget_usd:g} · 花费 ${out.call.cost_usd:.4f} · "
             f"{out.call.duration_ms} ms · 累计 ${total_spend(state):.4f}")
    L.append(f"参数：{' '.join(a if len(a) < 40 else a[:40] + '…' for a in out.call.argv[1:])}")
    if out.call.error:
        L.append(f"错误：{out.call.error}")
    L.append("")
    L.append(f"草稿 {len(out.drafts)} 条 → 通过 {len(out.accepted)} 条，"
             f"拒绝 {len(out.rejected)} 条")
    L.append("")
    for rule, gate in out.accepted:
        _store_candidate(state, rule, gate, topics, note_extra=" [llm]")
        L.append(f"## ✓ {rule['id']}  {rule['tool']}  {rule['action']}")
        L.append("")
        L.append("```json")
        L.append(json.dumps({k: rule[k] for k in
                             ("tool", "match", "matchers", "action", "scope",
                              "message") if k in rule},
                            ensure_ascii=False, indent=2))
        L.append("```")
        L.append(f"出生门 {gate.verdict} — {gate.counts()}")
        L.append("")
    for rej in out.rejected:
        L.append(f"## ✗ 拒绝：{rej.get('reason')}")
        L.append("")
        L.append("```json")
        L.append(json.dumps(rej.get("draft"), ensure_ascii=False, indent=2)[:1200])
        L.append("```")
        L.append("")
    if out.rejected_path:
        L.append(f"被拒草稿已写入 {out.rejected_path}（下次作为负例喂回提示词）")
    L.append(f"支出账本 {out.spend_path}")
    L.append("")
    L.append("机械拒绝覆盖 LLM 批准，永不反向：模型说什么都不能让一条规则跳过"
             "schema 校验或出生门。")
    L.append("")
    sys.stdout.write("\n".join(L))
    return 0 if out.accepted else 1


def _gate_block(gate) -> list[str]:
    L = ["## 出生门（TEMPORAL BIRTH GATE v1，离线，零模型调用）", ""]
    L.append(f"  t0（最早一次纠正）: {gate.t0}")
    L.append(f"  (a) 命中违规动作   : {len(gate.hits)}/{gate.n_violating_actions}"
             f"   → {'HIT' if gate.hit else 'NO HIT'}")
    L.append(f"      （分母是动作数：每条纠正回看最近 "
             f"{gate.preceding_tools_window} 次工具调用；"
             f"{gate.n_corrections_with_hit}/{gate.n_corrections} 条纠正有命中）")
    L.append(f"  (b) t0 之后可判定   : {gate.eligible_after} 次动作"
             f"（该工具被用到的次数）")
    L.append(f"      其中触发         : {gate.fires_after} 次 = 真阳 "
             f"{gate.n_true_positives}（后面 {gate.follow_up_turns} 轮内又被纠正）"
             f" + 误触 {gate.n_false_fires}")
    L.append(f"      误触率           : {gate.n_false_fires}/{gate.eligible_after}"
             f" = {gate.false_fire_rate * 100:.1f}%  (阈值 "
             f"{gate.epsilon * 100:.0f}%)")
    L.append(f"      误触分布（仅报告）: 出现在 {gate.n_false_fire_sessions}/"
             f"{gate.eligible_after_sessions} 个会话里 —— ε 是按动作算的，"
             f"语料越大越宽松，请读绝对次数")
    L.append(f"  (c) t0 之前         : {gate.fires_before}/{gate.eligible_before}"
             f"（只报告，不计入）")
    skipped = []
    if gate.unordered:
        skipped.append(f"{gate.unordered} 次无时间戳")
    if gate.out_of_scope:
        skipped.append(f"{gate.out_of_scope} 次不在 scope 内")
    L.append(f"  扫描的工具调用     : {gate.n_tool_calls_scanned}"
             + (f"（{'，'.join(skipped)}，未计入）" if skipped else ""))
    L.append(f"  判定               : **{gate.verdict}**")
    for f in gate.failures:
        L.append(f"    ✗ {f}")
    for n in gate.notes:
        L.append(f"    ! {n}")
    if gate.hits:
        L.append("")
        L.append("  命中的违规动作:")
        for ex in gate.hits[:3]:
            L.append(f"    - {ex['tool']} {ex['locator']}: {ex['summary'][:80]}")
            L.append(f"      → 紧接着的纠正 {ex['correctionLocator']}: "
                     f"“{ex['correctionQuote'][:60]}”")
    if gate.true_positives:
        L.append("")
        L.append("  t0 之后的真阳（触发后又被纠正）:")
        for ex in gate.true_positives[:3]:
            L.append(f"    - {ex['tool']} {ex['locator']} {ex['ts']}: "
                     f"{ex['value'][:60]} → {ex.get('confirmedBy')}")
    if gate.false_fires:
        L.append("")
        L.append("  t0 之后的误触（被容忍的触发）:")
        for ex in gate.false_fires[:5]:
            L.append(f"    - {ex['tool']} {ex['locator']} {ex['ts']}: "
                     f"{ex['value'][:60]}")
    return L


def _render_compile(topics, rule, gate) -> str:
    L: list[str] = []
    ids = ",".join(t.id for t in topics)
    L.append(f"# precedent compile {ids}")
    L.append("")
    for t in topics:
        L.append(f"主题 `{t.id}`: {t.label}  ·  {t.count} 次纠正 / "
                 f"{len(t.sessions)} 个会话")
    L.append("")
    L.append("## 规则（DSL v1）")
    L.append("")
    L.append(json.dumps({k: rule[k] for k in
                         ("id", "schemaVersion", "hook", "tool", "match",
                          "matchers", "action", "scope", "message")
                         if k in rule}, ensure_ascii=False, indent=2))
    L.append("")
    bits = [f"模板 {rule.get('template', 'llm')}"]
    for k in ("x", "y", "path", "field", "value", "flag", "regex"):
        if rule.get(k):
            bits.append(f"{k}={rule[k]}")
    L.append("  ".join(bits))
    L.append("")
    L.extend(_gate_block(gate))
    L.append("")
    if gate.verdict == "PASS":
        L.append(f"→ `precedent confirm {rule['id']}` 把它写进 precedents.json")
    elif gate.verdict == "INSUFFICIENT":
        L.append("→ 证据不足：规则留在 candidates.json。等这个工具在 t0 之后"
                 "被用得更多再重新 compile。")
    else:
        L.append("→ 出生门未通过：规则留在 candidates.json，不会被确认。"
                 "改用 --template/--field/--x/--y/--path 重编译，或放弃这个主题。")
    L.append("")
    return "\n".join(L)


def cmd_confirm(args) -> int:
    state = _open_state(args)
    rc, lines = confirm_rule(state, args.rule_id, force=args.force)
    for line in lines:
        print(line, file=sys.stderr if rc else sys.stdout)
    return rc


def cmd_retire(args) -> int:
    state = _open_state(args)
    rc, lines = retire_rule(state, args.rule_id, reason=args.reason)
    for line in lines:
        print(line, file=sys.stderr if rc else sys.stdout)
    return rc


def cmd_hooks(args) -> int:
    state = _open_state(args)
    if args.target != "claude-code":                      # pragma: no cover - argparse
        print(f"precedent: unknown hook target {args.target!r}", file=sys.stderr)
        return 2
    command = getattr(args, "hooks_command", "install")
    rules = [r for r in state.precedents() if r.get("status", "active") == "active"]
    settings_path = args.settings or os.path.join(state.claude_home, "settings.json")

    if command == "status":
        st = hooks_status(state, rules, settings_path)
        sys.stdout.write(render_status(st))
        if st["drift"]:
            print("precedent: re-run `precedent hooks install claude-code` "
                  "(dry-run) to see the merge that fixes this.", file=sys.stderr)
        return 1 if st["drift"] else 0

    uninstalling = command == "uninstall"
    plan, _merged = render_plan(state, rules, settings_path, apply=args.apply,
                                uninstall=uninstalling,
                                show_scripts=getattr(args, "show_scripts", False))
    sys.stdout.write(plan)

    if not args.apply:
        print("")
        print("precedent: dry run — nothing was written. Re-run with --apply "
              + ("to remove the hooks." if uninstalling else
                 "to write the hook scripts and merge settings.json."))
        return 0

    if is_real_claude_home(settings_path) and not args.i_know:
        print(f"precedent: {settings_path} is inside your real ~/.claude. "
              f"Re-run with --i-know if you really mean it (a timestamped backup "
              f"is written to {state.backups_dir} first either way).",
              file=sys.stderr)
        return 2

    ledger = state.ledger()
    if uninstalling:
        out = hooks_uninstall(state, settings_path)
        print("")
        if out.get("refused"):
            print(f"precedent: {settings_path} {out['refused']} — nothing was "
                  f"removed and the file was not touched.", file=sys.stderr)
            return 2
        print(f"precedent: removed {out['removed']} hook entr"
              f"{'y' if out['removed'] == 1 else 'ies'} from {settings_path}")
        if out["backup"]:
            print(f"  backup   : {out['backup']}")
        if not out["changed"]:
            print("  (nothing to do — already uninstalled; idempotent)")
        payload = {"settingsPath": settings_path, "removed": out["removed"],
                   "backup": out["backup"], "settingsWritten": out["changed"],
                   "reason": "hooks uninstall"}
        for r in rules or [{"id": "precedent:hooks"}]:
            ledger.append_event(r["id"], "retired", dict(payload))
        return 0

    out = hooks_install(state, rules, settings_path=settings_path,
                        write_settings_file=not args.scripts_only)
    try:
        created = build_agent_created(state, _run_scan(state, live=False))
        n_created = len(created["paths"])
    except Exception as exc:                              # pragma: no cover
        n_created = -1
        print(f"precedent: could not build the agent-created index ({exc})",
              file=sys.stderr)
    print("")
    print("precedent: wrote (state dir):")
    for w in out["scripts"]:
        print(f"  {w}")
    if args.scripts_only:
        print(f"precedent: {settings_path} was NOT written (--scripts-only); "
              f"paste the hooks block above yourself.")
    elif out.get("refused"):
        print(f"precedent: REFUSED to merge into {settings_path} — it "
              f"{out['refused']}.", file=sys.stderr)
        print("precedent: merging into a file precedent cannot parse would "
              "replace your settings instead of adding to them. Fix the JSON "
              "and re-run, or paste the hooks block above in by hand.",
              file=sys.stderr)
        print(f"precedent: the hook scripts were written to {state.hooks_dir} "
              f"and are inert until settings.json points at them.",
              file=sys.stderr)
        return 2
    elif out["changed"]:
        print(f"precedent: merged into {settings_path}")
        print(f"  backup   : {out['backup'] or '(no previous file)'}")
    else:
        print(f"precedent: {settings_path} already contained these hooks — "
              f"nothing changed (idempotent).")
    if n_created >= 0:
        print(f"precedent: agent-created index rebuilt "
              f"({n_created} path(s) — everything else in the governed trees "
              f"counts as yours) → {state.agent_created_path}")
    print(f"precedent: install receipt → {out['receipt']}")
    for r in rules or [{"id": "precedent:hooks"}]:
        ledger.append_event(r["id"], "activated",
                            {"activated": True,
                             "hookScript": state.hook_script_path,
                             "settingsPath": settings_path,
                             "settingsWritten": bool(out["changed"])})
    return 0


# --------------------------------------------------------------------------
# ownership
# --------------------------------------------------------------------------

def cmd_own(args) -> int:
    state = _open_state(args)
    if args.guard is not None:
        changed = set_guard(state, args.guard == "on")
        print(f"precedent: 所有权守卫 {'开' if args.guard == 'on' else '关'}"
              + ("" if changed else "（本来就是这样，没有改动）"))
        if args.guard == "on":
            print(f"  规则 {GUARD_RULE_ID} 已写入 {state.precedents_path}；"
                  f"子代理写你拥有的工件时钩子会 ask。")
            print("  下一步: precedent hooks install claude-code   (默认 dry-run)")
        return 0
    if not args.path:
        rows = read_owners(state).get("paths", {})
        if not rows:
            print("precedent: 还没有任何所有权声明。"
                  "`precedent own <path> --user` 把一个工件标成你的。")
        for path, rec in sorted(rows.items()):
            print(f"  {rec.get('owner'):<6} {path}   "
                  f"({rec.get('governed') or 'not governed'}, {rec.get('at', '')[:10]})")
        print(f"\n守卫: {'开' if guard_active(state) else '关'}   "
              f"（`precedent own --guard on|off`）")
        return 0
    owner = "agent" if args.agent else "user"
    rec = own(state, args.path, owner)
    print(f"precedent: {rec['path']} → owner={owner}")
    if rec.get("governed") is None:
        print("  ⚠️ 这个路径不在治理树里（memory / CLAUDE.md / .claude/rules / "
              ".claude/skills），钩子不会看它。")
    if owner == "user" and not guard_active(state):
        set_guard(state, True)
        print(f"  已同时打开所有权守卫（{GUARD_RULE_ID}）：从现在起，子代理写"
              f"你拥有的工件会被 ask，而不是直接写。")
        print("  下一步: precedent hooks install claude-code   (默认 dry-run)")
    state.ledger().append(Certificate(
        candidate_id=f"own:{rec['path']}", round=0,
        algorithm="ownership-declaration/v1", decision="ACCEPT",
        alpha_spent=0.0, cumulative_alpha=0.0,
        metrics={"owner": owner, "governed": rec.get("governed"),
                 "guard": guard_active(state)},
        evaluator_id=f"precedent/{__version__}", verification_rung="execution",
        closure="human_in",
        note=f"user declared {rec['path']} owned by {owner}"))
    return 0


# --------------------------------------------------------------------------
# docket
# --------------------------------------------------------------------------

def cmd_docket(args) -> int:
    state = _open_state(args)
    now = datetime.now(timezone.utc)
    action = getattr(args, "docket_command", None)
    if action in ("confirm", "reject", "snooze"):
        if action == "confirm":
            rc, lines = docket_confirm(state, args.entry_id,
                                       force=getattr(args, "force", False), now=now)
        elif action == "reject":
            rc, lines = docket_reject(state, args.entry_id,
                                      reason=getattr(args, "reason", None), now=now)
        else:
            rc, lines = docket_snooze(state, args.entry_id,
                                      days=getattr(args, "days", SNOOZE_DAYS),
                                      now=now)
        for line in lines:
            print(line, file=sys.stderr if rc else sys.stdout)
        return rc
    entries = build_entries(state, now=now, include_decided=args.all)
    if args.json_out:
        payload = {"schemaVersion": 1, "generatedAt": now_iso(now),
                   "entries": [{k: v for k, v in e.items() if k != "raw"}
                               for e in entries]}
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)) or ".",
                    exist_ok=True)
        state.write_json(args.json_out, payload)
        print(f"precedent: wrote {args.json_out}", file=sys.stderr)
    md = (render_batch(state, entries, now=now) if args.batch
          else render_docket(state, entries, now=now))
    if args.md_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.md_out)) or ".",
                    exist_ok=True)
        with open(args.md_out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"precedent: wrote {args.md_out}", file=sys.stderr)
    if not args.quiet:
        sys.stdout.write(md)
    return 0


def cmd_snapshot(args) -> int:
    state = _open_state(args)
    bad = _require_home(state)
    if bad:
        return bad
    result = _run_scan(state, project=args.project, last_n=args.last_n)
    cwds = [c for c in result.cwds.values() if c]
    man = take_snapshot(state, cwds=cwds, slugs=result.project_slugs,
                        dry_run=args.dry_run)
    print(f"precedent snapshot {man.id}")
    print(f"  roots   : {len(man.roots)}")
    for r in man.roots:
        print(f"      {r}")
    print(f"  files   : {len(man.files)}  ({man.n_bytes} bytes)")
    if man.skipped:
        print(f"  skipped : {len(man.skipped)}")
        for s in man.skipped[:5]:
            print(f"      {s['path']} — {s['reason']}")
    if args.dry_run:
        print("  (--dry-run: no blob and no manifest was written)")
    else:
        print(f"  manifest: {os.path.join(state.snapshots_dir, man.id + '.json')}")
        print(f"  blobs   : {state.blobs_dir}")
    if args.verbose:
        for f in man.files:
            print(f"    {f['sha256'][:12]}  {f['size']:>8}  {f['path']}")
    return 0


def cmd_undo(args) -> int:
    state = _open_state(args)
    bad = _require_home(state)
    if bad:
        return bad
    result = _run_scan(state, project=args.project, last_n=args.last_n)
    plan = plan_undo(state, args.session, result)

    print(f"# precedent undo --session {args.session}")
    print("")
    print(f"session started    : {plan.session_started_at}")
    print(f"snapshot used      : {plan.snapshot_id} ({plan.snapshot_created_at})")
    print(f"files touched      : {len(plan.actions)}")
    print(f"files that change  : {len(plan.changes)}")
    print("")
    for a in plan.actions:
        print(f"  {a['action']:<10} {a['path']}")
        print(f"             {a['reason']}")
    for n in plan.notes:
        print(f"  ! {n}")
    print("")
    if not args.apply:
        print("DRY RUN — nothing was written. Re-run with --apply "
              "(and --i-know if the target is your real ~/.claude).")
        return 0
    try:
        done = apply_undo(state, plan, i_know=args.i_know)
    except UndoRefused as exc:
        print(f"precedent: {exc}", file=sys.stderr)
        return 2
    for d in done:
        print(f"  {d['result']:<10} {d['path']}")
    ledger = state.ledger()
    ledger.append_event(f"session:{args.session}", "reverted",
                        {"nFiles": len(done),
                         "snapshot": plan.snapshot_id,
                         "paths": [d["path"] for d in done][:50]})
    print(f"\nprecedent: reverted {len(done)} file(s); recorded in {state.ledger_path}")
    return 0


def cmd_examine(args) -> int:
    """② ACCEPT — compile cassettes, run the two arms, certify the result."""
    state = _open_state(args)
    bad = _require_home(state)
    if bad:
        return bad
    mine_result = None
    if not args.no_mine:
        mine_result = _run_mine(state, project=args.project, last_n=args.last_n)
    try:
        ex = run_examine(
            state, args.candidate, mine_result=mine_result,
            project_filter=args.project, last_n=args.last_n,
            runs=args.runs, max_cost_usd=args.max_cost_usd,
            arm_budget_usd=args.arm_budget, model=args.model,
            dry_run=args.dry_run, allow_git=not args.no_git,
            allow_bash=args.allow_bash, force_fallback=args.fallback)
    except LLMUnavailable as exc:
        print(f"precedent: {exc}", file=sys.stderr)
        return 2

    acc = None
    if args.dry_run:
        acc = AcceptanceResult(
            candidate_id=ex.candidate.id, content_hash=ex.candidate.content_hash,
            decision="HOLD", alpha=args.alpha,
            reason="--dry-run: the eval suite was written, nothing ran and "
                   "nothing was certified",
            n_free_ties=len(ex.ties_free), notes=list(ex.notes))
        md = render_acceptance(acc, examine_result=ex)
    else:
        acc = accept_outcomes(
            state, ex.candidate, ex.all_outcomes,
            alpha0=args.alpha, harm_alpha=args.harm_alpha,
            require_floor=args.require_floor,
            evidence={"examine": {k: v for k, v in ex.to_dict().items()
                                  if k != "cassettes"}},
            hypothesis=ex.candidate.description)
        md = render_acceptance(acc, examine_result=ex)
        # The gate certificates live in the same chain as the lifecycle events,
        # so the ledger runs non-strict; replay just the gate certificates
        # through acceptor's own monitor and say so out loud if the audit trail
        # stopped making sense.
        audit = verify_acceptance(state)
        if not audit["ok"]:
            md += ("\n**账本审计失败**（`acceptor.CandidateMonitor` 复核 "
                   f"{audit['checked']} 张门控证书）:\n"
                   + "\n".join(f"- {p}" for p in audit["problems"][:5])
                   + "\n\n这一判定不可当作证据使用，退出码为 1。\n")

    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)) or ".",
                    exist_ok=True)
        payload = {"examine": ex.to_dict(),
                   "acceptance": acc.to_dict() if acc else None}
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"precedent: wrote {args.json_out}", file=sys.stderr)
    if not args.quiet:
        sys.stdout.write(md)
    if args.dry_run:
        return 0
    if not audit["ok"]:
        # A decision whose own audit trail does not replay is not a decision.
        return 1
    return {"ACCEPT": 0, "HOLD": 0, "NSF": 0, "REJECT": 1,
            "BLOCKED": 1}.get(acc.decision, 0)


def cmd_improve(args) -> int:
    """⑤ RE-EVOLVE — failure clusters -> one bounded edit each -> the gates."""
    state = _open_state(args)
    bad = _require_home(state)
    if bad:
        return bad
    mine_result = _run_mine(state, project=args.project, last_n=args.last_n)
    auto_compile(mine_result)

    examiner = None
    if not args.no_examine and not args.dry_run:
        def examiner(draft, base):
            path = (draft.get("declared_paths") or [None])[0]
            if not path or not os.path.exists(os.path.expanduser(path)):
                # The edit is a *proposal*: the file it names may not exist yet,
                # and precedent will not create it to make it measurable.
                return None
            # `improve` hands over what is LEFT of --budget-usd, not the whole
            # of it: one examine per draft, each given the full budget, is how
            # a $0.60 run becomes a $3.00 run.
            remaining = float(base.get("budgetRemainingUsd") or 0.0)
            if remaining <= 0:
                return None
            ex = run_examine(state, path, mine_result=mine_result,
                             runs=args.runs,
                             max_cost_usd=min(MAX_EXAM_COST_USD, remaining),
                             arm_budget_usd=min(args.per_call, remaining))
            if not ex.all_outcomes:
                return {"decision": None, "costUsd": ex.cost_usd,
                        "reservedUsd": (ex.fallback or {}).get("reservedUsd", 0.0)}
            acc = accept_outcomes(
                state, ex.candidate, ex.all_outcomes,
                surface=draft["surface"],
                declared_paths=draft["declared_paths"],
                hypothesis=draft["hypothesis"],
                expected_effect=draft["expected_effect"],
                evidence={"cluster": base.get("evidence", {}).get("cluster")})
            return {"decision": acc.decision, "reason": acc.reason,
                    "proposalId": acc.proposal_id, "costUsd": ex.cost_usd,
                    "reservedUsd": (ex.fallback or {}).get("reservedUsd", 0.0)}

    res = improve(state, sessions=mine_result.sessions, mine_result=mine_result,
                  top=args.top, budget_usd=args.budget_usd,
                  per_call_usd=args.per_call, model=args.model,
                  dry_run=args.dry_run, examiner=examiner,
                  min_count=args.min_count)
    md = render_improve(res)
    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)) or ".",
                    exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(res.to_dict(), fh, ensure_ascii=False, indent=2)
        print(f"precedent: wrote {args.json_out}", file=sys.stderr)
    if not args.quiet:
        sys.stdout.write(md)
    return 0


def cmd_loop(args) -> int:
    """One nightly cycle end to end, or the snippet that schedules it."""
    state = _open_state(args)
    bad = _require_home(state)
    if bad:
        return bad
    if args.cron:
        sys.stdout.write(cron_snippet(state, hour=args.hour, minute=args.minute,
                                      budget_usd=args.budget_usd))
        return 0
    res = run_cycle(state, project=args.project, last_n=args.last_n,
                    budget_usd=args.budget_usd, dry_run=not args.apply,
                    improve_top=args.top, candidate=args.candidate,
                    runs=args.runs, settings_path=args.settings)
    md = render_cycle(res, state)
    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)) or ".",
                    exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(res.to_dict(), fh, ensure_ascii=False, indent=2)
        print(f"precedent: wrote {args.json_out}", file=sys.stderr)
    if not args.quiet:
        sys.stdout.write(md)
    return 0


def cmd_report(args) -> int:
    state = _open_state(args)
    bad = _require_home(state)
    if bad:
        return bad
    now = datetime.now(timezone.utc)
    scan_result = _run_scan(state, project=args.project, last_n=args.last_n)
    mine_result = None
    if not args.no_mine:
        mine_result = _run_mine(state, project=args.project, last_n=args.last_n,
                                similarity=args.similarity)
        write_topics(state, mine_result)
    _len, _ok, records = _ledger_info(state)
    settings_path = getattr(args, "settings", None) or os.path.join(
        state.claude_home, "settings.json")
    md = render_digest(state, scan_result, mine_result, state.precedents(), records,
                       now=now, settings_path=settings_path,
                       live_summary=getattr(scan_result, "live", None),
                       days=getattr(args, "days", 7))
    if args.md_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.md_out)) or ".", exist_ok=True)
        with open(args.md_out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"precedent: wrote {args.md_out}", file=sys.stderr)
    if not args.quiet:
        sys.stdout.write(md)
    return 0


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="precedent",
        description="A self-evolving Claude Code harness: mine your corrections, "
                    "compile them into deterministic checks, gate them against "
                    "the record (temporal birth gate), enforce them with hooks. "
                    "Every command is free and offline except `compile --llm`.")
    p.add_argument("--version", action="version", version=f"precedent {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # init
    s = sub.add_parser("init", help="detect the Claude home, create the state dir "
                                    "and ledger, scan, print the first screen")
    _common(s)
    _scan_filters(s)
    s.set_defaults(func=cmd_init)

    # scan
    s = sub.add_parser("scan", help="receipts scan, archived into the state dir")
    _common(s)
    _scan_filters(s)
    s.add_argument("--json", dest="json_out", default=None, metavar="PATH")
    s.add_argument("--md", dest="md_out", default=None, metavar="PATH")
    s.add_argument("--no-subagents", action="store_true")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_scan)

    # mine
    s = sub.add_parser("mine", help="CORRECTION MINER v1 (+ auto-compile)")
    _common(s)
    _scan_filters(s)
    s.add_argument("--json", dest="json_out", default=None, metavar="PATH")
    s.add_argument("--md", dest="md_out", default=None, metavar="PATH")
    s.add_argument("--similarity", type=float, default=DEFAULT_SIMILARITY,
                   metavar="J", help="Jaccard threshold for grouping topics "
                                     f"(default {DEFAULT_SIMILARITY})")
    s.add_argument("--top", type=int, default=None, metavar="N",
                   help="only render the N most interesting topics")
    s.add_argument("--no-compile", action="store_true",
                   help="skip the auto-compile pass (topics only, no gate)")
    _gate_args(s)
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_mine)

    # compile
    s = sub.add_parser("compile", help="topic(s) -> DSL v1 rule + TEMPORAL BIRTH GATE")
    s.add_argument("topic_id", metavar="TOPIC_ID",
                   help="a `t-…` id or `T<n>` index; comma-separate several to "
                        "compile one rule from several topics")
    _common(s)
    _scan_filters(s)
    s.add_argument("--template", choices=TEMPLATES, default=None)
    s.add_argument("--x", default=None, help="the thing to use / not to use")
    s.add_argument("--y", default=None, help="the thing NOT to use (use_x_not_y)")
    s.add_argument("--path", default=None, help="the path not to touch")
    s.add_argument("--field", default=None,
                   help="the input field (require_field, or the field a regex "
                        "binds to): model, command, file_path, prompt, url, skill…")
    s.add_argument("--value", default=None,
                   help="the value that field must equal (require_field)")
    s.add_argument("--flag", default=None, help="the forbidden flag (forbid_flag)")
    s.add_argument("--regex", default=None,
                   help="the pattern the --field must match (require_regex); "
                        "linted for catastrophic backtracking before it is stored")
    s.add_argument("--command", dest="command_text", default=None,
                   help="restrict forbid_flag to this command (e.g. 'git push')")
    s.add_argument("--preset", choices=tuple(ASK_BEFORE_PRESETS), default=None,
                   help="ask_before preset")
    s.add_argument("--tool", default=None,
                   help="override the matched tool (e.g. 'Bash' or 'Agent|Workflow')")
    s.add_argument("--action", choices=("deny", "ask", "log"), default=None)
    s.add_argument("--message", default=None)
    s.add_argument("--scope", choices=("project", "global"), default="project")
    s.add_argument("--cwd-glob", dest="cwd_glob", default=None,
                   help="restrict a project-scoped rule to cwds matching this glob")
    _gate_args(s)
    s.add_argument("--llm", action="store_true",
                   help="ask `claude -p` for 1-3 DSL rules; every draft is still "
                        "schema-validated and must PASS the temporal gate")
    s.add_argument("--llm-model", default=DEFAULT_MODEL,
                   help=f"model for the draft call (default {DEFAULT_MODEL})")
    s.add_argument("--llm-budget", type=float, default=DEFAULT_BUDGET_USD,
                   metavar="USD", help=f"--max-budget-usd for the call "
                                       f"(default {DEFAULT_BUDGET_USD})")
    s.add_argument("--llm-timeout", type=int, default=180, metavar="S")
    s.add_argument("--similarity", type=float, default=None)
    s.add_argument("--json", dest="json_out", default=None, metavar="PATH")
    s.set_defaults(func=cmd_compile)

    # confirm
    s = sub.add_parser("confirm", help="write a compiled rule into precedents.json")
    s.add_argument("rule_id", metavar="RULE_ID", help="a `p-…` rule id or its topic id")
    _common(s)
    s.add_argument("--force", action="store_true",
                   help="confirm even though the birth gate failed (discouraged)")
    s.set_defaults(func=cmd_confirm)

    # retire
    s = sub.add_parser("retire",
                       help="stop enforcing an active precedent (keeps the record)")
    s.add_argument("rule_id", metavar="RULE_ID", help="a `p-…` rule id")
    _common(s)
    s.add_argument("--reason", default=None,
                   help="why it is being retired — it becomes a negative example")
    s.set_defaults(func=cmd_retire)

    # own
    s = sub.add_parser("own", help="declare who owns a governed artifact")
    _common(s)
    s.add_argument("path", nargs="?", default=None,
                   help="the file (or a glob like '~/.claude/skills/**'); "
                        "omit to list what is declared")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--user", action="store_true",
                   help="yours: a subagent write to it asks first (default)")
    g.add_argument("--agent", action="store_true",
                   help="the agent's: its writes go through without asking")
    s.add_argument("--guard", choices=("on", "off"), default=None,
                   help="turn the ownership guard itself on or off "
                        "(it is the confirmed rule that lets the hook ask)")
    s.set_defaults(func=cmd_own)

    # docket
    s = sub.add_parser("docket", help="the queue of candidates, with evidence")
    dsub = s.add_subparsers(dest="docket_command")
    _common(s)
    s.add_argument("--batch", action="store_true",
                   help="one-screen digest instead of the full evidence")
    s.add_argument("--all", action="store_true",
                   help="include entries already confirmed / rejected")
    s.add_argument("--json", dest="json_out", default=None, metavar="PATH")
    s.add_argument("--md", dest="md_out", default=None, metavar="PATH")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_docket, docket_command=None)
    for name, helptext in (("confirm", "accept it (a rule becomes active)"),
                           ("reject", "throw it out, with the reason"),
                           ("snooze", f"come back in {SNOOZE_DAYS} days")):
        dp = dsub.add_parser(name, help=helptext)
        dp.add_argument("entry_id", metavar="ID")
        _common(dp)
        if name == "confirm":
            dp.add_argument("--force", action="store_true",
                            help="confirm a rule that did not PASS the gate")
        if name == "reject":
            dp.add_argument("--reason", default=None,
                            help="why — it becomes a negative example")
        if name == "snooze":
            dp.add_argument("--days", type=int, default=SNOOZE_DAYS, metavar="N")
        dp.set_defaults(func=cmd_docket, docket_command=name, batch=False,
                        all=False, json_out=None, md_out=None, quiet=False)

    # hooks
    s = sub.add_parser("hooks", help="render/install/uninstall/audit the hook suite")
    hsub = s.add_subparsers(dest="hooks_command", required=True)
    for name, helptext in (("install", "write the hook scripts and merge the "
                                       "settings.json hooks block"),
                           ("uninstall", "remove precedent's entries from "
                                         "settings.json"),
                           ("status", "what is installed, and every way it has "
                                      "drifted")):
        hp = hsub.add_parser(name, help=helptext)
        hp.add_argument("target", choices=("claude-code",))
        _common(hp)
        hp.add_argument("--settings", default=None, metavar="PATH",
                        help="settings.json to merge into "
                             "(default: <claude home>/settings.json)")
        if name == "status":
            hp.set_defaults(apply=False, dry_run=True, i_know=False,
                            scripts_only=False, show_scripts=False)
            hp.set_defaults(func=cmd_hooks, hooks_command=name)
            continue
        g = hp.add_mutually_exclusive_group()
        g.add_argument("--dry-run", action="store_true", default=True,
                       help="print what would be written (the default)")
        g.add_argument("--apply", action="store_true",
                       help="actually write, after a timestamped backup under "
                            "the state dir")
        hp.add_argument("--i-know", dest="i_know", action="store_true",
                        help="yes, write into my real ~/.claude/settings.json")
        if name == "install":
            hp.add_argument("--scripts-only", action="store_true",
                            help="with --apply: write the hook scripts but leave "
                                 "settings.json alone")
            hp.add_argument("--show-scripts", dest="show_scripts",
                            action="store_true",
                            help="print every generated hook script in full")
        else:
            hp.set_defaults(scripts_only=False, show_scripts=False)
        hp.set_defaults(func=cmd_hooks, hooks_command=name)

    # snapshot
    s = sub.add_parser("snapshot", help="content-addressed snapshot of the "
                                        "learned-state tree")
    _common(s)
    _scan_filters(s)
    s.add_argument("--dry-run", action="store_true",
                   help="list what would be stored, store nothing")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(func=cmd_snapshot)

    # undo
    s = sub.add_parser("undo", help="restore what one session wrote (dry-run default)")
    s.add_argument("--session", required=True, metavar="ID")
    _common(s)
    _scan_filters(s)
    s.add_argument("--apply", action="store_true",
                   help="actually restore (refused on a real ~/.claude "
                        "without --i-know)")
    s.add_argument("--i-know", dest="i_know", action="store_true",
                   help="yes, write into my real ~/.claude")
    s.set_defaults(func=cmd_undo)

    # examine
    s = sub.add_parser("examine", help="cassettes -> claude plugin eval (or a "
                                       "paired claude -p fallback) -> the gate")
    _common(s)
    _scan_filters(s)
    s.add_argument("--candidate", required=True, metavar="PATH_OR_ID",
                   help="a skill dir / SKILL.md / CLAUDE.md / any file, or a "
                        "rule id (p-…) or proposal id (d-…)")
    s.add_argument("--runs", type=int, default=DEFAULT_RUNS, metavar="N",
                   help=f"runs per arm per case "
                        f"(default {DEFAULT_RUNS}, max {MAX_RUNS})")
    s.add_argument("--max-cost-usd", dest="max_cost_usd", type=float,
                   default=DEFAULT_MAX_COST_USD, metavar="USD",
                   help=f"hard ceiling for the whole exam "
                        f"(default {DEFAULT_MAX_COST_USD}, "
                        f"max {MAX_EXAM_COST_USD:g})")
    s.add_argument("--arm-budget", type=float, default=DEFAULT_ARM_BUDGET_USD,
                   metavar="USD", help="--max-budget-usd for one fallback arm "
                                       f"(default {DEFAULT_ARM_BUDGET_USD})")
    s.add_argument("--model", default=None,
                   help="model override for both arms (default: the case's)")
    s.add_argument("--alpha", type=float, default=DEFAULT_ALPHA0, metavar="A",
                   help=f"delta0 of the CTHS spend schedule "
                        f"(default {DEFAULT_ALPHA0})")
    s.add_argument("--harm-alpha", dest="harm_alpha", type=float,
                   default=DEFAULT_HARM_ALPHA, metavar="A",
                   help=f"level of the harm martingale "
                        f"(default {DEFAULT_HARM_ALPHA}; harm is deliberately "
                        f"cheaper to prove than improvement)")
    s.add_argument("--require-floor", dest="require_floor", action="store_true",
                   help="an ACCEPT whose TaskFloor is not PASS becomes HOLD")
    s.add_argument("--fallback", action="store_true",
                   help="skip `claude plugin eval` and use the paired "
                        "`claude -p` runner directly")
    s.add_argument("--no-git", dest="no_git", action="store_true",
                   help="do not shell out to git for the scaffold snapshot")
    s.add_argument("--allow-bash", dest="allow_bash", action="store_true",
                   help="let both arms run the session's own verification "
                        "command. OFF by default: the grant is a Bash(<prefix>*) "
                        "over your real working tree, and without it the exam "
                        "is read-only and usually returns all-ties")
    s.add_argument("--no-mine", dest="no_mine", action="store_true",
                   help="skip the miner (cassette prompts fall back to a "
                        "synthetic one)")
    s.add_argument("--dry-run", action="store_true",
                   help="write the eval suite and print the plan; run nothing")
    s.add_argument("--json", dest="json_out", default=None, metavar="PATH")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_examine)

    # improve
    s = sub.add_parser("improve", help="NIGHTLY IMPROVER: failure clusters -> "
                                       "one bounded edit each -> the gates")
    _common(s)
    _scan_filters(s)
    s.add_argument("--budget-usd", dest="budget_usd", type=float,
                   default=DEFAULT_TOTAL_BUDGET_USD, metavar="USD",
                   help=f"total spend for this run "
                        f"(default {DEFAULT_TOTAL_BUDGET_USD})")
    s.add_argument("--per-call", type=float, default=DEFAULT_BUDGET_USD,
                   metavar="USD", help=f"--max-budget-usd per draft call "
                                       f"(default {DEFAULT_BUDGET_USD})")
    s.add_argument("--top", type=int, default=DEFAULT_TOP_CLUSTERS, metavar="N",
                   help=f"how many clusters to draft for "
                        f"(default {DEFAULT_TOP_CLUSTERS})")
    s.add_argument("--min-count", dest="min_count", type=int, default=2,
                   metavar="N", help="minimum occurrences for a cluster")
    s.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"draft model (default {DEFAULT_MODEL})")
    s.add_argument("--runs", type=int, default=DEFAULT_RUNS, metavar="N")
    s.add_argument("--no-examine", dest="no_examine", action="store_true",
                   help="do not run the examiner on non-rule drafts; they are "
                        "HOLD 'no evidence' instead")
    s.add_argument("--dry-run", action="store_true",
                   help="cluster only: print the signatures, call no model")
    s.add_argument("--json", dest="json_out", default=None, metavar="PATH")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_improve)

    # loop
    s = sub.add_parser("loop", help="one nightly cycle end to end "
                                    "(--dry-run), or the cron/launchd snippet")
    _common(s)
    _scan_filters(s)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True,
                   help="walk the cycle with zero model calls (the default)")
    g.add_argument("--apply", action="store_true",
                   help="actually run the paid steps, inside --budget-usd")
    g.add_argument("--cron", action="store_true",
                   help="print a launchd plist and a crontab line; install "
                        "neither")
    s.add_argument("--budget-usd", dest="budget_usd", type=float, default=0.0,
                   metavar="USD", help="total spend for the paid steps")
    s.add_argument("--candidate", default=None, metavar="PATH_OR_ID",
                   help="also examine this candidate in step ②")
    s.add_argument("--top", type=int, default=DEFAULT_TOP_CLUSTERS, metavar="N")
    s.add_argument("--runs", type=int, default=DEFAULT_RUNS, metavar="N")
    s.add_argument("--hour", type=int, default=3, metavar="H",
                   help="--cron: the hour to run at (default 3)")
    s.add_argument("--minute", type=int, default=17, metavar="M",
                   help="--cron: the minute to run at (default 17)")
    s.add_argument("--settings", default=None, metavar="PATH")
    s.add_argument("--json", dest="json_out", default=None, metavar="PATH")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_loop)

    # report
    s = sub.add_parser("report", help="the daily digest: alarms, funnel, spend")
    _common(s)
    _scan_filters(s)
    s.add_argument("--md", dest="md_out", default=None, metavar="PATH")
    s.add_argument("--days", type=int, default=7, metavar="N",
                   help="how many days the daily table covers (default 7)")
    s.add_argument("--settings", default=None, metavar="PATH",
                   help="settings.json to audit for install drift")
    s.add_argument("--no-mine", action="store_true")
    s.add_argument("--similarity", type=float, default=DEFAULT_SIMILARITY)
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except BrokenPipeError:                               # pragma: no cover - piping
        return 0
    except KeyboardInterrupt:                             # pragma: no cover
        return 130
    except ClaudeHomeWriteRefused as exc:
        # The write guard fired.  It is meant to be loud, not a traceback.
        print(f"precedent: {exc}", file=sys.stderr)
        return 2
    except SettingsUnreadable as exc:
        print(f"precedent: {exc}", file=sys.stderr)
        return 2
