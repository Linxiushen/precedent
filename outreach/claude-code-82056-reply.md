@DanceNitra Checked it here before believing it, and you are right — with one correction to the mechanism that makes it worse, not better.

Across 510 transcripts on this machine, 108 sessions carry an `instructions` record and two of them carry `MEMORY.md` at two different lengths inside the one session (252 and 400 characters; 103 and 137). A file that grew while the session ran, which is what happens every time a memory is written mid-session. Smaller than your eleven-record session, same shape.

The correction: ours was never "whichever record it read". It took the delivery with the highest coverage and stopped at the first complete one. For a truncation detector that is worse than arbitrary — it is the single tie-break that systematically hides the thing the tool exists to find. A session whose index was cut for most of its life read `loaded_complete` because one late delivery happened to be whole. Arbitrary would at least have been unbiased.

Now the status follows the worst delivery in the session and a note records the spread; the cited line still points at the most complete record, since that is the one you want to open. On this machine that moved `truncated on load` from 2 files in 3 sessions to 3 files in 4. One session was reported clean and was not.

On your build edge: we do not check `version` at all. A session with no usable record already falls to `unknown`, so the pre-2.1.263 transcripts land in the right bucket by accident rather than by rule — which is not the same thing, and I would rather it were the rule.

Your resume-versus-replay caveat is the one I have no answer to either. The record cannot distinguish a faithful record of a stale delivery from a replay of one; only a request-body capture can. Worth stating wherever anyone publishes a number off these records.

Reading your probe next.

AI-assistance disclosure: drafted with Claude Code under my authorization; every number above was re-derived on my machine before posting.
