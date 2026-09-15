# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""THE NIGHTLY CYCLE — one command that shows the whole loop, end to end.

``precedent loop --dry-run`` walks ① propose → ② accept → ③ enforce → ④ audit →
⑤ re-evolve once and prints what each step found, **without one model call and
without one byte written into the Claude home**.  It is the honest version of
the five-minute demo: every number in it is re-derivable with ``grep``.

``precedent loop --cron`` prints a launchd plist (macOS) and a crontab line
(everything else) that run the same cycle nightly.  It prints them; it does not
install them.  Scheduling something that spends money is a decision the user
makes with their own hands, which is the same rule as ``hooks install --apply``.

The cycle, in order:

===  =============================  ==========================================
 ①   ``mine`` + ``auto_compile``    corrections -> candidate rules -> gate
 ①   ``improve``                    failure clusters -> bounded edits -> gate
 ②   ``examine`` -> ``accept``      cassettes -> paired arms -> certificate
 ③   ``hooks status``               is what was accepted actually installed?
 ④   ``report``                     funnel, STARVATION alarms, spend
 ⑤   ``rejected.jsonl``             what comes back as a negative example
===  =============================  ==========================================
"""

from __future__ import annotations

import os
import shlex
import sys
from dataclasses import dataclass, field
from xml.sax.saxutils import escape as _xml_escape

from . import __version__

__all__ = ["CycleStep", "LoopResult", "cron_snippet", "launchd_plist",
           "render_cycle", "run_cycle"]


@dataclass
class CycleStep:
    n: str                      # the circled step number, as text
    name: str
    command: str
    ran: bool = True
    lines: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return {"step": self.n, "name": self.name, "command": self.command,
                "ran": self.ran, "lines": self.lines,
                "costUsd": self.cost_usd, "error": self.error}


@dataclass
class LoopResult:
    steps: list[CycleStep] = field(default_factory=list)
    dry_run: bool = True
    budget_usd: float = 0.0
    spend_usd: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"tool": {"name": "precedent", "version": __version__},
                "dryRun": self.dry_run, "budgetUsd": self.budget_usd,
                "spendUsd": round(self.spend_usd, 6),
                "steps": [s.to_dict() for s in self.steps],
                "notes": self.notes}


def _safe(fn, step: CycleStep):
    try:
        fn(step)
    except Exception as exc:                             # pragma: no cover - defensive
        step.error = f"{type(exc).__name__}: {exc}"
        step.lines.append(f"(step failed, the cycle continues: {step.error})")
    return step


def run_cycle(state, *, project=None, last_n=None, similarity=None,
              budget_usd: float = 0.0, dry_run: bool = True,
              improve_top: int = 3, candidate: str | None = None,
              runs: int = 2, settings_path: str | None = None) -> LoopResult:
    """One nightly cycle.  ``dry_run`` (the default) makes zero model calls."""
    from .audit import alarms, funnel as audit_funnel, hook_health
    from .compile import auto_compile
    from .docket import build_entries, starving
    from .hooks import status as hooks_status
    from .improve import cluster_failures, improve
    from .mine import DEFAULT_SIMILARITY, mine, write_topics
    from .examine import build_cassettes

    res = LoopResult(dry_run=dry_run, budget_usd=float(budget_usd))
    similarity = similarity or DEFAULT_SIMILARITY
    settings = settings_path or os.path.join(state.claude_home, "settings.json")

    # ---- ① propose: the correction miner ------------------------------
    holder: dict = {}
    step = CycleStep("①", "propose · correction miner",
                     f"precedent mine --similarity {similarity}")

    def _mine(s: CycleStep):
        m = mine(state.claude_home, project_filter=project, last_n=last_n,
                 similarity=similarity)
        auto_compile(m)
        holder["mine"] = m
        n_auto = len([t for t in m.topics if t.auto and t.auto.get("compiled")])
        n_pass = len([t for t in m.topics if t.auto
                      and (t.auto.get("gate") or {}).get("verdict") == "PASS"])
        s.lines += [
            f"{m.n_sessions} 个会话 · {m.n_human_turns} 条人类输入 "
            f"(跳过 {m.n_skipped_expansions} 条展开)",
            f"{m.n_corrections} 条纠正 ({m.pct_corrections}%) → {len(m.topics)} 个主题，"
            f"重复主题 {len(m.repeated_topics)} 个",
            f"自动编译 {n_auto} 个，出生门 PASS {n_pass} 个 "
            f"(分词 {m.segmenter})",
        ]
        if not dry_run:
            write_topics(state, m)
    res.steps.append(_safe(_mine, step))
    m = holder.get("mine")

    # ---- ① propose: the nightly improver -------------------------------
    step = CycleStep("①", "propose · nightly improver",
                     f"precedent improve --budget-usd {budget_usd:g}"
                     + (" --dry-run" if dry_run else ""))

    def _improve(s: CycleStep):
        sessions = getattr(m, "sessions", []) or []
        if dry_run or budget_usd <= 0:
            clusters = cluster_failures(sessions, m, state.precedents())
            s.ran = False
            s.lines.append(f"{len(clusters)} 个失败签名聚类"
                           + ("（--dry-run：不调用模型）" if dry_run else
                              "（--budget-usd 为 0：不调用模型）"))
            for c in clusters[:5]:
                s.lines.append(f"  · [{c.kind}] x{c.count} / "
                               f"{len(c.sessions)} 会话 — {c.key[:80]}")
            return
        out = improve(state, sessions=sessions, mine_result=m, top=improve_top,
                      budget_usd=budget_usd)
        # What the drafting calls COMMITTED, not only what `claude` said they
        # charged: the examiner below is funded out of what is left, and a
        # binary that under-reports must not be able to refund step ② a budget
        # step ① already spent.
        booked = max(out.spend_usd, out.reserved_usd)
        s.cost_usd = booked
        res.spend_usd += booked
        s.lines += [
            f"{len(out.clusters)} 个聚类，试了 {len(out.attempted)} 个；"
            f"过门 {len(out.accepted)}，HOLD {len(out.held)}，"
            f"拒绝 {len(out.rejected)}",
            f"花费 ${out.spend_usd:.4f}",
        ]
        if out.stopped:
            s.lines.append(out.stopped)
    res.steps.append(_safe(_improve, step))

    # ---- ② accept: the examiner ---------------------------------------
    step = CycleStep("②", "accept · examiner + paired gate",
                     f"precedent examine --candidate "
                     f"{candidate or '<path-or-id>'}"
                     + (" --dry-run" if dry_run else ""))

    def _examine(s: CycleStep):
        sessions = getattr(m, "sessions", []) or []
        cassettes, skipped = build_cassettes(sessions, state.precedents(),
                                             None, allow_git=False)
        s.lines.append(f"{len(cassettes)} 个合格磁带 / {len(sessions)} 个会话"
                       f"（其余 {len(skipped)} 个没有终端验证命令，也没有可当"
                       f"评分器的先例）")
        for c in cassettes[:5]:
            v = (c.verification or {}).get("command", "(precedents only)")
            s.lines.append(f"  · {c.case_name} — `{v[:70]}`")
        if not cassettes:
            s.lines.append("没有磁带 → 任何非规则候选都会是 HOLD 'no evidence'，"
                           "这是诚实答案，不是通过")
        if candidate and not dry_run:
            from .accept import accept_outcomes
            from .examine import examine as run_examine
            from .examine import MAX_EXAM_COST_USD
            ex = run_examine(state, candidate, mine_result=m, runs=runs,
                             max_cost_usd=min(MAX_EXAM_COST_USD,
                                              max(0.0, budget_usd - res.spend_usd)))
            acc = accept_outcomes(state, ex.candidate, ex.all_outcomes,
                                  evidence={"examine": ex.to_dict()})
            s.cost_usd = ex.cost_usd
            res.spend_usd += ex.cost_usd
            s.lines.append(f"判定 **{acc.decision}** — {acc.reason}")
            s.lines.append(f"docket: {acc.proposal_id}（不会自动应用）")
        elif candidate:
            s.ran = False
            s.lines.append(f"--dry-run：不会执行 `{candidate}` 的双臂评估")
        else:
            s.ran = False
            s.lines.append("没有 --candidate：只列出磁带")
    res.steps.append(_safe(_examine, step))

    # ---- ③ enforce ------------------------------------------------------
    step = CycleStep("③", "enforce · hooks", "precedent hooks status claude-code")

    def _enforce(s: CycleStep):
        rules = state.precedents()
        active = [r for r in rules if r.get("status") == "active"]
        st = hooks_status(state, rules, settings)
        holder["hooks"] = st
        s.lines.append(f"已确认先例 {len(active)} 条 · "
                       f"已安装 {'是' if st.get('installed') else '否'} · "
                       f"我们的条目 {st.get('entriesFound', 0)}/"
                       f"{st.get('entriesExpected', 0)}")
        drift = st.get("drift") or []
        s.lines.append("漂移: " + ("无" if not drift else
                                   "; ".join(str(d) for d in drift[:3])))
    res.steps.append(_safe(_enforce, step))

    # ---- ④ audit --------------------------------------------------------
    step = CycleStep("④", "audit · docket + alarms", "precedent report")

    def _audit(s: CycleStep):
        entries = build_entries(state)
        starved = starving(entries)
        st = holder.get("hooks") or hooks_status(state, state.precedents(), settings)
        health = hook_health(state)
        fun = audit_funnel(state, entries, st, health)
        al = alarms(state, entries, fun, health, st)
        pending = len([e for e in entries if e["status"] == "pending"])
        s.lines.append(f"docket 待办 {pending} 条（饿死 ≥7 天 {len(starved)} 条）")
        st_ = fun.get("stages") or {}
        s.lines.append(f"漏斗 提出 {st_.get('proposed', 0)} → 接受 "
                       f"{st_.get('accepted', 0)} → 激活 "
                       f"{st_.get('activated', 0)} → 归因 "
                       f"{st_.get('attributed', 0)}")
        if al:
            for a in al[:5]:
                s.lines.append(f"  ALARM {a.get('code')}: "
                               f"{a.get('message', '')[:120]}")
        else:
            s.lines.append("  告警：无（通过时静默，阻止时响亮，饿死时绝不静默）")
    res.steps.append(_safe(_audit, step))

    # ---- ⑤ re-evolve ----------------------------------------------------
    step = CycleStep("⑤", "re-evolve · negative examples",
                     f"cat {state.rejected_path}")

    def _reevolve(s: CycleStep):
        from .llm import load_negatives
        negs = load_negatives(state, limit=50)
        s.lines.append(f"{len(negs)} 条被拒草稿会作为反例进入下一轮提示词")
        for n in negs[:4]:
            s.lines.append(f"  · {str(n.get('reason', ''))[:100]}")
    res.steps.append(_safe(_reevolve, step))

    if dry_run:
        res.notes.append("--dry-run：本次没有任何模型调用，也没有向 Claude home "
                         "写入任何字节")
    return res


def render_cycle(res: LoopResult, state=None) -> str:
    L = [f"# precedent loop — 一个夜间周期"
         + ("（--dry-run）" if res.dry_run else ""), ""]
    if state is not None:
        L.append(f"claude home `{state.claude_home}` · state `{state.root}`")
    L.append(f"预算 ${res.budget_usd:g} · 实花 ${res.spend_usd:.4f}")
    L.append("")
    for s in res.steps:
        mark = "" if s.ran else "（未执行）"
        L.append(f"## {s.n} {s.name}{mark}")
        L.append("")
        L.append(f"`{s.command}`")
        L.append("")
        for line in s.lines:
            L.append(f"- {line}")
        if s.error:
            L.append(f"- **error**: {s.error}")
        L.append("")
    for n in res.notes:
        L.append(f"> {n}")
    if res.notes:
        L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# --cron
# --------------------------------------------------------------------------

def _argv_for_cron(state, budget_usd: float, python: str | None = None,
                   extra: list[str] | None = None) -> str:
    """A **shell-quoted** command line.

    The interpreter and the state dir routinely contain spaces (a ``.venv``
    inside "AI coding open/自进化Agent Harness" does), and an unquoted crontab
    line then runs something else entirely, at 03:17, unattended.
    """
    exe = python or sys.executable
    argv = [exe, "-m", "precedent", "loop",
            "--budget-usd", f"{budget_usd:g}",
            "--state-dir", state.root,
            "--claude-home", state.claude_home] + list(extra or [])
    return " ".join(shlex.quote(a) for a in argv)


def launchd_plist(state, *, hour: int = 3, minute: int = 17,
                  budget_usd: float = 2.0, label: str = "dev.precedent.nightly",
                  python: str | None = None) -> str:
    """A macOS launchd job.  Printed, never installed."""
    exe = python or sys.executable
    args = ["-m", "precedent", "loop", "--budget-usd", f"{budget_usd:g}",
            "--state-dir", state.root, "--claude-home", state.claude_home]
    arg_xml = "\n".join(f"    <string>{_xml_escape(a)}</string>"
                        for a in [exe] + args)
    log = os.path.join(state.root, "loop.log")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{_xml_escape(label)}</string>
  <key>ProgramArguments</key>
  <array>
{arg_xml}
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>{hour}</integer>
    <key>Minute</key><integer>{minute}</integer>
  </dict>
  <key>StandardOutPath</key><string>{_xml_escape(log)}</string>
  <key>StandardErrorPath</key><string>{_xml_escape(log)}</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>"""


def cron_snippet(state, *, hour: int = 3, minute: int = 17,
                 budget_usd: float = 2.0, python: str | None = None) -> str:
    """The launchd plist + the crontab line + how to install each, by hand."""
    plist = launchd_plist(state, hour=hour, minute=minute,
                          budget_usd=budget_usd, python=python)
    plist_path = os.path.expanduser("~/Library/LaunchAgents/dev.precedent.nightly.plist")
    cron = (f"{minute} {hour} * * *  "
            f"{_argv_for_cron(state, budget_usd, python)} "
            f">> {shlex.quote(os.path.join(state.root, 'loop.log'))} 2>&1")
    L = ["# precedent loop --cron", "",
         f"每晚 {hour:02d}:{minute:02d} 跑一次完整周期，预算 ${budget_usd:g}。",
         "**下面两段都只是打印出来的文本——precedent 不会替你安装定时任务，",
         "就像它不会替你写 settings.json 一样。**", "",
         "## macOS (launchd)", "",
         f"写到 `{plist_path}`，然后 "
         f"`launchctl load -w {shlex.quote(plist_path)}`：", "",
         "```xml", plist, "```", "",
         "## crontab (Linux / 任何有 cron 的地方)", "",
         "`crontab -e`，加一行：", "",
         "```", cron, "```", "",
         "## 先确认它是无害的", "",
         "```", _argv_for_cron(state, 0, python, ["--dry-run"]), "```", "",
         "`--dry-run` 零模型调用、零写入 Claude home；确认输出符合预期之后，"
         "再把预算调上去。", ""]
    return "\n".join(L)
