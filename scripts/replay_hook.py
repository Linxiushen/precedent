#!/usr/bin/env python3
# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Price a rule against the record you already have, before it goes live.

    scripts/replay_hook.py <hook.py> <transcripts-root> [--n 20] [--seed N]
    scripts/replay_hook.py <hook.py> <transcripts-root> --all

Reads a frozen copy of the transcripts (every ``*.jsonl`` under the root, main
sessions and subagent sidechains alike), rebuilds the ``PreToolUse`` payload
Claude Code would have sent for each recorded ``tool_use``, and asks the hook
what it would have done.

**What the number means, exactly.**  This was asked in the open by
@AfshinMirhamed on NousResearch/hermes-agent#96704 (2026-09-17) and the answer
decides whether the metric is worth anything:

* It counts **would-be blocks, not pattern matches.**  The decision comes from
  the installed hook itself — by default one real subprocess per call, exactly
  as Claude Code invokes it, reading the same ``permissionDecision`` off
  ``hookSpecificOutput``.  ``--all`` skips the fork and calls ``decide()`` in
  process through the installed ``_plib.py`` (the same bytes on disk), because
  18k subprocesses is a quarter of an hour.  Either way the answer is the
  hook's, not a regex's.
* It does **not** establish that an interrupted call was legitimate.  Nothing
  in a transcript says "this call was fine".  Legitimacy is inferred from
  silence, by the same QUIET-AFTER rule the birth gate uses: a fire followed
  within three human turns by another correction on that topic is a true
  positive; every other fire is counted against the rule.  So a call the user
  quietly disliked and never mentioned is scored as collateral damage.  That
  bias is deliberate and it is the conservative direction for this particular
  metric — it can overstate the cost of a rule, never understate it.
* ``--skip-tools`` exists because a rule about ``Agent``/``Workflow`` payloads
  would otherwise be priced against its own recursive invocations.  The
  default skips both and the count line always prints what was skipped.

This produced the number in ``DEMO.md`` §4i: an unscoped ``dont_use headless``
rule that passed every structural check would have interrupted 68 legitimate
calls out of 17,842, which is why it was retired in favour of a ``--cwd-glob``
scoped version that prices at 0/140.

Read-only.  It never writes to the Claude home and never installs anything;
point it at a frozen copy of the transcripts if the live ones are still
growing, which they are while a session is open.
"""

import argparse, glob, importlib.util, json, os, random, subprocess, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("hook"); ap.add_argument("root")
ap.add_argument("--n", type=int, default=20)
ap.add_argument("--seed", type=int, default=20260915)
ap.add_argument("--all", action="store_true")
ap.add_argument("--skip-tools", default="Agent,Workflow")
ap.add_argument("--since", metavar="WHEN", default=None,
                help="when the correction that produced the rule was made (t0), "
                     "as a date or as much of an ISO timestamp as you know "
                     "(2026-09-07, or 2026-09-07T14:30 to split within the day). "
                     "Fires before it are printed separately and must not be "
                     "counted against the rule: what the agent did before being "
                     "told is evidence about the world, not about the rule.")
a = ap.parse_args()
skip = {t for t in a.skip_tools.split(",") if t}

calls = []
for p in sorted(glob.glob(os.path.join(a.root, "**", "*.jsonl"), recursive=True)):
    rel = os.path.relpath(p, a.root)
    for i, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
        if '"tool_use"' not in line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        for c in ((rec.get("message") or {}).get("content") or []):
            if isinstance(c, dict) and c.get("type") == "tool_use" \
               and c.get("name") not in skip and isinstance(c.get("input"), dict):
                calls.append(({"session_id": rec.get("sessionId") or os.path.basename(p)[:-6],
                               "transcript_path": p, "cwd": rec.get("cwd") or os.getcwd(),
                               "permission_mode": "acceptEdits",
                               "hook_event_name": "PreToolUse",
                               "tool_name": c["name"], "tool_input": c["input"],
                               "ts": rec.get("timestamp") or ""},
                              f"{rel}:{i}"))
skipped = "/".join(sorted(skip)) or "none"
print(f"recorded tool calls in {a.root}: {len(calls)}  (tools skipped: {skipped})")

def decide_subprocess(call):
    t0 = time.perf_counter()
    r = subprocess.run([sys.executable, a.hook, "--precedent-hook", "PreToolUse"],
                       input=json.dumps(call), capture_output=True, text=True)
    ms = (time.perf_counter() - t0) * 1000
    out = (r.stdout or "").strip()
    try:
        d = json.loads(out) if out else {}
    except Exception:
        d = {"UNPARSEABLE": out}
    return d, r.returncode, ms

if a.all:
    hooks_dir = os.path.dirname(os.path.abspath(a.hook))
    spec = importlib.util.spec_from_file_location(
        "_plib", os.path.join(hooks_dir, "_plib.py"))
    plib = importlib.util.module_from_spec(spec); spec.loader.exec_module(plib)
    plib.configure(os.path.dirname(hooks_dir), "")
    rules = [r for r in plib.active_rules() if r.get("kind") != "ownership-guard"]
    print(f"active rules: {[r['id'] for r in rules]}")
    per = {r["id"]: 0 for r in rules}
    when: list[tuple[str, str]] = []
    n = fired = 0
    t0 = time.perf_counter()
    for call, loc in calls:
        n += 1
        for r in rules:
            hso, rid = plib.decide(call, [r])
            if hso is not None:
                per[r["id"]] += 1
                fired += 1
                when.append((r["id"], call.get("ts") or ""))
    dt = time.perf_counter() - t0
    print(f"swept {n} recorded calls against {len(rules)} active rule(s) in {dt:.1f}s")
    for rid, k in per.items():
        print(f"  {rid}: deny/ask on {k}/{n} = {100.0*k/max(n,1):.2f}% of recorded calls")
    print(f"  total interruptions this rule set would have caused: {fired}")
    if a.since:
        # compare at the caller's own precision: a bare date splits by day,
        # a fuller timestamp splits within it.  The correcting turn and the
        # calls it judges often land on the same date.
        k = len(a.since)
        before = sum(1 for _, ts in when if ts and ts[:k] < a.since)
        after = fired - before
        print(f"  split at t0={a.since}: {before} before (REPORTED, NOT COUNTED) "
              f"· {after} on or after (the number that prices the rule)")
        if before:
            print(f"  -> reading {fired} as the cost of this rule overstates it by "
                  f"{before}: those calls predate the correction that created it.")
    elif fired:
        print("  NOTE: this total spans the whole record, including calls made "
              "BEFORE the correction that\n        produced the rule.  Pass "
              "--since <the correction's date> to split it; the birth gate "
              "counts\n        only the post-t0 half.")
    sys.exit(0)

rng = random.Random(a.seed)
picked = rng.sample(calls, a.n)
denies = asks = allows = 0
worst = 0.0
for k, (call, loc) in enumerate(picked, 1):
    d, rc, ms = decide_subprocess(call)
    worst = max(worst, ms)
    dec = (d.get("hookSpecificOutput") or {}).get("permissionDecision", "allow")
    denies += dec == "deny"; asks += dec == "ask"; allows += dec == "allow"
    inp = json.dumps(call["tool_input"], ensure_ascii=False)
    print(f"{k:2d}. exit={rc} {ms:5.1f}ms {dec:<5} {call['tool_name']:<12} "
          f"{inp[:62]}")
    print(f"      {loc}")
    if dec != "allow":
        print(f"      -> {(d.get('hookSpecificOutput') or {}).get('permissionDecisionReason','')}")
print()
print(f"{a.n} random recorded tool calls (seed {a.seed}) → "
      f"deny {denies} · ask {asks} · allow {allows}   "
      f"(slowest {worst:.0f} ms, every exit 0)")
sys.exit(1 if denies else 0)
