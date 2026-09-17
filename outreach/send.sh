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

set -euo pipefail
cd "$(dirname "$0")"

# --- Cleared to send ------------------------------------------------------

# microsoft/SkillOpt#155 — AgenticReplayBackend. Live maintainer invitation,
# cold since 2026-08-07. Adds the fifth producer of `inconclusive`; states
# plainly that we do NOT satisfy the sandboxing constraint. 400 words.
# gh issue comment 155 --repo microsoft/SkillOpt --body-file skillopt-155.md

# anthropics/claude-code#82056 — did the memory index load? 48 comments, no
# Anthropic staff has ever commented. Corrects one sentence, leads with the
# `unknown` caveat, points at the one-file zipapp. 250 words.
# gh issue comment 82056 --repo anthropics/claude-code --body-file claude-code-82056.md

# --- HELD. Do not uncomment without re-reading the file's banner first. ----

# microsoft/SkillOpt#174          — CLOSED COMPLETED, the ask shipped in PR #222.
# NousResearch/hermes-agent#95976 — P1 bug awaiting a merge, not a tool.
# NousResearch/hermes-agent#96704 — needs a maintainer, not a 7th outside voice.
# Human-Agent-Society/reef#357    — internal maintainer-authored triage ticket.

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
