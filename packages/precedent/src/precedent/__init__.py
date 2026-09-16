# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""precedent — a self-evolving Claude Code harness with a verified loop.

Five steps, and the two nobody implements are the product:

    (1) propose   the correction miner turns every user correction into a
                  candidate rule                              -> ``mine.py``
    (2) accept    two gates, one per kind of candidate.  A *rule* goes to the
                  TEMPORAL BIRTH GATE, which replays it against the record *in
                  time order*: it must fire on the violating action the user
                  objected to, and among the eligible actions after that
                  correction at most epsilon may be tolerated (unpunished)
                  fires                              -> ``compile.py``/``rules.py``
                  Anything else goes to the EXAMINER, which compiles eligible
                  sessions into ``claude plugin eval`` cases (with a paired
                  ``claude -p`` fallback), and the paired e-process gate, harm
                  martingale, CTHS spend schedule and task floor of
                  ``acceptor`` -> ``examine.py`` / ``accept.py``
    (3) enforce   a six-hook suite in settings.json: PreToolUse (confirmed
                  rules + path-level OWNERSHIP of the governed trees),
                  PostToolUse (change feed), UserPromptSubmit (trusted
                  origin), SessionStart + InstructionsLoaded (LIVE load
                  receipts), Stop (funnel counters)
                             -> ``hooks.py`` / ``_hooklib.py`` / ``ownership.py``
    (4) audit     the docket (confirm/reject/snooze, nothing piles up
                  silently), the funnel proposed->accepted->activated->
                  attributed, four STARVATION alarms, a spend meter, receipts,
                  a hash-chained ledger and snapshot/undo
                  -> ``docket.py`` / ``audit.py`` / ``live.py`` / ``snapshot.py``
    (5) re-evolve the nightly IMPROVER clusters failure signatures (tool
                  errors, retries, corrections with no precedent, "written but
                  violated"), asks for ONE bounded edit per top cluster, and
                  enforces diff == declared paths, a lint and the competent
                  gate before anything reaches the docket; rejected drafts come
                  back as negative examples -> ``improve.py`` / ``proposals.py``
                  ``loop.py`` runs the whole cycle once (``--dry-run``) or
                  prints the schedule for it (``--cron``).

Alongside the loop, ``bundle.py`` grades a **skill bundle** for an OpenClaw
``skill_proposal_evaluate`` hook: structure, DLP, evidence discipline, scope
leakage, the SafeEvolve baseline invariant, the risk delta and size, all
deterministic, all fail-closed — OpenClaw does not block on a thrown error, so
an internal failure becomes an explicit ``decision: "block"`` rather than an
exception (``precedent evaluate-bundle``).

Everything here is regex, set algebra and sha256.  Three commands can spend
money and all three are capped, recorded and optional: ``compile --llm`` and
``improve`` draft with ``claude -p`` (``llm.py``), and ``examine`` runs the
two-armed exam (``examine.py``).  Whatever they produce still has to survive
schema validation and a mechanical gate, and **precedent never applies an
edit**: an accepted proposal is a certificate and a docket entry, not a write.

Safety invariants, enforced mechanically and covered by tests:

* nothing is ever written under the Claude home this tool reads
  (:func:`precedent.state.guard_not_under_claude_home`), except
  ``hooks install/uninstall --apply``, which edits ``settings.json`` only after
  a timestamped backup under the state dir;
* every command that could touch a Claude home defaults to ``--dry-run``;
* every ``claude -p`` call carries ``--max-budget-usd``,
  ``--no-session-persistence`` and an explicit ``--model``, never
  ``--dangerously-skip-permissions``, and drafting calls additionally run
  ``--safe-mode --tools "" --disable-slash-commands`` so the proposer has no
  tools and no view of the state it is drafting against;
* all state lives under ``~/.precedent`` (``--state-dir`` in tests).
"""

__version__ = "0.1.0"
SCHEMA_VERSION = 1

__all__ = ["__version__", "SCHEMA_VERSION"]
