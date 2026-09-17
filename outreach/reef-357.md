# DO NOT SEND

**Human-Agent-Society/reef#357 — held. The draft below is written and must not be posted as things stand.**

This is a maintainer-authored, maintainer-checkbox roadmap ticket sitting in `needs-triage` with zero comments, eight days old. "Ownership is open; no delivery date is assigned" means an unassigned internal work item, not an open call for outside contributors — there is no invitation anywhere in the text, and the only timeline activity is three other maintainers cross-referencing it from their own issues. The first comment on an internal triage ticket being an outside product link reads as exactly what it is. There is also a hard scope mismatch: half the deliverable is weight-learning workloads on accelerators and serving-impact measurement, coordinated with the GPU-lane issue #27, and we have nothing whatsoever for that half. The two points of genuine contact — *"distinguish a committed artifact from an update actually used by serving"* (the activation half of our funnel) and *"degraded learning cannot appear as an efficiency gain"* (what a null stream and a three-valued verdict exist for) — are contact points, not a door.

**Send only if** a Reef maintainer opens the ticket to outside contributors, or a triage comment asks for prior art. Even then, the better thread is **#356** (candidate publication gates), where their own per-task-regression admission lives — and #356 has not been read in full yet, so read it before writing anything.

---

## HELD DRAFT — 400 words

> On the acceptance criterion *"define task-quality constraints and correctness checks so that reduced evaluation, failed requests, or degraded learning cannot appear as an efficiency gain"* — that one is testable without touching the accelerator lane, because its ground truth can be planted rather than measured.
>
> The construction: label the candidate stream instead of scoring the system. Five families: `null` (an edit with zero true lift, so every commit is a false commit by construction), `regression` (deterministically breaks a task the incumbent passes k/k), `unsafe`, `tamper` (the edit moves the thing that produces the number), and `good` (real lift, so every reject is a miss). Two of the five need zero held-out evaluations to score, which is what makes it affordable next to a serving-impact benchmark. Labels are re-verified before a table prints by an oracle sharing no code with the generator, in both directions: the property must hold of the candidate and must not already hold of the incumbent.
>
> What it caught across 11 acceptors, 17 variants, 200 runs, 37,400 decisions, zero model calls, in 2.0 s: `greedy` — commit iff the candidate's mean beats the incumbent's, what most loops actually ship, W/L margins included — false-commits **47.0%** of the null stream and **100.0%** of `tamper`. The tamper column is the one that bears on this ticket: a tampered edit's *reported* lift is the largest in the stream, so every statistics-only acceptor commits 71–100% of it. Degraded learning appearing as an efficiency gain is not a hypothetical failure of the report format; it is the default behaviour of the gate.
>
> The honest cost is on the same row. Our gate is 0% on all four wrong families at 5.8 paired evaluations per decision, and **52.2% missed improvements** — it rejects half the genuinely good edits at a declared +0.25 lift over 40 paired binary outcomes. A benchmark that reports only the first half of that row is selling something. The paired *outcomes* are simulated from a declared per-family lift that the table header prints; the artefacts, edits and labels are real.
>
> The streams ship as a 17-case Harbor task family (`bench/harbor/`, plain `task.toml` + `tests/` + `solution/`), so they can be lifted into a Reef recipe without taking the tool: github.com/Linxiushen/precedent (Apache-2.0, pre-release). Nothing in it touches the weight-learning half of this ticket.
>
> AI-assistance disclosure: drafted with Claude Code under my authorization; every number re-derived on my machine before posting.
