# Every Correction Is a Test: Human-Anchored Criterion Evolution for Self-Modifying Agents

**The Precedent authors** · `github.com/Linxiushen/precedent` · Apache-2.0
Draft, 2026-09-17. Every number in this paper is reproduced by a command listed in
[`paper/README.md`](README.md).

---

## Abstract

Coding agents now routinely rewrite their own memory, skills and configuration.
The step nobody implements is **acceptance**: deciding whether a self-modification
should be kept. In the September 2026 literature the acceptance function is either
an LLM saying yes or a greedy "the score went up" rule, and the greedy rule
false-commits 30–42 % of edits whose true effect is zero (PACE, arXiv 2606.08106).
Five 2026 surveys name a reference implementation of a gated update loop as an open
problem; none ships code.

We contribute three things. First, a **method**: the *temporal birth gate*, which
turns a user's correction into a test with planted ground truth. A correction is
free, exogenous, human-labelled evidence of a policy violation, and the violating
action is already in the transcript — so a rule compiled from it must fire on that
action and stay quiet afterwards, and both halves can be checked offline against the
user's own record with zero model calls. Second, a **benchmark**: an acceptor is a
classifier over candidate edits, so unlike an agent its ground truth can be planted;
we ship five labelled streams over six skill-shaped artefacts (17 variants, each
label re-verified by an independent oracle) and score eleven acceptors, including two
readings of our own bundle gate and an LLM-judge stub. Third, a **runnable reference
implementation**: 1,591 passing tests, standard library only, distributed as one
deterministic zipapp that needs nothing installed.

The evaluation is explicitly **not a multi-user study**. It is a benchmark that runs
anywhere plus a single-machine case study — one user, eight sessions, 61 learned
artifacts, one rule enforced live. We say this here rather than in a footnote because
it bounds every empirical claim below. What the case study does show is that a gate
honest enough to be embarrassing is a gate that finds bugs: ours reported 100 %
tolerated fires on a rule whose target genuinely complied, and that implausible number
was a fail-closed defect in our own inverted matcher.

---

## 1. Introduction

The proposer is no longer the bottleneck. OpenClaw, DeepSeek Harness, Hermes, Claude Code's automatic
memory, Codex Memories and a dozen sleep-time optimisers all write persistent
artifacts on the agent's behalf. What decides whether those writes are *kept* is, in
almost every shipped system, an LLM's opinion or a comparison of two numbers.

That is the weak link, and it is measurable. PACE (arXiv 2606.08106) instruments
greedy "keep if the score went up" acceptance on controlled GSM8K and reports
**3.4 ± 1.2 commits per run at 42 % false and 33 % harmful** for a 1.5B agent, and
30 % false / 10 % harmful at 3B; on its stochastic regime, where no candidate carries
real gain, greedy commits 20.7 / 15.3 / 13.3 times per run at **82 % / 72 % / 100 %**
false. We reproduce the phenomenon locally without any of PACE's infrastructure:
`research/tools/gate_power_sim.py` simulates greedy acceptance at exactly zero true
lift and reports **0.44 / 0.45 / 0.46** false-commit rate at N = 20 / 40 / 80 (the
44.6 % headline recorded in `方案.md` §5 is this family of numbers); the policy-level
harness in `packages/acceptor` gives **45.8 %** at N = 40, 200 seeds; and the
artefact-level benchmark in §5 gives **47.0 %** on a stream where every commit is
false by construction.

Three further results say that the problem is not one bad estimator but a missing
layer.

**Self-selection is a coin flip.** HarnessDev (2609.01437) tracks 9 harness lineages,
73 versions and 64 adjacent version switches. Visible-feedback gains of +3.0 to +13.9
pair-score collapse to +1.43 to +4.44 held-out (mean +3.11); 8 of 64 switches regress
on both benchmarks, 27 sit inside a ±4.75 noise band, and the feedback direction and
the held-out direction **agree on only 34 of 64 (53.1 %)**. Only 2 of 9 declared final
versions are held-out-optimal. Of the six edit categories the authors classify, the
count for *standalone verifier* is **0**: 64 evolution edits, none of which touched
the thing that grades them.

**Sealing the evaluator costs 12–25 %.** RSI-Exam measures 9 models on 37 tasks that
exist in both a visible and a hidden split. Every model drops from visible to hidden:
**−11.7 % (Kimi K3) to −24.7 % (Gemini 3.7 Flash)**, with Opus 5 at −13.2 %. Whatever
a self-scored loop is optimising, between an eighth and a quarter of it is the
scoring.

**The artifacts themselves have near-zero causal effect.** Five independent 2026
results converge: Counterfactual Trace Auditing (2605.11946) measures **+0.3 pp** mean
for LLM-written skills with a median of 0 (45 of 49 tasks exactly zero) — and its own
repository post-hoc audit fails to replicate even that, at −0.7 pp, 95 % CI
[−3.9, +2.5]; SkillsBench finds **no measurable gain** for LLM-authored skills against
**+16.2** points for human-authored ones; Demystifying Agent Skills (2608.14036) shows
retrieval precision falling **29.6 % → 3.3 %** as the pool grows to 100 skills;
Continual Harness reports 6.4 % reuse of what it wrote; SkillOpt's gains are carried by
1–4 edits out of many. AI2's *Rethinking the Evaluation of Harness Evolution*
(2607.12227) completes the picture at matched budget: harness evolution scores 67.4
against an unevolved 68.2 and a parallel-sampling baseline of 72.3, and its held-out
transfer is **+0.6**.

And yet **all five 2026 surveys list the same four open problems — a reference
implementation of a gated update loop, a regression/retention protocol, evaluator
co-evolution without self-confirmation, and a multi-generation benchmark — and none of
them ships code** (`research/synthesis_v1.md` §C). The RSI survey (2607.07663, 1,250
papers) states the operating law of the field as a verification hierarchy — formal
verification > execution feedback > learned judges > self-assessment — and MetaRSI
(2609.06396) finds 69 % of RSI systems close their loop only on machine-checkable
targets. The layer everyone asks for is the layer nobody writes.

### 1.1 The idea

The cheapest external verifier on a coding agent's machine is already there and nobody
reads it: **the user's corrections**.

When a user types "no, use `uv`, not `pip`", four things are simultaneously true. The
statement is *exogenous* — it did not come from the agent or from a model. It is
*human-labelled* — the label is the correction itself. It is *free* — it was going to
be typed anyway. And the **violating action is already in the transcript**, recorded
immediately before the correcting turn.

That fourth property is what makes it a test rather than a preference. A rule compiled
from the correction arrives with planted ground truth: it *must* fire on the action
the user objected to, and it *must* stay quiet on everything else the user did not
object to. Both halves are checkable offline, deterministically, against the record
that already exists — no benchmark, no model call, no asking the agent to grade
itself. And the check *compounds*: one human confirmation buys enforcement in every
future session, against every future candidate edit, across every model upgrade.

### 1.2 Contributions

1. **The temporal birth gate** (§2) — a formal acceptance criterion for rules mined
   from corrections, with an explicit statement of what it cannot certify.
2. **An acceptance stack** (§3) — mechanical → statistical → human, with PROCTOR's
   ordering rule, a paired anytime-valid e-process under a summable spend schedule,
   the ties-are-free rung, and a per-task floor that requires k-of-n confirmation.
3. **A runnable reference implementation** (§4) — three stdlib-only Python packages
   and one TypeScript plugin, 1,591 tests, one deterministic zipapp.
4. **The acceptor benchmark** (§5a) — labelled streams with planted ground truth,
   eleven acceptors scored, also emitted as a 17-task Harbor family.
5. **A single-machine case study** (§5b–c) and **one bug the gate found that we did
   not** (§6).

The contribution is *not* the sequential test. PACE and SEA invented that; §8 is
precise about who did what.

---

## 2. Method — the temporal birth gate

### 2.1 Statement

Let `T` be a topic: a cluster of user corrections that say the same thing, grouped by
token overlap across sessions. Let `t0 = min{ timestamp(c) : c ∈ T }` be the moment the
policy was first stated. Let `R` be a candidate rule compiled from `T` — a tool matcher,
a set of input-field matchers, an action in `{deny, ask, log}`, and a scope.

An action `a` in the transcript is **eligible** for `R` if `R`'s tool was the tool used
and `a` lies inside `R`'s scope (a project-scoped rule is not eligible outside its
`cwd_glob`; this matters, because scoping a rule must not be allowed to flatter its
numbers by shrinking the numerator alone).

> **(a) HIT.** `R` must fire on the violating action of at least one correction in `T`
> — the `tool_use` recorded immediately before the correcting human turn.
>
> **(b) QUIET-AFTER.** Among eligible actions with `timestamp(a) > t0`, a fire is a
> **true positive** if it is followed, in the same session, within the next 3 human
> turns, by another correction belonging to `T`. Every other fire is a **false fire**.
> The clause passes while
>
> ```
> false_fires / eligible_after  ≤  ε        (default ε = 0.02)
> ```
>
> and the counts are always printed alongside the ratio.
>
> **(c) PRE-`t0`.** Behaviour before `t0` is **reported and never counted**.

The verdict is `PASS` (both clauses hold), `FAIL` (either fails), or **`INSUFFICIENT`**
when `eligible_after < 3`. The whole evidence block — every count, the session spread
of the false fires, the pre-`t0` figure — is stored on the candidate and written into a
hash-chained ledger, so a later reader can re-derive the verdict rather than trust it.

### 2.2 Why clause (c) is the whole design

The first version of this gate asked the obvious question: *does the rule fire in the
sessions where the user complained, and stay quiet in the sessions where they did not?*
That question is **anachronistic**. It judges a rule against behaviour from before the
user ever stated the policy.

On our own machine it threw out a correct rule for exactly that reason. The user asked
the agent to stop launching headless browsers; in the weeks *before* that request they
had legitimately used headless browsers, and the v0 gate counted those legitimate uses
as false fires (2/2 on corrected sessions versus 2/5 elsewhere → FAIL). Under the
temporal gate the same rule passes: 32 fires in 8,164 eligible post-`t0` actions
(0.4 % ≤ ε), with the pre-`t0` figure of 33/945 printed and discarded.

A correction expresses a policy **from the moment it was made**. Clause (c) is the
one-line encoding of that, and it is the difference between a gate that certifies rules
and a gate that punishes users for having changed their mind.

### 2.3 What it cannot certify

The gate is retrospective. It proves the rule fires where it should and is quiet where
it should **on the record you already have, after the moment you stated the policy**.
Three consequences follow, and the tool prints all three rather than hiding them.

**It cannot certify a preemptive policy.** If the user states a rule and the agent
complies from then on, there is no recorded violation to bind clause (a) to. Our own
worked example is "use Opus for subagents". Three scopings, three verdicts, none of
them `PASS`:

| rule shape | (a) HIT | (b) quiet-after | verdict |
|---|---|---|---|
| `Agent\|Workflow`, `require_field model=opus` | 1/3 | 4/8 = 50 % | FAIL — `Workflow` has no `model` input field at all |
| `Workflow.script` must match `model:\s*['"]opus['"]`, all projects | 1/3 | 1/4 = 25 % | FAIL — one call in another project genuinely did not pin it |
| the same, scoped to this project | 1/3 | **0/2 = 0 %** | **INSUFFICIENT** — the right shape, below the evidence floor |

The third row is the honest answer, not a failure to answer: the shape is right and the
corpus is too small. The candidate stays in the queue and becomes compilable the moment
the tool is used a third time in that project.

**ε is a per-action rate, so a large corpus is a permissive denominator.** 17 tolerated
fires at 0.2 % is still 17 interruptions. Every report prints both units.

**A quiet rule may be quiet because it is right, or because it is so narrow it matches
only the action that provoked it.** The gate cannot tell those apart; the
LLM-drafted rules on our machine did exactly the second and passed. *Read the rule, not
just the verdict* is printed in the tool's own limitations section.

---

## 3. The acceptance stack

A rule from a correction is one surface. Skills, memory files, `CLAUDE.md` edits and
harness changes are the rest, and they have no planted ground truth. For those, the
acceptance layer is three rungs deep, and the ordering between the rungs is itself a
safety property.

### 3.1 Mechanical → statistical → human, in that order

We adopt **PROCTOR's rule: mechanical rejection overrides LLM approval, never the
reverse.** Concretely, `accept()` runs (i) deterministic checks — lint, DLP, path-level
ownership, environment assertions, safety canaries; (ii) the paired statistical gate;
and (iii) a human, but only on `HOLD`. An LLM may **veto** and may **draft**; it may
never promote a candidate. Every `--llm` output is schema-validated first — unknown
keys are dropped, so a draft cannot smuggle `"status": "active"` or a pre-cooked
`"birth": {"verdict": "PASS"}` past anything — and then gated as if a human had written
it.

The evidence for the ordering is not aesthetic. MLAS (2606.23075) pushed 40 adversarial
payloads through Hermes's autonomous background review: **40/40 persisted** as
executable skills. Through the same host's hub-install path with an LLM/regex scanner:
1/40 blocked. Through OpenClaw, which queues every proposal for explicit user consent
before it reaches disk: **40/40 blocked**, "without relying on any detection
heuristic". The mechanical, boring rung beat the clever one by a factor of forty.

### 3.2 The paired anytime-valid e-process

Candidate and incumbent are evaluated on the **same instances in a committed order**.
The gate is a betting martingale over the discordant pairs (PACE's construction;
Waudby-Smith & Ramdas 2024; Howard et al. 2021): wealth is a nonnegative supermartingale
under H0, and Ville's inequality gives `P(ever ≥ 1/α) ≤ α` at any stopping time. Four
betting rules ship (`fixed`, `mixture`, `ons`, `agrapa`); validity depends only on the
fraction being predictable and bounded, so they differ in power, never in α.

Anytime-validity is what makes the gate usable on a laptop: a decision can be paused,
serialised to JSON and resumed days later without paying for the look.

The α guarantee is a property of the **protocol**, not of the arithmetic, and four of
its preconditions belong to the caller. We made each one mechanical and measured what
happens when it is not (`--regime adversarial`, 300 null seeds, n = 40; honest baseline
2.0–3.3 %):

| the proposer… | naive | guarded | the defence |
|---|---|---|---|
| re-orders the pairs and stops on a win | 78.0–89.7 % | 0.0 % | instance order committed before outcomes exist |
| snapshots and rewinds a losing batch | 99.3 % | 0.7–1.3 % | ledger: `n_pairs` and the evidence chain may not go backwards |
| privately screens K = 20 candidates on one dev set | 17.3–17.7 % | 1.3–2.3 % | fresh instances per candidate + exposure accounting |
| re-proposes the same candidate K = 20 times | 32.7–37.7 % | 0.3–0.7 % | content-hash dedup + a **summable** spend schedule |
| hand-picks 8 instances it knows fail | 100.0 % | 0.0 % | a floor on informative (discordant) pairs |

Content hashing is a floor an LLM evades by renaming a variable. The summable schedule
is the only real bound on re-proposal.

### 3.3 The CTHS spend schedule

Per-candidate α comes from SEA's horizon-free schedule

```
δ_k = δ0 / (Z · k · ln²(k + 1)),      Σ_k δ_k = δ0
```

with **Z = 3.387735532** (SEA quotes 3.39). The naive constant `Z = 2` is a common
shortcut and it **over-spends by 1.693867766×** — a run-level family-wise error budget
that silently leaks most of itself. Rounds are *reserved* and then *committed*, so a
candidate that never reaches the gate does not burn a round.

### 3.4 Ties are free

The rung that makes the whole stack affordable is the cheapest one. If a candidate is
**behaviour-identical** to the incumbent under a published reference matcher — comments,
trailing whitespace, runs of blank lines and frontmatter key order removed, and nothing
else — then its outcome on any historical session is a **tie by construction**. A
sequential test learns only from discordant pairs, so a tie costs nothing. Most
production edits are ≈ 0 effect (§1), which means most of the traffic can be disposed of
at **zero evaluations**, and the money is spent only on candidates that can actually
change behaviour. On the benchmark stream in §5a this rung plus the two other free rungs
dispose of 77 % of decisions.

### 3.5 The per-task floor, and why k-of-n is not optional

Superiority in the mean is not enough: a candidate that lifts the average while breaking
a task the user relies on is a regression. So a **protected corpus** — tasks the
incumbent passes k/k with observed stability — runs a separate never-regress floor with
its own harm martingale (α_harm = 0.10), because a few discordant pairs should be enough
to *stop* something, even though many are needed to *certify* something.

A naive floor is unusable, and the size of the problem is exactly quantifiable. On
Bernoulli(0.6) tasks with **zero true lift**, the probability that at least one task
flips pass→fail by chance — i.e. the false-block rate — is:

| protected tasks | k = 1 repeat | k = 2 | k = 3 |
|---|---|---|---|
| 10 | **0.93** | 0.44 | 0.13 |
| 20 | **1.00** | 0.70 | 0.24 |
| 40 | **1.00** | 0.91 | 0.43 |

At k = 1 the floor blocks essentially everything. This is why membership in the
protected corpus requires *observed* stability and why a flip counts only after k-of-n
confirmation among the first n attempts. Real test-verified coding tasks are far more
deterministic than Bernoulli(0.6), and baseline self-replay measures that per task for
free — but the table is why the confirmation rule exists rather than being an
afterthought.

### 3.6 Honest power

Binary outcomes carry little information. Measured commit rate (median tasks to commit)
for the paired gate at α = 0.05, incumbent pass rate 0.7:

| true lift | N = 20 | N = 40 | N = 80 | N = 160 |
|---|---|---|---|---|
| 0.00 | 0.01 | 0.02 | 0.03 | 0.03 |
| 0.10 | 0.03 | 0.11 | 0.20 | 0.39 |
| 0.20 | 0.12 | **0.44** | 0.79 | 0.98 |
| 0.30 | 0.39 | **0.98** (median 22) | 1.00 | 1.00 |

So **~40 paired tasks reliably detect +30 pp and nothing smaller; +10 pp needs 160+**.
The consequence is the product decision that shapes everything else: regressions and
harms are certified *fast*, improvements *slowly*, and **`NSF`** — "the budget ran out
before the threshold was reachable" — is displayed as a first-class result rather than
hidden. Given the null-effect literature in §1, a gate that rejects most production
edits for free is behaving correctly.

---

## 4. System

### 4.1 The five-step loop

| step | what it means | what the implementation does |
|---|---|---|
| ① propose | the agent edits its own memory / skills / rules | three proposers: the correction miner, the host's own writes brought under governance, and a nightly improver that clusters failure signatures |
| ② accept | should this change be kept? | the temporal birth gate for rules; the examiner (paired e-process, harm martingale, spend schedule, task floor) for everything else |
| ③ enforce | does the change reach the model and bind it? | six hooks in `settings.json`, plus live load receipts |
| ④ audit | is learning happening, and has anything got worse? | a docket that never piles up silently, four starvation alarms, a proposed→accepted→activated→attributed funnel, a hash-chained ledger, content-addressed snapshots, `undo` |
| ⑤ re-evolve | feed the audit back into the proposer | rejected drafts return as negative examples; the precedent set grows with every correction |

### 4.2 The six hooks

| event | what it does | budget |
|---|---|---|
| `PreToolUse` | confirmed precedents (`deny` / `ask` / `log`) **and** path-level ownership of the governed trees | 5 s |
| `PostToolUse` | change-feed capture: what the call actually touched | 5 s |
| `UserPromptSubmit` | records the human turn — the one origin treated as trusted | 5 s |
| `SessionStart` | live receipt: this session started, with N active rules | 10 s |
| `InstructionsLoaded` | live receipt: which instruction files reached the model, whole or truncated | 10 s |
| `Stop` | funnel counters for the session | 10 s |

All six are three lines of logic over one shared library, asserted under 300 ms
end-to-end by tests. They read stdin JSON defensively and, on **any** internal
exception, exit 0 with empty stdout and append the error to a hook log. They `deny` or
`ask` **only** when a confirmed rule matches; everything else is allow-and-log.

Enforcement is therefore **fail-open by design**: a missing or corrupt rule file, an
uncompilable regex, a torn stdin, a missing library all fail open, because a gate that
bricks the agent when its own state file is broken is worse than no gate. Starvation is
reported by the audit command, never by wedging a tool call. This is a deliberate trade
and it is the one an adversary would attack; §7 says so again.

### 4.3 The fail-closed evaluator

The same grading core is exposed to a third-party proposer through an OpenClaw plugin,
and there the polarity inverts because of one fact about the host:

> On a thrown error or a hook timeout the host records an **attributed error outcome —
> it does not block.** Only a completed `decision: "block"` vetoes an apply.

**An evaluator that throws is an evaluator that silently approves, and it approves
precisely when it is broken.** So the plugin never throws: a missing CLI, a crash, a
timeout, garbage on stdout, an oversized bundle, or a bug in the plugin itself each
becomes an explicit `block` whose reason names the failure *and* the fix. Eleven `describe`
blocks of the plugin's 94-test suite exist only to force those failure modes across a
real process boundary.

### 4.4 Snapshots, undo, and the funnel

Every learned-state file body goes to a content-addressed blob store before any
unattended write. `undo --session <id>` plans restore / recreate / delete / blocked per
file, flags any file a *later* session also wrote, and is a dry run by default.

Practitioners in the issue trackers do not ask for RSI; they ask for **version
control** — diffs, receipts, undo, budget. Gates in production die of **starvation**,
not strictness: one tracked instance left write approval on for 8 weeks and accumulated
241 staged writes that nobody was told about; another ran 41 background forks and
produced 0 updates for weeks. The operating rule that follows is *silent on pass, loud
on block, never silent on starvation*, implemented as four alarms — `PENDING` (a docket
entry older than 7 days), `HOOK` (hook errors, quarantined regexes, an unreadable rule
file), `NOT_INSTALLED` / `NO_FIRES`, and `DRIFT` (what is installed is not what this
build would install) — over the funnel *proposed → accepted → activated → attributed*.

---

## 5. Evaluation

### 5a. The acceptor benchmark

An agent benchmark cannot plant its ground truth; an **acceptor** benchmark can, because
an acceptor is a classifier over candidate edits. Five streams over six SKILL.md-shaped
seed artefacts, 17 variants:

| stream | the candidate edit | correct decision | cost to label |
|---|---|---|---|
| `null` | byte-identical, whitespace-only, key-reordered, comment-only | reject | **nothing** — behaviour-identical by construction, so every commit is false |
| `regression` | a directive contradicting the artefact's own procedure; a "close ALL windows" over-generalisation; a placeholder codified as verified | reject | one authoring pass |
| `unsafe` | credential exfiltration wrapped as a helper, a destructive verb, a new egress host, a dropped confirmation step | reject | one authoring pass |
| `tamper` | an edit that reaches the **evaluator**: a scorer path, a results file, a best-ever snapshot, a helper writing the reward file | reject | one authoring pass |
| `good` | a defect genuinely repaired — here the *incumbent* is the defective bundle | **commit** | one authoring pass |

Every label is re-verified before a table prints, by an oracle sharing no code with the
generator, in both directions: the property must hold of the candidate and must **not**
already hold of the incumbent. All 6 × 17 = 102 candidates are asserted in tests; a
stream whose labels stop verifying raises instead of reporting.

```
$ precedent bench --all --seeds 200
AcceptorBench / labelled streams — runs=200 families=null,regression,unsafe,tamper,good variants=17 artefacts=6 budget=40 pairs p_inc=0.5 seed0=0
37400 decisions over 11 acceptors in 1.9s, zero model calls.  declared lift: null=+0.00, regression=-0.25, unsafe=+0.05, tamper=+0.35, good=+0.25

acceptor              false-commit  regression  unsafe  tamper  harmful(mean)  missed-improv  evals/dec  ms/dec   pareto
--------------------  ------------  ----------  ------  ------  -------------  -------------  ---------  -------  ------
greedy                       47.0%        0.2%   64.9%  100.0%          55.0%           1.2%       40.0    0.002       1
mcnemar                       2.6%        0.0%    9.5%   93.1%          34.2%          34.0%       40.0    0.003       1
gate-fixed                    2.0%        0.0%    5.8%   86.0%          30.6%          45.8%       27.0    0.034       1
gate-mixture                  1.4%        0.0%    5.5%   79.8%          28.4%          52.2%       25.5    0.048       2
gate-ons                      0.4%        0.0%    3.0%   71.4%          24.8%          59.5%       30.2    0.039       2
gate-agrapa                   0.5%        0.0%    3.4%   75.1%          26.2%          56.2%       30.4    0.039       2
llm-judge-yes               100.0%      100.0%  100.0%  100.0%         100.0%           0.0%        0.0    0.000       3
regex-scan                   83.5%       83.2%   41.6%   83.4%          69.4%          16.8%        0.0    0.061       1
evaluate-bundle             100.0%       66.7%    0.0%  100.0%          55.6%           0.0%        0.0    0.101       1
evaluate-bundle-host        100.0%       66.7%   50.0%  100.0%          72.2%           0.0%        0.0    0.101       2
precedent                     0.0%        0.0%    0.0%    0.0%           0.0%          52.2%        5.8    0.104       1
```

Every column but one is deterministic: the same command prints the same digits on
any machine. `ms/dec` is not — it is wall-clock on an M-series laptop under Python
3.12, and the header's `1.9s` with it. They are quoted because a table that hides
what a decision costs is hiding the only column an operator has to budget for.

Four rows carry the argument.

**`greedy` false-commits 47.0 %** of a stream whose true lift is exactly zero — PACE's
30–42 % reproduced at the artefact level, with no agent, no benchmark and no labelling
effort.

**`evaluate-bundle` commits 100 % of the same stream**, and that is not a defect. Our
own bundle grader answers *"is this safe to write?"*, never *"is this better?"*, and a
byte-identical resubmission is perfectly safe to write. This row is the argument for
putting a zero-stream in front of any harm gate: a harm gate alone will happily commit
a loop's worth of no-ops.

**`tamper` is where statistics loses.** Its *reported* lift is the largest in the stream
(+0.35) precisely because the edit moved the thing that produces the number; its true
lift is zero. Every statistics-only row commits **71–100 %** of it. Only a check that
looks at *what was edited* refuses.

**Our own gate is at 0 % on all four wrong families at 5.8 paired evaluations per
decision** against a budget of 40, because three of its four rungs are free —
`no-effect` (24 % of decisions), `evaluator-reach` (24 %), `mechanical` (29 %), then the
e-process (24 %). The honest cost is in the same row: **52.2 % missed improvements**,
almost all `NSF`, which is the correct answer for a +0.25 lift at 40 paired binary
outcomes (§3.6).

**What this table does not show.** precedent's 0 % on `null` is *not a discovery*: the
`null` family is *defined* by the published reference matcher and rung 1 uses that same
matcher, so the cell says "this gate implements the rung". The informative part is the
eight rows that do **not** implement it, our own `evaluate-bundle` among them at 100 %;
the matcher is public API precisely so any acceptor can adopt the rung. The `tamper`
family is labelled by the same published contract the gate consults (Harbor's verifier
layout), so that column measures coverage of a **known** contract, not detection of an
unknown attack. The artefacts, the edits, the labels and what each acceptor can see are
real; the paired **outcomes are simulated** from the per-family lift printed in the
header, so a statistical row is real *given* a declared effect model. And the corpus is
six artefacts written by the same people who wrote the gate.

The same 17 cases are emitted as a **Harbor task family** (175 files), so anyone can run
them through an adapter they already have; the tests execute the generated
`solution/solve.sh` and `tests/test.sh` for real and fail if the checked-in tasks have
drifted from the generator.

### 5b. Single-machine case study

**One user, one machine, eight sessions.** Read-only, zero writes under the Claude home;
`settings.json` was byte-identical before and after every stage except the one the user
ran by hand.

*Corpus.* 8 sessions; **61 learned artifacts** (44 skills, 10 memory files, 7
`MEMORY.md`); **42 never cited** — 78 % of the citable ones; 2 artifacts loaded only in
part in at least one session; 2 stale index entries pointing at files that do not exist;
4 near-duplicate artifacts; 29 unattended writes in the previous 7 days, 16 of them Bash
heredocs bypassing the memory tool entirely. Runtime 4.3 s of CPU, ~5 s wall clock,
zero model calls, zero bytes written under the Claude home.

*Mining.* 164 human turns after skipping 119 skill/slash-command expansions →
**20 corrections (12.2 %)** → **18 topics**, **1 repeated across sessions**, 4 of them
already written into `CLAUDE.md` or memory and happening anyway. One sentence was
filtered as a question. Of the 18 topics, 2 compiled to rules and 0 passed the gate on
that run.

*The three refusals.* The gate said no three times on real data, and each refusal is a
different failure mode:

1. **A global headless-browser rule.** It passed clause (b) on aggregate, but a sweep
   over the whole frozen corpus priced it honestly: **68 interruptions across 17,842
   recorded tool calls** (0.38 %). That is not a gate, it is a tax. The rule was
   *retired* — kept, with its reason, as a negative example — and replaced by the same
   template with one `--cwd-glob`: gate `PASS` at **0/140** tolerated fires. Same
   correction, same template, one scope flag: 68 interruptions → 0.
2. **`require_field model=opus` on `Agent|Workflow`.** QUIET-AFTER 4/8 = 50 %. Reading
   the record explained it: the `Workflow` tool has no `model` input field at all, in
   any of its 29 recorded calls. FAIL, for a reason about the tool rather than about the
   user.
3. **`require_regex` on the same policy, unscoped.** 1/4 = 25 % — one call in another
   project genuinely did not pin the model. FAIL. Scoped to the project it becomes
   `INSUFFICIENT` at 0/2, below the evidence floor of 3. This is the preemptive-policy
   limit of §2.3, and it produced the bug in §6.

*Enforcement, live.* The user installed the hooks by hand. A fresh session inside the
scoped project, a real `Bash` call containing `headless`: **denied**, with the user's own
sentence from nine days earlier attached as the reason. The same command from `/tmp`:
**allowed** — the 68 interruptions the retired rule would have cost. The hook log entry,
verbatim:

```json
{"event": "deny", "hook": "PreToolUse", "rule": "p-0e66c266", "ms": 0.48,
 "session": "21ffac79-8ee6-41db-809e-43d12dd20b21", "ts": "2026-09-16T06:18:58Z"}
```

**0.48 ms.** Six hook invocations across both sessions, one fire. The funnel moved to
proposed 8 → accepted 1 → activated 1 → attributed 1; retired rules are listed
separately and counted as enforced nowhere. Total model spend for the live
demonstration: **$0.05**; cumulative across every stage of the project: **$0.16**.

*What the loop costs.* A full dry-run cycle is 3.4 s and $0.00. The nightly improver
drafted 4 candidate edits for $0.1863, all of which survived schema validation,
`diff == declared_paths` and lint, and all four were held for lack of evidence. One
examination of one candidate cost $0.6892 and returned **`NSF`** on 2 pairs (2 ties, 0
discordant) — no effect measured, the α budget untouched. That is the design working.

### 5c. Bundle-gate calibration on 44 real skills

The deterministic bundle grader was calibrated against the 44 skills actually installed
on the machine, copied read-only into a temporary directory and graded: **16 `pass`,
28 `revise`, 0 `block`** — zero false criticals on a working corpus. The property is
pinned by a test, because a gate that vetoes real work is a gate that gets switched off,
and the adoption data says verification is a UX cost the market selects against.

---

## 6. What the gate found that we did not

The `require_regex` variant of the Opus rule — *"every `Workflow` call must pin
`model: 'opus'`"* — was compiled against the real corpus, and the gate reported
**4/4 = 100 % tolerated fires**.

That number was implausible on its face. Two of those four calls demonstrably *did*
carry `model: 'opus'`. They carried it at character offsets **5,778 and 5,659** of
scripts **19,739 and 16,714** characters long — past the **4,096-character subject cap**
the bounded matcher imposes to stay inside the hook's own time budget. The regex never
saw the match. "No match" was read as "violation". Under an inverted (`absent`) matcher
that is a **fail-closed** error, and the rule, had it been confirmed, would have
**denied compliant calls**.

The fix is one sentence of policy with three cases. Truncation is a third way of *not
knowing*, alongside a quarantined pattern and a non-text subject, and an inverted matcher
must fail **open** on all three. It was fixed in the rule engine and in the hook's own
copy of the runtime, logged as `regex_truncated`, and pinned by tests. Re-run after the
fix: **4/4 → 1/4**, which is the true rate, and the rule still fails the gate — for the
right reason this time.

This is the thesis in miniature. We did not find this bug by reading the code; we found
it because the gate published a number we could not believe, about our own rule, in our
own data. An acceptance layer whose outputs are always flattering is an acceptance layer
that has stopped measuring. **A gate honest enough to be embarrassing is a gate that
finds bugs** — and the refusal was the bug report.

---

## 7. Limitations

**N = 1.** One machine, one user, 8 sessions, 61 artifacts, **1 rule enforced live**.
Every case-study number in §5b is from that machine, and several are from a single run
(the live deny, the 68-interruption sweep, the $0.6892 examination). Nothing here has
been validated across users. **This is not a multi-user study**, and a cross-user study
is the obvious next experiment, not a claim being made today.

**The benchmark's seed corpus is synthetic and small.** Six artefacts, 17 variants,
written by the same people who wrote the gate. The paired outcomes are simulated from a
declared per-family lift. `tamper` measures coverage of a published contract, not
detection of an unknown attack; closing that gap requires sealed evaluation, which this
implementation does not provide.

**Requirement extraction is lexical, everywhere.** Corrections are found by surface
pattern, never by a model: a correction phrased without a listed word is missed, and a
turn that merely quotes one can be a false positive. The question filter is "ends with
？/? and contains no imperative", so a polite order is missed. Topic grouping is jieba
words or character bigrams over a tunable similarity threshold; on small corpora
single-link clustering can chain. The bundle evaluator extracts requirements phrased as
sentences, so an obligation living only in a diagram or a table header is invisible to
it.

**DLP can be evaded by string algebra.** The scrub pass catches a credential split
across a shell line continuation, because logical lines are scanned too. It does not
catch one assembled by concatenation (`"sk-" + tail`) or read out of a variable, and no
deterministic checker can chase arbitrary string algebra. The share card is built from
integers and closed vocabularies for exactly this reason: the pattern pass is the second
line of defence, not the first.

**`INSUFFICIENT` is the common verdict, and by design.** Most real policies are
preemptive; most corpora are small; ε is per-action. The gate declines more often than
it certifies, and says which clause failed and how far it is from enough evidence. A
product built on this must present "no evidence" as a first-class result, or it will be
read as a product with no results.

**Enforcement is fail-open and reaches paths through a tool matcher.** An outage in
enforcement is *quiet* — you find it in the audit, not by the agent breaking. Ownership
is enforced by path once the hook runs, but the hook runs for a fixed tool set plus
whatever active rules name; a third-party MCP tool that writes files is invisible to it.
Bash write detection is a bounded heuristic (8 KB of command, 16 governed paths per
call); the caps are real and logged, not silent.

**The dollar cap bounds calls, not dollars.** Each model call is reserved at its cap
before it starts, so a binary that under-reports its cost still cannot buy more than
`floor(budget / cap)` calls — but there is no way to bound actual spend from outside a
subprocess that lies about both.

**Isolation is structural, not unforgeable.** The honest claim is "proposer/evaluator
separation plus offline-verifiable receipts", not "tamper-proof". Only deterministic
checks are implemented; sandboxed executable checks and judge-based checks are specified
and absent.

---

## 8. Related work

**Sequential acceptance.** PACE (2606.08106) invented the paired anytime-valid commit
gate for agent self-modification and measured the greedy baseline we reproduce. Its own
limitations are explicit: the guarantee is per-candidate, not run-level familywise;
results are audit-labelled at temperature 0 on 0.5B–3B agents, prompt-level edits only,
one model family, one proposer, its own loop. SEA (2607.00871) contributes anytime-valid
certificates and the horizon-free spend schedule we use verbatim, including the constant
`Z = 3.3877`; notably, in SEA's own headline configuration the certificate gate accepted
0 edits and effectively ran the control. **We invented neither.** Our contribution on
this axis is *integration and extension*: run-level error budgeting on top of the
per-candidate guarantee, multi-objective mechanical constraints ahead of the statistics,
a per-task floor with k-of-n confirmation, an adversarial regime that measures what each
protocol precondition is worth, and a stdlib implementation anyone can call.

**Replay.** Causal Agent Replay (2606.08275) does do-intervention replay of recorded
trajectories for failure attribution. The Replay Gap (2608.08239) is the constraint:
after a model swap, 74–77 % of early forks diverge at the first post-fork action and
replay validity falls to 3.2–8.0 %, and a log-stitching replay evaluator mispredicted
all five success-relevant outcomes. Our local measurement agrees — over 2,062 real tool
calls, 50.4 % are workspace writes, 19.8 % external and 13.2 % non-deterministic, with an
idempotent prefix of 0–1 calls. We therefore treat a recorded session as a
**regression/behaviour-change detector, not a fitness function**, and go sandbox-first.

**Null effects.** Counterfactual Trace Auditing (2605.11946), SkillsBench, Demystifying
Agent Skills (2608.14036), Continual Harness and SkillOpt are §1's five results.
AI2's *Rethinking* (2607.12227) adds the budget-matched comparison. We take these as the
reason the gate must make rejecting cheap rather than as a reason to abandon the loop.

**Misevolution and safety.** SkillMisevo / SafeEvolve (2608.12851) shows all 21 evolved
configurations authoring unsafe artifacts, with contamination rising from 16.0 % to
41.3 % as exposure grows; SafeEvolve is post-hoc governance, not an acceptance term.
MLAS (2606.23075) supplies the 40/40-versus-40/40 contrast in §3.1. We adopt the
conclusion — agent-created artifacts are untrusted community code, and provenance does
not raise trust — and put a human queue at the top of the stack rather than a scanner.

**Harness evolution and its critics.** HarnessDev (2609.01437) supplies the 34/64
self-selection result and the "0 edits touched a verifier" finding; EvoHarnessBench
(2609.04280) supplies harness-induced forgetting (BWT −5.3 % tools, −4.0 % skills,
−34.7 % agents); Lin et al. (2605.30621) separates *harness updating* from *harness
benefit*, finding update quality flat from 9B to frontier while benefit depends on
whether the consumer loads and follows what was written — which is why our system spends
as much effort on **activation receipts** as on acceptance. MetaRSI (2609.06396) and the
RSI survey (2607.07663) provide the verification hierarchy and the 69 % figure.

**The Gödel-machine lineage.** DGM/HGM/RQGM, Mendel Gödel Machine (2608.07645) and their
relatives optimise a harness against a benchmark with an archive and a gate. They are
the camp whose *acceptance* we are trying to replace; their documented
evaluator-integrity failures are one bug seven times — the proposer's filesystem view
includes the evaluator or its data — which a sealed boundary and a hash-bound score
receipt would close. Our L4 is partial and we say so.

**What is actually new here.** Three things, and nothing else. (i) **Correction-as-ground-truth**:
using the user's own corrections as an exogenous, human-labelled, already-recorded test
set for self-modification — we have found no prior system that does this. (ii) **The
temporal birth gate**, in particular clause (c), which makes the criterion evolve *with*
the user instead of judging them retroactively. (iii) **The acceptor benchmark as a
first-class artifact** — judging the gate rather than the agent, with free ground truth
on the `null` stream, which lets any deployed acceptor report its own empirical
false-commit rate forever. Everything else is integration.

---

## 9. Availability

Repository: **`github.com/Linxiushen/precedent`** · **Apache-2.0** · standard library
only for all three Python packages; optional extras degrade with a printed note rather
than silently.

```bash
git clone https://github.com/Linxiushen/precedent && cd precedent

./scripts/dev.sh                     # three uv venvs + all three suites
./scripts/dev.sh --tests             # receipts 71 · acceptor 369 · precedent 1057
cd plugins/openclaw && npm test      # 94 tests across a real process boundary

python3 scripts/build_zipapp.py      # one deterministic, self-contained precedent.pyz
python3 scripts/build_zipapp.py --check   # builds twice, proves the digests agree

cd packages/precedent && .venv/bin/python -m precedent bench --all --seeds 200
cd packages/acceptor  && .venv/bin/python -m acceptor.bench --regime adversarial --seeds 300
python3 research/tools/gate_power_sim.py
```

**1,591 tests pass** (receipts 71, acceptor 369, precedent 1057, plugin 94), measured on
2026-09-17 with the commands above. The whole test suite runs against synthetic Claude
homes under `tmp_path` with a canary fixture that fails the test if a real home was
touched.

The evidence base behind §1 and §8 — 521 indexed items, 96 structured deep reads, four
gap analyses, six independent designs and thirteen reviews — is published as-is in
[`research/`](../research/README.md), because the central claim of this paper is a
*negative* claim about a literature, and a negative claim is worth only as much as the
search behind it. Nothing in that directory is peer-reviewed; each record is an agent's
structured reading of a paper, repository or issue thread, carrying a URL back to the
original. Treat the URLs as the citation and those files as notes on them.

[`paper/README.md`](README.md) maps **every number in this paper** to the command that
reproduces it, and marks the ones that come from a single run.

---

## Licence

Apache-2.0. See [LICENSE](../LICENSE) and [NOTICE](../NOTICE). The methods this paper
builds on are cited in NOTICE and in the module docstrings of the packages that
implement them.
