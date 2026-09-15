# Precedent

**Every correction is a test.**

Precedent is a self-evolving [Claude Code](https://claude.com/claude-code)
harness whose core is a *verified* self-modification loop. It mines the
corrections you already make ("no, use `uv`, not `pip`"), compiles them into
deterministic rules, **gates them against your own transcript in time order**,
and enforces the survivors as `settings.json` hooks — so the next session is
blocked before it repeats the mistake.

The point is not that an agent can rewrite itself. Everything rewrites itself
now. The point is the two steps everybody skips: **deciding whether a
self-modification should be kept, and noticing when the loop has quietly
stopped working.**

[Plan page (design + evidence)](https://claude.ai/artifact/STVtU54PzQ5pfPqr5BMoaT)
· [中文说明](#中文) · Apache-2.0

---

## The five-step loop

A complete self-evolving loop has five steps. The field has one of them.

| step | what it means | the state of the art (Sept 2026) | what Precedent does |
|---|---|---|---|
| ① **propose** | the agent edits its own memory / skills / rules | everywhere: auto-memory, skill-creator, background review, sleep-time optimisers | three proposers: the **correction miner**, Claude Code's own writes (brought under governance), and a **nightly improver** that clusters failure signatures |
| ② **accept** | should this change be kept? | **nobody.** Acceptance is an LLM saying yes; greedy "score went up" acceptance is 30–45 % false commits (PACE 2606.08106; reproduced here at 44.6 %) | two gates: the **temporal birth gate** for rules (free, offline, deterministic) and the **examiner** for everything else — paired e-process, harm martingale, spend schedule, task floor |
| ③ **enforce** | does the change actually reach the model and bind it? | "written but never loaded" — 41 of 60 artifacts on the author's machine had never once been referenced | a **six-hook suite** in `settings.json`: `PreToolUse` deny/ask plus path-level ownership, `PostToolUse` change capture, live load receipts, funnel counters |
| ④ **audit** | is learning happening, and has anything got worse? | **nobody.** Production loops fail silently for weeks (hermes#95976: 41 background forks, 0 updates) | a **docket** that never piles up silently, four starvation alarms, a proposed→accepted→activated→attributed funnel, a hash-chained ledger, content-addressed snapshots and `undo` |
| ⑤ **re-evolve** | feed the audit back into the proposer | — | rejected drafts return as negative examples; the precedent set grows with every correction |

The difference is not *whether* it changes itself. It is **whether the change
was right, whether you can prove it, and whether you can take it back.**

### Why corrections are the right training signal

A user correction is free, exogenous, human-labelled data about a policy
violation — and the violating action is already in the transcript. So a rule
compiled from a correction arrives with **planted ground truth**: it must fire
on the action you objected to, and stay quiet afterwards. That is a test you
can run without a model, without a benchmark, and without asking the agent to
grade itself. And it **compounds**: you confirm once, and every session, every
candidate edit and every model upgrade is checked for free from then on.

---

## Install

```bash
pipx install precedent-cli          # or: uv tool install precedent-cli
precedent --version
```

> **Not on PyPI yet.** v0.1.0 is the first tagged release and the packages have
> not been published, so the two commands above are the *intended* install path
> and do not work today. Install from a clone until they do:
> `uv tool install ./packages/precedent` (after `uv build`-ing `receipts` and
> `acceptor`, or from the dev venvs below).

From this repository (what the three packages actually are):

```bash
git clone <this repo> && cd precedent
./scripts/dev.sh                    # three uv venvs + all three test suites
```

Python 3.11+ (3.12 recommended). **Standard library only.** The one optional
extra is `precedent-cli[zh]`, which installs
[jieba](https://github.com/fxsjy/jieba) so Chinese topics are grouped by words
instead of character bigrams; without it the tokeniser falls back to bigrams
and every report says which one ran. Nothing is silently degraded.

---

## Five-minute quickstart

The first four minutes cost **zero model calls** and write nothing to your
Claude home.

> **[DEMO.md](DEMO.md)** is this quickstart actually run, on a real machine, with
> every command and its verbatim output — including the candidate that **failed**
> the temporal gate and why, the exact `settings.json` diff a `--dry-run` prints,
> a deny/allow proof against a throwaway Claude home, and a before/after
> fingerprint of `~/.claude` with every changed file attributed. It ends with the
> three-command runbook for turning enforcement on yourself.

### 1. `precedent init` — what is actually in there (10 s, read-only)

```bash
precedent init
```

```
precedent 0.1.0 — 首屏（只读，零模型调用）
 1. Claude home     : /Users/you/.claude
 2. 状态目录        : /Users/you/.precedent
 3. 会话            : 扫描 8 / 发现 8 个
 4. 学习工件        : 60 个（skill 55 / memory 3 / CLAUDE.md 2）
 5. 从未被引用      : 41 个（可引用工件的 71%）
 6. 曾被截断        : 1 个工件在 ≥1 个会话里只加载了一部分
 7. 失效索引/缺文件 : 2 条
 8. 近重复工件      : 8 个
 9. 无人值守写入    : 最近 7 天 34 次（子代理 3 次，Bash 绕过记忆工具 31 次）
10. 已强制执行的先例: 0 条  → 下一步：precedent mine
```

Every line is a count you can re-derive by hand with `grep` — each claim in the
full report carries a `<transcript file>:<line>` locator.

### 2. `precedent mine` — your corrections become candidates

```bash
precedent mine --last 20 --md corrections.md
```

Human turns are split into sentences, skill/slash-command expansions are
skipped (and counted), questions are filtered out, and five layered signals
produce a confidence score — including the correction you made with **no words
at all**, by reverting the agent's edit. Topics that recur across sessions are
grouped, and each is run straight through the compiler and the gate:

```
8 sessions · 160 human turns (119 expansions skipped)
18 corrections (11.2 %) → 16 topics, 1 repeated; 2 compiled, 0 PASS
```

### 3. `precedent compile` — a rule, and the temporal birth gate

```bash
precedent compile t-1a2b3c4d
```

A topic becomes a rule in a small deterministic DSL (a tool matcher, input
matchers, `deny`/`ask`/`log`, a scope, and the quote from your correction as
the message). Then it is replayed against your transcripts **in time order**:

```
  t0（最早一次纠正）: 2026-09-07T08:26:39.020Z
  (a) 命中违规动作   : 1/3   → HIT
  (b) t0 之后可判定   : 7474 次动作
      其中触发         : 17 次 = 真阳 0 + 误触 17
      误触率           : 17/7474 = 0.2%  (阈值 2%)
      误触分布（仅报告）: 出现在 1/6 个会话里
  (c) t0 之前         : 33/945（只报告，不计入）
  判定               : **PASS**
```

This is the one idea in the gate that took a rewrite to get right: a correction
expresses a policy **from the moment it was made**, so behaviour from before
that moment is *reported and never counted*. The earlier gate threw out a
perfectly good rule because the user had legitimately used a headless browser
in the weeks before they asked for it to stop. See
[the temporal birth gate](#the-temporal-birth-gate).

### 4. `precedent docket confirm` — one human decision

```bash
precedent docket                      # the queue, with evidence and diffs
precedent docket confirm p-9f8e7d6c   # …or reject <id> --reason … / snooze <id>
```

Confirming writes the rule into `~/.precedent/precedents.json` with
`status: active` — the only file the hook trusts — and a hash-chained
certificate into the ledger. A candidate that did not `PASS` needs `--force`,
and the forced verdict is recorded on the rule forever.

### 5. `precedent hooks install --apply` — enforcement (**you run this, by hand**)

```bash
precedent hooks install claude-code                 # dry run: prints the exact merge
precedent hooks install claude-code --apply --i-know
```

`--dry-run` is the default and prints the settings merge, the hooks block, an
inventory of every script with its sha256, and the decision contract.
`--apply` writes the six hook scripts, **backs up `settings.json` to
`~/.precedent/backups/settings-<timestamp>.json`**, and merges our entries into
it — non-destructively (your other hooks survive), idempotently (a second run
changes nothing and writes no second backup) and atomically (`os.replace`,
preserving the file's mode). `--i-know` is required for your real `~/.claude`.

### 6. A new session gets blocked

Open a fresh Claude Code session and ask for the thing you told it not to do:

```
> install requests

⏺ Bash(pip install requests)
  ⎿  Blocked by hook:
     [precedent p-1a2b3c4d] 用 uv，不要用 pip
     — from your correction on 2026-09-12: 不要用 pip，用 uv
```

That is the loop closed: a correction you made on Tuesday, gated against the
record on Wednesday, enforcing itself on Thursday, with the original quote
attached so the agent knows *why*.

```bash
precedent report --md digest.md    # alarms, funnel, docket, receipts, spend
precedent hooks uninstall claude-code --apply --i-know    # removes exactly ours
```

---

## Safety guarantees

These are not aspirations. Each one is an invariant with tests behind it, and
the test suite runs entirely against synthetic Claude homes under `tmp_path`
with a `real_home_canary` fixture that fails the test if the real `~/.claude`
or `~/.precedent` was touched.

**1. One command may write a `settings.json`, and only after a backup.**
`precedent hooks install --apply` and `hooks uninstall --apply` are the only
writers. Every other command is read-only on the Claude home, enforced by
`state.guard_not_under_claude_home()`, which raises rather than writes. A
`settings.json` precedent cannot parse is **never** written — merging into
something that is not a JSON object would mean *replacing* your file rather
than adding to it, so `--apply` refuses, names the reason and exits 2. All of
precedent's own state lives in `~/.precedent/` (or `--state-dir`).

**2. A hook can never break Claude Code.** All six scripts are three lines of
logic over one shared library, and the tests assert **< 300 ms end-to-end for
every one of them**. They read stdin JSON defensively; on **any** internal
exception they exit 0 with no stdout and append the error to
`~/.precedent/hooklog.jsonl`. They `deny` or `ask` **only** when a confirmed
precedent (`status: active`) matches. Everything else is allow + log. A
missing or corrupt `precedents.json`, an uncompilable regex, a torn stdin, a
missing library — all fail **open**, because a gate that bricks the agent when
its own state file is broken is worse than no gate. Starvation is reported by
`precedent report`, never by wedging a tool call.

**3. Nothing is deleted or overwritten without a content-addressed snapshot
first, and `undo` round-trips.** Every learned-state file body goes to
`blobs/<sha256>`; `undo --session <id>` plans restore / recreate / delete /
blocked per file, flags any file a *later* session also wrote, and is a dry run
by default.

**4. Every `claude -p` call is capped, sandboxed and recorded.** Each carries
`--max-budget-usd` (default $0.30–0.50 per call, and a budget outside
`(0, 1.00]` is refused before the subprocess starts), `--no-session-persistence`,
an explicit `--model` (sonnet for drafts, haiku for judges), and
**never** `--dangerously-skip-permissions`. Drafting calls additionally carry
`--safe-mode --tools "" --disable-slash-commands`: a draftsman needs no tools,
and it must not be able to read the CLAUDE.md, skills and hooks it is drafting
*about*. Spend goes to `spend.jsonl`; the budget check does not trust the
binary's own cost report (each call is *reserved* at its cap first, so a
`claude` that reports `$0.00` still cannot buy more than
`floor(budget / per-call cap)` calls). LLM output is untrusted:
schema-validate, then deterministic gates. **Mechanical rejection overrides
LLM approval, never the reverse** — the PROCTOR rule.

**5. Nothing is ever applied automatically.** An `ACCEPT` is a certificate and
a docket entry. `improve` and `examine` write proposals, not files. The bytes
are yours to write, by hand, after you have read them. An acceptance layer that
also writes is a proposer with a rubber stamp.

---

## The temporal birth gate

For a candidate rule `R` compiled from topic `T`, whose earliest correction is
at time `t0`:

- **(a) HIT** — `R` must fire on the violating action recorded immediately
  before at least one correction in `T` (the `tool_use` preceding the
  correcting human turn).
- **(b) QUIET-AFTER** — among the actions **after `t0`** where `R` was
  *eligible* (its tool was used, in scope), a fire followed within the same
  session by another correction from `T` in the next 3 human turns is a **true
  positive**. Every other fire is a **false fire**, tolerated only while
  `false_fires / eligible_after ≤ ε` (default 2 %, **always shown as counts**).
- **(c)** pre-`t0` behaviour is **reported, never counted**.

Verdict: `PASS` / `FAIL` / `INSUFFICIENT` (fewer than 3 eligible actions after
`t0`). The whole evidence block is stored on the candidate and in the ledger.

Read the absolute numbers, not the percentage: ε is a per-**action** rate, so a
large corpus is a permissive denominator. 17 tolerated fires is 17 times the
agent would have been interrupted, and the report prints it in both units.

---

## Architecture map

```
precedent/
├── packages/precedent/   the CLI and the loop          (precedent-cli 0.1.0)
├── packages/receipts/    L0: did it actually load?     (receipts 0.1.0)
├── packages/acceptor/    L3: the acceptance statistics (acceptor 0.2.0)
├── research/             the 4-day evidence base — see research/README.md
├── scripts/dev.sh        three uv venvs + all three suites
└── 方案.md               the full design document (Chinese)
```

Layers, following [方案.md](方案.md) §3.3:

| layer | what it is | where |
|---|---|---|
| **L0** | load receipts, change feed, activation funnel — $0, no learning loop required | `packages/receipts`, and the live hooks in `precedent/live.py` |
| **L1** | snapshots, undo, path-level ownership, deterministic lint, the spend ledger | `precedent/snapshot.py`, `ownership.py`, `improve.py`, `audit.py` |
| **L2** | **the Precedent engine**: miner → compiler → temporal birth gate → docket → enforcer → examiner | `precedent/mine.py`, `rules.py`, `compile.py`, `docket.py`, `hooks.py`, `examine.py` |
| **L3** | the acceptance gate: paired e-process, CTHS spend schedule, harm martingale, protected-task floor, hash-chained certificates | `packages/acceptor`, wired up in `precedent/accept.py` |
| **L4** | sealed evaluation and score receipts | partial: the proposer/evaluator separation (`--safe-mode --tools ""`), case-name binding, hash-chained receipts. Real isolation (mount namespace / container) is **not** implemented |
| **L5** | the research track | `research/`, and the three claims C1/C2/C3 in 方案.md §3.3 |

Inside `packages/precedent/src/precedent/`: `mine.py` (corrections),
`rules.py` (the DSL, the regex linter, the bounded matcher), `compile.py` (the
temporal gate), `_hooklib.py` (**the hook runtime, copied byte-for-byte to
`<state>/hooks/_plib.py` — the code Claude Code runs is the code the tests
import**), `hooks.py` (rendering, backup, merge, drift), `examine.py` (the
examiner), `accept.py` (the gate wiring), `improve.py` (the nightly improver),
`loop.py` (one cycle), `audit.py` / `report.py` (the digest). Each package's
own README is the detailed reference.

---

## Honest limitations

The long version is in each package's README. The short version:

- **Corrections are found by surface pattern, never by a model.** A correction
  phrased without any listed word is missed; a turn that merely quotes one can
  be a false positive. The output is a confidence score, not a boolean.
- **The gate is retrospective, not predictive.** It proves the rule fires where
  it should and is quiet where it should *on the record you already have*. It
  does not prove generalisation, and it cannot distinguish "quiet because the
  rule is right" from "quiet because the rule is so narrow it only matches the
  one action that provoked it". **Read the rule, not just the verdict.**
- **ε is a per-action rate**, so a big corpus makes it permissive. That is why
  the absolute false-fire count and the number of sessions it spans are always
  printed.
- **Hooks are fail-open by design.** An outage in enforcement is *quiet*; you
  find it in `precedent report`, not by the agent breaking. That is a
  deliberate trade, and it is the one an adversary would attack.
- **Enforcement reaches paths through a tool matcher.** Ownership is enforced
  by path once the hook runs, but the hook runs for
  `Bash|Edit|MultiEdit|NotebookEdit|Write` plus whatever active rules name. A
  third-party MCP tool that writes files is invisible to it. Bash write
  detection is a bounded heuristic (8 KB of command, 16 governed paths per
  call — the cap is real, and it is logged, not silent).
- **Statistical power is small and the tool says so.** With binary outcomes,
  ~40 paired tasks can only reliably detect a **+30 pp** improvement; +10 pp
  needs 160+. `NSF` ("the budget ran out before the threshold was reachable")
  is the expected and *correct* answer for most real edits, and it is displayed
  as a first-class result rather than hidden.
- **`claude plugin eval` is early access**, so on most machines the examiner
  runs its own paired `claude -p` fallback, which cannot replay a transcript.
  Both arms are handicapped identically, so the pairing is sound, but the arms
  start colder. Every fallback result carries the note.
- **L4 isolation is structural, not unforgeable.** On macOS 方案 §L4 already
  labels the boundary weaker. The honest claim for v1 is "structural separation
  plus offline-verifiable receipts", not "tamper-proof".
- **The dollar cap bounds *calls*, not dollars.** If the `claude` binary
  ignores `--max-budget-usd` *and* under-reports its cost, the call count is
  still bounded but the money is not. There is no way to bound actual spend
  from outside a subprocess that lies about both.
- **Only deterministic checks exist.** Executable checks (sandboxed tests) and
  judge-based checks are specified in 方案.md and not implemented.
- **One machine, one user.** Every number in these READMEs comes from the
  author's own `~/.claude` (8 sessions). Nothing here has been validated across
  users; that is C1/C2/C3 in the research track, not a claim being made today.

---

## Key citations

The full corpus — 521 indexed items, 96 deep reads, four gap analyses — is in
[`research/`](research/README.md), and every claim there carries a URL.

**The acceptance problem.** PACE (2606.08106) — paired anytime-valid commit
gate; greedy acceptance false-commits 30–42 %. SEA (2607.00871) — anytime-valid
certificates, the CTHS spend schedule this repo uses (Z = 3.3877). HarnessDev
(2609.01437) — self-selected versions ≈ a coin flip on held-out; 0 of 64
evolution edits touched a verifier. PROCTOR — **mechanical rejection overrides
LLM approval, never the reverse.**

**Why judges cannot be the authority.** "More Convincing, Not More Correct"
(judge pass rate 0.72→0.94 while accuracy stays 0.20). Blind Curator (retirement
collapses above a judge false-pass rate of ~0.45). RSI-Exam — every frontier
model drops 12–25 % from the visible to the sealed split.

**Why self-written artifacts need a gate.** SkillsBench — LLM-written skills:
**0** benefit; human-written: +16.2. CTA (2605.11946) — +0.3 pp. Demystifying
Agent Skills (2608.14036) — retrieval precision 29.6 % → 3.3 % at 100 skills.
SkillMisevo / SafeEvolve (2608.12851) — 21/21 evolution configs produced unsafe
artifacts. MLAS (2606.23075) — 40/40 malicious payloads persisted through
automated background review; a human queue blocked 40/40.

**Why replay is a regression detector, not a fitness function.** The Replay Gap
(2608.08239) — after a model swap, only 3–8 % of replayed states stay valid.
Causal Agent Replay (2606.08275).

**Why enforcement matters more than authorship.** Lin et al. (2605.30621) —
"harness updating ≠ harness benefit"; a 9B model's updates are as good as
Opus's, and the benefit depends on whether the consumer loads and follows them
(load rate 25 % vs 96 %). EvoHarnessBench (2609.04280) — harness-induced
forgetting, BWT −5/−4/−35 %.

**The verification hierarchy.** RSI survey (2607.07663, 1,250 papers): formal
verification > execution feedback > learned judges > self-assessment; "the
difference between a loop that improves and a loop that circles is one rung of
external verification". MetaRSI (2609.06396) — 69 % of RSI systems close their
loop only on machine-checkable targets.

**What users actually ask for.** hermes-agent#12238 (back up and version
`~/.hermes` — the most-reacted loop issue), #95976 (the learning loop silently
broken for weeks: 41 background forks, 0 updates), #105770 (write approval on
for 8 weeks, 241 staged writes, nobody told), #99729 ("the approval gate is a
convention" — a Bash heredoc walks past it), claude-code#82056 (48 comments:
"there is no way to tell whether memory loaded whole, truncated, or not at
all"), microsoft/SkillOpt#155 (replay through the real CLI, gate on the diff —
endorsed twice, with three hard constraints).

---

## Repository layout, development, contributing

```bash
./scripts/dev.sh          # create three uv venvs and run all three suites
./scripts/dev.sh --venvs  # only (re)create the venvs
./scripts/dev.sh --tests  # only run the suites
```

Each package is independent and uv-managed on Python 3.12; `precedent`'s venv
has `receipts` and `acceptor` installed editable. Tests:
`cd packages/<pkg> && .venv/bin/python -m pytest -q`. CI runs all three suites
on Python 3.11 and 3.12.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the rules that matter (stdlib only;
never touch a real `~/.claude` in a test; every safety invariant needs a test
that would fail without it), [SECURITY.md](SECURITY.md) for the threat model,
the fail-open semantics and how to uninstall, and [DEMO.md](DEMO.md) for a full
run on a real machine with the audit that proves `~/.claude` was not touched.

Licensed under the Apache License 2.0 — see [LICENSE](LICENSE) and
[NOTICE](NOTICE).

---
---

<a name="中文"></a>

# Precedent（先例）— 中文说明

**每一次纠正都是一个测试。**

Precedent 是一个**自进化的 Claude Code harness**，它的核心是一个**可验证的自我修改
闭环**。它从你已经做过的纠正里挖掘规则（"不要用 pip，用 uv"），编译成确定性检查，
**按时间顺序在你自己的历史记录上做门控**，然后把通过的规则写成 `settings.json`
钩子强制执行——于是下一个会话在犯同样的错之前就被拦住。

重点不是"agent 会不会改自己"。现在什么都会改自己。重点是所有人都跳过的那两步：
**这次自我修改到底该不该保留，以及当循环悄悄失效时有没有人知道。**

[方案页面（设计与证据）](https://claude.ai/artifact/STVtU54PzQ5pfPqr5BMoaT)
· [完整方案：方案.md](方案.md) · Apache-2.0

## 五步闭环

| 步骤 | 含义 | 市面现状（2026-09） | Precedent |
|---|---|---|---|
| ① 提议 | agent 改自己的记忆/技能/规则 | 到处都是：自动记忆、skill-creator、后台审查、夜间优化器 | 三个提议者：**纠正挖掘器**、Claude Code 自带的写入（纳入治理）、夜间**改进器** |
| ② **接受** | 这次改动该不该留 | **没人做**。接受 = LLM 自己说了算；贪心"分数上升就保留"= 30–45% 误接受（PACE 2606.08106；本仓库复现 44.6%） | 两道门：规则走**时序出生门**（免费、离线、确定性），其余走**考官**——配对 e-process、伤害鞅、支出调度、逐任务底线 |
| ③ 执行 | 改动真的到达模型并约束它 | "写了但从未加载"——作者机器上 60 个工件有 41 个从未被引用 | `settings.json` 里的**六钩子套件**：`PreToolUse` deny/ask + 按路径的所有权、`PostToolUse` 变更捕获、实时加载收据、漏斗计数 |
| ④ **审计** | 学习真的发生了吗、有没有变坏 | **没人做**。生产环境里循环静默失效数周（hermes#95976：41 次后台 fork、0 次更新） | **绝不静默堆积**的 docket、四个饿死告警、提议→接受→激活→归因漏斗、哈希链账本、内容寻址快照与 `undo` |
| ⑤ 再进化 | 用审计结果调整提议者 | 无 | 被拒草稿作为负例回灌；先例集随每一次纠正持续生长 |

**为什么纠正是对的信号**：用户的纠正是免费的、外生的、由人标注的策略违规数据，而且
违规动作已经录在轨迹里。所以由纠正编译出的规则天生带着**种植的真值**——它必须在你
当初反对的那个动作上触发，并在那之后保持安静。这个测试不需要模型、不需要基准、
不需要让 agent 给自己打分。而且它**复利**：你按一次确认，之后每个会话、每个候选
修改、每次模型升级都免费受检。

## 安装

```bash
pipx install precedent-cli          # 或：uv tool install precedent-cli
```

> **尚未发布到 PyPI。** v0.1.0 是第一个版本，包还没有上传，所以上面两条是**计划中的**
> 安装方式，今天还不可用。在发布之前请从仓库克隆后本地安装（见下面的 `scripts/dev.sh`）。

Python 3.11+（建议 3.12），**纯标准库**。唯一的可选依赖是 `precedent-cli[zh]`
（jieba），装了它中文主题按**词**分组，不装就退化成字符二元组——并且每份报告都会
写明当前用的是哪一种，绝不静默降级。

## 五分钟上手（前四分钟零模型调用、零写入）

> **[DEMO.md](DEMO.md)** 是这一节在真机上的实录：每条命令 + 逐字输出，包括**没
> 通过**时序出生门的候选规则和原因、`--dry-run` 打印的 `settings.json` 精确
> diff、在一次性假 home 上的 deny/allow 端到端验证，以及 `~/.claude` 运行前后
> 的指纹对比（每个变动文件都有归因）。最后是你自己开启强制执行的三条命令。

```bash
precedent init                        # 10 秒首屏：会话/工件/从未加载/截断/带外写入
precedent mine --last 20              # 纠正挖掘 → 主题 → 自动编译 + 出生门
precedent compile t-1a2b3c4d          # 主题 → DSL 规则 → 时序出生门（证据全量打印）
precedent docket                      # 队列：候选 + 证据 + diff
precedent docket confirm p-9f8e7d6c   # 一次人类确认 → 规则 status: active
precedent hooks install claude-code                 # 默认 dry-run，打印完整合并方案
precedent hooks install claude-code --apply --i-know   # 这一步请你亲手运行
```

然后开一个**新的 Claude Code 会话**，让它做那件你说过不要做的事：

```
⏺ Bash(pip install requests)
  ⎿  Blocked by hook:
     [precedent p-1a2b3c4d] 用 uv，不要用 pip
     — from your correction on 2026-09-12: 不要用 pip，用 uv
```

闭环到此合上：周二的一句纠正，周三在历史记录上过门，周四自己开始执行，并且把当初
的原话附在拦截理由里，让 agent 知道**为什么**。

```bash
precedent report --md digest.md                        # 告警/漏斗/docket/收据/支出
precedent hooks uninstall claude-code --apply --i-know # 只删我们自己写的条目
```

## 时序出生门（核心机制）

对由主题 `T` 编译出的候选规则 `R`，设 `T` 中最早一次纠正的时间为 `t0`：

- **(a) 命中**：`R` 必须在 `T` 里至少一次纠正之前紧邻记录的那个违规动作上触发
  （纠正性人类回合前面的那个 `tool_use`）。
- **(b) 之后安静**：在 `t0` **之后**、`R` 可判定（它的工具被用到、且在作用域内）的
  那些动作里，如果一次触发在同一会话的接下来 3 个人类回合内又跟着一条来自 `T` 的
  纠正，那是**真阳性**；其余每一次触发都是**误触**，只在
  `误触数 / t0 后可判定数 ≤ ε`（默认 2%，**永远同时给出计数**）时被容忍。
- **(c)** `t0` **之前**的行为**只报告、不计入**。

判定为 `PASS` / `FAIL` / `INSUFFICIENT`（`t0` 之后可判定动作少于 3 次）。整块证据
存进候选和账本。

这一条是整个门控里唯一需要推倒重写的地方：**一条纠正从它被说出的那一刻起才表达策略**。
旧版把规则拿去跟"用户还没提出要求之前"的行为对照，结果误杀了一条完全正确的规则——
因为用户在提出要求之前的几周里合法地用过无头浏览器。

**请看绝对数字，不要只看百分比**：ε 是按**动作**算的比率，语料越大分母越宽松。
17 次被容忍的误触意味着 agent 会被打断 17 次，所以报告里两种单位都会打印。

## 安全保证

1. **只有一个命令可以写 `settings.json`，而且必须先备份。** 只有
   `hooks install/uninstall --apply` 会写；合并是非破坏性的、幂等的、原子的；
   解析不了的 `settings.json` **绝不写入**（会拒绝、说明原因并以 2 退出）。其余每个
   命令对 Claude home 都是**只读**的，由 `guard_not_under_claude_home()` 强制——
   它宁可抛异常也不写。所有状态都在 `~/.precedent/`（或 `--state-dir`）。
2. **钩子永远不会弄坏 Claude Code。** 六个脚本都是一层共享库上的三行逻辑，测试断言
   **每一个都 < 300 ms**；防御性地读 stdin JSON；**任何**内部异常都以 0 退出、不输出
   任何 stdout，并把错误追加到 `hooklog.jsonl`；**只有**当一条已确认（`status:
   active`）的先例匹配时才 deny/ask，其余一律放行 + 记录。**按构造 fail-open**——
   一个在自己状态文件损坏时就把 agent 卡死的门，比没有门更糟；饿死由
   `precedent report` 报告，而不是靠卡住一次工具调用来提醒你。
3. **没有内容寻址快照就不删除、不覆盖任何用户文件**，`undo` 可往返；默认 dry-run，
   并且会标出"后续会话也写过"的冲突文件。
4. **每一次 `claude -p` 都受限、隔离并记账**：`--max-budget-usd`、
   `--no-session-persistence`、显式 `--model`，**绝不** `--dangerously-skip-permissions`；
   起草调用额外带 `--safe-mode --tools "" --disable-slash-commands`（起草者不需要工具，
   而且不能读它正在起草的那些 CLAUDE.md / 技能 / 钩子）。预算检查**不信任**二进制自报的
   花费（每次调用先按上限预留）。LLM 输出一律视为不可信：先 schema 校验，再走确定性
   门控。**机械拒绝覆盖 LLM 批准，永不反向。**
5. **任何东西都不会被自动应用。** `ACCEPT` 只是一张证书加一条 docket 条目；
   `improve` / `examine` 产出的是提案，不是文件。字节由你亲手写入——一个既做接受又做
   写入的层，本质上是一个自带橡皮图章的提议者。

**测试**全部跑在 `tmp_path` 下的合成 Claude home 上，并有 `real_home_canary` 夹具：
真实 `~/.claude` / `~/.precedent` 一旦被碰过，测试就失败。

## 诚实的限制

- 纠正靠**表层模式**识别，不过模型：没用到词表里任何词的纠正会被漏掉，只是引用了这些
  词的回合可能被误判。输出是置信度，不是布尔值。
- **门控是回溯的，不是预测的。** 它只能证明规则在你**已有的记录**上该触发时触发、该
  安静时安静；它不能证明泛化，也分不清"安静是因为规则对"和"安静是因为规则窄到只匹配
  当初那一个动作"。**要读规则本身，不要只读判定。**
- ε 是按动作算的比率，大语料会让它变宽松——所以永远打印绝对计数和涉及的会话数。
- 钩子**按设计 fail-open**：强制执行出故障是**安静**的，要靠 `precedent report` 才能
  发现。这是一个刻意的取舍，也是攻击者会盯上的那一点。
- 强制执行仍然要**先经过工具 matcher** 才能按路径生效：第三方 MCP 写文件的工具对它
  不可见；Bash 写入检测是有界启发式（8 KB 命令、每次调用 16 条受管路径——上限是真的，
  但会被记录，不是静默丢弃）。
- **统计功效很小，而且工具会直说**：二值信号下约 40 对任务只能可靠检出 **+30pp**，
  +10pp 需要 160+ 对。`NSF`（预算耗尽前达不到阈值）是大多数真实编辑的**正确**答案，
  并且作为一等结果展示，而不是藏起来。
- `claude plugin eval` 仍在早期访问阶段，所以多数机器上考官走自己的配对 `claude -p`
  回退路径，它无法回放轨迹；两臂受同样的限制，配对仍然成立，但起点更冷。
- L4 隔离是**结构性的，不是不可伪造的**；macOS 上 方案.md §L4 已明确标注这是较弱的边界。
- 美元上限约束的是**调用次数**，不是钱：如果 `claude` 既忽略 `--max-budget-usd` 又少报
  花费，次数仍然有界，金额没有。
- **只有确定性检查**：沙箱可执行检查和判据型检查在方案里写了，没实现。
- **一台机器、一个用户**：所有数字都来自作者自己的 `~/.claude`（8 个会话）。跨用户的
  验证是研究轨道的 C1/C2/C3，不是今天在做的主张。

## 仓库结构

```
packages/precedent/   CLI 与闭环        （precedent-cli 0.1.0）
packages/receipts/    L0：到底加载了没有 （receipts 0.1.0）
packages/acceptor/    L3：接受的统计学   （acceptor 0.2.0）
research/             四天调研的原始证据（见 research/README.md）
scripts/dev.sh        三个 uv venv + 三套测试
方案.md               完整设计文档
```

许可证 Apache-2.0，见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)；贡献规则见
[CONTRIBUTING.md](CONTRIBUTING.md)，威胁模型与卸载方式见 [SECURITY.md](SECURITY.md)。
