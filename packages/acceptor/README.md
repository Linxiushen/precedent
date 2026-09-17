# acceptor

The pure decision core of an acceptance layer for self-modifying agents ("CI/CD for
harness-level RSI"). Stdlib-only Python 3.11+, deterministic, every stateful object is
JSON-serialisable so a decision can be paused and resumed across days.

| module | what it decides | statistical claim |
|---|---|---|
| `eprocess` | `PairedBinaryGate`, `PairedBoundedGate` — paired candidate-vs-incumbent testing by betting | wealth is a nonnegative supermartingale under H0; Ville: P(ever ≥ 1/α) ≤ α at any stopping time, **for acceptor-sampled, in-order pairs on an unrewound process** (PACE 2606.08106; Waudby-Smith & Ramdas 2024; Howard et al. 2021) |
| `instances` | `InstanceSampler` — who chooses what is measured, in what order | commits the instance set + order before outcomes exist; `bind_instances` makes a re-ordering a `ValueError`, not silent α inflation |
| `schedule` | `SpendSchedule` δ_k = δ₀/(Z·k·ln²(k+1)), `AlphaInvesting` | Σδ_k = δ₀ with Z = 3.387735532 (SEA 2607.00871 quotes 3.39; the naive Z=2 over-spends 1.69×) |
| `floor` | `ProtectedCorpus` — stability-gated membership, k-of-n flip confirmation (window frozen at the first n), BWT/FWT | closed-form false-block rate for Bernoulli tasks (matches simulation, also when the adapter re-runs beyond `n_confirm`) |
| `objectives` | `HardConstraints`, `MultiObjective` — mechanical gates before statistics; Pareto tie-break | PROCTOR rule: mechanical rejection overrides everything; fail-closed on missing metrics, missing denominators and non-finite values |
| `ledger` | `Ledger`, `Certificate` — sha256 hash-chained JSONL; per-candidate anti-rewind checks; funnel proposed→accepted→activated→attributed | interior edits are detected by `verify_chain()`; tail truncation needs an anchor (`verify_chain(expected_head=…)`) |
| `bench` | `AcceptorBench` — PACE's planted / stochastic regimes plus an **adversarial** regime | greedy ≈45 % false commits at zero lift; gates ≤ α; each protocol attack measured with the defence off and on |
| `streams` | **labelled streams**: candidate edits over SKILL.md-shaped artefacts with sealed labels (`null` / `regression` / `unsafe` / `tamper` / `good`), a reference matcher, an oracle per family, and `score()` over any `Acceptor` | the `null` stream's ground truth is free: every commit there is false *by construction*, so any acceptor can report its own empirical false-commit rate forever |

## Install / test / bench

```bash
cd packages/acceptor
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e . pytest
.venv/bin/python -m pytest                      # 369 tests, ~7 s
.venv/bin/python -m acceptor.bench --regime planted --n 40 --lift 0.2 --seeds 200
.venv/bin/python -m acceptor.bench --regime stochastic --n 40 --t 10 --seeds 200 --schedule
.venv/bin/python -m acceptor.bench --regime adversarial --seeds 300
```

The artefact-level half is driven from the `precedent` CLI, because scoring
`precedent evaluate-bundle` needs that package:

```bash
cd ../precedent && .venv/bin/python -m precedent bench --all --seeds 200
```

## What the α guarantee actually requires

Ville's inequality is arithmetic; the guarantee is a property of the **protocol** around
it. Four preconditions are the caller's, and this package now makes each one mechanical
rather than assumed (all rates from `--regime adversarial`, 300 null seeds, n = 40):

| the proposer… | naive | guarded | defence |
|---|---|---|---|
| re-orders the pairs, wins first | 78–90 % | 0 % | `InstanceSampler` + `gate.bind_instances(order)` |
| snapshots and rewinds on a losing batch | 99 % | 0.7–1.3 % | `Ledger` / `CandidateMonitor`: `n_pairs` and the evidence chain may not go backwards |
| screens K = 20 candidates on one dev set | 17–18 % | 1.3–2.3 % | fresh instances per candidate + `Ledger.exposure()` |
| re-proposes the same candidate K = 20 times | 33–38 % | 0.3–0.7 % | `content_hash` dedup + the summable CTHS schedule |
| hand-picks 8 instances it knows fail | 100 % | 0 % | `min_informative` |

Honest baseline for the same candidates: 2.0–3.3 %. Content hashing is a floor an LLM
evades by renaming a variable — **the summable schedule is the only real bound** on
re-proposal. And never feed the protected corpus (instances the incumbent passes) to a
gate: the floor and the gate consume disjoint streams.

## Quickstart: a gate that spans several days

```python
import json
from acceptor import (PairedBinaryGate, GateState, SpendSchedule, MultiObjective,
                      default_constraints, ProtectedCorpus, Ledger, Certificate,
                      InstanceSampler, content_hash)

# 0. Mechanical constraints run BEFORE any statistics (PROCTOR).  Fail-closed --
#    including on rates with no denominator (safety_n / activation_n).
mo = MultiObjective(default_constraints(max_token_increase=0.10, safety_canary_floor=1.0))
screen = mo.screen(candidate={"tokens": 1050, "latency": 1.0, "harness_size": 100,
                              "safety_pass_rate": 1.0, "safety_n": 64,
                              "activation_rate": 0.9, "activation_n": 50},
                   incumbent={"tokens": 1000, "latency": 1.0, "harness_size": 100})
assert screen.passed, screen.violations          # else decision = BLOCKED, no gate is run

# 1. Per-candidate alpha from the horizon-free spend schedule (run-level FWER <= delta0).
#    reserve/commit so a candidate that never reaches the gate does not burn a round.
sched = SpendSchedule(delta0=0.05)
ch = content_hash(open(__file__, "rb").read())    # candidate identity, fixed before eval
alpha = sched.reserve()                           # candidate #1 gets 0.05/(Z*1*ln^2 2) = 0.0307

# 2. The ACCEPTOR draws the instances and their order, from the content hash + a salt.
sampler = InstanceSampler([f"task-{i}" for i in range(200)], salt="run-salt", reuse_window=5)
order = sampler.draw(ch, 60)                      # committed before any outcome exists
gate = PairedBinaryGate(alpha=alpha, betting="mixture", max_pairs=60,
                        harm_alpha=0.05, min_informative=10).bind_instances(order)
sched.commit(ch)                                  # the round is spent now, not before

# 3. Day 1: paired outcomes on the committed instances, in the committed order.
day1 = [(1, 1), (0, 1), (0, 1), (1, 0), (0, 1), (0, 0), (0, 1), (0, 1)]  # (incumbent, candidate)
state = gate.update_many(day1, order.instance_ids[:8])   # GateState.CONTINUE
open("gate.json", "w").write(gate.to_json())      # pause: persist the wealth process

# 4. Day 2: resume -- anytime validity means stopping/resuming costs nothing.
#    (It does NOT mean you may reload an OLDER snapshot: the ledger below refuses that.)
gate = PairedBinaryGate.from_json(open("gate.json").read())
state = gate.update_many([(0, 1)] * 8, order.instance_ids[8:16])
print(state, round(gate.wealth, 2), "p =", round(gate.p_value, 4), "harm =", gate.harm_detected)

# 5. Per-task floor: only tasks the incumbent passed k/k are protected; flips need k-of-n
#    among the FIRST n_confirm attempts.  Gate on verdict == "PASS", never on `not blocked`.
corpus = ProtectedCorpus(k_stability=2, k_confirm=2, n_confirm=3)
for t in ("t1", "t2"):
    corpus.record_incumbent(t, True); corpus.record_incumbent(t, True)
corpus.record_candidate("t1", False)              # one failure -> pending, not a violation
v = corpus.check()
print(v.verdict, v.reason, "coverage:", v.coverage)   # INCOMPLETE: run the rest

# 6. Certificate into the hash-chained ledger; verify later.
ledger = Ledger("ledger.jsonl")                   # strict=True: no rewinds, no re-proposals
cert = ledger.append(Certificate.from_gate(
    "cand-001", round=2, gate=gate, alpha_spent=alpha, cumulative_alpha=sched.spent(),
    content_hash=ch, instance_ids=list(order.instance_ids),
    evidence_refs=["eval://day1", "eval://day2"], evaluator_id="sealed-eval-v1",
    verification_rung="execution", closure="human_on",
    note="post-acceptance canary continues"))
ledger.append_event("cand-001", "activated", {"activated": True, "activation_n": 50})
print(ledger.verify_chain())                      # (True, None, 'ok (2 records)')
anchor = ledger.head                              # store this OUTSIDE the file
print(ledger.funnel().as_dict()["alarms"])        # () -- alarms fire on dead/starving loops

# 7. Bench the policy you just used against greedy acceptance, honest and adversarial.
from acceptor import run_bench, run_adversarial
print(run_bench(regime="planted", n=40, lift=0.3, seeds=100).table())
print(run_adversarial(seeds=100).table())
```

## Labelled streams: benchmarking the gate instead of the agent

`bench` measures an acceptance *policy* over synthetic Bernoulli outcomes.
`streams` measures an acceptance *layer* over real candidate edits, so that a
gate which reads bytes (a lint, a scanner, a bundle evaluator, an LLM judge) can
be scored on the same axes as one that reads numbers.

The reason this is possible at all — and the reason an agent benchmark is not —
is that **an acceptor is a classifier over candidate edits, so its ground truth
can be planted**. The extreme case costs nothing:

```python
from acceptor import streams

c = streams.make_candidate(streams.SEEDS[0], "null", "comment-only")
assert streams.behaviour_identical(c.incumbent, c.candidate)   # a tie, by construction
print(streams.verify_label(c))       # (True, 'behaviour-identical under the reference matcher')
```

Four `null` variants (byte-identical, whitespace-only, key-reordered,
comment-only) are behaviour-identical to the incumbent under
`behaviour_key`, which removes exactly four things — comments, trailing
whitespace, runs of blank lines, frontmatter key order — and nothing else. Every
commit on that stream is therefore a false commit with no human in the loop.
This is the zero-stream self-calibration of 方案.md §3.3 L3 (Turnstile).

The other four families cost one authoring pass each: `regression` (a directive
that contradicts the artefact, an over-generalised step, a placeholder codified
as verified), `unsafe` (credential exfiltration, a destructive verb, a new
egress host, a dropped confirmation step), `tamper` (an edit that reaches the
evaluator: a scorer path, a results file, a best-ever snapshot, a helper that
writes the reward file) and `good` (a real defect repaired — here the
*incumbent* is the defective bundle).

Each family has an oracle that shares no code with its generator, and
`verify_label` runs both directions: the property must hold of the candidate and
must **not** already hold of the incumbent. `tests/test_streams.py` asserts that
for all 6 × 17 = 102 candidates.

Scoring any acceptor is one call:

```python
from acceptor import streams

class MyGate:
    name = "mine"
    def accept(self, candidate, incumbent, budget):
        if streams.behaviour_identical(candidate, incumbent):
            return streams.Verdict("reject", "no effect")      # free
        pairs = budget.draw_all()                              # costs evaluations
        return streams.Verdict("commit" if sum(c for _, c in pairs) > 20 else "reject")

res = streams.score(list(streams.reference_acceptors()) + [MyGate()], runs=50)
print(res.table())
```

Reported per acceptor: false-commit rate (from `null`), commit rate per harmful
family, missed-improvement rate (from `good`), evaluations per decision,
milliseconds per decision, and a Pareto layer over the four of those that are
minimised. Common random numbers: the paired outcomes for a candidate are drawn
once and every acceptor gets its own `Budget` over the same tuple, so a
difference between two rows is a difference between two policies.

**Honest scope.** The artefacts, the edits and the labels are real; the paired
outcomes are simulated from a declared per-family lift (`DEFAULT_LIFTS`), so a
statistical row is real *given* an effect model. `tamper`'s declared lift is
`+0.35` and its true lift is zero — the measurement moved because the
measurement was edited — which is exactly why every statistics-only policy
commits most of that family and only a mechanical check refuses. The
`EVALUATOR_SURFACE` those edits reach is Harbor's published verifier layout, so
the column measures coverage of a *known* contract rather than detection of an
unknown attack.

## Design notes

* **Validity does not depend on the betting rule**, only on the fraction being
  predictable and inside `[0, 1/|g_min|]` — so `fixed`, `mixture`, `ons`, `agrapa` all
  keep the α guarantee; they differ only in power. Measured power at n = 40
  (`--regime planted --k 0 --seeds 2000`), fixed / mixture / ons / agrapa:
  p_inc 0.5, lift +0.3 → **72.5 / 65.8 / 54.1 / 59.8 %**; p_inc 0.7, lift +0.2 →
  **43.5 / 43.2 / 29.5 / 33.1 %**.
* **A fix may delay a decision, never weaken a null.** `min_informative` refuses to
  accept on a handful of discordant pairs; that intersects the crossing event with
  another event, so the bound can only shrink. Nothing here "buys" power by loosening H0.
* **NSF** is a futility stop ("no sufficient funds"): the remaining budget cannot lift
  the wealth to 1/α even if every remaining pair is a win. Stopping early never inflates α.
* **Harm martingale** (`harm_alpha`) tests H0′ `E[d] ≥ harm_mu0` and can move an
  `ACCEPT` to `REJECT(harm_detected_after_decision)` — the post-acceptance canary.
  `harm_mu0` defaults to 0 and is *deliberately not* tied to the superiority margin
  `mu0`: anchored at `mu0`, an equal-quality candidate is flagged as harmful 48 % of the
  time at `mu0 = 0.1` and 99 % at `mu0 = 0.2`. `accepted_at` and `wealth_at_decision`
  survive the canary; `harm_detected_at` records the regression separately.
* **Instance selection is part of the null.** Feeding only instances the incumbent
  failed makes an equal candidate ACCEPT 100 % of the time; feeding only instances it
  passed makes the harm canary fire 100 % of the time. `summary()["selection_suspected"]`
  flags the all-pass / all-fail fingerprint on ≥ 10 pairs (a heuristic, not a proof).
* **Power on binary outcomes is low** (+0.2 lift at p_inc = 0.7 is detected ≈ 43 % of the
  time at n = 40); use `PairedBoundedGate` on denser per-task signals (steps, tokens,
  normalised scores) and keep accumulating evidence after acceptance.
* **The ledger is a receipt, not a security boundary.** Interior edits are detected;
  tail truncation and last-record rewriting are not, without an external anchor. One
  writer per path (a second writer raises `ConcurrentWrite`); a torn trailing line is
  reported, not raised. `cumulative_alpha()` sums the *maximum* α per candidate, so a
  later `alpha_spent=0.0` cannot walk the total back.
* No network, no LLM calls, no harness adapters here — those live in the adapter layer.

## Known limitations

* A fully malicious adapter can fabricate gate JSON and certificates outright; the
  defences assume the adapter uses this library and that the ledger is read by someone.
  They convert silent α inflation into a mechanical, logged refusal.
* `InstanceSampler` cannot make instances fresh to a proposer that has memorised the
  pool; it rotates, records exposure, and reports the stale fraction.
* `expected_false_block()` returns a prior-shaped number when the only incumbent runs
  are the ones that granted membership — `pass_prob_evidence()` says which tasks those
  are, and `require_evidence=True` raises instead.
* `min_informative` limits *fragility*, not overfitting: a proposer with enough
  hand-picked instances still satisfies it.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). `acceptor` is one of
the three packages in the [Precedent](../../README.md) monorepo; the methods it
implements are cited in NOTICE and in the module docstrings.
