# `paper/` — how to reproduce every number

[`every-correction-is-a-test.md`](every-correction-is-a-test.md) — *Every Correction Is
a Test: Human-Anchored Criterion Evolution for Self-Modifying Agents*.

This file is the audit trail. **Every quantitative claim in the paper appears below with
the command that produces it**, and each row says which of four kinds of number it is:

| kind | meaning |
|---|---|
| **deterministic** | the same command gives the same digits on any machine (seeded, offline) |
| **machine-dependent** | timings; deterministic in structure, not in digits |
| **single run** | measured once, on the author's machine, and not re-run — treat as an anecdote with a receipt |
| **corpus** | a reading of an external paper or repository, stored in `research/`; the URL is the citation, the file is the note |

Nothing in the paper is a number we could not produce this way. Where a number in the
paper differs from an older number in `README.md` or `方案.md`, the paper uses the value
that today's command prints and says so.

---

## 0. Setup (once)

```bash
git clone https://github.com/Linxiushen/precedent && cd precedent
./scripts/dev.sh --venvs          # three uv venvs, Python 3.12
```

Python **3.11+** (3.12 recommended). The system Python on macOS is 3.9 and will not do.
Everything below is standard library only, offline, and makes **zero model calls** unless
a row says otherwise.

---

## 1. Test totals and CI  (§5, §9)

| claim | command | kind |
|---|---|---|
| receipts **71** | `cd packages/receipts && ./.venv/bin/python -m pytest` | deterministic |
| acceptor **369** | `cd packages/acceptor && ./.venv/bin/python -m pytest` | deterministic |
| precedent **1057** | `cd packages/precedent && ./.venv/bin/python -m pytest` | deterministic |
| plugin **94** | `cd plugins/openclaw && npm install && npm test` | deterministic |
| **1,591 total** | all four of the above | deterministic |
| all of it, one command | `./scripts/dev.sh --tests` then the npm line | deterministic |
| CI runs the same suites on 3.11 and 3.12, plus zipapp / extras / wheels | `.github/workflows/ci.yml` | — |

> `pyproject.toml` already sets `addopts = "-q"`. Adding a second `-q` suppresses the
> `N passed` summary line — run `pytest` with no extra flags to see the totals.

Measured 2026-09-17: `71 passed in 2.33s`, `369 passed in 7.44s`, `1060 passed in
51.80s`, `ℹ tests 94 / ℹ pass 94 / ℹ fail 0`.

---

## 2. The acceptor benchmark  (§5a)

| claim | command | kind |
|---|---|---|
| the whole eleven-row table; 37,400 decisions, zero model calls | `cd packages/precedent && ./.venv/bin/python -m precedent bench --all --seeds 200` | deterministic |
| `greedy` **47.0 %** false-commit on `null` | same | deterministic |
| `evaluate-bundle` **100 %** on `null`; **0 %** on `unsafe` | same | deterministic |
| every statistics-only row commits **71–100 %** of `tamper` | same (`mcnemar` 93.1 %, `gate-ons` 71.4 %, `greedy` 100 %) | deterministic |
| `precedent` **0 %** on all four wrong families at **5.8** evals/decision | same | deterministic |
| `precedent` **52.2 %** missed improvements | same | deterministic |
| rung split — no-effect 24 %, evaluator-reach 24 %, mechanical 29 %, e-process 24 % (so **77 %** disposed of free) | same, last line of the footer; or `--json` and read `acceptor_detail.precedent.rungs` | deterministic |
| declared lifts (`null` +0.00 … `tamper` +0.35, `good` +0.25) | printed in the table header | deterministic |
| **102** candidates, each re-verified by an independent oracle, **0** failed | same footer; `--streams` shows each variant and its oracle | deterministic |
| `ms/dec` column, and the header's elapsed time | same | **machine-dependent** (M-series laptop, Python 3.12); the only non-portable digits in that table — re-running on 2026-09-17 moved `gate-fixed` from 0.036 to 0.034 ms/dec and nothing else |
| the 17 cases as a **Harbor family**, 175 files | `./.venv/bin/python -m precedent bench --emit-harbor ../../bench/harbor` ; `find bench/harbor -type f \| wc -l` | deterministic |
| the emitted `solve.sh` / `test.sh` really run | `cd packages/precedent && ./.venv/bin/python -m pytest tests/test_bench.py` | deterministic |

The seeds are fixed by `--seed0` (default 0); `--seeds 200` is the number of runs.
Changing either changes the digits — the paper quotes `--seeds 200 --seed0 0`.

---

## 3. Greedy acceptance, gate power, and the task floor  (§1, §3.5, §3.6)

```bash
python3 research/tools/gate_power_sim.py          # stdlib, seeded (random.seed(7))
```

| claim | where in the output | kind |
|---|---|---|
| greedy false-commits **0.44 / 0.45 / 0.46** at N = 20 / 40 / 80, zero true lift | `GREEDY 'keep if mean went up'` block | deterministic |
| gate power at p_inc 0.7: +0.10 → 0.11 (N=40) / 0.39 (N=160); +0.20 → 0.44 / 0.98; +0.30 → 0.98 at N=40, median 22 tasks | `incumbent pass rate 0.7` table | deterministic |
| per-task floor false-block on Bernoulli(0.6): k=1 → 0.93/1.00/1.00, k=2 → 0.44/0.70/0.91, k=3 → 0.13/0.24/0.43 (10/20/40 tasks) | `PER-TASK NEVER-REGRESS FLOOR` block | deterministic |

The policy-level harness gives the same phenomenon with the shipped gate rather than a
sketch:

```bash
cd packages/acceptor
./.venv/bin/python -m acceptor.bench --regime planted    --n 40 --lift 0.2 --seeds 200
./.venv/bin/python -m acceptor.bench --regime stochastic --n 40 --t 10   --seeds 200 --schedule
./.venv/bin/python -m acceptor.bench --regime adversarial --seeds 300
```

| claim | kind |
|---|---|
| greedy **45.8 %** false-commit (planted, n=40, 200 seeds) | deterministic |
| greedy **45.3 %** false-commit (stochastic) | deterministic |
| the five-attack table: naive 78.0–89.7 / 99.3 / 17.3–17.7 / 32.7–37.7 / 100.0 %, guarded 0.0 / 0.7–1.3 / 1.3–2.3 / 0.3–0.7 / 0.0 %, honest baseline 2.0–3.3 % | deterministic |

> **Provenance note.** `方案.md` §5 carried **44.6 %** for greedy through the
> v0.2.0 build, and README quoted it long after it had stopped being true;
> both now quote the current numbers, and this note is the record of what
> the older one said. Re-running the same commands today gives **45.8 %** (planted) and
> **45.3 %** (stochastic); `gate_power_sim.py` gives 0.44–0.46. The paper quotes today's
> numbers and the 0.44–0.46 range. The phenomenon — PACE's 30–42 %, reproduced — is the
> claim; the third digit is not.

CTHS constants (§3.3):

```bash
cd packages/acceptor && ./.venv/bin/python -c \
  "from acceptor.schedule import Z_CTHS, Z_NAIVE; print(Z_CTHS, Z_NAIVE, Z_CTHS/Z_NAIVE)"
# 3.387735532 2.0 1.693867766
```

---

## 4. The temporal birth gate  (§2)

| claim | where | kind |
|---|---|---|
| the formal statement (a)/(b)/(c), ε = 2 %, `PASS`/`FAIL`/`INSUFFICIENT` at < 3 eligible post-`t0` actions | `packages/precedent/src/precedent/compile.py`; `README.md` §*The temporal birth gate*; `packages/precedent/README.md` §*The TEMPORAL BIRTH GATE* | code |
| the gate's behaviour, on synthetic transcripts | `cd packages/precedent && ./.venv/bin/python -m pytest tests/test_compile.py tests/test_gate.py` | deterministic |
| the headless rule: v0 FAIL (2/2 vs 2/5) → temporal PASS (32/8164 = 0.4 %, pre-`t0` 33/945 reported) | `方案.md` §5; `DEMO.md` §2d | **single run** (author's machine) |
| the three Opus scopings — 4/8 = 50 % FAIL, 1/4 = 25 % FAIL, 0/2 INSUFFICIENT | `DEMO.md` §9 ¶4 (table) | **single run** |
| `Workflow` has no `model` field in any of its 29 recorded calls | `DEMO.md` §2b | **single run** |

To exercise the miner and the gate on a machine that is not the author's, point them at
your own transcripts — everything is read-only:

```bash
cd packages/precedent
./.venv/bin/python -m precedent mine --last 20 --state-dir /tmp/demo-state
./.venv/bin/python -m precedent compile <topic-id> --state-dir /tmp/demo-state
```

The shipped fixture home (`python3 scripts/make_fixture_home.py /tmp/demo-home`) is built
for `init` and `audit --share`, not for the miner: it carries **3 sessions, 4 human turns
and 0 corrections**, so `mine` against it correctly reports nothing and there is no topic
to compile. The gate's own behaviour — HIT, QUIET-AFTER, the pre-`t0` split, and the three
verdicts including `INSUFFICIENT` — is covered deterministically by
`tests/test_compile.py` and `tests/test_gate.py` on synthetic transcripts built under
`tmp_path`.

---

## 5. The single-machine case study  (§5b)

**All rows here are single runs on one machine (8 sessions, 2026-09-15/16).** They are
reproducible only in the sense that the commands are in the repo and the verbatim
transcripts are in [`DEMO.md`](../DEMO.md); on your machine they will print your numbers.

| claim | command / source | kind |
|---|---|---|
| 8 sessions; **61** artifacts (skill 44 / memory 10 / MEMORY.md 7); **42** never cited (78 %); 2 truncated; 2 stale index entries; 4 near-duplicates; 29 unattended writes in 7 days, 16 Bash heredocs | `precedent init` — `DEMO.md` §1 | single run |
| 164 human turns (119 expansions skipped) → **20** corrections (12.2 %) → **18** topics, **1** repeated, 4 written-but-violated, 1 question filtered | `precedent mine` — `DEMO.md` §1 | single run |
| **68** interruptions from the unscoped headless rule across **17,842** recorded calls (0.38 %); 53 from the force-confirmed opus rule; 121 total | the corpus sweep in `DEMO.md` §4g (`replay_hook.py`, scratchpad) | single run |
| the scoped replacement passes at **0/140** | `DEMO.md` §9 ¶1 | single run |
| live deny in **0.48 ms**; 6 hook invocations, 1 fire; funnel 8 → 1 → 1 → 1 | `DEMO.md` §10, hook-log line quoted verbatim | single run |
| live demo spend **$0.05**; cumulative `claude -p` spend **$0.16** | `DEMO.md` §10 | single run |
| `loop --dry-run` 3.4 s / $0.00; `improve` 4 drafts for **$0.1863**, 4 × HOLD; `examine` **$0.6892**, verdict `NSF`, 2 pairs (2 ties, 0 discordant) | `packages/precedent/README.md` §*Measured on this machine* | single run |
| `~/.claude` byte-identical before and after (1,625 files fingerprinted, `settings.json` sha256 unchanged) | `DEMO.md` §7 | single run |
| the deny → allow → uninstall chain, on a synthetic home, as a test | `cd packages/precedent && ./.venv/bin/python -m pytest tests/test_integration_enforce.py` | **deterministic** |
| hook latency budget (< 300 ms end to end, each of the six, interpreter start included) | `cd packages/precedent && ./.venv/bin/python -m pytest tests/test_hooks.py tests/test_hooks_suite.py` | deterministic |

The numbers in `README.md`'s first-screen example (60 artifacts / 41 never cited) and the
numbers here (61 / 42) are two runs of the same command on the same machine a day apart —
the transcripts are live and grow. The paper quotes the `DEMO.md` run and dates it.

---

## 6. Bundle-gate calibration  (§5c)

| claim | command | kind |
|---|---|---|
| **16 pass / 28 revise / 0 block** over the 44 skills installed on the author's machine, zero false criticals | `plugins/openclaw/README.md` §*Calibrated against real work*; the property is pinned by a test in `cd plugins/openclaw && npm test` | **single run** (the corpus is one machine's skills); the *no-false-critical* property is deterministic |
| the grader itself | `cd packages/precedent && ./.venv/bin/python -m precedent evaluate-bundle --help` | — |
| the fail-closed contract (a throwing evaluator silently approves, so the plugin never throws) | `cd plugins/openclaw && npm test` — the eleven `failure:` / `fail-closed:` / `failClosed` `describe` blocks in `index.test.ts` and `test/adversarial.test.ts` | deterministic |

To re-calibrate on your own skills, copy them read-only into a temp dir and grade each
one; do not point the grader at a live Claude home.

---

## 7. The truncation bug  (§6)

| claim | source | kind |
|---|---|---|
| `require_regex` reported **4/4 = 100 %** tolerated fires | `DEMO.md` §9 ¶3 | single run |
| the two compliant calls carried `model: 'opus'` at offsets **5,778** and **5,659** of scripts **19,739** and **16,714** characters long | `DEMO.md` §9 ¶3 | single run |
| the subject cap is **4,096** characters | `packages/precedent/src/precedent/rules.py` (and the byte-identical hook copy `_hooklib.py`) | code |
| after the fix, **4/4 → 1/4** | `DEMO.md` §9 ¶3 | single run |
| the bug itself, as a named regression test carrying the real offsets | `cd packages/precedent && ./.venv/bin/python -m pytest tests/test_rules.py -k subject_cap -v` → `test_require_regex_does_not_deny_when_the_match_is_past_the_subject_cap` | **deterministic** |
| an inverted matcher fails **open** on all three ways of not knowing (truncated subject, quarantined pattern, non-text subject) | `cd packages/precedent && ./.venv/bin/python -m pytest tests/test_rules.py` | deterministic |
| the hook runs the *same* code the tests import — `_hooklib.py` is copied byte-for-byte to `<state>/hooks/_plib.py` | `cd packages/precedent && ./.venv/bin/python -m pytest tests/test_enforce_adversarial.py -k plib_is_hooklib` | deterministic |

The bug is an anecdote; the regression tests that keep it fixed are not.

---

## 8. Literature numbers  (§1, §8)

Every external result is a record in `research/`. The corpus is **not peer-reviewed**: each
entry is an agent's structured reading of a paper, repository or issue thread, carrying a
URL back to the original. **Cite the URL, not us.** Recipe:

```bash
python3 - <<'EOF'
import json
d = json.load(open("research/rsi.json"))          # or research/landscape.json
for r in d["deep_reads"]:
    if "PACE" in r["name"]:                        # swap in any name below
        print(r["name"], r["url"], sep="\n")
        print(r["key_results"])
        print(r["limitations"])
EOF
```

| paper claim | record | field |
|---|---|---|
| PACE 2606.08106: greedy 42 % false / 33 % harmful (1.5B), 30 % / 10 % (3B); stochastic 82 / 72 / 100 % false; ~18 % fewer paired problems | `rsi.json` → `PACE…` | `key_results` |
| PACE's own scope limits (per-candidate guarantee, 0.5B–3B, prompt edits only, one proposer) | same | `limitations` |
| SEA 2607.00871: anytime-valid certificates; the gate accepted 0 edits in the headline config | `rsi.json` → `SEA…` | `key_results`, `limitations` |
| HarnessDev 2609.01437: 34/64 agreement (53.1 %), 8 regress on both, 27 inside ±4.75, 2/9 finals held-out-optimal, standalone-verifier edits **0**, 0 checkpoint events in 26,679 trajectories | `rsi.json` → `HarnessDev…` | `key_results` |
| RSI-Exam: visible→hidden **−11.7 % to −24.7 %** across 9 models, 37 tasks | `rsi.json` → `RSI-Exam 0.1…` | `key_results` |
| CTA 2605.11946: **+0.3 pp** mean, median 0, 45/49 tasks exactly 0; repo audit −0.7 pp, 95 % CI [−3.9, +2.5] | `rsi.json` → `Counterfactual Trace Auditing…` | `key_results` |
| SkillsBench: LLM-authored **no measurable gain**, human-authored **+16.2** | `rsi.json` → `Recursive Self-Improvement in AI (survey 2607.07663)` | `key_results` |
| Demystifying Agent Skills 2608.14036: retrieval precision **29.6 % → 3.3 %** at 100 skills | `grep -o 2608.14036 research/rsi.json` | corpus |
| AI2 2607.12227: harness evolution 67.4 vs initial 68.2 vs parallel sampling 72.3; held-out **+0.6** | `rsi.json` → `Rethinking the Evaluation…` | `key_results` |
| The Replay Gap 2608.08239: 74–77 % of early swaps diverge at the first post-fork action; replay validity **3.2–8.0 %**; the stitching evaluator mispredicts all 5 outcomes | `rsi.json` → `The Replay Gap…` | `key_results` |
| SkillMisevo / SafeEvolve 2608.12851: **21/21** evolved configs author unsafe artifacts; pooled C-ASR 16.0 % → 41.3 % | `rsi.json` → `SkillMisevo-Gym…` | `key_results` |
| MLAS 2606.23075: Hermes background review **40/40 persisted**; hub scanner 1/40 blocked; OpenClaw human queue **40/40 blocked** | `rsi.json` → `Safety in Self-Evolving LLM Agent Systems…` | `key_results` |
| EvoHarnessBench 2609.04280: BWT **−5.3 % / −4.0 % / −34.7 %** (tools / skills / agents) | `rsi.json` → `EvoHarnessBench…` | `key_results` |
| Lin et al. 2605.30621: update quality flat 9B→frontier; benefit depends on the consumer; phase adherence .89→.80 (Opus) vs .52→.13 (Qwen3-32B) | `rsi.json` → `Harness Updating Is Not Harness Benefit…` | `key_results` |
| RSI survey 2607.07663: 1,250 papers; the verification hierarchy | `rsi.json` → `Recursive Self-Improvement in AI…` | `key_results` |
| MetaRSI 2609.06396: 69 % of RSI systems close only on machine-checkable targets | `rsi.json` → `MetaRSI-v1 / RSI²…` | `key_results` |
| Mendel Gödel Machine 2608.07645 and the DGM/HGM lineage | `rsi.json`, `landscape.json` | — |
| **All five 2026 surveys list the same four open problems and none ships code** | `research/synthesis_v1.md` §C, first bullet | synthesis |
| **Five independent 2026 results converge on ≈0 causal effect for LLM-written artifacts** (CTA, SkillsBench, Continual Harness 6.4 % reuse, Demystifying Agent Skills, SkillOpt) | `research/synthesis_v1.md` §C, third bullet | synthesis |
| PROCTOR: *mechanical rejection overrides LLM approval, never the reverse* | `research/gap_analyses_all.json` (safety lens); `research/synthesis_v2_rsi.md` | corpus |
| starvation in production: 241 staged writes over 8 weeks; 41 background forks, 0 updates | `grep -c 241 research/gap_analyses_all.json` (practitioner lens, ~120 issues across 14 repositories mined via `gh api` on 2026-09-14); indexed in `research/README.md` | corpus |

Local replayability measurement quoted in §8:

```bash
python3 research/tools/cassette_extract.py    # reads ~/.claude/projects/*/*.jsonl, read-only
```

The paper's figures (2,062 tool calls; 50.4 % workspace write, 19.8 % external, 16.5 %
idempotent read, 13.2 % non-deterministic; idempotent prefix 0–1) are **a single run on
the author's machine**. On your machine it prints yours.

---

## 9. Everything else quoted in the paper

| claim | command | kind |
|---|---|---|
| the share card, both languages, from a synthetic tree | `./scripts/dev.sh --demo` | deterministic |
| the card refuses to print if anything scrub would mask survives (exit 2, empty stdout) | `cd packages/precedent && ./.venv/bin/python -m pytest tests/test_share.py` | deterministic |
| **16 planted secrets** — credential, AWS key, two phone formats, email, WeChat id, home path, Unix account, a project *and* a skill named after a customer, two session uuids, a git remote in both syntaxes — reach no `--share` output in either language, and `--verify` refuses rather than prints when each is forced back in (including only-in-Chinese and only-in-JSON) | `python3 scripts/make_fixture_home.py /tmp/hostile --hostile` ; `cd packages/precedent && ./.venv/bin/python -m pytest tests/test_share_adversarial.py` | deterministic |
| the receipt line's `23 checks` does not move with the machine (the per-secret literal tests are excluded for exactly that reason) | `./.venv/bin/python -c "from precedent.share import verify_checks; print(verify_checks())"` | deterministic |
| the zipapp is deterministic and runs with nothing installed | `python3 scripts/build_zipapp.py --check` ; `./scripts/dev.sh --zipapp` | deterministic |
| …and a decoy `receipts.py` first on `PYTHONPATH` does **not** shadow it: every module resolves inside the archive, and the decoy (which exits non-zero and writes a marker if imported) is never reached | `./scripts/dev.sh --zipapp` (second half) ; `cd packages/precedent && ./.venv/bin/python -m pytest tests/test_zipapp.py` | deterministic |
| the six hooks, their events and timeouts | `cd packages/precedent && ./.venv/bin/python -m precedent hooks install claude-code` (dry run, the default) | deterministic |
| the four starvation alarms | `packages/precedent/README.md` §`precedent report`; `cd packages/precedent && ./.venv/bin/python -m pytest tests/test_audit.py tests/test_docket.py` | deterministic |
| what each benchmark variant plants, and its oracle | `cd packages/precedent && ./.venv/bin/python -m precedent bench --streams` | deterministic |
| the acceptance stack's four rungs, in order | `packages/precedent/src/precedent/bench.py`, `PrecedentAcceptor.accept` | code |

---

## 10. Standing caveats

1. **`~/.claude` is read-only to everything here except `hooks install --apply` /
   `hooks uninstall --apply`**, which you run by hand. Every command in this file is safe
   to run against a real machine; the two that are not are named.
2. **No number in the paper was produced by a model.** The two commands that spend money
   (`precedent improve`, `precedent examine`) appear only in §5b, are labelled single
   run, and carry their dollar cost.
3. **The case study is N = 1.** One user, one machine, 8 sessions, 1 rule enforced.
   Section 7 of the paper says this; this file repeats it because a table of commands can
   otherwise look like a table of evidence.
4. **The benchmark's outcomes are simulated** from a declared per-family lift printed in
   its own header. The artefacts, edits, labels and what each acceptor can see are real.
5. If a command above does not reproduce a number, that is a bug in this repository.
   Please open an issue with the command and the output.

---

## Licence

Apache-2.0. See [LICENSE](../LICENSE) and [NOTICE](../NOTICE).
