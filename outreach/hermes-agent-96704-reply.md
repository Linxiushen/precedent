@AfshinMirhamed Both answers, and a trap I walked into while checking them.

**Would-be blocks, not rule matches.** The verdict comes from the installed hook itself, not from re-running a pattern. By default it is one real subprocess per recorded call with the same `PreToolUse` payload Claude Code sends, and the count reads `permissionDecision` off `hookSpecificOutput`. A full sweep calls `decide()` in process through the same hook bytes on disk, because 20k subprocesses is a quarter of an hour. So the number is what the enforcement path would actually have done, including any scope or path condition the rule carries.

**Legitimacy is inferred, not observed.** Nothing in a transcript says "this call was fine". A fire followed within three human turns by another correction on that topic is scored a true positive — the user objected again. Every other fire counts against the rule. So a call the user quietly disliked and never mentioned is scored as collateral damage. That bias is deliberate: for a collateral-damage metric it can overstate a rule's cost and never understate it.

**The trap.** Swept over the whole record, the rule I have live shows 27 fires, which reads as 27 interruptions. It is not. Every one of them predates the correction that created the rule, and what an agent did before being told is evidence about the world, not about the rule. Worse, the granularity of t0 decides the answer: splitting at the correcting turn's *date* gives 13 fires after it; splitting at its *timestamp* — 2026-09-07T08:26:39Z — gives 0. The correcting turn and the calls it judges land on the same day.

So if this lands in Hermes, the metric is post-t0 fires over post-t0 eligible calls, t0 is a timestamp rather than a date, and a tool that prints a bare lifetime total is reporting a number nobody should quote. Mine did until this morning.

AI-assistance disclosure: drafted with Claude Code under my authorization; every number re-derived on my machine before posting.
