# DO NOT SEND

**NousResearch/hermes-agent#95976 — no comment belongs here, and no draft exists below.**

yaozhen asked for a merge, not a discussion. This is a complete P1 bug report with a diagnosed root cause (the read-before-write guard only clears on the `skill_view` path that returns content; the repeat-view dedup returns a `content_returned: false` stub; the background review fork deliberately shares the parent's `session_id`, which is the dedup key — so the fork hits the parent's stub and every patch is refused), production evidence over a month (408 refusals; 41 forks completed, `result=skill` 0 times), a written fix, two open PRs (#95975, #95993), and four independent reproductions from separate installs. Nothing is missing but a maintainer pressing merge, and nothing about an external Claude Code harness moves that. Posting there would bury a fifth reproduction under an advert and would cost the people waiting something real. Our legitimate use of this thread is the one we already make: citing it as evidence that production learning loops can fail silently for weeks. That is a citation, not participation.

## The finding to carry home, not to post

ZeCoL's run: told it had not read a file it had just read three times, the model **shrank its writes into probes** — two carrying explicit probe text aimed at real user skills, and 6 of 8 degenerate patches with `old_string == new_string`; 11 minutes and 117 tool calls to accomplish nothing.

That is an argument for our fail-open design, and for counting *refused* write attempts rather than only landed ones. It belongs in our own SECURITY.md, not in their thread.
