# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""A synthetic, fully-controlled fake ``~/.claude`` tree.

Every fixture here writes ONLY under pytest's ``tmp_path``.  The real Claude home
is never touched by the test suite.

The tree deliberately contains one instance of each failure mode the tool exists
to report:

* ``kept.md``     pre-exists the session, loads complete, is cited with a
                  ``<cc-memory>`` tag and is then updated by a ``Write``
* ``MEMORY.md``   has 210 index lines — past the documented 200-line cap — and the
                  session's ``instructions`` payload carries only the first 200
* ``ghost.md``    is linked from the index but does not exist (stale entry)
* ``orphan.md``   exists in the memory dir but nothing links to it (orphan)
* ``drifted.md``  was written by a recorded session but differs on disk (out-of-band)
* ``touched.md``  matches its recorded write byte for byte but its mtime is two days
                  later than that write (out-of-band by mtime)
* ``alpha`` / ``beta`` skills share a description (near-duplicate)
* ``gamma``       is on disk but absent from the session's skill listing
* mutations arrive via Write, Edit and a Bash heredoc, each with a stated reason
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _stamp(path: str, iso: str) -> None:
    """Pin a file's mtime so the out-of-band mtime check is deterministic."""
    ts = _epoch(iso)
    os.utime(path, (ts, ts))

SESSION_1 = "11111111-1111-4111-8111-111111111111"
SESSION_2 = "22222222-2222-4222-8222-222222222222"
SLUG = "-tmp-workspace"

KEPT_BODY = (
    "Leon prefers deliverables published to the doc tool rather than left on disk.\n"
    "\n"
    "**Why:** he reviews everything in one place and a second local copy drifts.\n"
    "\n"
    "**How to apply:** import the markdown, verify one page fetched back, then "
    "delete the local copy. Never delete templates he still has to fill in himself.\n"
)
KEPT_FILE = (
    "---\n"
    "name: kept\n"
    "description: publish deliverables to the doc tool, delete the local copy\n"
    "metadata:\n"
    "  node_type: memory\n"
    "  type: feedback\n"
    f"  originSessionId: {SESSION_1}\n"
    "  modified: 2026-09-10T10:00:00.000Z\n"
    "---\n"
    "\n" + KEPT_BODY
)

KEPT_FILE_BEFORE = KEPT_FILE.replace(
    "Never delete templates he still has to fill in himself.",
    "Delete every local copy once the import is confirmed.")

ORPHAN_FILE = (
    "---\n"
    "name: orphan\n"
    "description: a fact nobody indexed\n"
    "metadata:\n"
    "  type: reference\n"
    "---\n"
    "\nThe staging dashboard lives at https://example.invalid/staging and needs the "
    "VPN. Nothing in MEMORY.md points at this file, so recall will never surface it.\n"
)

DRIFTED_WRITTEN = (
    "---\nname: drifted\ndescription: a fact that someone changed behind the agent's back\n---\n"
    "\nThe deploy runbook lives in ops/runbook.md and the on-call rotation is weekly.\n"
)
DRIFTED_ON_DISK = (
    "---\nname: drifted\ndescription: a fact that someone changed behind the agent's back\n---\n"
    "\nSomebody edited this by hand: the runbook moved to ops/deploy/runbook.md and the "
    "rotation is now daily, which no recorded session ever wrote.\n"
)

TOUCHED_FILE = (
    "---\nname: touched\ndescription: content nobody changed, mtime somebody did\n---\n"
    "\nThe nightly job runs at 03:00 UTC and writes its log to var/log/nightly.log.\n"
)

CLAUDE_MD = (
    "# Project rules\n"
    "\n"
    "Always run the full test suite before you claim a change works, and never "
    "commit directly to the default branch without asking first.\n"
    "\n"
    "## Style\n"
    "\n"
    "Prefer small modules with explicit names over one large file with clever "
    "abstractions that nobody can grep for later.\n"
)

ALPHA_DESC = ("Publish a document to the workspace doc tool: create, read, edit, export "
              "and share pages, and attach images to a page body.")
BETA_DESC = ("Publish a document to the workspace doc tool: create, read, edit and "
             "share pages, and attach screenshots to the page body.")
GAMMA_DESC = "Render an invoice PDF from a line-item table and email it to the customer."


def _skill(desc: str, name: str) -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n\nBody of the {name} skill.\n"


def write_jsonl(path: str, records: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _rec(rtype: str, ts: str, session: str, cwd: str, **kw) -> dict:
    base = {
        "type": rtype, "timestamp": ts, "sessionId": session, "cwd": cwd,
        "gitBranch": "main", "version": "2.1.268", "isSidechain": False,
        "uuid": f"{rtype}-{ts}",
    }
    base.update(kw)
    return base


def _assistant_text(ts: str, session: str, cwd: str, text: str) -> dict:
    return _rec("assistant", ts, session, cwd,
                message={"model": "claude-opus-5", "content": [{"type": "text", "text": text}],
                         "usage": {"input_tokens": 10, "output_tokens": 20,
                                   "cache_read_input_tokens": 5,
                                   "cache_creation_input_tokens": 1}})


def _tool_use(ts: str, session: str, cwd: str, tid: str, name: str, inp: dict) -> dict:
    return _rec("assistant", ts, session, cwd,
                message={"model": "claude-opus-5",
                         "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]})


def _tool_result(ts: str, session: str, cwd: str, tid: str, structured: dict) -> dict:
    return _rec("user", ts, session, cwd,
                message={"content": [{"type": "tool_result", "tool_use_id": tid,
                                      "content": "ok"}]},
                toolUseResult=structured)


def _attachment(ts: str, session: str, cwd: str, attachment: dict, rendered: str | None = None) -> dict:
    rec = _rec("attachment", ts, session, cwd, attachment=attachment)
    if rendered:
        rec["rendered"] = [{"content": rendered}]
    return rec


@pytest.fixture
def fake_home(tmp_path):
    """Build the tree and return a small namespace of the interesting paths."""
    home = tmp_path / "claude"
    workspace = tmp_path / "workspace"
    mem = home / "projects" / SLUG / "memory"
    mem.mkdir(parents=True)
    workspace.mkdir()
    (workspace / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8")

    rules = workspace / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "security.md").write_text(
        "# Security rule\n\nNever write a credential into a file that git tracks; "
        "put it in the environment and reference it by name instead.\n", encoding="utf-8")

    for name, desc in (("alpha", ALPHA_DESC), ("beta", BETA_DESC), ("gamma", GAMMA_DESC)):
        d = home / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(_skill(desc, name), encoding="utf-8")

    (mem / "kept.md").write_text(KEPT_FILE, encoding="utf-8")
    (mem / "orphan.md").write_text(ORPHAN_FILE, encoding="utf-8")
    (mem / "drifted.md").write_text(DRIFTED_ON_DISK, encoding="utf-8")
    (mem / "touched.md").write_text(TOUCHED_FILE, encoding="utf-8")

    # 210 index lines: 200 fit under the cap, 10 do not.  Line 3 is stale.
    index_lines = [
        "- [Kept](kept.md) — publish deliverables to the doc tool, delete the local copy",
        "- [Drifted](drifted.md) — deploy runbook and on-call rotation",
        "- [Touched](touched.md) — nightly job schedule",
        "- [Ghost](ghost.md) — this file was deleted but the index still points at it",
    ]
    index_lines += [f"- [Filler {i}](filler-{i}.md) — synthetic index line number {i}"
                    for i in range(4, 211)]
    index_text = "\n".join(index_lines) + "\n"
    (mem / "MEMORY.md").write_text(index_text, encoding="utf-8")

    cwd = str(workspace)
    loaded_index = "\n".join(index_lines[:200])  # the cap truncates the tail

    skill_listing_content = (
        f"- alpha: {ALPHA_DESC}\n"
        f"- beta: {BETA_DESC}\n"
    )
    system_prompt = [
        "You are an interactive agent that helps users with software engineering tasks.",
        "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__",
        "# Memory\n\nYou have a persistent file-based memory at "
        f"`{mem}`. Whenever you use or cite content from a memory, wrap the sentence in "
        "<cc-memory filenames=\"{comma separated memory file names}\"> tags.",
        "<system-reminder>\nRecalled memory (kept.md):\n" + KEPT_BODY + "\n</system-reminder>",
        "Contents of " + str(workspace / "CLAUDE.md") + ":\n\n" + CLAUDE_MD,
    ]

    t = "2026-09-12T"
    s1 = [
        _attachment(t + "09:00:00.000Z", SESSION_1, cwd,
                    {"type": "prompt_snapshot", "systemPrompt": system_prompt}),
        _attachment(t + "09:00:01.000Z", SESSION_1, cwd,
                    {"type": "instructions", "reason": "session_start", "changed": True,
                     "files": [{"path": str(mem / "MEMORY.md"), "type": "AutoMem",
                                "content": loaded_index}]},
                    rendered="<system-reminder>\n" + loaded_index + "\n</system-reminder>"),
        _attachment(t + "09:00:02.000Z", SESSION_1, cwd,
                    {"type": "skill_listing", "isInitial": True, "skillCount": 2,
                     "names": ["alpha", "beta"], "content": skill_listing_content}),
        _rec("user", t + "09:00:03.000Z", SESSION_1, cwd, origin={"kind": "human"},
             message={"content": [{"type": "text", "text": "Update the memory please."}]}),
        _assistant_text(t + "09:01:00.000Z", SESSION_1, cwd,
                        "<cc-memory filenames=\"kept.md\">You asked me to publish "
                        "deliverables to the doc tool rather than leaving them on disk.</cc-memory>"),
        _assistant_text(t + "09:02:00.000Z", SESSION_1, cwd,
                        "Recording the publish-to-doc-tool preference as a memory so it "
                        "survives this session."),
        _tool_use(t + "09:02:01.000Z", SESSION_1, cwd, "tu_write_kept", "Write",
                  {"file_path": str(mem / "kept.md"), "content": KEPT_FILE}),
        _tool_result(t + "09:02:02.000Z", SESSION_1, cwd, "tu_write_kept",
                     {"type": "update", "filePath": str(mem / "kept.md"),
                      "content": KEPT_FILE, "originalFile": KEPT_FILE_BEFORE,
                      "memdirStamped": True}),
        _rec("file-history-delta", t + "09:02:03.000Z", SESSION_1, cwd,
             trackingPath=str(mem / "kept.md"),
             backup={"version": 1, "backupTime": t + "09:02:02.900Z"}),
        _assistant_text(t + "09:03:00.000Z", SESSION_1, cwd,
                        "Tightening the project rule about the default branch."),
        _tool_use(t + "09:03:01.000Z", SESSION_1, cwd, "tu_edit_claude", "Edit",
                  {"file_path": str(workspace / "CLAUDE.md"),
                   "old_string": "never commit directly to the default branch",
                   "new_string": "never commit directly to the default branch",
                   "replace_all": False}),
        _tool_result(t + "09:03:02.000Z", SESSION_1, cwd, "tu_edit_claude",
                     {"filePath": str(workspace / "CLAUDE.md"),
                      "oldString": "never commit directly to the default branch",
                      "newString": "never commit directly to the default branch",
                      "replaceAll": False, "userModified": False}),
        _assistant_text(t + "09:04:00.000Z", SESSION_1, cwd,
                        "Stamping the orphan note through a heredoc because the "
                        "replacement spans several lines."),
        _tool_use(t + "09:04:01.000Z", SESSION_1, cwd, "tu_bash_orphan", "Bash",
                  {"command": f"M={mem / 'orphan.md'} && python3 - \"$M\" <<'EOF'\n"
                              "import sys\np = sys.argv[1]\n"
                              "s = open(p).read()\n"
                              "open(p, 'w').write(s.replace('VPN', 'VPN (see ops)'))\n"
                              "EOF"}),
        _tool_result(t + "09:04:02.000Z", SESSION_1, cwd, "tu_bash_orphan",
                     {"stdout": "", "stderr": "", "interrupted": False}),
        _assistant_text(t + "09:05:00.000Z", SESSION_1, cwd,
                        "Writing the deploy runbook fact so the next session has it."),
        _tool_use(t + "09:05:01.000Z", SESSION_1, cwd, "tu_write_drift", "Write",
                  {"file_path": str(mem / "drifted.md"), "content": DRIFTED_WRITTEN}),
        _tool_result(t + "09:05:02.000Z", SESSION_1, cwd, "tu_write_drift",
                     {"type": "create", "filePath": str(mem / "drifted.md"),
                      "content": DRIFTED_WRITTEN, "originalFile": None}),
        _assistant_text(t + "09:05:30.000Z", SESSION_1, cwd,
                        "Saving the nightly job schedule."),
        _tool_use(t + "09:05:31.000Z", SESSION_1, cwd, "tu_write_touched", "Write",
                  {"file_path": str(mem / "touched.md"), "content": TOUCHED_FILE}),
        _tool_result(t + "09:05:32.000Z", SESSION_1, cwd, "tu_write_touched",
                     {"type": "create", "filePath": str(mem / "touched.md"),
                      "content": TOUCHED_FILE, "originalFile": None}),
        _tool_use(t + "09:06:00.000Z", SESSION_1, cwd, "tu_skill", "Skill",
                  {"skill": "alpha"}),
        _tool_result(t + "09:06:01.000Z", SESSION_1, cwd, "tu_skill",
                     {"success": True, "commandName": "alpha"}),
        _tool_use(t + "09:07:00.000Z", SESSION_1, cwd, "tu_read", "Bash",
                  {"command": f"cat {mem / 'kept.md'} && ls {mem}"}),
        _tool_result(t + "09:07:01.000Z", SESSION_1, cwd, "tu_read",
                     {"stdout": "...", "stderr": "", "interrupted": False}),
    ]
    write_jsonl(str(home / "projects" / SLUG / f"{SESSION_1}.jsonl"), s1)

    # A subagent of session 1 writes a skill file: it must fold into the parent
    # session's feed and be marked as a sidechain write.
    sub = [
        _assistant_text(t + "09:08:00.000Z", SESSION_1, cwd,
                        "Bumping the gamma skill timeout the user asked for."),
        _tool_use(t + "09:08:01.000Z", SESSION_1, cwd, "tu_sub_sed", "Bash",
                  {"command": "sed -i '' 's/timeout=30/timeout=60/' "
                              f"{home / 'skills' / 'gamma' / 'SKILL.md'}"}),
        _tool_result(t + "09:08:02.000Z", SESSION_1, cwd, "tu_sub_sed",
                     {"stdout": "", "stderr": "", "interrupted": False}),
    ]
    for r in sub:
        r["isSidechain"] = True
    write_jsonl(str(home / "projects" / SLUG / SESSION_1 / "subagents" /
                    "agent-abc123.jsonl"), sub)

    # Session 2 records nothing about its context -> everything must read `unknown`.
    s2 = [
        _rec("user", "2026-09-13T08:00:00.000Z", SESSION_2, cwd, origin={"kind": "human"},
             message={"content": [{"type": "text", "text": "hello"}]}),
        _assistant_text("2026-09-13T08:00:10.000Z", SESSION_2, cwd, "Hi."),
    ]
    write_jsonl(str(home / "projects" / SLUG / f"{SESSION_2}.jsonl"), s2)

    # Pin mtimes so the mtime-based out-of-band check is deterministic:
    # every file sits just after the write that produced it, except touched.md,
    # which someone `touch`ed two days later without changing a byte.
    _stamp(str(mem / "kept.md"), t + "09:02:03.000Z")
    _stamp(str(mem / "orphan.md"), t + "09:04:03.000Z")
    _stamp(str(mem / "drifted.md"), t + "09:05:03.000Z")
    _stamp(str(mem / "touched.md"), "2026-09-14T09:05:32.000Z")
    _stamp(str(mem / "MEMORY.md"), t + "08:59:00.000Z")
    _stamp(str(workspace / "CLAUDE.md"), t + "09:03:03.000Z")
    _stamp(str(home / "skills" / "gamma" / "SKILL.md"), t + "09:08:03.000Z")

    class Tree:
        pass

    tree = Tree()
    tree.home = str(home)
    tree.workspace = str(workspace)
    tree.memory = str(mem)
    tree.slug = SLUG
    tree.session_1 = SESSION_1
    tree.session_2 = SESSION_2
    tree.index_text = index_text
    return tree
