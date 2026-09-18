# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""I18N — the frame is translated, the evidence never is.

This tool was Chinese-only for its first three weeks, which meant the very
first thing a stranger saw — ``precedent init`` — was ten lines they could not
read.  So the *frame* (labels, headings, summary sentences) now comes from a
dict per string id, and English is the default.

Three rules, and the second is the one that matters:

**One dict, no dependency.**  :data:`STRINGS` maps ``string id -> {lang: text}``.
No gettext, no ``.mo`` files, no build step, no runtime dependency — the
packages are standard library only and an i18n layer is not where that stops
being true.  A test walks the whole table and fails if an id is missing a
language or if the two languages disagree about their ``{placeholders}``, so a
half-translated string cannot ship.

**The evidence is never translated.**  A quote from your transcript, a rule
message, a file path, a tool name and a matcher are reproduced byte for byte in
every language.  Translating evidence would mean the report no longer says what
the record says, and the whole point of this tool is that every line can be
re-derived by hand.  Translation stops at the sentence *around* the quote.

**Auto-detected, never guessed twice.**  :func:`resolve` reads, in order, the
explicit ``--lang``, ``PRECEDENT_LANG``, ``LC_ALL``, ``LC_MESSAGES``, ``LANG``,
and falls back to English.  Anything starting ``zh`` (``zh_CN.UTF-8``,
``zh-Hant``, ``zh``) is Chinese; everything else — including an unset or ``C``
locale — is English.

The current language is process-global (:func:`set_lang`) because the renderers
are called from twenty places that would otherwise all have to thread a
parameter through; every renderer still accepts an explicit ``lang=`` that wins
over the global, which is what the tests use.
"""

from __future__ import annotations

import os
import unicodedata

__all__ = [
    "DEFAULT_LANG", "LANGS", "PLURALS", "STRINGS", "display_width", "get_lang",
    "pad", "plural", "resolve", "set_lang", "t", "using",
]

#: The languages every string id must carry.
LANGS = ("en", "zh")

#: What you get with no ``--lang``, no ``PRECEDENT_LANG`` and no locale.
DEFAULT_LANG = "en"

_CURRENT = DEFAULT_LANG


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

def normalise(value) -> str | None:
    """``'zh_CN.UTF-8'`` -> ``'zh'``; ``'en_GB'`` -> ``'en'``; junk -> ``None``.

    ``C`` and ``POSIX`` are *not* a language choice, they are the absence of
    one, so they resolve to ``None`` and let the next source have its say.
    """
    if not isinstance(value, str):
        return None
    tag = value.strip().replace("_", "-").split(".")[0].split("@")[0].lower()
    if not tag or tag in ("c", "posix"):
        return None
    if tag.startswith("zh"):
        return "zh"
    if tag.startswith("en"):
        return "en"
    # A locale we have no strings for is not an error: English is the fallback
    # everywhere, and saying so here keeps the precedence chain honest.
    return DEFAULT_LANG if tag[:2].isalpha() else None


def resolve(explicit: str | None = None, environ: dict | None = None) -> str:
    """``--lang`` > ``PRECEDENT_LANG`` > ``LC_ALL`` > ``LC_MESSAGES`` > ``LANG`` > en."""
    env = os.environ if environ is None else environ
    if explicit:
        lang = normalise(explicit)
        if lang:
            return lang
    for key in ("PRECEDENT_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        lang = normalise(env.get(key))
        if lang:
            return lang
    return DEFAULT_LANG


def get_lang() -> str:
    return _CURRENT


def set_lang(lang: str | None) -> str:
    """Set the process-wide language; returns the one that was in force."""
    global _CURRENT
    previous = _CURRENT
    _CURRENT = normalise(lang) or DEFAULT_LANG
    return previous


class using:
    """``with using("zh"): ...`` — a scoped language, for tests and renderers."""

    def __init__(self, lang: str | None):
        self._lang = lang
        self._previous = None

    def __enter__(self) -> str:
        self._previous = set_lang(self._lang)
        return get_lang()

    def __exit__(self, *exc) -> bool:
        set_lang(self._previous)
        return False


# --------------------------------------------------------------------------
# alignment: a CJK label is two columns wide, so len() is the wrong ruler
# --------------------------------------------------------------------------

def display_width(text: str) -> int:
    """Terminal columns ``text`` occupies, counting wide/fullwidth as 2."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def pad(text: str, width: int) -> str:
    """Right-pad ``text`` to ``width`` *columns* (not characters)."""
    return text + " " * max(0, width - display_width(text))


# --------------------------------------------------------------------------
# the table
# --------------------------------------------------------------------------

#: Counted nouns, ``key -> {lang: (singular, plural)}``.  English needs the
#: distinction and Chinese does not (the measure word does not inflect), so the
#: two Chinese forms are deliberately identical: both languages keep the same
#: ``{placeholders}``, which is what lets the table test compare them.
PLURALS: dict[str, dict[str, tuple]] = {
    "session": {"en": ("session", "sessions"), "zh": ("个会话", "个会话")},
    "project": {"en": ("project", "projects"), "zh": ("个项目", "个项目")},
    "artifact": {"en": ("artifact", "artifacts"), "zh": ("个工件", "个工件")},
    "instruction_file": {"en": ("instruction file", "instruction files"),
                         "zh": ("个指令文件", "个指令文件")},
    "index_entry": {"en": ("index entry", "index entries"),
                    "zh": ("条索引", "条索引")},
    "pair": {"en": ("pair", "pairs"), "zh": ("对", "对")},
    "precedent": {"en": ("precedent", "precedents"), "zh": ("条先例", "条先例")},
    "record": {"en": ("record", "records"), "zh": ("条记录", "条记录")},
    "learned_artifact": {"en": ("learned artifact", "learned artifacts"),
                         "zh": ("个学习工件", "个学习工件")},
}


def plural(n: int, key: str, lang: str | None = None) -> str:
    """The counted noun for ``n`` — English inflects, Chinese does not."""
    one, many = PLURALS[key][lang or _CURRENT]
    return one if abs(n) == 1 else many


STRINGS: dict[str, dict[str, str]] = {
    # ---- init: the ten-line first screen ---------------------------------
    "init.title": {
        # "read-only" on its own read as "writes nothing"; it only ever meant
        # "never writes your Claude home".  `init` does create <state> and
        # write a scan archive there, so the word had to name its object.
        "en": "precedent {version} — first screen "
              "(never writes your Claude home, zero model calls)",
        "zh": "precedent {version} — 首屏（绝不写你的 Claude home，零模型调用）"},
    "init.label.home": {"en": "Claude home", "zh": "Claude home"},
    "init.label.state": {"en": "state dir", "zh": "状态目录"},
    "init.label.sessions": {"en": "sessions", "zh": "会话"},
    "init.label.artifacts": {"en": "learned artifacts", "zh": "学习工件"},
    "init.label.never_cited": {"en": "never cited", "zh": "从未被引用"},
    "init.label.truncated": {"en": "loaded truncated", "zh": "曾被截断"},
    "init.label.stale": {"en": "stale index / gone", "zh": "失效索引/缺文件"},
    "init.label.duplicates": {"en": "near-duplicates", "zh": "近重复工件"},
    "init.label.unattended": {"en": "unattended writes", "zh": "无人值守写入"},
    "init.label.enforced": {"en": "precedents enforced", "zh": "已强制执行的先例"},
    "init.value.sessions": {
        "en": "{scanned} scanned / {found} found",
        "zh": "扫描 {scanned} / 发现 {found} 个"},
    "init.value.artifacts": {"en": "{n} ({mix})", "zh": "{n} 个（{mix}）"},
    "init.value.artifacts_none": {"en": "0", "zh": "0 个"},
    "init.value.never_cited": {
        "en": "{n} ({pct} of the citable ones)",
        "zh": "{n} 个（可引用工件的 {pct}）"},
    "init.value.truncated": {
        "en": "{n} {artifact_w} loaded only in part in ≥1 session",
        "zh": "{n} {artifact_w}在 ≥1 个会话里只加载了一部分"},
    "init.value.stale": {"en": "{n}", "zh": "{n} 条"},
    "init.value.duplicates": {"en": "{n}", "zh": "{n} 个"},
    "init.value.unattended": {
        "en": "{n} in the last {days} days "
              "(subagent {sub}, Bash bypassing the memory tool {bash})",
        "zh": "最近 {days} 天 {n} 次（子代理 {sub} 次，Bash 绕过记忆工具 {bash} 次）"},
    "init.value.enforced": {"en": "{n}", "zh": "{n} 条"},
    "init.next": {"en": "  → next: precedent mine", "zh": "  → 下一步：precedent mine"},
    "init.ledger": {
        "en": "ledger {path} (hash-chained, {n} {record_w}, chain {ok})",
        "zh": "账本 {path}（哈希链，{n} {record_w}，校验 {ok}）"},
    # ---- docket: the markdown frame.  README promises the frame is
    # translated and only quoted user text stays as written; render_docket and
    # render_batch were emitting 64 Chinese lines under `--lang en`.
    # ---- audit alarms.  All five were hard-coded Chinese and printed as-is
    # under `--lang en`; they simply never showed up in an English test run
    # because a machine with no alarms prints "alarms: none".
    "alarm.pending": {
        "en": "{n} docket entries have waited ≥{days} days (oldest {oldest}): {ids}",
        "zh": "{n} 条 docket 条目等待 ≥{days} 天（最老 {oldest} 天）：{ids}"},
    "alarm.hook": {
        "en": "the hook log carries {errors} exception(s), {unreadable} "
              "unreadable precedents.json, {quarantined} regex quarantine(s)",
        "zh": "钩子日志里有 {errors} 个异常、{unreadable} 次 precedents.json "
              "读不出、{quarantined} 次正则隔离"},
    "alarm.hook.last": {"en": "; most recent: {last}", "zh": "；最近一次：{last}"},
    "alarm.not_installed": {
        "en": "{n} confirmed precedents, but settings.json carries none of our "
              "hooks — enforcement is 0",
        "zh": "{n} 条已确认的先例，但 settings.json 里没有我们的钩子——强制执行 = 0"},
    "alarm.no_fires.never": {
        "en": "{n} active rule(s) installed{age}, and not one has ever fired "
              "({calls} hook calls) — either the agent really changed, or the "
              "rules do not match reality",
        "zh": "{n} 条 active 规则已安装{age}，一次都没触发过（共 {calls} 次钩子"
              "调用）——要么 agent 真的改了，要么规则匹配不到现实"},
    "alarm.no_fires.quiet": {
        "en": "{n} active rule(s) installed, last fired {quiet} days ago — "
              "nothing in {days} days ({calls} hook calls). A rule that has "
              "gone quiet is not the same as a rule that works",
        "zh": "{n} 条 active 规则已安装，上次触发在 {quiet} 天前——已有 {days} "
              "天没动静（共 {calls} 次钩子调用）。安静的规则不等于有效的规则"},
    "alarm.no_fires.age": {"en": " {n} days ago", "zh": " {n} 天"},
    "alarm.drift": {"en": "install drift, {n} item(s): {items}",
                    "zh": "安装漂移 {n} 项：{items}"},
    "docket.write.evidence": {
        "en": "{agent} write via {tool} ({how}) → {governed} artifact",
        "zh": "{agent} 经由 {tool} 写入（{how}）→ {governed} 工件"},
    "docket.gate": {"en": "birth gate {verdict}", "zh": "出生门 {verdict}"},
    "docket.quote": {"en": "verbatim ({date}): {quote}",
                     "zh": "原话（{date}）：{quote}"},
    "docket.title": {"en": "# precedent docket — {n} pending",
                     "zh": "# precedent docket — {n} 条待办"},
    "docket.title.snoozed": {"en": ", {n} snoozed", "zh": "，{n} 条已推迟"},
    "docket.generated": {"en": "generated {ts} · state `{state}`",
                         "zh": "生成于 {ts} · state `{state}`"},
    "docket.starvation": {
        "en": "**STARVATION: {n} have waited ≥{days} days** — {ids}",
        "zh": "**STARVATION：{n} 条已经等了 ≥{days} 天** — {ids}"},
    "docket.empty": {
        "en": "(Empty. `precedent mine` finds corrections; `precedent hooks "
              "install claude-code --apply` starts recording writes to the "
              "governed tree.)",
        "zh": "（空。`precedent mine` 找纠正，`precedent hooks install "
              "claude-code --apply` 让所有权钩子开始记录治理树里的写入。）"},
    "docket.age": {"en": "{n} days ago", "zh": "{n} 天前"},
    "docket.raised": {"en": "- raised {ts}  {age}", "zh": "- 提出于 {ts}  {age}"},
    "docket.actions": {
        "en": "  `precedent docket confirm {id}` · `reject {id} --reason …` · "
              "`snooze {id}` ({days} days)",
        "zh": "  `precedent docket confirm {id}` · `reject {id} --reason …` · "
              "`snooze {id}`（{days} 天）"},
    "docket.batch.counts": {
        "en": "{ts} · {n} pending (rules {rules} / writes {writes} / "
              "proposals {props})",
        "zh": "{ts} · {n} 条待办（规则 {rules} / 写入 {writes} / 提案 {props})"},
    "docket.batch.starved": {"en": " · **{n} ≥{days} days**",
                             "zh": " · **{n} 条 ≥{days} 天**"},
    "docket.batch.rules": {"en": "## Rule candidates", "zh": "## 规则候选"},
    "docket.batch.rules.head": {"en": "| id | birth gate | evidence | what |",
                                "zh": "| id | 出生门 | 证据 | 说明 |"},
    "docket.batch.writes": {"en": "## Writes to the governed tree",
                            "zh": "## 治理树写入"},
    "docket.batch.writes.n": {
        "en": "- {total} in total: {sub} from subagents, {asks} of them stopped to ask",
        "zh": "- 共 {total} 条：子代理 {sub}，其中被拦下询问 {asks}"},
    "docket.batch.writes.dist": {"en": "- by tree: {dist}", "zh": "- 分布：{dist}"},
    "docket.batch.writes.head": {"en": "| id | agent | path | diff | owner |",
                                 "zh": "| id | agent | 路径 | diff | owner |"},
    "docket.batch.props": {
        "en": "## Proposals (examiner / nightly improver) — **never auto-applied**",
        "zh": "## 提案（examiner / 夜间改进器）——**永远不会自动应用**"},
    "docket.batch.props.head": {"en": "| id | surface | verdict | evidence | paths |",
                                "zh": "| id | surface | 判定 | 证据 | 路径 |"},
    "docket.batch.none": {"en": "(Nothing pending.)", "zh": "（没有待办。）"},
    "docket.batch.footer": {
        "en": "Confirm / reject / snooze: `precedent docket "
              "confirm|reject|snooze <id>`",
        "zh": "确认/拒绝/推迟：`precedent docket confirm|reject|snooze <id>`"},
    "init.ok": {"en": "ok", "zh": "ok"},
    "init.failed": {"en": "BROKEN", "zh": "失败"},
    "init.live": {
        "en": "(live hook receipts: {rows} line(s) / {sessions} session(s), "
              "overriding {overrides} inferred verdict(s))",
        "zh": "（实时钩子收据: {rows} 行 / {sessions} 个会话，覆盖 {overrides} 条推断结论）"},
    "init.archived": {
        "en": "(receipts scan archived: {path})",
        "zh": "（receipts 扫描已存档: {path}）"},

    # ---- mine ------------------------------------------------------------
    "mine.title": {"en": "# precedent mine — correction miner v1",
                   "zh": "# precedent mine — 纠正挖掘 v1"},
    "mine.meta": {
        "en": "Claude home: `{home}`  ·  generated {ts}  ·  Chinese segmenter `{seg}`",
        "zh": "Claude home: `{home}`  ·  生成于 {ts}  ·  中文分词 `{seg}`"},
    "mine.filter": {"en": "filter: project=`{project}` last={last}",
                    "zh": "过滤: project=`{project}` last={last}"},
    "mine.table.head": {"en": "| metric | value |", "zh": "| 指标 | 值 |"},
    "mine.metric.sessions": {"en": "sessions", "zh": "会话"},
    "mine.metric.turns": {
        "en": "human turns (skill/command expansions removed: {n})",
        "zh": "人类轮次（已剔除 skill/命令展开 {n} 条）"},
    "mine.metric.corrections": {"en": "corrections detected", "zh": "检出纠正"},
    "mine.metric.questions": {"en": "sentences filtered out as questions",
                              "zh": "被问句过滤掉的句子"},
    "mine.metric.reverts": {"en": "revert / rollback actions", "zh": "撤销/回滚动作"},
    "mine.metric.topics": {"en": "topics", "zh": "主题"},
    "mine.metric.repeated": {"en": "**repeated topics** (≥2 sessions)",
                             "zh": "**重复主题**（≥2 个会话）"},
    "mine.metric.written_violated": {
        "en": "already written into CLAUDE.md / memory and still happening "
              "(written but violated)",
        "zh": "已写进 CLAUDE.md/记忆、却仍在发生（written but violated）"},
    "mine.autocompile": {
        "en": "**Auto-compile: {topics} topics, {compiled} compiled, {passed} through "
              "the birth gate (PASS)**",
        "zh": "**自动编译：{topics} 个主题，{compiled} 个编译成功，{passed} 个通过出生门"
              "（PASS）**"},
    "mine.autocompile.insufficient": {
        "en": ", {n} with too little evidence (INSUFFICIENT)",
        "zh": "，{n} 个证据不足（INSUFFICIENT）"},
    "mine.autocompile.failed": {"en": ", {n} FAIL.", "zh": "，{n} 个 FAIL。"},
    "mine.summary": {
        "en": "You corrected the agent **{corrections}** times; **{repeated}** topics "
              "recur across sessions, and **{written}** of them are already written "
              "into CLAUDE.md / memory and still happen.",
        "zh": "你纠正了 agent **{corrections}** 次，其中 **{repeated}** 个主题重复出现；"
              "**{written}** 个主题已经写在 CLAUDE.md / 记忆里、但仍然发生。"},
    "mine.section.topics": {"en": "## Topics", "zh": "## 主题"},
    "mine.no_topics": {
        "en": "(No corrections detected. That may be true, or the patterns may not "
              "cover how you phrase them — see the limitations below.)",
        "zh": "（没有检出纠正。这可能是真的，也可能是模式没覆盖到你的说法——"
              "见下面的 limitations。）"},
    "mine.topic.stats": {
        "en": "- count **{count}** · sessions **{sessions}** · repeated {repeated} · "
              "top confidence {conf} · t0 {t0}",
        "zh": "- 次数 **{count}** · 会话 **{sessions}** · 重复 {repeated} · "
              "最高置信度 {conf} · t0 {t0}"},
    "mine.yes": {"en": "yes", "zh": "是"},
    "mine.no": {"en": "no", "zh": "否"},
    "mine.topic.written_in": {
        "en": "- ⚠️ **written but violated**: already present in {names} "
              "(matched tokens: {tokens})",
        "zh": "- ⚠️ **written but violated**：已出现在 {names}（命中 token: {tokens}）"},
    "mine.topic.quotes": {"en": "- what you said, verbatim:", "zh": "- 原话："},
    "mine.topic.quote_meta": {
        "en": "confidence {conf}, signals {signals}, patterns {patterns}",
        "zh": "置信度 {conf}, 信号 {signals}, 模式 {patterns}"},
    "mine.topic.revert": {"en": "↩ revert", "zh": "↩ 撤销动作"},
    "mine.topic.violating": {
        "en": "- the action you objected to (violating action):",
        "zh": "- 被纠正前的动作（violating action）："},
    "mine.topic.no_violating": {
        "en": "- the action you objected to: (none — these corrections do not "
              "follow any tool call)",
        "zh": "- 被纠正前的动作：（无——这些纠正不紧跟任何工具调用）"},
    "mine.topic.suggested": {"en": "  suggested check:", "zh": "  建议检查:"},
    "mine.topic.uncompilable": {
        "en": "  suggested check: (cannot be compiled automatically) {note}",
        "zh": "  建议检查: （无法自动编译）{note}"},
    "mine.topic.auto_none": {"en": "  - auto-compile: not compiled — {note}",
                             "zh": "  - 自动编译: 未编译 — {note}"},
    "mine.topic.auto": {
        "en": "  - auto-compile: `{id}` {tool} {action} [{match}] template {template} "
              "→ birth gate **{verdict}** {mark}",
        "zh": "  - 自动编译: `{id}` {tool} {action} [{match}] 模板 {template} "
              "→ 出生门 **{verdict}** {mark}"},
    "mine.topic.auto_matchers": {"en": "    matchers: {matchers}",
                                 "zh": "    匹配器: {matchers}"},
    "mine.topic.auto_evidence": {"en": "    evidence: {counts}",
                                 "zh": "    证据  : {counts}"},
    "mine.section.reverts": {
        "en": "## Reverts (an agent edit undone by git revert/checkout/restore)",
        "zh": "## 撤销/回滚（agent 编辑被 git revert/checkout/restore 掉）"},
    "mine.revert.undid": {"en": "↩ undid the agent's write to ",
                          "zh": "↩ 撤销了 agent 写过的 "},
    "mine.revert.unmatched": {"en": "(no agent write matched)",
                              "zh": "（未匹配到 agent 的写入）"},
    "mine.section.limitations": {"en": "## Limitations", "zh": "## Limitations"},

    # ---- report: section headings ----------------------------------------
    "report.title": {"en": "# precedent report", "zh": "# precedent report"},
    "report.meta": {
        "en": "generated {ts} · state `{state}` · claude home `{home}`",
        "zh": "生成于 {ts} · state `{state}` · claude home `{home}`"},
    "report.h0": {"en": "## 0. Alarms (STARVATION)", "zh": "## 0. 告警（STARVATION）"},
    "report.h1": {"en": "## 1. First screen", "zh": "## 1. 首屏"},
    "report.h2": {
        "en": "## 2. The funnel: proposed → accepted → activated → attributed",
        "zh": "## 2. 漏斗：proposed → accepted → activated → attributed"},
    "report.h3": {"en": "## 3. The docket", "zh": "## 3. 待办（docket）"},
    "report.h4": {"en": "## 4. Ownership of the governed trees",
                  "zh": "## 4. 治理树所有权"},
    "report.h5": {"en": "## 5. Load receipts (live wins)",
                  "zh": "## 5. 加载收据（live 优先）"},
    "report.h6": {"en": "## 6. Correction mining", "zh": "## 6. 纠正挖掘"},
    "report.h7": {"en": "## 7. Confirmed precedents (enforced)",
                  "zh": "## 7. 已确认的先例（enforced）"},
    "report.h8": {"en": "## 8. Spend", "zh": "## 8. 支出表"},
    "report.h9": {"en": "## 9. The last {days} days", "zh": "## 9. 最近 {days} 天"},
    "report.h10": {"en": "## 10. Ledger tail (hash chain)",
                   "zh": "## 10. 账本尾部（哈希链）"},
    "report.h11": {"en": "## 11. Install status", "zh": "## 11. 安装状态"},
    "report.h12": {"en": "## 12. Honest limitations", "zh": "## 12. 诚实的限制"},
    "report.alarms.none": {
        "en": "(No alarms: nothing overdue in the docket, no hook errors, no install "
              "drift. Passing is silent — this line is the whole output.)",
        "zh": "（无告警：没有超期待办、没有钩子异常、没有安装漂移。"
              "通过时静默——这一行就是全部输出。）"},
    "report.alarms.count": {
        "en": "**{n} alarm(s). Starvation is never silent.**",
        "zh": "**{n} 条告警。饿死绝不静默。**"},
    "report.alarms.head": {"en": "| level | code | what | next |",
                           "zh": "| 级别 | 代码 | 说明 | 下一步 |"},
    "report.funnel.head": {"en": "| stage | count | meaning |",
                           "zh": "| 阶段 | 数量 | 含义 |"},
    "report.funnel.proposed": {
        "en": "rule candidates {rules} + governed-tree write candidates {writes} + "
              "proposals (examiner / nightly improver) {proposals}",
        "zh": "规则候选 {rules} + 治理树写入候选 {writes} + "
              "提案（examiner / 夜间改进器）{proposals}"},
    "report.funnel.accepted": {
        "en": "precedents that passed the gate and you confirmed {rules} + writes you "
              "accepted {writes} + proposals you confirmed {proposals} (confirming a "
              "proposal records a decision; precedent never writes the file for you)",
        "zh": "过了门并被你确认的先例 {rules} + 你接受的写入 {writes} + "
              "你确认的提案 {proposals}（确认提案只记录决定，precedent 不会替你写文件）"},
    "report.funnel.activated": {
        "en": "actually inside the PreToolUse matcher in settings.json right now",
        "zh": "现在真的在 settings.json 的 PreToolUse matcher 覆盖范围里"},
    "report.funnel.attributed": {
        "en": "rules the hook log shows really firing",
        "zh": "钩子日志里真的触发过的规则"},
    "report.funnel.calls": {
        "en": "{calls} hook calls, {fires} of them fires ({pct}). Ownership guard: {guard}.",
        "zh": "钩子调用 {calls} 次，其中触发 {fires} 次（{pct}）。所有权守卫：{guard}。"},
    "report.on": {"en": "on", "zh": "开"},
    "report.off": {"en": "off", "zh": "关"},
    "report.funnel.rules_head": {"en": "| rule | fires |", "zh": "| 规则 | 触发次数 |"},
    "report.funnel.gap": {
        "en": "⚠️ {n} accepted but not activated — this is the industry's "
              "\"written but never loaded\", the same cell as 41 of 60 artifacts "
              "never cited on this machine.",
        "zh": "⚠️ {n} 条已接受但未激活——这就是全行业的 “written but never loaded”，"
              "本机 60 个工件里 41 个从未被引用的同一格。"},
    "report.docket.summary": {
        "en": "- {pending} pending (rules {rules} / writes {writes}), {snoozed} snoozed",
        "zh": "- 待办 {pending} 条（规则 {rules} / 写入 {writes}），已推迟 {snoozed} 条"},
    "report.docket.head": {"en": "| id | kind | age | evidence |",
                           "zh": "| id | 类型 | 年龄 | 证据 |"},
    "report.docket.days": {"en": "{n} d", "zh": "{n} 天"},
    "report.docket.more": {
        "en": "`precedent docket --batch` for all of them; "
              "`precedent docket confirm|reject|snooze <id>`.",
        "zh": "`precedent docket --batch` 看全部；"
              "`precedent docket confirm|reject|snooze <id>`。"},
    "report.daily.head": {
        "en": "| day | new | confirmed | rejected | snoozed | hook calls | fires | "
              "errors | sessions | $ |",
        "zh": "| 日期 | 新候选 | 确认 | 拒绝 | 推迟 | 钩子调用 | 触发 | 异常 | 会话 | $ |"},
    "report.daily.none": {"en": "(No activity recorded in this window.)",
                          "zh": "（这段时间没有任何活动记录。）"},
    "report.empty": {"en": "(empty)", "zh": "（空）"},

    # ---- audit --share: the card -----------------------------------------
    "audit.title": {"en": "precedent audit — share card",
                    "zh": "precedent audit — 可分享卡片"},
    "audit.label.corpus": {"en": "corpus", "zh": "语料"},
    "audit.label.alarms": {"en": "alarms", "zh": "告警"},
    "audit.label.reproduce": {"en": "reproduce", "zh": "复现"},
    "audit.label.verified": {"en": "verified", "zh": "校验"},
    "audit.corpus": {
        "en": "{sessions} {session_w} · {projects} {project_w} · "
              "{artifacts} {learned_w}",
        "zh": "{sessions} {session_w} · {projects} {project_w} · "
              "{artifacts} {learned_w}"},
    "audit.label.never_cited": {"en": "never cited", "zh": "从未被引用"},
    "audit.label.unattended": {"en": "unattended writes", "zh": "无人值守写入"},
    "audit.label.truncated": {"en": "truncated on load", "zh": "加载时被截断"},
    "audit.label.stale": {"en": "stale index entries", "zh": "失效索引条目"},
    "audit.label.duplicates": {"en": "near-duplicate artifacts", "zh": "近重复工件"},
    "audit.label.enforced": {"en": "enforcement", "zh": "强制执行"},
    "audit.value.never_cited": {
        "en": "{n} of {of} citable artifacts ({pct}) have never been cited or invoked",
        "zh": "{of} 个可引用工件里有 {n} 个（{pct}）从未被引用或调用"},
    "audit.value.unattended": {
        "en": "{n} in {days} days — {bypass} bypassed the memory tool, "
              "{sub} came from a subagent",
        "zh": "{days} 天内 {n} 次——其中 {bypass} 次绕过记忆工具，{sub} 次来自子代理"},
    "audit.value.truncated": {
        "en": "{n} {file_w} reached the model only in part, in {sessions} "
              "{session_w}{where}",
        "zh": "{n} {file_w}只有一部分到达模型，涉及 {sessions} {session_w}{where}"},
    "audit.value.stale": {
        "en": "{n} {entry_w} with no file on disk",
        "zh": "{n} {entry_w}指向磁盘上不存在的文件"},
    "audit.value.duplicates": {
        "en": "{n} {artifact_w} in {pairs} near-duplicate {pair_w}",
        "zh": "{n} {artifact_w}构成 {pairs} {pair_w}近重复"},
    "audit.value.enforced": {
        "en": "{active} {precedent_w} active, {activated} reaching the hook, "
              "{fired} ever fired; {pending} in the docket",
        "zh": "{active} {precedent_w}生效、{activated} 条接到钩子、"
              "{fired} 条触发过；docket 里还有 {pending} 条"},
    "audit.in_sessions": {"en": " ({labels})", "zh": "（{labels}）"},
    "audit.byproject": {"en": "by project (names replaced):",
                        "zh": "按项目（名称已替换）："},
    "audit.byproject.row": {
        "en": "  {label}  {artifacts} {artifact_w} · {never} never cited · "
              "{sessions} {session_w}",
        "zh": "  {label}  {artifacts} {artifact_w} · {never} 个从未被引用 · "
              "{sessions} {session_w}"},
    "audit.alarms": {"en": "{codes}", "zh": "{codes}"},
    "audit.alarms.none": {"en": "none", "zh": "无"},
    "audit.footer": {
        "en": "counts only — no quotes, no paths, no project names, no session ids.",
        "zh": "只有计数——没有引文、没有路径、没有项目名、没有会话 id。"},
    "audit.footer.local": {
        "en": "`--share` prints everything above this line and drops everything "
              "below it: counts only, no quotes, no paths, no names, no ids.",
        "zh": "`--share` 只打印这条线以上的内容，线以下全部丢掉："
              "只有计数，没有引文、路径、名字或 id。"},
    "audit.reproduce": {
        "en": "precedent audit --share   ·  precedent {version}",
        "zh": "precedent audit --share   ·  precedent {version}"},
    "audit.verified": {
        "en": "scrub + vocabulary + this machine's names: "
              "{n} checks, {langs} languages, 0 leaks.",
        "zh": "scrub + 词表 + 本机名称：{n} 项检查，{langs} 种语言，0 处泄漏。"},
    "audit.local.head": {"en": "local (this block is dropped by --share):",
                         "zh": "本机信息（--share 会丢掉这一段）："},
    "audit.local.home": {"en": "  Claude home : {path}", "zh": "  Claude home : {path}"},
    "audit.local.state": {"en": "  state dir   : {path}", "zh": "  状态目录    : {path}"},
    "audit.local.project": {"en": "  {label} = {slug}", "zh": "  {label} = {slug}"},
    "audit.refused": {
        "en": "precedent: REFUSING to print the share card — {n} thing(s) that "
              "precedent.scrub would mask survived into it:",
        "zh": "precedent: 拒绝打印可分享卡片——有 {n} 处 precedent.scrub 会屏蔽的内容"
              "留在了卡片里："},
    "audit.refused.hint": {
        "en": "This is a bug in the card renderer, not in your data. Please open an "
              "issue with the finding kinds above (they carry no content).",
        "zh": "这是卡片渲染器的 bug，不是你的数据的问题。请带上上面的类别（不含内容）提 issue。"},
}


def t(key: str, lang: str | None = None, **kw) -> str:
    """The string for ``key``, formatted with ``kw``.

    A missing id or a missing language raises: a silently-English string in a
    Chinese report is the failure mode this table exists to prevent, and the
    table test walks every id anyway.
    """
    row = STRINGS[key]
    text = row[lang or _CURRENT]
    return text.format(**kw) if kw else text
