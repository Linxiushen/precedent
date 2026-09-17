# DO NOT SEND

**NousResearch/hermes-agent#96704 — held. The draft below is written and must not be posted as things stand.**

The thread is not short of ideas; it is short of a maintainer. Six outside voices have already spoken, two of them carrying self-project links, and AngelCampa1's three direct questions have gone unanswered for three weeks on an issue labelled `needs-decision` and `P3`. A seventh outside comment pushes the actual ask further down the page, which is a real cost. There is also a technical mismatch we would be caught on in one reply: Arm A is defined over `.usage.json` and the Hermes skills directory, and our audit reads `~/.claude` — it does not run on their install. What we have that is genuinely new is narrow enough to belong in a reply to a named person, not in a seventh unsolicited comment.

**Send only if** a maintainer comments first, or AfshinMirhamed or gfpyc asks a question our data answers. Then post the draft below as a reply to that person, not as a fresh top-level comment.

---

## HELD DRAFT — 248 words

> Two cross-checks on Arm A's metric definitions, from a different harness. Stated first so nobody wastes a reply on it: this reads `~/.claude`, not `~/.hermes`. It does not run on a Hermes install, and no number below is measured on your data.
>
> On @AfshinMirhamed's chain — `eligible → activated → adhered → effective` is, near enough, the funnel we shipped as `proposed → accepted → activated → attributed`. The warning that came with ours is worth taking: `attributed` means *fired in the hook log*, nothing more. Calling any of those stages causal needs ablation runs that neither of us is doing.
>
> On @gfpyc's failure mode #8, the night fork rewriting a working skill with "close ALL Chrome windows": that over-generalisation family is a planted stream in our benchmark, and the useful part is that it can be priced *before* the artefact goes live. Ours was an unscoped rule that passed every structural check; swept over the user's own record it would have interrupted 68 legitimate calls out of 17,842, and we retired it on that number. Scoped to one project it survived — and then the temporal gate returned INSUFFICIENT at 0/2, the common verdict, and the honest cost of a check that refuses to guess.
>
> One Arm A metric that costs nothing, then: retrospective interruption count over the record you already have.
>
> github.com/Linxiushen/precedent (Apache-2.0, pre-release; one machine, 8 sessions, one rule enforced live).
>
> AI-assistance disclosure: drafted with Claude Code under my authorization; every number re-derived before posting.
