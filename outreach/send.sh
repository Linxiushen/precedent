#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# outreach/send.sh — nothing in this file runs by default. Every command is
# commented out, on purpose. Uncomment ONE line, run it, then stop.
#
# READ THIS BEFORE UNCOMMENTING ANYTHING
#
#   1. These post PUBLICLY, under the GitHub account `gh` is authenticated as.
#      Run `gh auth status` first and confirm it is the account you mean.
#   2. They are effectively permanent. A deleted comment still sits in every
#      subscriber's email, in the issue's event timeline, and in any mirror.
#      Treat "delete" as damage control, never as undo.
#   3. Send ONE AT A TIME, with a pause between. Wait for a reaction — a reply,
#      a maintainer response, a thumbs-down, silence — and read it before
#      sending the next. Two of these threads (SkillOpt#155, claude-code#82056)
#      contain people who re-run posted numbers and who have publicly retracted
#      their own claims mid-thread. If one of ours is challenged, the right move
#      is to answer there, not to keep posting elsewhere.
#   4. A HUMAN must read the body file end to end immediately before sending.
#      See the pre-send checklist in README.md.
#   5. Only two threads are cleared to send. The other four files carry a
#      DO NOT SEND banner and are NOT valid `--body-file` inputs — posting one
#      would publish the banner and the reasoning behind it.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# SENT LOG — this file is no longer hypothetical.  On the user's explicit
# instruction ("全部发"), four of the six were sent on 2026-09-17 as
# `Linxiushen`.  Each was read back from the API after posting; the permalinks
# below are the read-back, not the send.
#
#   anthropics/claude-code#82056      SENT 2026-09-17T06:20:23Z
#     .../issues/82056#issuecomment-5709877335
#   microsoft/SkillOpt#155            SENT 2026-09-17T06:21:05Z
#     .../issues/155#issuecomment-5709885656
#   NousResearch/hermes-agent#96704   SENT 2026-09-17T06:21:57Z   (was HELD)
#     .../issues/96704#issuecomment-5709895843
#   Human-Agent-Society/reef#357      SENT 2026-09-17T06:57:03Z   (was HELD)
#     .../issues/357#issuecomment-5710342682
#     The first comment on the ticket.  The hold reason in reef-357.md stands
#     untouched — zero comments, maintainer-authored triage ticket, no
#     invitation anywhere in the text — and was overruled deliberately.
#
#   microsoft/SkillOpt#174            NOT SENT — no draft exists.  The issue
#   NousResearch/hermes-agent#95976   NOT SENT — no draft exists.  Both files
#     are DO-NOT-SEND rationale, 0 lines of comment body between them.  There
#     was nothing to send even had we wanted to.
#
# Four of the six went out; three of those four were HELD.  That was the
# user's call, made twice, after reading the reasons.  The reasons were not
# withdrawn and are still in the files: a hold that gets edited away every
# time it is overruled stops being a record of anything.  If one of these
# lands badly, the file says what we knew before we sent it.
# ---------------------------------------------------------------------------

set -euo pipefail
cd "$(dirname "$0")"

# --- Cleared to send ------------------------------------------------------

# microsoft/SkillOpt#155 — AgenticReplayBackend. Live maintainer invitation,
# cold since 2026-08-07. Adds the fifth producer of `inconclusive`; states
# plainly that we do NOT satisfy the sandboxing constraint. 400 words.
# SENT 2026-09-17 -- do not re-run; use --edit-last to amend.
# gh issue comment 155 --repo microsoft/SkillOpt --body-file skillopt-155.md

# anthropics/claude-code#82056 — did the memory index load? 48 comments, no
# Anthropic staff has ever commented. Corrects one sentence, leads with the
# `unknown` caveat, points at the one-file zipapp. 250 words.
# SENT 2026-09-17 -- do not re-run; use --edit-last to amend.
# gh issue comment 82056 --repo anthropics/claude-code --body-file claude-code-82056.md

# --- HELD. Do not uncomment without re-reading the file's banner first. ----

# microsoft/SkillOpt#174          — CLOSED COMPLETED, the ask shipped in PR #222.
# NousResearch/hermes-agent#95976 — P1 bug awaiting a merge, not a tool.
# NousResearch/hermes-agent#96704 — SENT 2026-09-17 on the user's instruction,
#                                   over this hold.  Do not send again.
# Human-Agent-Society/reef#357    — SENT 2026-09-17 on the user's instruction,
#                                   over this hold.  Do not send again.

# ---------------------------------------------------------------------------
# RECOVERY
#
# Fix a typo in the comment you just posted (preferred — no delete, no churn):
#
#   gh issue comment 155 --repo microsoft/SkillOpt --edit-last --body-file skillopt-155.md
#
# Delete it outright. `gh` has no delete verb for issue comments; go through
# the API. First find the id (it is the number after `#issuecomment-` in the
# comment's permalink, or):
#
#   gh api repos/microsoft/SkillOpt/issues/155/comments \
#     --jq '.[] | select(.user.login == "YOUR_LOGIN") | "\(.id)\t\(.created_at)"'
#
# then:
#
#   gh api -X DELETE repos/microsoft/SkillOpt/issues/comments/<comment_id>
#
# Note the path: /issues/comments/<id>, NOT /issues/<n>/comments/<id>.
# Deletion is immediate and irreversible, and does not unsend the notification
# emails. If the comment is merely imperfect, edit it; if it was a mistake,
# delete it and say so plainly in a short follow-up rather than quietly.
# ---------------------------------------------------------------------------
