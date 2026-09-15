# `research/` — the evidence base

Every number in [`方案.md`](../方案.md) and in the package READMEs can be
checked against a file in this directory. It is the raw output of a four-day
survey run in September 2026: **20 discovery scouts, 96 deep reads, 6 narrative
sweeps, 4 gap analyses from different professional lenses, 6 independent
designs and 13 reviews**, plus two standalone simulation tools.

This is published as-is, for two reasons. The first is that the project's
central claim — *the acceptor is the weakest link in the field and there is no
runnable open-source implementation of one* — is a negative claim about a whole
literature, and a negative claim is only worth as much as the search behind it.
The second is that these files record the moments the project's own hypotheses
were **wrong**, which is the part a summary would quietly drop
(`synthesis_v1.md` §A is a list of them).

**Nothing here is a peer-reviewed source.** Each record is an agent's structured
reading of a paper, repository or issue thread, carrying a URL back to the
original. Treat the URLs as the citation and these files as notes on them.

**Privacy.** These files have been scrubbed of absolute personal paths and
e-mail addresses. Author names, institutions, repository URLs and arXiv ids are
kept — they are the citation. See *Provenance and scrubbing* at the end.

---

## Index

Reading order for a newcomer: `synthesis_v2_rsi.md` → `landscape_digest.md` →
`gap_analyses_all.json` → `design_results.json`.

### Syntheses — the argument, in order, including its corrections

| file | what | how | date |
|---|---|---|---|
| [`synthesis_v0.md`](synthesis_v0.md) (9 KB) | The first independent synthesis: the **three-camp** framing (production accumulators / research optimizers / weight-level self-play) and the first statement of the acceptance problem. Explicitly labelled *hypotheses for the design phase to challenge, not conclusions*. | Written by the orchestrator after personally reading ~25 deep reads (HyperAgents, Hermes, Prime, AHE, Meta-Harness, SkillOpt, DGM, HGM, RQGM, GEPA, ACE, AZR …). | 2026-09-11 |
| [`synthesis_v1.md`](synthesis_v1.md) (15 KB) | **Supersedes v0.** Section A is the honest record of what v0 got wrong: "nobody does sequential testing" is false as *invention* (PACE, SEA exist) and true only as *adoption*; "nobody does counterfactual replay" is false; The Replay Gap is a direct threat to the cassette idea. The surviving contribution is re-scoped to *integration + extension*. | Written after the scientist and safety gap analyses plus a critic pass, with external verification of prior art the earlier synthesis had not cited. | 2026-09-14 |
| [`synthesis_v2_rsi.md`](synthesis_v2_rsi.md) (29 KB) | **The current argument.** What "RSI" means at three altitudes in Sept 2026; the substrate-seam map (which host exposes which acceptance hook, and whether anyone has implemented it); the hard design rules the RSI literature imposes (the verification hierarchy; PROCTOR's *mechanical rejection overrides LLM approval*); the refined thesis. | Written after the RSI sweep (6 narratives + 32 deep reads) on top of v1. | 2026-09-14 |

### Landscape — what exists, camp by camp

| file | what | how | date |
|---|---|---|---|
| [`landscape.json`](landscape.json) (1.0 MB) | The primary corpus: `index[]` **521 discovered items** (name, URL, kind, stars, one-liner), `deep_reads[]` **64 structured readings** (mechanism, eval loop, fitness/acceptance, key results, limitations, safety, open gaps), `scout_notes[]` 20 scout narratives, and a `critic` block on coverage and what the taxonomy missed. | 20 discovery agents over GitHub, arXiv, issue trackers and docs, then 64 deep-read agents each given one item and a fixed schema. | 2026-09-11 |
| [`landscape_digest.md`](landscape_digest.md) (176 KB) | The human-readable rendering of the above: 58 deep-read projects ranked by impact + novelty, each with mechanism, fitness/acceptance, key insight and limitations. **Read this rather than the JSON.** | Rendered from `landscape.json`. | 2026-09-11 |
| [`deep_reads_partial.json`](deep_reads_partial.json) (564 KB) | An earlier **57-item** snapshot of the deep-read stream, taken while the sweep was still running. Kept because it is the checkpoint the first synthesis was written against — superseded by `landscape.json.deep_reads`, and useful only for auditing what was known when. | Partial write-out of the same agents. | 2026-09-11 |

### The RSI wave — the September 2026 discourse, and the acceptance prior art

| file | what | how | date |
|---|---|---|---|
| [`rsi.json`](rsi.json) (840 KB) | `narratives[]` **6 scout narratives** on what RSI means now (model-level / harness-level / open-ended, and the benchmarks that split along those lines), `deep_reads[]` **32 readings** of the acceptance, replay and safety prior art (PACE, SEA, Causal Agent Replay, The Replay Gap, CTA, SkillMisevo, MLAS, EvoHarnessBench, HarnessDev, RewardHackingAgents, EvoUndo …), `items[]` 227 indexed items. This is where the statistical machinery in `packages/acceptor` comes from. | 6 narrative scouts + 32 deep-read agents, run as a second targeted sweep after the landscape showed acceptance was the thin spot. | 2026-09-14 |
| [`rsi_digest.md`](rsi_digest.md) (275 KB) | The human-readable rendering: the six narratives in full, then each deep read with mechanism, results and limitations. **Read this rather than the JSON.** | Rendered from `rsi.json`. | 2026-09-14 |

### Gap analyses — the same corpus through four professional lenses

| file | what | how | date |
|---|---|---|---|
| [`gap_analyses_all.json`](gap_analyses_all.json) (207 KB) | Four independent analyses, each with `hypothesis_verdicts[]` (every claim in the synthesis marked supported / refuted / unverifiable, with the evidence), `gaps[]` and `surprising_observations[]`. The lenses: **scientific** (is this a falsifiable research problem or engineering?), **safety/verification** (where does each system's `accept()` fail, which documented incident shows it, what minimal machinery would have prevented it), **ecosystem/standards** (protocols, seams, registries, shared formats, benchmarks), **practitioner/adoption** ("would I install this tomorrow, and would it survive a week without being switched off?"). | Four agents, each given the full digests and prior syntheses plus its own external verification pass. The practitioner lens mined ~120 real issues across 14 repositories via `gh api` on 2026-09-14 — that lens is where the *users say "version control", not "RSI"* finding comes from. | 2026-09-14 |
| [`gap_analyses_partial.json`](gap_analyses_partial.json) (91 KB) | The first **two** lenses (scientific, safety), written out before the other two finished. Superseded by `gap_analyses_all.json`; kept as the checkpoint `synthesis_v1.md` was written against. | Same agents, partial write-out. | 2026-09-14 |

### Designs — six independent proposals, then thirteen reviews

| file | what | how | date |
|---|---|---|---|
| [`design_results.json`](design_results.json) (724 KB) | `designs{}` **six complete independent designs** — `harness-owns-the-test` → **Hermetic** (a sealed-evaluator runtime with score receipts), `acceptor-benchmark` → **Turnstile** (judge the gate, not the agent), `ci-for-rsi` → **Pawl** (CI for the state your agent writes to itself), `artifact-lifecycle` → **Clade** (a package manager for what your agent learns), `rsi-substrate` → **Meristem** (a ~2k-line API-only reference loop), `wildcard` → **Precedent** (every correction becomes a test). Plus `judgments{}` **13 reviews**, each scoring one design and naming its fatal flaw. The shipped project is the wildcard, with the zero-flow self-calibration idea taken from Turnstile, the per-artifact environment assertions from Clade, and the sealed-receipt discipline from Hermetic. | Six design agents given the same brief and corpus but different stances, run without seeing each other; then reviewers scored each design cold. The reviewers' unanimous verdict — *every design is 3–5× what 1–3 people can build* — is what produced the scope discipline in 方案.md §4. | 2026-09-15 |
| [`designs_partial.json`](designs_partial.json) (210 KB) | The first **three** designs (Hermetic, Turnstile, Pawl) as written out mid-run, each with thesis, architecture, data formats, integration surface and a two-week MVP. Superseded by `design_results.json`; kept because the reviews cite it by name. | Same agents, partial write-out. | 2026-09-14 |

### Tools — two standalone simulations, runnable today

| file | what | how | date |
|---|---|---|---|
| [`tools/cassette_extract.py`](tools/cassette_extract.py) (6 KB) | Converts Claude Code transcripts into normalised "cassettes" (ordered user / assistant / tool_use / tool_result turns) and classifies every tool call by **replayability**: `IDEMPOTENT_READ`, `WORKSPACE_WRITE`, `EXTERNAL`, `NONDETERMINISTIC`. This is the measurement behind the decision to go *sandbox-first* instead of pure VCR replay: on 2,062 real tool calls, 50 % were workspace writes, 20 % external and 13 % non-deterministic, with an idempotent prefix of 0–1. | stdlib Python 3, reads `~/.claude/projects/*/*.jsonl`, read-only. Run: `python3 research/tools/cassette_extract.py`. | 2026-09-14 |
| [`tools/gate_power_sim.py`](tools/gate_power_sim.py) (3 KB) | Monte-Carlo power of a PACE-style paired anytime-valid gate on binary outcomes: commit rate and median tasks-to-commit by true lift, plus the false-alarm rate of a per-task never-regress floor at 1 vs 2 repeats. This is where the honest statement *"~40 paired tasks only reliably detect +30 pp; +10 pp needs 160+"* comes from, and why the floor requires k-of-n confirmation. | stdlib Python 3, seeded (`random.seed(7)`), no inputs. Run: `python3 research/tools/gate_power_sim.py`. | 2026-09-14 |

---

## How to read the JSON

The digests (`landscape_digest.md`, `rsi_digest.md`) are the rendered form of
the two big corpora and are much easier to read. When you do need the JSON:

```bash
python3 -m json.tool research/landscape.json | less           # pretty-print
python3 -c "import json;d=json.load(open('research/landscape.json'));print(len(d['index']),len(d['deep_reads']))"

# every deep read that names a specific paper
python3 - <<'EOF'
import json
d = json.load(open("research/rsi.json"))
for r in d["deep_reads"]:
    if "2606.08106" in json.dumps(r):        # PACE
        print(r["name"], "|", r["url"])
        print(r["eval_loop"][:400])
EOF
```

Common fields across `deep_reads[]` in both corpora: `name`, `url`, `kind`,
`org_or_authors`, `date`, `last_activity`, `stars`, `venue_or_citations`,
`what_evolves`, `mechanism`, `eval_loop`, `key_results`, `limitations`,
`safety`, `open_gaps`.

## What the corpus is used for in the shipped code

| finding, and where it lives here | what it became |
|---|---|
| Greedy acceptance false-commits 30–42 % (PACE, `rsi.json`) — reproduced at 44.6 % | `packages/acceptor`: paired e-process gates, and the benchmark that measures the difference |
| SEA's CTHS spend schedule, Z = 3.3877 (`rsi.json`) | `acceptor/schedule.py`, numerically verified (the naive Z = 2 over-spends 1.69×) |
| PROCTOR: *mechanical rejection overrides LLM approval, never the reverse* (`gap_analyses_all.json`, safety lens) | The ordering of every gate in `precedent`, and rule 4 in CONTRIBUTING.md |
| The Replay Gap: only 3–8 % of replayed states stay valid after an intervention (`rsi.json`) | Cassettes are a regression detector, not a fitness function; sandbox-first replay; `tools/cassette_extract.py` measured the local version of this |
| Judges are more convincing, not more correct (`rsi.json`) | An LLM may veto, never approve; `--llm` output is schema-validated then gated |
| Users say *version control*, not *RSI* (`gap_analyses_all.json`, practitioner lens) | `snapshot` / `undo` / receipts / the spend meter are the front door, not the statistics |
| Gates die of **starvation**, not strictness — 241 staged writes over 8 weeks with nobody told (`gap_analyses_all.json`) | *Silent on pass, loud on block, never silent on starvation*: the four alarms in `precedent report` and the 7-day docket ageing |
| A 2:17am background fork rewrote a user-verified skill with a Bash heredoc, straight past the memory tool's gate (`gap_analyses_all.json`) | Write permission enforced **by path**, not by tool: the ownership pass in `_hooklib.py` |
| LLM-written skills: 0 benefit; human-written: +16.2 (SkillsBench, `rsi.json`) | The improver's output is a draft, always gated, never applied |
| Statistical power: ~40 pairs detects +30 pp, not +10 pp (`tools/gate_power_sim.py`) | `NSF` is a first-class printed result; regressions are certified fast, improvements slowly |

---

## Provenance and scrubbing

Generated 2026-09-11 → 2026-09-15 by agent sweeps run from Claude Code, on
public sources: arXiv, GitHub (repositories, issues and discussions via
`gh api`), official documentation, and web search. Where a source was
unreachable from the sandbox the record says so — e.g. the ecosystem lens notes
that arxiv.org was unreachable and that three abstracts are cited from search
snippets rather than full text. Star counts, issue counts and "last activity"
are as of the date in each table above and will be stale.

Before publication these files were scrubbed. Changed:

| file | what was removed | count |
|---|---|---|
| `design_results.json` | `file:///Users/<name>/…` absolute URLs pointing at a sibling design file, rewritten to the repo-relative path `research/designs_partial.json` | 2 |
| `rsi.json` | an absolute path to the author's local scratchpad (containing the username and a session id), replaced with `<local scratchpad>` | 1 |
| `rsi.json` | three paper-author e-mail addresses, replaced with the authors' names and institutions alone | 3 |
| `rsi_digest.md` | one paper-author e-mail address, same treatment | 1 |

No other absolute personal path, e-mail address, credential or machine
identifier was found. Author names, institutions, repository URLs, arXiv ids
and issue numbers were deliberately **kept**: they are the attribution.

Licensed under Apache-2.0 with the rest of the repository; see [`../NOTICE`](../NOTICE)
for how that applies to summaries of third-party work.
