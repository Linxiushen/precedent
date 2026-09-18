#!/usr/bin/env python3
# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Build a small, synthetic Claude home so every number in the docs is runnable.

    python3 scripts/make_fixture_home.py /tmp/demo-home

The README shows a real ``precedent audit --share`` card.  A card printed from
the author's own ``~/.claude`` is not reproducible by a stranger and not
publishable either, so the documented card comes from *this* tree instead:
three sessions across two projects, with one instance of each failure the tool
reports.

    * ``alpha`` / ``beta``      two skills with near-identical descriptions
    * ``gamma``                 a skill on disk that no session ever listed
    * ``kept.md``               loads complete and is cited — the control
    * ``MEMORY.md``             210 index lines, of which only 200 reach the
                                model: **truncated**
    * ``ghost.md``              indexed, absent from disk: a **stale entry**
    * ``orphan.md``             on disk, indexed by nobody
    * a ``Bash`` heredoc        a write to the memory tree that **bypassed** the
                                memory tool
    * a subagent ``sed -i``     an **unattended** write from a background fork

It also deliberately plants the four things ``--share`` must never emit, so the
anonymisation has something to fail on: a fake API key, a home path, a project
name (``acme-payments``) and a session id.  ``scripts/dev.sh --demo`` builds the
tree and prints the card; the test suite plants the same four strings and
asserts none of them can reach a card.

The tree is deterministic: fixed session ids, fixed timestamps, fixed mtimes.
Two runs on two machines produce byte-identical files, so the documented card
is the card you get.

``--hostile`` builds a *second*, adversarial tree instead (:func:`build_hostile`).
The demo tree's shape is pinned by the README, so it cannot grow new plants
without moving every documented number; the hostile tree has no documentation
duty and carries one of every shape a reviewer would think of — credential,
phone number, email, WeChat id, home path, a project and a skill named after a
paying customer, session uuids and a git remote — each in a field a careless
renderer could pick up.  ``test_share_adversarial.py`` builds it and asserts
that not one of them reaches a card, in either language.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

# The planted secrets.  Every one of these is fake; the point is that the card
# must not contain them even though the transcripts do.
PLANTED_TOKEN = "sk-demo-NOTREAL-4f2b8c1d9e7a5306"          # noqa: S105 - fixture
PLANTED_EMAIL = "release-bot@acme-payments.invalid"
PROJECT_A_SLUG = "-Users-demo-code-acme-payments"
PROJECT_B_SLUG = "-Users-demo-code-side-project"
SESSION_1 = "a1b2c3d4-1111-4111-8111-aaaaaaaaaaaa"
SESSION_2 = "b2c3d4e5-2222-4222-8222-bbbbbbbbbbbb"
SESSION_3 = "c3d4e5f6-3333-4333-8333-cccccccccccc"

ALPHA_DESC = ("Publish a release note to the workspace doc tool: create, read, "
              "edit, export and share pages, and attach images to a page body.")
BETA_DESC = ("Publish a release note to the workspace doc tool: create, read, "
             "edit and share pages, and attach screenshots to the page body.")
GAMMA_DESC = ("Render an invoice PDF from a line-item table and email it to the "
              "customer on file.")

#: Eleven ordinary memory entries.  Ten of them are never cited by any session
#: in this tree, which is the whole point: "written but never loaded" is the
#: normal state of a memory store, not an exotic failure.
NOTES = [
    ("deploy-runbook", "the deploy runbook lives in ops/runbook.md, rotation weekly"),
    ("ci-timeout", "CI times out at 20 minutes; split the suite before raising it"),
    ("db-migrations", "migrations run forward-only, never squash a shipped one"),
    ("review-style", "small modules with explicit names over clever abstractions"),
    ("release-window", "releases go out Tuesday morning, never on a Friday"),
    ("secrets-policy", "credentials come from the environment, never from a file"),
    ("oncall", "the on-call rotation is weekly and starts Monday 10:00"),
    ("test-data", "fixtures are synthetic; never copy a row out of production"),
    ("lint-rules", "the linter is authoritative; do not disable a rule inline"),
    ("api-versioning", "API versions are additive; a field is never removed"),
    ("branch-policy", "feature branches rebase, the default branch never does"),
]

KEPT_BODY = ("The team reviews deliverables in the doc tool, not on disk; import "
             "the markdown, verify one page fetched back, then delete the local "
             "copy.\n")
KEPT_FILE = (
    "---\nname: kept\ndescription: publish deliverables to the doc tool, delete "
    "the local copy\nmetadata:\n  node_type: memory\n  type: feedback\n"
    f"  originSessionId: {SESSION_1}\n  modified: 2026-09-10T10:00:00.000Z\n"
    "---\n\n" + KEPT_BODY)
ORPHAN_FILE = (
    "---\nname: orphan\ndescription: a fact nobody indexed\n---\n\n"
    f"The staging dashboard needs the VPN; ask {PLANTED_EMAIL} for access.\n")
CLAUDE_MD = (
    "# Project rules\n\nAlways run the full test suite before you claim a change "
    "works, and never commit directly to the default branch without asking.\n")


def _skill(name: str, desc: str) -> str:
    return (f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n\n"
            f"Body of the {name} skill.\n")


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _stamp(path: str, iso: str) -> None:
    ts = _epoch(iso)
    os.utime(path, (ts, ts))


def _rec(rtype: str, ts: str, session: str, cwd: str, **kw) -> dict:
    base = {"type": rtype, "timestamp": ts, "sessionId": session, "cwd": cwd,
            "gitBranch": "main", "version": "2.1.268", "isSidechain": False,
            "uuid": f"{rtype}-{session[:8]}-{ts}"}
    base.update(kw)
    return base


def _human(ts, session, cwd, text):
    return _rec("user", ts, session, cwd, origin={"kind": "human"},
                promptSource="cli", permissionMode="default",
                message={"content": [{"type": "text", "text": text}]})


def _assistant(ts, session, cwd, text):
    return _rec("assistant", ts, session, cwd,
                message={"model": "claude-opus-5",
                         "content": [{"type": "text", "text": text}],
                         "usage": {"input_tokens": 1200, "output_tokens": 340,
                                   "cache_read_input_tokens": 8000,
                                   "cache_creation_input_tokens": 200}})


def _tool(ts, session, cwd, tid, name, inp):
    return _rec("assistant", ts, session, cwd,
                message={"model": "claude-opus-5",
                         "content": [{"type": "tool_use", "id": tid,
                                      "name": name, "input": inp}]})


def _result(ts, session, cwd, tid, structured):
    return _rec("user", ts, session, cwd,
                message={"content": [{"type": "tool_result",
                                      "tool_use_id": tid, "content": "ok"}]},
                toolUseResult=structured)


def _attachment(ts, session, cwd, attachment, rendered=None):
    rec = _rec("attachment", ts, session, cwd, attachment=attachment)
    if rendered:
        rec["rendered"] = [{"content": rendered}]
    return rec


def write_jsonl(path: str, records: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def build(root: str, force: bool = False) -> str:
    # realpath, not abspath: the transcripts record absolute paths, and
    # `receipts.scan` resolves the Claude home with os.path.realpath before it
    # classifies them.  On macOS a temp dir is /var/folders/… whose real path
    # is /private/var/folders/…, so a fixture written with abspath would have
    # every mutation silently fail to classify — the tree would look pristine
    # for the least interesting reason.
    home = os.path.realpath(os.path.expanduser(root))
    if os.path.exists(home):
        if not force:
            print(f"make_fixture_home: {home} already exists (use --force)",
                  file=sys.stderr)
            return home
        shutil.rmtree(home)

    ws_a = os.path.join(home, "_workspaces", "acme-payments")
    ws_b = os.path.join(home, "_workspaces", "side-project")
    mem_a = os.path.join(home, "projects", PROJECT_A_SLUG, "memory")
    os.makedirs(mem_a)
    os.makedirs(ws_a)
    os.makedirs(ws_b)
    os.makedirs(os.path.join(home, "projects", PROJECT_B_SLUG), exist_ok=True)

    for name, desc in (("alpha", ALPHA_DESC), ("beta", BETA_DESC),
                       ("gamma", GAMMA_DESC)):
        d = os.path.join(home, "skills", name)
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(_skill(name, desc))

    with open(os.path.join(ws_a, "CLAUDE.md"), "w", encoding="utf-8") as fh:
        fh.write(CLAUDE_MD)
    with open(os.path.join(mem_a, "kept.md"), "w", encoding="utf-8") as fh:
        fh.write(KEPT_FILE)
    with open(os.path.join(mem_a, "orphan.md"), "w", encoding="utf-8") as fh:
        fh.write(ORPHAN_FILE)

    # A memory store the size a real one reaches after a month.  Every entry but
    # `ghost.md` has a file behind it; `ghost.md` is the stale index entry.
    for name, desc in NOTES:
        with open(os.path.join(mem_a, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write(f"---\nname: {name}\ndescription: {desc}\n---\n\n"
                     f"{desc.capitalize()}. Recorded so the next session has it "
                     f"without being told again.\n")
    index_lines = (
        ["- [Kept](kept.md) — publish deliverables to the doc tool",
         "- [Orphan](orphan.md) — staging dashboard access"]
        + [f"- [{n.replace('-', ' ').title()}]({n}.md) — {d}" for n, d in NOTES]
        + ["- [Ghost](ghost.md) — deleted months ago, still indexed"])
    index_text = "\n".join(index_lines) + "\n"
    with open(os.path.join(mem_a, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write(index_text)

    system_prompt = [
        "You are an interactive agent that helps users with software engineering "
        "tasks.",
        "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__",
        "# Memory\n\nYou have a persistent file-based memory at "
        f"`{mem_a}`. Whenever you use or cite content from a memory, wrap the "
        "sentence in <cc-memory filenames=\"{comma separated memory file names}\"> "
        "tags.",
        "<system-reminder>\nRecalled memory (kept.md):\n" + KEPT_BODY
        + "\n</system-reminder>",
        "Contents of " + os.path.join(ws_a, "CLAUDE.md") + ":\n\n" + CLAUDE_MD,
    ]
    loaded_index = "\n".join(index_lines[:8])      # the tail never arrives
    listing = f"- alpha: {ALPHA_DESC}\n- beta: {BETA_DESC}\n"

    t = "2026-09-12T"
    s1 = [
        _attachment(t + "09:00:00.000Z", SESSION_1, ws_a,
                    {"type": "prompt_snapshot", "systemPrompt": system_prompt}),
        _attachment(t + "09:00:01.000Z", SESSION_1, ws_a,
                    {"type": "instructions", "reason": "session_start",
                     "changed": True,
                     "files": [{"path": os.path.join(mem_a, "MEMORY.md"),
                                "type": "AutoMem", "content": loaded_index}]},
                    rendered="<system-reminder>\n" + loaded_index
                             + "\n</system-reminder>"),
        _attachment(t + "09:00:02.000Z", SESSION_1, ws_a,
                    {"type": "skill_listing", "isInitial": True, "skillCount": 2,
                     "names": ["alpha", "beta"], "content": listing}),
        _human(t + "09:00:03.000Z", SESSION_1, ws_a,
               "cut the release and write the note"),
        _assistant(t + "09:01:00.000Z", SESSION_1, ws_a,
                   "<cc-memory filenames=\"kept.md\">You asked me to publish "
                   "deliverables to the doc tool.</cc-memory>"),
        _tool(t + "09:02:00.000Z", SESSION_1, ws_a, "tu_skill", "Skill",
              {"skill": "alpha"}),
        _result(t + "09:02:01.000Z", SESSION_1, ws_a, "tu_skill",
                {"success": True, "commandName": "alpha"}),
        _assistant(t + "09:03:00.000Z", SESSION_1, ws_a,
                   "Recording the publish preference so it survives the session."),
        _tool(t + "09:03:01.000Z", SESSION_1, ws_a, "tu_write", "Write",
              {"file_path": os.path.join(mem_a, "kept.md"), "content": KEPT_FILE}),
        _result(t + "09:03:02.000Z", SESSION_1, ws_a, "tu_write",
                {"type": "update", "filePath": os.path.join(mem_a, "kept.md"),
                 "content": KEPT_FILE, "originalFile": KEPT_FILE,
                 "memdirStamped": True}),
        # a write to the memory tree that never went through the memory tool
        _assistant(t + "09:04:00.000Z", SESSION_1, ws_a,
                   "Stamping the orphan note through a heredoc."),
        _tool(t + "09:04:01.000Z", SESSION_1, ws_a, "tu_bash", "Bash",
              {"command": f"cat >> {os.path.join(mem_a, 'orphan.md')} <<'EOF'\n"
                          "Staging needs the VPN.\nEOF"}),
        _result(t + "09:04:02.000Z", SESSION_1, ws_a, "tu_bash",
                {"stdout": "", "stderr": "", "interrupted": False}),
        # the planted credential, in a place `precedent mine` would quote
        _human(t + "09:05:00.000Z", SESSION_1, ws_a,
               f"use the release bot token {PLANTED_TOKEN} and mail "
               f"{PLANTED_EMAIL} when it is out"),
        _assistant(t + "09:05:10.000Z", SESSION_1, ws_a, "Understood."),
    ]
    write_jsonl(os.path.join(home, "projects", PROJECT_A_SLUG,
                             f"{SESSION_1}.jsonl"), s1)

    # a background fork editing a skill: unattended, and a subagent
    sub = [
        _assistant(t + "09:06:00.000Z", SESSION_1, ws_a,
                   "Bumping the gamma skill timeout."),
        _tool(t + "09:06:01.000Z", SESSION_1, ws_a, "tu_sub", "Bash",
              {"command": "sed -i '' 's/timeout=30/timeout=60/' "
                          + os.path.join(home, "skills", "gamma", "SKILL.md")}),
        _result(t + "09:06:02.000Z", SESSION_1, ws_a, "tu_sub",
                {"stdout": "", "stderr": "", "interrupted": False}),
    ]
    for r in sub:
        r["isSidechain"] = True
    write_jsonl(os.path.join(home, "projects", PROJECT_A_SLUG, SESSION_1,
                             "subagents", "agent-nightly.jsonl"), sub)

    # a second session in the same project that records nothing about its context
    s2 = [
        _human("2026-09-13T08:00:00.000Z", SESSION_2, ws_a, "ship the hotfix"),
        _assistant("2026-09-13T08:00:10.000Z", SESSION_2, ws_a, "Shipping it."),
        _tool("2026-09-13T08:01:00.000Z", SESSION_2, ws_a, "tu_edit", "Edit",
              {"file_path": os.path.join(ws_a, "CLAUDE.md"),
               "old_string": "never commit directly to the default branch",
               "new_string": "never commit directly to the default branch"}),
        _result("2026-09-13T08:01:01.000Z", SESSION_2, ws_a, "tu_edit",
                {"filePath": os.path.join(ws_a, "CLAUDE.md"),
                 "oldString": "never commit directly to the default branch",
                 "newString": "never commit directly to the default branch",
                 "replaceAll": False, "userModified": False}),

        # ---- the correction the quickstart is written around --------------
        # README's worked example is "use uv, not pip", and the fixture had no
        # corrections at all, so `mine` reported 0, the docket stayed empty,
        # and steps 2-4 of the documented six dead-ended on the tree we ship.
        # A quickstart that cannot be run on the fixture that ships with it is
        # a quickstart nobody can check.
        _tool("2026-09-13T08:02:00.000Z", SESSION_2, ws_a, "tu_pip1", "Bash",
              {"command": "pip install requests"}),
        _result("2026-09-13T08:02:01.000Z", SESSION_2, ws_a, "tu_pip1",
                {"stdout": "Successfully installed requests-2.32.3",
                 "stderr": "", "interrupted": False}),
        _human("2026-09-13T08:02:30.000Z", SESSION_2, ws_a,
               "don't use pip here, use uv — it is what the lockfile is for"),
        _assistant("2026-09-13T08:02:40.000Z", SESSION_2, ws_a,
                   "Understood — uv from here on."),

        # t0 is that turn.  The gate needs eligible actions after it that do
        # NOT fire, or the verdict is INSUFFICIENT rather than PASS.
        _tool("2026-09-13T08:03:00.000Z", SESSION_2, ws_a, "tu_uv1", "Bash",
              {"command": "uv add requests"}),
        _result("2026-09-13T08:03:01.000Z", SESSION_2, ws_a, "tu_uv1",
                {"stdout": "Resolved 4 packages", "stderr": "",
                 "interrupted": False}),
        _tool("2026-09-13T08:04:00.000Z", SESSION_2, ws_a, "tu_test", "Bash",
              {"command": "uv run pytest -q"}),
        _result("2026-09-13T08:04:01.000Z", SESSION_2, ws_a, "tu_test",
                {"stdout": "12 passed", "stderr": "", "interrupted": False}),
        _tool("2026-09-13T08:05:00.000Z", SESSION_2, ws_a, "tu_git", "Bash",
              {"command": "git status --short"}),
        _result("2026-09-13T08:05:01.000Z", SESSION_2, ws_a, "tu_git",
                {"stdout": "", "stderr": "", "interrupted": False}),
    ]
    write_jsonl(os.path.join(home, "projects", PROJECT_A_SLUG,
                             f"{SESSION_2}.jsonl"), s2)

    # a third session, in a different project
    s3 = [
        _human("2026-09-14T11:00:00.000Z", SESSION_3, ws_b,
               "set up the scratch repo"),
        _assistant("2026-09-14T11:00:05.000Z", SESSION_3, ws_b, "Setting it up."),
        _tool("2026-09-14T11:00:06.000Z", SESSION_3, ws_b, "tu_npm", "Bash",
              {"command": "npm install express"}),
        _result("2026-09-14T11:00:07.000Z", SESSION_3, ws_b, "tu_npm",
                {"stdout": "ok", "stderr": "", "interrupted": False}),

        # The same correction a second time, in a different project and a day
        # later: one correction is an incident, two is a topic, and the miner
        # groups by topic.  It is also what makes this rule worth enforcing
        # rather than worth remembering.
        _tool("2026-09-14T11:01:00.000Z", SESSION_3, ws_b, "tu_pip2", "Bash",
              {"command": "pip install flask"}),
        _result("2026-09-14T11:01:01.000Z", SESSION_3, ws_b, "tu_pip2",
                {"stdout": "Successfully installed flask-3.0.3", "stderr": "",
                 "interrupted": False}),
        _human("2026-09-14T11:01:20.000Z", SESSION_3, ws_b,
               "pip again — I said use uv"),
        _assistant("2026-09-14T11:01:30.000Z", SESSION_3, ws_b,
                   "Sorry — switching to uv."),
        _tool("2026-09-14T11:02:00.000Z", SESSION_3, ws_b, "tu_uv2", "Bash",
              {"command": "uv add flask"}),
        _result("2026-09-14T11:02:01.000Z", SESSION_3, ws_b, "tu_uv2",
                {"stdout": "Resolved 7 packages", "stderr": "",
                 "interrupted": False}),
        _tool("2026-09-14T11:03:00.000Z", SESSION_3, ws_b, "tu_ls", "Bash",
              {"command": "ls -la"}),
        _result("2026-09-14T11:03:01.000Z", SESSION_3, ws_b, "tu_ls",
                {"stdout": "total 0", "stderr": "", "interrupted": False}),
    ]
    write_jsonl(os.path.join(home, "projects", PROJECT_B_SLUG,
                             f"{SESSION_3}.jsonl"), s3)

    for rel, iso in (("projects/%s/memory/kept.md" % PROJECT_A_SLUG,
                      t + "09:03:03.000Z"),
                     ("projects/%s/memory/orphan.md" % PROJECT_A_SLUG,
                      t + "09:04:03.000Z"),
                     ("projects/%s/memory/MEMORY.md" % PROJECT_A_SLUG,
                      t + "08:59:00.000Z"),
                     ("skills/alpha/SKILL.md", t + "08:00:00.000Z"),
                     ("skills/beta/SKILL.md", t + "08:00:00.000Z"),
                     ("skills/gamma/SKILL.md", t + "09:06:03.000Z")):
        _stamp(os.path.join(home, rel), iso)
    for name, _desc in NOTES:
        _stamp(os.path.join(mem_a, name + ".md"), t + "08:30:00.000Z")
    _stamp(os.path.join(ws_a, "CLAUDE.md"), "2026-09-13T08:01:02.000Z")
    return home


# ---------------------------------------------------------------------------
# the adversarial fixture: everything a share card must never emit, at once
# ---------------------------------------------------------------------------
#
# The demo tree above plants four secrets and is *also* the tree the README's
# numbers come from, so its shape is pinned by the docs.  This second tree has
# no documentation duty: its only job is to be as hostile as a real machine can
# be to ``audit --share``.  It plants one of every shape a reviewer would think
# of — a credential, a phone number, an email, a WeChat id, a home path, a
# project named after a paying customer, a session uuid, a git remote and a
# skill named after that customer — and puts each one somewhere a careless
# renderer could pick it up: a project slug, a directory name, an artifact
# name, a YAML ``description``, a tool input, a memory body, a human turn.
#
#     python3 scripts/make_fixture_home.py /tmp/hostile --hostile
#     precedent audit --share --claude-home /tmp/hostile --state-dir /tmp/hs
#
# ``packages/precedent/tests/test_share_adversarial.py`` builds the same tree
# and asserts that not one of :data:`HOSTILE_PLANTS` survives into the card, in
# either language, in text or in JSON.

HOSTILE_COMPANY = "northwind-treasury"
HOSTILE_CLIENT_SKILL = "northwind-treasury-invoices"
HOSTILE_SECOND_SKILL = "northwind-treasury-billing"
HOSTILE_USER = "jdoe"
HOSTILE_SLUG = "-Users-jdoe-src-northwind-treasury"
HOSTILE_ABS_PATH = "/Users/jdoe/src/northwind-treasury"
HOSTILE_SESSION_1 = "deadbeef-1234-4abc-8def-feedfacecafe"
HOSTILE_SESSION_2 = "cafebabe-5678-4bcd-9012-0123456789ab"
HOSTILE_TOKEN = "sk-live-NOTREAL-9f3c1a7e0b5d2846"           # noqa: S105 - fake
HOSTILE_AWS_KEY = "AKIANOTREALKEY78EXAM"                     # noqa: S105 - fake
HOSTILE_EMAIL = "cfo@northwind-treasury.example"
HOSTILE_PHONE_CN = "13912345678"
HOSTILE_PHONE_INTL = "+1 415 555 0134"
HOSTILE_WECHAT_ID = "wangjian_nw88"
HOSTILE_GIT_SSH = "git@github.com:northwind-treasury/ledger-core.git"
HOSTILE_GIT_HTTPS = "https://github.com/northwind-treasury/ledger-core.git"

#: ``(what it is, the literal string)`` — the tests iterate this, so adding a
#: shape here is the only edit needed to widen the adversarial review.
HOSTILE_PLANTS = [
    ("api key", HOSTILE_TOKEN),
    ("aws key", HOSTILE_AWS_KEY),
    ("phone (cn)", HOSTILE_PHONE_CN),
    ("phone (intl)", HOSTILE_PHONE_INTL),
    ("email", HOSTILE_EMAIL),
    ("wechat id", HOSTILE_WECHAT_ID),
    ("home path", HOSTILE_ABS_PATH),
    ("unix account", HOSTILE_USER),
    ("customer name", HOSTILE_COMPANY),
    ("project slug", HOSTILE_SLUG),
    ("session uuid", HOSTILE_SESSION_1),
    ("session uuid", HOSTILE_SESSION_2),
    ("git remote (ssh)", HOSTILE_GIT_SSH),
    ("git remote (https)", HOSTILE_GIT_HTTPS),
    ("client-named skill", HOSTILE_CLIENT_SKILL),
    ("client-named skill", HOSTILE_SECOND_SKILL),
]

_H_SKILL_DESC = ("Raise and send a {kind} to the customer from the ledger "
                 "export, then file the PDF under the account directory.")

_H_NOTES = [
    ("wire-cutoff", "wires to the customer cut off at 15:00, anything later "
                    "settles the next business day"),
    ("ledger-export", "the ledger export runs nightly and lands in the account "
                      "directory as a dated CSV"),
    ("fx-rounding", "FX amounts round half-up at two decimals, never truncate"),
    ("vat-codes", "VAT codes come from the finance sheet, never from memory"),
    ("statement-cycle", "statements go out on the second business day"),
    ("dunning", "dunning mail is manual: draft it, never send automatically"),
    ("reconcile", "reconcile against the bank feed before closing a period"),
    ("audit-trail", "every ledger write keeps the operator id and a reason"),
]


def build_hostile(root: str, force: bool = False) -> str:
    """Build the adversarial Claude home.  Writes only under ``root``."""
    home = os.path.realpath(os.path.expanduser(root))
    if os.path.exists(home):
        if not force:
            print(f"make_fixture_home: {home} already exists (use --force)",
                  file=sys.stderr)
            return home
        shutil.rmtree(home)

    # the workspace directory is itself named after the customer
    ws = os.path.join(home, "_workspaces", HOSTILE_COMPANY)
    mem = os.path.join(home, "projects", HOSTILE_SLUG, "memory")
    os.makedirs(mem)
    os.makedirs(ws)

    # two skills named after the customer, with near-identical descriptions,
    # plus one nobody ever listed
    for name, kind in ((HOSTILE_CLIENT_SKILL, "invoice"),
                       (HOSTILE_SECOND_SKILL, "billing statement"),
                       ("scratch-helper", "receipt")):
        d = os.path.join(home, "skills", name)
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(_skill(name, _H_SKILL_DESC.format(kind=kind)))

    claude_md = (
        "# Project rules\n\n"
        f"The repository is {HOSTILE_GIT_HTTPS}; clone it over SSH as\n"
        f"`git clone {HOSTILE_GIT_SSH}` and keep the checkout at "
        f"{HOSTILE_ABS_PATH}.\nRun the full suite before you claim a change "
        "works.\n")
    with open(os.path.join(ws, "CLAUDE.md"), "w", encoding="utf-8") as fh:
        fh.write(claude_md)

    kept_body = ("Deliverables are published to the customer portal, not "
                 "emailed as attachments.\n")
    kept = ("---\nname: kept\ndescription: publish deliverables to the "
            "customer portal\nmetadata:\n  node_type: memory\n"
            f"  originSessionId: {HOSTILE_SESSION_1}\n"
            "  modified: 2026-09-10T10:00:00.000Z\n---\n\n" + kept_body)
    with open(os.path.join(mem, "kept.md"), "w", encoding="utf-8") as fh:
        fh.write(kept)

    # an artifact whose *name* is the customer, whose *description* is a phone
    # number and whose *body* is a credential: three different fields, one blast
    # radius
    with open(os.path.join(mem, f"{HOSTILE_COMPANY}-contacts.md"), "w",
              encoding="utf-8") as fh:
        fh.write(f"---\nname: {HOSTILE_COMPANY}-contacts\n"
                 f"description: the finance contact is {HOSTILE_PHONE_CN}, "
                 f"WeChat {HOSTILE_WECHAT_ID}\n---\n\n"
                 f"Finance: {HOSTILE_EMAIL}, {HOSTILE_PHONE_INTL}.\n"
                 f"微信：{HOSTILE_WECHAT_ID}\n"
                 f"Portal token {HOSTILE_TOKEN}; S3 key {HOSTILE_AWS_KEY}.\n")
    for name, desc in _H_NOTES:
        with open(os.path.join(mem, name + ".md"), "w", encoding="utf-8") as fh:
            fh.write(f"---\nname: {name}\ndescription: {desc}\n---\n\n"
                     f"{desc.capitalize()}.\n")

    index_lines = (
        ["- [Kept](kept.md) — publish deliverables to the customer portal",
         f"- [{HOSTILE_COMPANY.title()} Contacts]({HOSTILE_COMPANY}-contacts.md)"
         f" — finance contact {HOSTILE_PHONE_CN}"]
        + [f"- [{n.replace('-', ' ').title()}]({n}.md) — {d}"
           for n, d in _H_NOTES]
        + [f"- [Gone](gone.md) — deleted with the {HOSTILE_COMPANY} repo, "
           "still indexed"])
    with open(os.path.join(mem, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(index_lines) + "\n")

    system_prompt = [
        "You are an interactive agent that helps users with software "
        "engineering tasks.",
        "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__",
        f"# Memory\n\nYou have a persistent file-based memory at `{mem}`.",
        "<system-reminder>\nRecalled memory (kept.md):\n" + kept_body
        + "\n</system-reminder>",
        "Contents of " + os.path.join(ws, "CLAUDE.md") + ":\n\n" + claude_md,
    ]
    loaded_index = "\n".join(index_lines[:5])        # the tail never arrives
    listing = "".join(
        f"- {n}: {_H_SKILL_DESC.format(kind=k)}\n"
        for n, k in ((HOSTILE_CLIENT_SKILL, "invoice"),
                     (HOSTILE_SECOND_SKILL, "billing statement")))

    t = "2026-09-12T"
    s1 = [
        _attachment(t + "09:00:00.000Z", HOSTILE_SESSION_1, ws,
                    {"type": "prompt_snapshot", "systemPrompt": system_prompt}),
        _attachment(t + "09:00:01.000Z", HOSTILE_SESSION_1, ws,
                    {"type": "instructions", "reason": "session_start",
                     "changed": True,
                     "files": [{"path": os.path.join(mem, "MEMORY.md"),
                                "type": "AutoMem", "content": loaded_index}]},
                    rendered="<system-reminder>\n" + loaded_index
                             + "\n</system-reminder>"),
        _attachment(t + "09:00:02.000Z", HOSTILE_SESSION_1, ws,
                    {"type": "skill_listing", "isInitial": True, "skillCount": 2,
                     "names": [HOSTILE_CLIENT_SKILL, HOSTILE_SECOND_SKILL],
                     "content": listing}),
        _human(t + "09:00:03.000Z", HOSTILE_SESSION_1, ws,
               f"clone {HOSTILE_GIT_SSH} into {HOSTILE_ABS_PATH} and raise this "
               f"month's invoice"),
        _assistant(t + "09:01:00.000Z", HOSTILE_SESSION_1, ws,
                   "<cc-memory filenames=\"kept.md\">You publish deliverables "
                   "to the customer portal.</cc-memory>"),
        _tool(t + "09:02:00.000Z", HOSTILE_SESSION_1, ws, "tu_skill", "Skill",
              {"skill": HOSTILE_CLIENT_SKILL}),
        _result(t + "09:02:01.000Z", HOSTILE_SESSION_1, ws, "tu_skill",
                {"success": True, "commandName": HOSTILE_CLIENT_SKILL}),
        _tool(t + "09:03:00.000Z", HOSTILE_SESSION_1, ws, "tu_write", "Write",
              {"file_path": os.path.join(mem, "kept.md"), "content": kept}),
        _result(t + "09:03:01.000Z", HOSTILE_SESSION_1, ws, "tu_write",
                {"type": "update", "filePath": os.path.join(mem, "kept.md"),
                 "content": kept, "originalFile": kept, "memdirStamped": True}),
        # a write to the memory tree that never went through the memory tool
        _tool(t + "09:04:01.000Z", HOSTILE_SESSION_1, ws, "tu_bash", "Bash",
              {"command": "cat >> "
                          + os.path.join(mem, f"{HOSTILE_COMPANY}-contacts.md")
                          + f" <<'EOF'\nEscalation: {HOSTILE_PHONE_INTL}\nEOF"}),
        _result(t + "09:04:02.000Z", HOSTILE_SESSION_1, ws, "tu_bash",
                {"stdout": "", "stderr": "", "interrupted": False}),
        # every remaining shape, in the one place `precedent mine` would quote
        _human(t + "09:05:00.000Z", HOSTILE_SESSION_1, ws,
               f"use the portal token {HOSTILE_TOKEN} and the S3 key "
               f"{HOSTILE_AWS_KEY}; mail {HOSTILE_EMAIL}, or ring "
               f"{HOSTILE_PHONE_CN} / {HOSTILE_PHONE_INTL}; 微信 "
               f"{HOSTILE_WECHAT_ID} if that fails"),
        _assistant(t + "09:05:10.000Z", HOSTILE_SESSION_1, ws, "Understood."),
    ]
    write_jsonl(os.path.join(home, "projects", HOSTILE_SLUG,
                             f"{HOSTILE_SESSION_1}.jsonl"), s1)

    sub = [
        _assistant(t + "09:06:00.000Z", HOSTILE_SESSION_1, ws,
                   "Bumping the scratch skill timeout."),
        _tool(t + "09:06:01.000Z", HOSTILE_SESSION_1, ws, "tu_sub", "Bash",
              {"command": "sed -i '' 's/timeout=30/timeout=60/' "
                          + os.path.join(home, "skills", "scratch-helper",
                                         "SKILL.md")}),
        _result(t + "09:06:02.000Z", HOSTILE_SESSION_1, ws, "tu_sub",
                {"stdout": "", "stderr": "", "interrupted": False}),
    ]
    for r in sub:
        r["isSidechain"] = True
    write_jsonl(os.path.join(home, "projects", HOSTILE_SLUG, HOSTILE_SESSION_1,
                             "subagents", "agent-nightly.jsonl"), sub)

    s2 = [
        _human("2026-09-13T08:00:00.000Z", HOSTILE_SESSION_2, ws,
               f"push the fix to {HOSTILE_GIT_HTTPS}"),
        _assistant("2026-09-13T08:00:10.000Z", HOSTILE_SESSION_2, ws, "Pushing."),
        _tool("2026-09-13T08:01:00.000Z", HOSTILE_SESSION_2, ws, "tu_edit",
              "Edit", {"file_path": os.path.join(ws, "CLAUDE.md"),
                       "old_string": "Run the full suite",
                       "new_string": "Run the full suite"}),
        _result("2026-09-13T08:01:01.000Z", HOSTILE_SESSION_2, ws, "tu_edit",
                {"filePath": os.path.join(ws, "CLAUDE.md"),
                 "oldString": "Run the full suite",
                 "newString": "Run the full suite", "replaceAll": False,
                 "userModified": False}),
    ]
    write_jsonl(os.path.join(home, "projects", HOSTILE_SLUG,
                             f"{HOSTILE_SESSION_2}.jsonl"), s2)

    for rel, iso in ((f"projects/{HOSTILE_SLUG}/memory/kept.md",
                      t + "09:03:03.000Z"),
                     (f"projects/{HOSTILE_SLUG}/memory/MEMORY.md",
                      t + "08:59:00.000Z"),
                     (f"skills/{HOSTILE_CLIENT_SKILL}/SKILL.md",
                      t + "08:00:00.000Z"),
                     (f"skills/{HOSTILE_SECOND_SKILL}/SKILL.md",
                      t + "08:00:00.000Z"),
                     ("skills/scratch-helper/SKILL.md", t + "09:06:03.000Z")):
        _stamp(os.path.join(home, rel), iso)
    _stamp(os.path.join(mem, f"{HOSTILE_COMPANY}-contacts.md"),
           t + "09:04:03.000Z")
    for name, _desc in _H_NOTES:
        _stamp(os.path.join(mem, name + ".md"), t + "08:30:00.000Z")
    _stamp(os.path.join(ws, "CLAUDE.md"), "2026-09-13T08:01:02.000Z")
    return home


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="make_fixture_home.py",
        description="Build the synthetic Claude home the README's numbers come "
                    "from. Writes only under the directory you name.")
    ap.add_argument("root", help="directory to create (must not exist)")
    ap.add_argument("--force", action="store_true",
                    help="delete and rebuild if it already exists")
    ap.add_argument("--hostile", action="store_true",
                    help="build the ADVERSARIAL tree instead: one of every "
                         "shape `audit --share` must never emit (credential, "
                         "phone, email, WeChat id, home path, customer-named "
                         "project and skill, session uuid, git remote)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    real = os.path.realpath(os.path.expanduser(args.root))
    if real == os.path.realpath(os.path.expanduser("~/.claude")) or \
            real.startswith(os.path.realpath(os.path.expanduser("~/.claude"))
                            + os.sep):
        print("make_fixture_home: refusing to build inside your real ~/.claude",
              file=sys.stderr)
        return 2
    builder = build_hostile if args.hostile else build
    home = builder(real, force=args.force)
    if not args.quiet:
        print(home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
