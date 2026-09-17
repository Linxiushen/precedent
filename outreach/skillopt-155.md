On the three-outcome question: the four producers of `inconclusive` named above — a cap firing mid-task, a disabled network, a fixture that will not come up at the pinned commit, a flaky test — are all infrastructure weather. There is a fifth: the run completes cleanly, the verifier returns a real result, and there is still not enough of it to decide.

We hit that one on real data. Our gate replays a candidate rule against the user's own recorded transcript in time order and returns PASS / FAIL / INSUFFICIENT, where INSUFFICIENT means fewer than three post-`t0` decidable actions. One candidate came back INSUFFICIENT at 0/2: nothing timed out, nothing failed, the rule was simply scoped narrowly enough that the record held two decidable actions. Collapsing it either way is wrong in the manner described above, and no timeout or budget flag catches it, so a reason code of "below the evidence floor" belongs in the schema alongside the four weather ones.

Against the three binding constraints, including where we are no use to you:

- **Three-valued outcome** — satisfied and shipped, the INSUFFICIENT branch exercised on real data rather than only in tests.
- **Execution off by default** — satisfied trivially, because our gate executes nothing: zero model calls, reading a record that already exists.
- **Sandboxing** — **not** satisfied. We have proposer/evaluator separation and hash-chained receipts; real isolation (mount namespace or container) is not implemented and our README says so. On the constraint governing the first slice we have nothing.

One number for the tier policy, since it decides whether the expensive tier earns its budget: the cost of a wrong-but-accepted candidate. One of ours passed every structural check, and sweeping it over the user's own record priced it at 68 legitimate interruptions across 17,842 recorded tool calls. We retired it on that number alone. Re-scoped to one project it survived the structural checks, and then the gate returned INSUFFICIENT at 0/2, which is the common verdict here and the honest cost of refusing to guess.

Offline, deterministic, zero model calls: github.com/Linxiushen/precedent (Apache-2.0, pre-release; N=1 machine, 8 sessions, one rule enforced live — not a multi-user study). Happy to write the INSUFFICIENT branch and its reason codes as a small draft PR if wanted, without touching the workspace-lifecycle slice.

AI-assistance disclosure: drafted with Claude Code under my authorization; every number was re-derived on my machine before posting.
