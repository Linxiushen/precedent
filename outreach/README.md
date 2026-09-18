# outreach — six threads, two replies

**All of this has been sent.** Four of the six went out on 2026-09-17 and two
had no comment body to send at all — `send.sh` carries the log with the
read-back permalinks. Three of the four were in the held set and were posted
anyway on the user's explicit instruction; the hold reasons stay in their files
verbatim, overruled rather than withdrawn, because a hold that gets edited away
every time it is overruled stops being a record of anything.

Two replies came back and both are answered. One of them, on
anthropics/claude-code#82056, was a correction we could not have found
ourselves and is now a code change and four regression tests.

Three of the four comments were also edited on 2026-09-18: they carried a
repository link that is not public, so for about two hours they pointed
strangers at a 404, and two of them carried a factual error about which rule
received which gate verdict. Both are corrected in the body of the comments
rather than silently, because that thread re-runs numbers.

Drafts for the six issues read in full in [`THREADS.md`](THREADS.md).

The `send?` column below is what the reading recommended *before* anything was
sent, kept as written. It is no longer what happened: see the top of this file
and the SENT LOG in [`send.sh`](send.sh) for what actually went out, and note
that the table's `INSUFFICIENT at 0/2` claim for SkillOpt#155 was wrong — that
verdict belongs to a different rule, and the posted comment carries a public
correction.

## The threads

| ref | what they asked | what we offer | send? | words |
|---|---|---|---|---|
| [microsoft/SkillOpt#155](https://github.com/microsoft/SkillOpt/issues/155) | An agentic replay backend under three binding constraints: a worktree is not a sandbox, outcomes must be `pass`/`fail`/`inconclusive`, execution off by default. Maintainer invited a draft PR; cold six weeks. | A fifth producer of `inconclusive` that nobody has named — the run completes cleanly and there is still not enough to decide (`INSUFFICIENT` at 0/2 on real data) — plus which of their three constraints we satisfy and the one (sandboxing) we do not. | **yes** | 400 |
| [microsoft/SkillOpt#174](https://github.com/microsoft/SkillOpt/issues/174) | An opt-in `gate_no_regression` and per-task attribution, with six nights of real numbers. | Nothing. It shipped in PR #222 on 2026-08-13 and the issue closed. | **no** | 0 |
| [NousResearch/hermes-agent#96704](https://github.com/NousResearch/hermes-agent/issues/96704) | A paired-arm harness measuring whether agent-written skills help. Three direct questions to maintainers, unanswered for three weeks. | Two narrow cross-checks: the funnel naming, and an over-generalisation priced at 68 interruptions in 17,842 calls *before* it went live. Held as a reply to a named person. | **no** | 248 (held) |
| [NousResearch/hermes-agent#95976](https://github.com/NousResearch/hermes-agent/issues/95976) | A merge. P1 bug, root cause diagnosed, fix written, two PRs open, four independent reproductions. | Nothing that moves a merge button. | **no** | 0 |
| [anthropics/claude-code#82056](https://github.com/anthropics/claude-code/issues/82056) | Whether the auto-memory index loaded whole, truncated, or not at all. 48 comments, no Anthropic staff has ever commented. | A correction to the one sentence that closes the thread's options: after the fact it *is* buildable, from `instructions` / `prompt_snapshot` attachments Claude Code already writes. Points at the load receipts and the one-file zipapp. | **yes** | 250 |
| [Human-Agent-Society/reef#357](https://github.com/Human-Agent-Society/reef/issues/357) | Reproducible Reef benchmarks, as an internal roadmap task. | The labelled-stream construction and the null/tamper calibration, offered as a Harbor task family liftable into a Reef recipe. Held. | **no** | 400 (held) |

Two sends out of six. The gradient that decides it is the invitation: exactly
one thread has a live maintainer invitation, two have had **no maintainer
response at all** despite being months old and well sourced, one shipped, one
is waiting on a merge, one is internal work tracking.

## Reading order

1. **[`THREADS.md`](THREADS.md)** first, in full. The drafts are unreadable
   without it — every one of them is built to say the one thing its thread has
   not already said, and you cannot check that without knowing what was said.
2. **[`claude-code-82056.md`](claude-code-82056.md)** — the strongest fit, and
   the shortest path from "their problem" to "our artefact".
3. **[`skillopt-155.md`](skillopt-155.md)** — the only thread with a standing
   invitation, and the only draft that spends words declining to claim
   something.
4. The four held files, in any order. **[`reef-357.md`](reef-357.md)** and
   **[`hermes-agent-96704.md`](hermes-agent-96704.md)** contain complete held
   drafts and a stated trigger condition for sending them.
   **[`skillopt-174.md`](skillopt-174.md)** and
   **[`hermes-agent-95976.md`](hermes-agent-95976.md)** contain no draft at
   all, only the reason there is none and the one finding worth carrying home.
5. **[`send.sh`](send.sh)** last, and only when a human is about to post.

## Pre-send checklist

Run this against the body file, out loud, immediately before uncommenting a
line in `send.sh`. Any "no" means do not send.

- [ ] **Does sentence one answer their question?** Not ours. Their problem, in
      their words. If the thread asked for a number, the number is in the first
      sentence.
- [ ] **Is there a cost in it?** At least one honest bound we paid: the 52.2%
      missed-improvement rate, `N=1` machine, the three rules the gate refused,
      `INSUFFICIENT` being the common verdict, or the constraint we do not
      satisfy. A reply with no cost in it reads as marketing, and both send
      threads have publicly corrected people for less.
- [ ] **Is the link at the end, once?** One link, last, after an argument that
      still stands if the link is deleted. If removing the link collapses the
      comment, do not post the comment.
- [ ] **Does it repeat anything the thread already established?** Restating a
      thread's own finding back at it with our name attached is the me-too
      failure. Check against `THREADS.md`'s "what has already been said".
- [ ] **Is every number in the verified set, and re-derived today?**
      `python3 dist/precedent.pyz bench --all --seeds 200` reprints the
      acceptor table; `DEMO.md` carries the 68 / 17,842 sweep and the 0.48 ms
      deny. Nothing rounded, nothing remembered.
- [ ] **Is the AI-assistance disclosure present?** It is the norm in both send
      threads and its absence is noticed there.
- [ ] **Does it claim an endorsement or a user we do not have?** It must not.
      No Anthropic, Microsoft or Nous affiliation is implied anywhere, and the
      project is used by its author and nobody else. Say so.
- [ ] **Has a human read the whole body, end to end, in this sitting?**
- [ ] **Is this the only thing being sent right now?** One at a time, then wait
      and read the reaction.

## A note on two numbers

The brief this batch was written from quoted `49.2%` greedy false-commit and
`55.0%` missed improvements. Both are wrong, and neither appears in any draft.
Re-derived on this machine on 2026-09-17:

```
$ python3 dist/precedent.pyz bench --all --seeds 200
greedy       false-commit 47.0% … harmful(mean) 55.0% … missed-improv  1.2%
precedent    false-commit  0.0% … harmful(mean)  0.0% … missed-improv 52.2%   evals/dec 5.8
```

`55.0%` is `greedy`'s mean harmful-commit rate, not our missed-improvement
rate; `49.2%` appears nowhere. The drafts use **47.0%** and **52.2%**. Both
send threads re-run posted numbers as a matter of habit, so this distinction is
the difference between a contribution and a retraction.
