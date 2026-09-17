# DO NOT SEND

**microsoft/SkillOpt#174 — CLOSED COMPLETED 2026-08-13. No comment belongs here, and no draft exists below.**

The thing vedmalex asked for has shipped. Maintainer Yif-Yang specified the fix in three numbered items on 2026-08-07, and PR #222 (Boulea7) merged it on 2026-08-13 with a default-off `gate_no_regression`, per-task observed changes in both report formats, and a regression test for the exact counterexample; the issue closed one second after the merge. Commenting on a closed, completed issue notifies every subscriber for nothing and signals that we did not read the timeline — and there is no version of an outside-tool comment on a solved issue that is not noise, however good the comment. The value of this thread to us is entirely as reading and as a citation: it is a clean, user-supplied, real-numbers instance of aggregate-mean acceptance keeping an edit that damaged one task and improved none (`essay_w3` 0.55→1.00, `essay_wl2` 0.91→0.82, aggregate 0.682→0.852, verdict `accept_new_best`), which is exactly the failure our `null` stream and per-task floor exist for. Cite it; do not post in it.

## The constraint to carry home, not to post

Yif-Yang, 2026-08-07: report wording should describe *"observed task-level changes rather than claim that a particular edit caused them; true per-rule causal attribution would require separate ablation runs."*

That binds our own language everywhere, not just in this repo. The `attributed` stage of our `proposed → accepted → activated → attributed` funnel means **fired in the hook log** and nothing more. It must never be written, in a README, a paper or a reply, as *caused the improvement*.
