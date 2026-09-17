# Six threads, read in full

Read 2026-09-17 via `gh api` (issue body + every comment + timeline; PR bodies where
a timeline showed one closing an issue). Numbers below are quoted from the threads
themselves or re-derived on this machine — nothing is rounded or remembered.

**Verdicts up front.**

| thread | state | maintainer engaged? | post a link? |
|---|---|---|---|
| microsoft/SkillOpt#155 | open, invited, stalled 6 wks | **yes, twice, invited a PR** | **yes** — one argument, one link, last |
| microsoft/SkillOpt#174 | **closed completed**, PR merged | yes, then shipped it | **no** — the ask has shipped |
| NousResearch/hermes-agent#96704 | open, `needs-decision`, `P3` | **no maintainer has ever commented** | **no** — 6 outside voices deep, waiting on a maintainer |
| NousResearch/hermes-agent#95976 | open, `P1` bug, 2 fix PRs waiting | **no maintainer has ever commented** | **no** — flatly. It needs a merge, not a tool |
| anthropics/claude-code#82056 | open, 48 comments, 7 wks | **no Anthropic staff has ever commented** | **yes** — the best fit of the six |
| Human-Agent-Society/reef#357 | open, `needs-triage`, 0 comments | maintainer-authored roadmap ticket | **no** — it is internal work tracking |

---

## 1. microsoft/SkillOpt#155 — AgenticReplayBackend

`Proposal: AgenticReplayBackend — replay mined tasks with a real agentic CLI in a
throwaway worktree, gate on the diff`
Opened 2026-07-19 by **Alphaxalchemy** (CONTRIBUTOR). Open. 4 comments. 0 reactions.
Last activity **2026-08-07** — six weeks cold. No PR has been opened against it.

### What the author actually asked for

Replay today scores one text completion: candidate skill + memory + task go in as a
prompt, one block of text comes out, checked programmatically. That is honest for
text-expressible behaviour and structurally cannot evaluate "run the tests, read the
failure, fix the config". He proposes a backend that hands the mined task to a real
agentic CLI in a throwaway git worktree, lets it run tools and edit files, and gates
on the diff (does it build, do tests pass, does it resemble the change the user
accepted), surfacing the diff for human review.

He pre-empts the cost objection himself: **not** "make every replay agentic". A tiered
gate — cheap text replay stays the default; agentic replay fires only for the
candidate category cheap replay structurally cannot validate, only for candidates that
already survived the cheap filters, under an explicit per-night budget, opt-in.

Closing line: "Happy to help spec this out if there's maintainer interest."

### What maintainers said

**Yif-Yang replied twice, and both times invited a contribution.**

2026-07-21: agrees the gap is real, calls the tiered design "a sensible direction to
explore", and sketches a safe first slice. 2026-08-07, after a volunteer appeared:
*"we would be very happy to have you take a first pass at this… Please feel free to
open a Draft PR early so we can review the architecture and help keep the first
implementation small and safe."*

This is the only one of the six threads with a live, unambiguous, still-open
invitation.

### The constraints they stated — binding

1. **A git worktree is disposable state, not a security boundary.** Said in both
   maintainer comments, and the second adds that it "should not yet be treated or
   named as a sandbox". Real agent execution needs a container or OS-level sandbox
   with isolated home, credentials, configuration, filesystem access, and network
   disabled by default.
2. **Outcomes must be three-valued: `pass` / `fail` / `inconclusive`.** "Timeouts,
   budget exhaustion, fixture failures, or unavailable dependencies should not be
   counted as evidence that a candidate skill failed."
3. **Agent execution off by default.** The first slice is the disposable workspace
   lifecycle and its tests, with execution disabled; fully opt-in; one agent backend,
   one narrow task category.

And three more the digest should not lose:

- **Task verification is the primary gate; diff similarity is strictly secondary.**
  Builds, tests, task-specific verifiers, allowed-file boundaries, regression checks.
  "Multiple substantially different diffs may solve the same task correctly."
- **Explicit token, turn, time and replay-count budgets**, with reliable cleanup
  after every outcome, plus a pinned starting commit and reproducible fixture.
- **A staged diff/report for review, without automatic adoption.**

### What has already been said (do not repeat)

- **siddhivmd** (2026-07-22) volunteered for step 1, the sandboxed worktree setup,
  and was told yes on 08-07. They have not delivered. Do not step on this.
- **OnourImpram** (2026-07-26) already made the entire three-outcome argument, at
  length and well: collapsing into `fail` makes measured precision track harness
  health instead of rule quality; collapsing into `pass` lets an unvalidated rule
  carry the expensive gate's endorsement. They also gave the follow-on — order the
  staged report by *verifier passed, diff diverged*, which turns the secondary signal
  into a review queue. They enumerated the four producers of a non-verdict: a
  turn/time/spend cap firing mid-task, a build needing a fetch with network disabled,
  the container or fixture not coming up at the pinned commit, a flaky test.
- OnourImpram also **linked their own project** (Mergen, verdict set
  `pass`/`conditional_pass`/`fail`/`unverifiable`, where `unverifiable` can never
  resolve to `pass`) with an explicit AI-assistance disclosure — and the maintainer
  thanked them by name. **A self-project link is within this thread's norms when it
  carries an argument the thread does not already have.**

### Would a link read as help or spam?

**Help — if and only if it says something the thread has not.** The three-outcome
argument is taken; restating it with our logo on it is the obvious failure mode and
would read as me-too.

What is genuinely missing: every producer of `inconclusive` the thread has named is
*infrastructure weather*. Nobody has said that a run can complete cleanly, return a
real verifier result, and still not be enough to decide. Our gate returns
`INSUFFICIENT` when fewer than 3 post-`t0` eligible actions exist, and it returned
exactly that on real data — `0/2`, nothing timed out, nothing failed. That is a fifth
producer, and it lands on their enum design before a report schema exists, which is
their own stated reason for settling it now.

Second gap: nobody has priced a wrong-but-accepted rule in the unit that decides
whether the expensive tier earns its budget. We have that number — an unscoped rule
that passed every structural check would have interrupted **68 legitimate calls across
17,842 recorded tool calls**, and it was retired on that number alone.

**Recommend: send.** One argument, one link at the end, an AI-assistance disclosure
(the thread's norm), and an offer scoped narrowly enough not to collide with
siddhivmd's slice.

### Draft, if sent (264 words)

> The three-outcome call above is right, and I want to add the producer of
> `inconclusive` that the list does not cover.
>
> The four cases named — a cap firing mid-task, a disabled network, a fixture that
> will not come up, a flake — are all infrastructure weather. There is a fifth: the
> run completes cleanly, the verifier returns a real result, and there is still not
> enough of it to decide. A gate that only distinguishes "worked" from "broke" scores
> that as evidence.
>
> We hit it on real data. Our gate replays a candidate rule against the user's own
> recorded transcript in time order and returns PASS / FAIL / INSUFFICIENT, where
> INSUFFICIENT means fewer than 3 post-t0 eligible actions. One candidate came back
> INSUFFICIENT at 0/2: nothing timed out, nothing failed, the rule was simply scoped
> narrowly enough that the record held two decidable actions. Collapsing that either
> way would have been wrong in exactly the manner described above.
>
> The other number worth having before the tier policy is written is what a
> wrong-but-accepted rule costs. One of ours passed every structural check and would
> have interrupted 68 legitimate calls across 17,842 recorded ones; we retired it on
> that number alone. That is the figure that tells you whether the expensive tier is
> earning its budget.
>
> Offline, deterministic, zero model calls: github.com/Linxiushen/precedent
> (Apache-2.0, pre-release). Happy to write the INSUFFICIENT branch as a small draft
> PR if it is wanted, without touching the workspace-lifecycle slice above.
>
> AI-assistance disclosure: drafted with Claude Code under my authorization; every
> number was re-derived on my machine before posting.

---

## 2. microsoft/SkillOpt#174 — the aggregate-mean gate

`Gate compares slice averages only: an aggregate gain can hide a per-task regression,
and improvements are never attributed to the rule that caused them`
Opened 2026-07-26 by **vedmalex** (CONTRIBUTOR). **CLOSED completed 2026-08-13.**
1 comment.

### What the author asked for

Six nights of SkillOpt-Sleep against real task sets on two of his own skills. Two
structural problems, with per-task numbers:

1. **No "no task regressed" condition.** `consolidate.py` accepts on
   `cand_score > base_score`, both slice means. Night 1: `essay_w3` 0.55→1.00,
   `essay_wl2` 0.91→**0.82**, aggregate 0.682→0.852, verdict `accept_new_best`. The
   accepted rule had made the model drop a required scriptural citation — and the
   +0.17 was not quality at all, it came from the rule satisfying a too-strict
   heading-format check of his own. So the gate accepted an edit that damaged one task
   and improved none. Ask: an opt-in `gate_no_regression: true`.
2. **Improvements are never attributed.** Night 4: a genuinely correct rule accepted
   0.900→1.000, but the entire delta came from `tajaka_sixty_hour`, an unrelated task
   the rule says nothing about, and re-running it gave 1/5 — that night's pass was
   luck. The task the rule actually fixes was in `train`, invisible to the gate. **In
   3 of 3 accepts across six nights the reported reason for acceptance was not the
   actual reason.** Ask: record per accepted candidate which val tasks changed state
   and surface it next to the delta.

### What maintainers said

**Yif-Yang (2026-08-07) agreed it is a valid gap and specified the fix in three
numbered items** — a backward-compatible, default-off `gate_no_regression`; structured
per-task deltas (task ID/tags, baseline, candidate, improved/regressed/unchanged) in
both the report and the machine-readable diagnostics; and a regression test for the
exact counterexample plus proof the default preserves existing behaviour. They
welcomed it "either from you or from another interested contributor".

**One constraint worth carrying to every other thread:** *"For the report wording, we
should describe these as observed task-level changes rather than claim that a
particular edit caused them; true per-rule causal attribution would require separate
ablation runs and can be explored as a follow-up."* If we ever describe our
proposed→accepted→activated→attributed funnel to this project, "attributed" has to be
defined as *fired in the hook log*, not as *caused the improvement*.

### Current state

**Done.** PR **#222** by **Boulea7**, `fix(sleep): add optional per-task regression
gate`, merged 2026-08-13T16:49:08Z — default-off `gate_no_regression`, per-task
observed changes in both report formats, missing/non-finite results treated as
regressions when enabled, 953 tests passing. The issue closed one second later.

### Would a link read as help or spam?

**Spam, unambiguously.** The feature asked for shipped five weeks ago. Commenting on a
closed, completed issue notifies everyone subscribed for nothing and signals that we
did not read the timeline. There is no version of an outside-tool comment here that is
not noise.

Its value to us is as reading and as a citation: it is a clean, user-supplied,
real-numbers instance of aggregate-mean acceptance accepting an edit that helped
nothing and broke something — the exact failure our `null` stream and per-task floor
exist for. Cite it; do not post in it.

---

## 3. NousResearch/hermes-agent#96704 — `evals/skills/`

`RFC: add evals/skills/, a paired-arm harness measuring whether agent-written skills
actually help`
Opened 2026-08-27 by **AngelCampa1** (NONE). Open. 7 comments, 1 👍. Labels:
`type/feature`, `innovation`, `tool/memory`, `tool/skills`, **`P3`**,
**`needs-decision`**. Last activity 2026-09-14.

### What the author actually asked for

Hermes has four eval harnesses and none of them varies a skill or memory entry and
measures the downstream task outcome — so there is no answer to "does a skill written
by the background review make the next session better, worse, or neither?", while the
README leads with "the only agent with a built-in learning loop". He tabulates all four
existing harnesses and their self-published limits first, to be accurate about the gap.

Proposal, revised in-thread into **three arms, cheapest first**:

- **Arm A, write-side metrics** — creation rate per session, trigger precision
  (fraction of agent-created skills firing within N days), duplicate-class rate.
  Computed from `.usage.json` and the skills directory on any existing install, **no
  task battery, no API spend, numbers within a day**.
- **Arm B, artifact efficacy** — one variable, skill loaded vs absent; mechanical
  oracles, no LLM judging; 3–5 on-target tasks plus 2–3 off-target to catch collateral
  damage; at least 2 models.
- **Arm C, loop holdout** — the review fires normally, its skill withheld at use time.
  Measures the loop rather than the artifact.
- **P1**, two additive ledger fields (skills loaded in the producing session;
  `authoring_model`), a prerequisite for B over agent-created skills and for C at all.

Plus three direct questions to maintainers, and "Happy to implement P0 and P1 and open
a PR if there is appetite."

### What maintainers said

**Nothing. No maintainer has commented in three weeks.** The issue carries
`needs-decision` and `P3`. The bottleneck is a maintainer decision, not ideas.

### Constraints stated (all by other users, none binding)

- **Variance floor**: 3–5 replays per cell, pass rate with stddev, never a point
  estimate (jimy-r, from operating it; the repo independently says "Success-rate deltas
  at n=3 are noise").
- **Removal is a first-class verdict** — an arm that only ratifies additions inherits
  the build bias it is meant to escape.
- **Tag harness artifacts so they cannot feed the measurement** (the harness's own
  scorecards land in sessions the FTS5 index sees).
- **No LLM judging.** `adhered` is only defined for instructions with a mechanical
  check; `protocol_valid` stays separate from `oracle_pass` (AfshinMirhamed).

### What has already been said (extensively)

- **gfpyc** posted **eight write-side failure modes from a 4-agent, 182-skill
  deployment** with dates and counts: premature crystallization (a proposal persisted
  ~10 seconds after being submitted for user review); three duplicate clusters
  including 3 skills in 8 hours with zero references; skills duplicating the knowledge
  base; a 199-character description truncating at "...known issues, and bre" with zero
  trigger words in the effective 57-char window; a concurrent-write hazard; memory-layer
  pollution to 99% of char budget; stale decay. And **#8, the severest**: a night fork
  silently rewrote a working, user-built `chrome-devtools-mcp` skill with "you must
  close ALL Chrome windows before starting the debug instance" — a wrong
  over-generalisation, no ledger entry, which nearly killed the user's everyday Chrome.
- **jimy-r** contributed the variance floor, the self-invalidating-measurement trap and
  the holdout question, **and linked their own reference architecture**
  (agent-workspace-architecture PATTERNS.md).
- **AfshinMirhamed** proposed the per-task attribution chain
  `eligible → activated → adhered → effective` and offered two deterministic fixtures.
- **GeniusTechnoMystic** dropped a 2026-09-14 research note on paired-arm requirements.
- The author has already absorbed all of it into the body and credited everyone.

### Would a link read as help or spam?

**Lean spam, and recommend not sending.** Three reasons, in order of weight:

1. **The thread is not short of ideas; it is short of a maintainer.** Six outside
   voices, two of them already carrying self-project links, and the author's three
   direct questions have gone unanswered for three weeks. A seventh outside comment
   pushes the actual ask further down the page. That is a real cost, not a
   hypothetical one.
2. **There is a technical mismatch we would get caught on.** Arm A is defined over
   `.usage.json` and the Hermes skills directory. Our audit reads `~/.claude`. It does
   not run on their install, and implying otherwise in a thread this well-sourced would
   be found out in one reply.
3. What we *do* have that is new is narrow: gfpyc's failure mode #8 is precisely the
   over-generalisation family our benchmark plants (the "close ALL windows" case is
   literally one of our `regression` variants), and we have an independent numbered
   instance — an unscoped rule priced at 68 interruptions across 17,842 recorded calls
   and **refused before it went live**, surviving only once scoped to one project. And
   AfshinMirhamed's `eligible → activated → adhered → effective` is our shipped
   `proposed → accepted → activated → attributed` funnel. Both are cross-checks worth
   offering someday — in a reply to *him*, or after a maintainer has spoken. Not as a
   seventh unsolicited comment on a `needs-decision` RFC.

If anyone overrides this: the only defensible shape is a reply addressed to a specific
person, 90% their data and ours side by side, one link, and an explicit "this reads
`~/.claude`, not `~/.hermes`" in the first paragraph.

---

## 4. NousResearch/hermes-agent#95976 — the review fork never lands a skill update

`Background review fork fails to update skills 100% of the time — read-before-write
guard conflicts with skill_view repeat-view dedup`
Opened 2026-08-27 by **yaozhen** (NONE). Open. 4 comments. Labels: `type/bug`,
`comp/agent`, `tool/skills`, **`P1`**, `area/usage-cost`. Last activity 2026-09-11.

### What the author actually asked for

A merge. This is a complete bug report with a root cause and a fix already written.
Two mechanisms that are individually sensible are structurally incompatible in the
fork: the read-before-write guard only clears when `skill_view` takes the path that
actually returns file content, and the repeat-view dedup returns a
`content_returned: false` stub — and the fork shares the parent's `session_id`
(deliberately, for prefix-cache parity), which is the dedup key. So the fork's
`skill_view` hits the parent's dedup stub, the read is never marked, and every patch
is refused.

Production evidence 2026-07-24 → 08-26: **408 refusals** (49 on a single day);
**41 forks completed, `result=skill` appeared 0 times**, `result=memory` 9,
`result=none` 32. Memory updates work; skill updates never do. Fix: namespace the
dedup key on `is_background_review()`. PR **#95975** exists with regression tests. The
author explicitly rejects the cheaper mark-read-on-dedup-hit fix as papering over the
guard's intent.

### What maintainers said

**Nothing. No maintainer has commented.** Two open fix PRs (#95975, #95993) are
waiting.

### What has already been said

Three further independent reproductions, each with environment and run IDs:

- **konsone** — separate self-hosted deployment, 54 refusals in one log window,
  100% of them the read-before-write class, zero from the ownership guard; ~200 over a
  longer window concentrated on 3 skills, several turns ending in
  `review_input_budget_exhausted`.
- **shaggy2626** — v0.21.0, a fresh manual `hermes curator run`; 12 `skill_view` calls
  on one SKILL.md trying six path spellings before `same_tool_failure_halt`; 0
  background patches landed on existing skills 09-03→09-08; a half-finished
  consolidation left on disk. **Narrows the fix**: the per-turn fork is not 100%; what
  matters is whether the fork's *first* view returns content.
- **G1zq** — third install, current main, `same_tool_failure_halt` after 8, the merged
  salvages are not enough.
- **ZeCoL** — a fourth path (`hermes curator run`, the LLM consolidation pass),
  194-skill library, 108 `skill_view` calls / 97 unique, every patch refused. And the
  finding worth stealing for our own docs: **told it had not read a file it had just
  read three times, the model shrank its writes into probes** — two carried explicit
  probe text (`(curator test - harmless no-op line)`, `(placeholder)`) aimed at real
  user skills, and 6 of 8 were degenerate patches with `old_string == new_string`.
  11 minutes and 117 tool calls to accomplish nothing.

### Would a link read as help or spam?

**Spam. Flatly, and there is no version of this that works.** This is a P1 bug with a
diagnosed root cause, a written fix, four independent reproductions, and nothing
missing but a maintainer pressing merge. Nothing about an external Claude Code harness
moves that. Posting there would bury a fifth reproduction under an advert and actively
cost the people waiting.

Our legitimate use of this thread is the one we already make: citing it in the README
as evidence that production learning loops fail silently for weeks (41 forks, 0 skill
updates, 408 refusals). That is a citation, not participation. **Do not comment.**

Worth carrying home, though: ZeCoL's probing behaviour is an argument for our
fail-open design and for counting refused write attempts rather than only landed ones
— a note for our own SECURITY.md, not for their thread.

---

## 5. anthropics/claude-code#82056 — did the memory index load?

`A session cannot determine whether its auto-memory index loaded whole, truncated, or
not at all`
Opened 2026-07-28 by **shawnacason** (NONE). Open. **48 comments**, 1 👍. **No labels.**
Last activity 2026-09-03.

### What the author actually asked for

Expose, in-session, what auto-memory actually loaded, so a partial load is detectable
when it happens rather than inferred later from behaviour. Three failure modes are
indistinguishable from inside a session — index truncated, index not read, fact written
to a different project store — and in all three the session "states the negative with
confidence". The size warning that does exist goes **to the model, not the user**, and
**fires only on write**, which most sessions never do.

Four proposals, cheapest first: (1) a one-line in-session load receipt — index lines
read, truncated y/n, topic file count, resolved store path; (2) surface truncation in
the CLI on read, not only write; (3) a `claude memory status` command; (4) show the
resolved store path at write time. He is explicit that he is not asking for the keying
to change, only to be visible. Cost of the gap in practice: a process ruling written to
the wrong store, seven days of drift, a 118k-subagent-token audit with no product
output.

### What maintainers said

**Nothing. Zero Anthropic staff comments across 48.** No labels, no triage, seven
weeks. Everything below is users.

### The constraints this thread has set for itself

These are norms, and they are enforced hard. Breaking one gets you corrected in public:

- **Every number must be re-derivable, and others re-run it.** tonydzi retracted his
  own first pass after finding writer sessions contaminated it; DanceNitra retracted
  four cells.
- **AI assistance is disclosed in-line.** The author discloses; tonydzi posts as an
  autonomous agent and says so in every comment, unreviewed.
- **ASCII fixtures are distrusted** — bytes and UTF-16 code units coincide only in
  ASCII, so an ASCII canary "measures the right boundary while naming the wrong
  quantity, and it will do that correctly on every run. It cannot fail, so it cannot
  warn you."

### What has already been established (do not restate any of it)

- The constants: `MEMORY.md`, 200 lines, 25,000 **UTF-16 code units** (not bytes),
  trailing whitespace trimmed before both threshold and cut.
- The truncation function itself, read out of the minified bundle on four builds:
  **line cap first, then `lastIndexOf("\n", 25000)` retreating to a newline** — so the
  surviving line number is a property of where your newlines fall, not of the cap.
  `wasLineTruncated` / `wasByteTruncated` exist in the harness and are not surfaced.
- The transcript keys to cwd while the memory index keys to the git repo root, so a
  probe keyed on `transcript_path` reads the wrong index with no symptom (split out as
  #90046); an unauthenticated launch creates the project directory anyway.
- Unreachable topic files found by a transitive wikilink walk — 228 files, 218
  reachable, **10 that recall will never return**.
- tonydzi's delivery study over **703 sessions**: hub sub-index body ever opened in
  **2.6%**, topic-file body present in **50.9%**; after excluding writer sessions,
  being named in the loaded index is worth about **1.7×**, not an order of magnitude.
  His conclusion: a load receipt on this store "would report four green numbers about
  the channel delivering the smaller share of the work".
- **Two self-project links are already precedent here and neither was rejected**:
  hjqcan linked GoodMemory in the first comment (with an explicit "it does not repair
  or replace Claude Code's native auto-memory loader"); tonydzi published a gist that
  measures an index on all three quantities, says which cap binds, and ships 11
  self-tests including Cyrillic and a surrogate pair.
- The author's own workaround: a `SessionStart` hook receipt built on this thread's
  measured constants.

### The one sentence that makes a reply worth sending

shawnacason, 2026-08-27, on his own hook:

> "It is still a workaround, and **it cannot see whether the read actually happened**,
> which is proposal 1 above and **the thing none of us can build**."

That is the claim our `receipts` package contradicts — not in-session, but after the
fact, because Claude Code already writes the record. The transcript carries
`attachment.type == "instructions"` with `files[{path, type, content}]` (an
authoritative per-file load record) and `attachment.type == "prompt_snapshot"` with
`systemPrompt[]` (the rendered context, not a reconstruction of the cut). Comparing an
artifact body against those blobs — whole-body exact, then per-section with `MEMORY.md`
line by line, then a 200-character LCS for reflowed text — yields per session and per
artifact: `loaded_complete` / `loaded_truncated` / `not_loaded` / `missing_on_disk` /
`unknown`. Every claim carries a `<transcript file>:<line>` locator.

**The caveat has to lead, or this audience will shred it:** `unknown` is load-bearing
and is the honest answer for most sessions, because many carry only a couple of
`prompt_snapshot` attachments and reporting those as `not_loaded` would be a lie. And
it proves the text was present in the rendered context, not that the model attended to
it. Both limits are already written into the package's own Limits section — which, in
this thread, is the credential.

### Would a link read as help or spam?

**Help, and this is the strongest of the six** — provided the comment leads with the
correction to that one sentence and not with "I built a harness". The thread is a group
of people who reverse-engineered a minified function because they could not get the
answer any other way; handing them a way to get the answer from a file they already
have is the definition of on-topic. The self-link norm is already established twice
over.

Two things not to do: do not claim it closes proposal 1 (it does not — the running
session still cannot know), and do not restate any constant the thread measured.

One extra that costs nothing and fits a live need: several participants are posting
one-box numbers from work machines and hand-redacting. `precedent audit --share` is
counts-only and structurally anonymised — no quotes, no paths, no project names, no
session ids.

**Recommend: send.**

### Draft, if sent (304 words)

> One correction to a single sentence, because it is the one that closes the thread's
> options.
>
> @shawnacason, 2026-08-27: "it cannot see whether the read actually happened, which is
> proposal 1 above and the thing none of us can build."
>
> After the fact, it is buildable today, because Claude Code already writes the record.
> The session transcript carries `attachment.type == "instructions"` with
> `files[{path, type, content}]`, and `attachment.type == "prompt_snapshot"` with
> `systemPrompt[]` — the rendered context itself, not a reconstruction of where the cut
> landed. Comparing an artifact's body against those blobs (whole-body exact, then
> per-section, with MEMORY.md compared line by line, then a 200-character LCS for
> reflowed text) gives, per session and per artifact: loaded_complete,
> loaded_truncated, not_loaded, missing_on_disk, unknown.
>
> The caveat belongs first, because this thread re-runs numbers. `unknown` is
> load-bearing and it is the honest answer for most sessions — many carry only a couple
> of prompt_snapshot attachments, and reporting those as not_loaded would be a lie. It
> also proves the text was present in the rendered context, not that the model attended
> to it.
>
> This does not close proposal 1. The running session still cannot know. What it means
> is that "this instruction file reached the model only in part, in these sessions, at
> this transcript line" is available without waiting on the harness, and every claim
> carries a `<file>:<line>` you can check with `sed -n`.
>
> Stdlib only, reads the Claude home and never writes to it:
> github.com/Linxiushen/precedent (Apache-2.0, pre-release; `packages/receipts` is the
> relevant part). Its `audit --share` card is counts-only — no quotes, paths, project
> names or session ids — which may help the people here posting from work machines.
>
> AI-assistance disclosure: drafted with Claude Code under my authorization. Every
> mechanism above is implemented and tested in the linked repo, and the limits stated
> are the ones its own Limits section states.

---

## 6. Human-Agent-Society/reef#357 — reproducible Reef benchmarks

`[Task] Build reproducible Reef benchmarks`
Opened 2026-09-09 by **BobbyZhouZijian** (CONTRIBUTOR), who ticked *"I am a Reef
maintainer or a maintainer asked me to create this task."* Open. **0 comments.**
0 reactions. Labels: `area: ci`, `type: task`, **`status: needs-triage`**. Renamed and
relabelled by the same maintainer 2026-09-15.

### What the author actually asked for

Not a discussion — a work item. Build reproducible benchmarks for Reef as a
continual-learning **system**: a benchmark specification, then reference workloads and
a measurement pipeline, deliverable as two PRs. Initial deliverable: one
weight-learning workload and one harness-learning workload, each exercising sustained
serving and repeated learning updates, plus a reproducible baseline-comparison report.
Acceptance criteria cover metrics and units, measurement boundaries, resource/cost
accounting, baseline selection, run manifests (Reef revision, dependency/model/recipe/
scorer versions, hardware, seeds), and variability reporting.

Explicitly: *"Ownership is open; no delivery date is assigned."* Weight-path runs
require a supported accelerator environment; accelerator execution is coordinated with
a separate GPU-lane issue (#27).

### What maintainers said

The maintainer *is* the author. Nobody has replied in eight days. The only timeline
activity is three other maintainers cross-referencing it from their own issues (#370,
#420, #428) and the author adding a label and absorbing #358 into it. There is no
invitation to outside contributors anywhere in the text — "ownership is open" means an
unassigned Reef work item, not an open call.

### Constraints stated

Scope-partitioning constraints, all of them internal: #355 evaluates model/harness
releases while this evaluates Reef system revisions; **#356 owns candidate publication
gates** and benchmark quality checks must constrain comparisons without defining
deployment publication policy; #23 supplies operational metrics; #27 owns the
accelerator lane; #22 supplies reproducibility standards. Two substantive ones worth
noting: *"Distinguish a committed artifact from an update actually used by serving"*
and *"define task-quality constraints and correctness checks so that reduced
evaluation, failed requests, or degraded learning cannot appear as an efficiency
gain."*

### Would a link read as help or spam?

**Spam, and the most obviously so of the six.** This is a maintainer-authored,
maintainer-checkbox roadmap ticket sitting in `needs-triage` with zero comments. The
first comment on an internal triage ticket being an outside product link reads as
exactly what it is. There is also a hard scope mismatch: half the deliverable is
weight-learning workloads on GPU accelerators and serving-impact latency, and we have
nothing whatsoever for that half.

The two points of genuine contact are real but are not an invitation. "Distinguish a
committed artifact from an update actually used by serving" is the activation half of
our funnel — the gap between an artifact written and an artifact that reaches the model
— and "degraded learning cannot appear as an efficiency gain" is what a null stream and
an INSUFFICIENT verdict exist for. Contact points are not a door.

If Reef is ever worth engaging, the thread is **#356** (candidate publication gates),
where their own per-task-regression admission lives — and only after reading it in
full, which has not been done. **Do not comment on #357.**

---

## Cross-thread notes

**The invitation gradient is the whole signal.** Exactly one thread has a live
maintainer invitation (SkillOpt#155). One had one and shipped the work (#174). Two have
had *no maintainer response at all* despite being months old and well-sourced
(hermes#96704, cc#82056) — in those, the scarce resource is maintainer attention, and
adding to the pile is a cost whether or not the comment is good. One is a bug waiting
on a merge (hermes#95976). One is internal work tracking (reef#357). Two sends out of
six is the honest ratio.

**Where self-links are already normal, they are normal because they carried an
argument.** OnourImpram (Mergen), hjqcan (GoodMemory), tonydzi (a gist), jimy-r (a
reference architecture) all linked their own work and none was treated as spam. In
every case the link came last, after a technical contribution that stood on its own,
and in three of four cases with an explicit AI-assistance disclosure. That is the
template, and it is also the test: if the comment collapses when the link is removed,
it should not be posted.

**Two threads (#155, #82056) will check our numbers.** Both have publicly retracted
their own claims mid-thread. Every figure in a reply must be one of the verified set,
stated with its cost attached — 0% harmful **at 52.2% missed improvements**, not 0%
harmful.
