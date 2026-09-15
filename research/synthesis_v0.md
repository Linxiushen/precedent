# Orchestrator's independent synthesis (v0, 2026-09-11)

Written after personally reading ~25 deep-reads (HyperAgents, Hermes, Prime Agent, AHE, Meta-Harness, SkillOpt, CORAL, SIA, EvoMap, SkillClaw, Reef, OpenRSI, ECC, Dr. Zero, OpenViking, DGM, HGM, RQGM, Misevolve, Self-Harness, Lilian Weng's essay, GenericAgent, GEPA, ACE, AZR). These are HYPOTHESES for the design phase to challenge, not conclusions.

## 1. The field has split into three camps that do not talk to each other

**Camp A — "Production accumulators"** (Hermes 244k★, ECC 256k★, OpenViking 37k★, Prime Agent 20k★, GenericAgent 14k★, EvoMap 9k★, SkillClaw 2.6k★).
Evolve skills / memory / prompt-notes from real usage, inside real harnesses (Claude Code, OpenClaw, Codex, pi). Massive adoption.
**Zero outcome-based fitness.** Acceptance = the refiner LLM's own judgment. Consent, ledgers and rollback substitute for fitness.
Documented consequences: Hermes' learning loop silently broken for weeks in production (41 background forks, 0 skill updates, issue #95976); Prime Agent persisted a Factorio reward-hack exploit as a reusable skill; ECC's confidence update rules are documented but unimplemented; EvoMap's "fitness" is a constant 0.85/0.2; OpenViking's held-out delta is reported but never gates; SkillClaw ships with verifier OFF.

**Camp B — "Research optimizers"** (DGM, HGM, HyperAgents, RQGM, Meta-Harness, Self-Harness, AHE, SkillOpt, SIA, OpenRSI, CORAL, GEPA).
Real benchmark fitness, archives/populations, gates. Scientifically serious.
But: $500–$5,000 per run, hours–days wall-clock, need a benchmark with a programmatic verifier, discard everything learned between runs, and are **unusable by application developers**. Also systematically weak on statistics: "any positive delta over 2 repeats" is accepted (Meta-Harness, Self-Harness, AHE), search set == eval set (AHE, Meta-Harness TB2), test-set contents leak into the mutator (HyperAgents #39, HGM diagnosis sees private test patch), per-task regressions hidden by aggregate means (SkillOpt, Reef #356).

**Camp C — weight-level self-play** (AZR, Agent0, R-Zero, Dr. Zero, AgentEvolver, OpenRSI-ERL). Needs multi-GPU RL. Out of scope for a harness framework, but their "hard-but-solvable band" curriculum idea (Dr. Zero k=1/5) is transferable.

## 2. The single convergent unsolved problem: THE ACCEPTANCE PROBLEM

Across all ~25 projects the #1 open gap is identical: *how do you decide — cheaply, statistically honestly, safely, using the agent's own real data — whether a proposed self-modification is actually an improvement?*

Nobody has, in one system:
1. **Counterfactual attribution.** "Skill was read" ≠ "skill helped" (Hermes, SkillClaw, ECC, OpenViking all conflate these). The Continual Harness paper found most memory entries are never referenced and subagent specs reused 6.4%.
2. **A noise model + sequential test.** Every Camp-B system promotes on any positive delta. TB2 with 2 trials has ±1–2pp noise; +0.6pp is treated as signal (AHE).
3. **A protected per-task regression floor.** Aggregate-mean gates let an edit break one task while improving none (SkillOpt real-user report; Reef admits it in #356).
4. **Safety inside the gate.** Misevolve shows utility-only acceptance silently trades away safety in every component (memory alone drops refusal 99.4%→54.4%); post-hoc patches recover only part. No framework has a safety regression suite in its acceptance criterion.
5. **Loop-health telemetry.** Nothing notices when learning silently stops (Hermes) or when instincts are produced at zero yield (ECC).
6. **Evaluator-Goodhart detection.** SkillOpt's optimizer copied judge regexes verbatim into the skill. RQGM's anchor-gated evaluator replacement is the only principled answer and its code is an empty stub.
7. **Cross-instance merge with verification.** Hermes Collective Wisdom, SkillClaw, EvoMap Hub all share by file copy; hallucinated notes become team doctrine (CORAL #67/#124).

## 3. Secondary convergent gaps
- **No common schema for the harness genome.** Lilian Weng explicitly asks for one. AHE's 7 components, Self-Harness' hook menu, Prime's 4 entry kinds, Reef's composition tree, Hermes' SKILL.md, ECC's instincts — all bespoke. No "editable-surface manifest" + evolution-event ledger format exists.
- **No cross-run / cross-task transfer of learned mechanisms** (Meta-Harness, CORAL, HGM, HyperAgents, SkillOpt discard per run).
- **Cost.** No cheap proxy evaluation, staged eval, or bandit allocation; every candidate = full benchmark.
- **Rejected edits are logged but never used** (only SkillOpt feeds them back).
- **No benchmark for self-evolution loops themselves** — improvement rate, transfer, safety decay, cost (Reef #357/#358 open). Lin et al. 2026: harness-updating ability is flat across model sizes; harness-benefit is non-monotonic.

## 4. A candidate technical wedge: trace-cassette counterfactual replay
The reason Camp A has no fitness is that re-running real sessions is expensive and non-deterministic. But every real session already contains the tool outputs. If the harness records sessions as *cassettes* (VCR-style: prompt → LLM response → tool call → recorded tool result), then a proposed harness edit can be evaluated by replaying the cassette with the edit applied: only the LLM calls are re-issued; tool results are served from the recording while the action sequence matches, and divergence is detected the moment the agent chooses a different action. That gives a cheap counterfactual (with-edit vs without-edit on the same real task), a natural protected regression corpus (past successes), and a divergence signal that is itself informative. SkillClaw does single-turn text replay only; nobody does full tool-trajectory replay for gating.

## 5. What "influential" should mean here
(a) A 5-minute demo any Claude Code / Hermes / OpenClaw user can run that shows "N accepted, M rejected (would have regressed on your own past tasks), K blocked (safety)"; (b) a paper-worthy contribution (counterfactual replay gating + sequential acceptance + safety-in-the-loop + loop-health); (c) a schema others adopt.

## 6. Candidate directions for the design phase (each designer takes one stance; judges decide)
1. Acceptance layer / "CI for self-evolution" — harness-agnostic gate middleware (above).
2. Harness-genome standard + ledger — the schema Lilian Weng asks for; plugs into Claude Code / Hermes / OpenClaw / Prime.
3. Self-evolution gym — measure improvement rate, transfer, safety decay, cost of any loop.
4. Federated verified evolution — cross-instance merge with local verification and provenance.
5. Cheap population search for app developers — archive + crossover + bandit eval on API models, no GPU, no benchmark required.
6. Minimal-seed crystallization with real selection pressure — GenericAgent/Hermes-style UX with an actual fitness signal.

## 7. Build-phase findings (orchestrator, 2026-09-11 20:45)
- Local Claude Code transcripts (`~/.claude/projects/<proj>/<session>.jsonl`) contain complete `tool_use` (id/name/input) ↔ `tool_result` (content) + structured `toolUseResult` pairs, plus `usage`, `model`, `cwd`, `gitBranch`, `permissionMode`. 8 sessions / ~2,000 tool calls exist on this machine today. This IS the cassette raw material — no new instrumentation needed to start.
- Claude Code hooks (docs, 2026-09): `PreToolUse` → `updatedInput` / `permissionDecision`; `PostToolUse` → **`updatedToolResponse`** (substitute what the model sees); `UserPromptSubmit` → `updatedInput`/`additionalContext`; `Stop` → receives `tool_calls`; `SessionStart` → `additionalContext`; `InstructionsLoaded`, `ConfigChange`, `PostToolBatch`. Hook types: command / http / prompt / agent / mcp_tool. Plugin hooks in `hooks/hooks.json`; skill-scoped hooks in SKILL.md frontmatter.
- ⇒ Counterfactual replay can run INSIDE Claude Code: `claude -p "<original prompt>"` headless, with a candidate harness (CLAUDE.md / skills / hooks) applied, and a replay hook pair that (a) PreToolUse: rewrites the call to a no-op (e.g. Bash → `true`, Edit/Write → deny-with-reason) and (b) PostToolUse: returns the recorded `tool_result` via `updatedToolResponse` when the call matches the cassette's next expected call; on mismatch → divergence event (record it, optionally fall back to sandboxed real execution or stop). Cost per counterfactual ≈ one LLM run; zero side effects.
- **VERIFIED 2026-09-11 20:50**: replay-through-hooks works end to end on Claude Code 2.1.258. `claude -p "<prompt>" --settings '<hooks json>' --tools Bash --no-session-persistence --output-format json < /dev/null`; PreToolUse hook rewrites `Bash.command` → `true # REPLAY-NOOP` via `updatedInput`; PostToolUse hook returns the cassette's recorded result via **`updatedToolOutput`** (NOT `updatedToolResponse`; the value must match the tool's output shape — for Bash: `{stdout, stderr, interrupted, isImage, noOutputExpected}`). The model's final answer quoted the sentinel verbatim; the real command never ran. Cost $0.007 (haiku). Scratch prototype: scratchpad/replay-test/{pre.py,post.py,cassette.json,settings.json}.
