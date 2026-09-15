# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Synthetic fake homes.  Everything here writes ONLY under ``tmp_path``.

Two fixtures:

``fake_home``
    re-exported from ``packages/receipts/tests/conftest.py`` — the tree that
    already covers a complete load, a 210-line truncated index, a stale entry,
    an orphan, Write/Edit/Bash mutations and an out-of-band change.  Used for
    init / scan / snapshot / undo / report.

``mining_home``
    six sessions built here, with **planted corrections** whose ground truth
    the mining tests assert against:

    ==========  ==================================================  ==========
    session     content                                             topic
    ==========  ==================================================  ==========
    S1          Bash ``pip install requests`` then "不要用 pip，用 uv"  pip
                plus four skill/command expansions that must be skipped
                plus a Write to ``notes.md`` (uncorrected Edit/Write traffic)
    S2          Bash ``pip install flask`` then "我说过了，别用 pip，改用 uv"  pip
    S3          Edit ``protected.md`` then "不要修改 protected.md 这个文件"  path
    S4          Write ``protected.md`` then the same correction        path
    S5          Bash ``curl …`` then "stop using curl, use httpie instead"  curl
    S6          Bash ``curl …`` then "don't use curl again, i said httpie"  curl
    S7          Bash ``npm install express``, no correction            —
    ==========  ==================================================  ==========

    Every session also holds at least one *non*-correction human turn, so the
    "% of human turns" denominator is real, and S1 holds a decoy turn built
    from 特别 / 别的 / 不用担心 that the lookaround guards must **not** flag.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys

import pytest

# --------------------------------------------------------------------------
# reuse the receipts fixture tree rather than rebuilding it
# --------------------------------------------------------------------------

_RECEIPTS_CONFTEST = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "receipts", "tests", "conftest.py"))

if os.path.isfile(_RECEIPTS_CONFTEST):
    _spec = importlib.util.spec_from_file_location("receipts_test_fixtures",
                                                   _RECEIPTS_CONFTEST)
    receipts_fixtures = importlib.util.module_from_spec(_spec)
    sys.modules["receipts_test_fixtures"] = receipts_fixtures
    _spec.loader.exec_module(receipts_fixtures)
    fake_home = receipts_fixtures.fake_home            # re-exported fixture
    write_jsonl = receipts_fixtures.write_jsonl
    KEPT_FILE = receipts_fixtures.KEPT_FILE
    KEPT_FILE_BEFORE = receipts_fixtures.KEPT_FILE_BEFORE
    DRIFTED_ON_DISK = receipts_fixtures.DRIFTED_ON_DISK
    RECEIPTS_SESSION_1 = receipts_fixtures.SESSION_1
else:                                                  # pragma: no cover
    raise RuntimeError(f"receipts test fixtures not found at {_RECEIPTS_CONFTEST}; "
                       "precedent's tests extend them by design")


# --------------------------------------------------------------------------
# the mining tree
# --------------------------------------------------------------------------

SLUG = "-tmp-proj"
S = {n: f"{n}{n}{n}{n}{n}{n}{n}{n}-{n}{n}{n}{n}-4{n}{n}{n}-8{n}{n}{n}-"
        f"{n}{n}{n}{n}{n}{n}{n}{n}{n}{n}{n}{n}" for n in "1234567"}

CLAUDE_MD_MINING = (
    "# Project rules\n"
    "\n"
    "Dependency management: this repo uses uv. Do not reach for pip install; "
    "`uv add` and `uv pip install` are the only supported paths.\n"
    "\n"
    "## Memory\n"
    "\n"
    "`protected.md` is hand-maintained. Leave it alone.\n"
)


def _rec(rtype, ts, session, cwd, **kw):
    base = {"type": rtype, "timestamp": ts, "sessionId": session, "cwd": cwd,
            "gitBranch": "main", "version": "2.1.268", "isSidechain": False,
            "uuid": f"{rtype}-{session[:4]}-{ts}"}
    base.update(kw)
    return base


def _human(ts, session, cwd, text):
    return _rec("user", ts, session, cwd, origin={"kind": "human"},
                promptSource="cli", permissionMode="default",
                message={"content": [{"type": "text", "text": text}]})


def _expansion(ts, session, cwd, text, marker=None):
    """A skill / slash-command expansion: a user record that is not a human."""
    extra = {marker: True} if marker else {}
    return _rec("user", ts, session, cwd,
                message={"content": [{"type": "text", "text": text}]}, **extra)


def _assistant(ts, session, cwd, text):
    return _rec("assistant", ts, session, cwd,
                message={"model": "claude-opus-5",
                         "content": [{"type": "text", "text": text}],
                         "usage": {"input_tokens": 10, "output_tokens": 5,
                                   "cache_read_input_tokens": 0,
                                   "cache_creation_input_tokens": 0}})


def _tool(ts, session, cwd, tid, name, inp):
    return _rec("assistant", ts, session, cwd,
                message={"model": "claude-opus-5",
                         "content": [{"type": "tool_use", "id": tid,
                                      "name": name, "input": inp}]})


def _result(ts, session, cwd, tid, structured):
    return _rec("user", ts, session, cwd,
                message={"content": [{"type": "tool_result", "tool_use_id": tid,
                                      "content": "ok"}]},
                toolUseResult=structured)


@pytest.fixture
def mining_home(tmp_path):
    home = tmp_path / "claude"
    workspace = tmp_path / "proj"
    mem = home / "projects" / SLUG / "memory"
    mem.mkdir(parents=True)
    workspace.mkdir()
    (workspace / "CLAUDE.md").write_text(CLAUDE_MD_MINING, encoding="utf-8")
    protected = mem / "protected.md"
    protected.write_text("---\nname: protected\ndescription: hand-maintained "
                         "notes about the release process\n---\n\nDo not edit.\n",
                         encoding="utf-8")
    (mem / "notes.md").write_text("---\nname: notes\ndescription: scratch notes "
                                  "about the build\n---\n\nScratch.\n", encoding="utf-8")
    (mem / "MEMORY.md").write_text(
        "- [Protected](protected.md) — hand-maintained release notes\n"
        "- [Notes](notes.md) — scratch notes about the build\n", encoding="utf-8")
    cwd = str(workspace)

    def day(n, hh, mm=0, ss=0):
        return f"2026-09-{n:02d}T{hh:02d}:{mm:02d}:{ss:02d}.000Z"

    # ---- S1: pip, and four expansions the skip list must remove -----------
    s1 = [
        _human(day(1, 9), S["1"], cwd, "帮我把依赖装好，然后跑一下测试。"),
        _assistant(day(1, 9, 1), S["1"], cwd, "Installing the dependencies."),
        _tool(day(1, 9, 2), S["1"], cwd, "t1a", "Bash",
              {"command": "pip install requests"}),
        _result(day(1, 9, 3), S["1"], cwd, "t1a",
                {"stdout": "ok", "stderr": "", "interrupted": False}),
        _human(day(1, 9, 4), S["1"], cwd, "不要用 pip，用 uv。"),
        # expansions — none of these is a human turn, all contain 不要/don't
        _expansion(day(1, 9, 5), S["1"], cwd,
                   "<command-message>update-config</command-message>\n"
                   "<command-name>update-config</command-name>\n不要用 pip",
                   marker="turnCompanion"),
        _expansion(day(1, 9, 6), S["1"], cwd,
                   "Base directory for this skill: /skills/deps\n\n"
                   "# deps\n\n不要用 pip，永远用 uv。don't use pip.",
                   marker="sourceToolUseID"),
        _expansion(day(1, 9, 7), S["1"], cwd,
                   "# Workflow authoring reference\n\nA workflow structures work. "
                   "Never use pip; stop and revert."),
        _expansion(day(1, 9, 8), S["1"], cwd,
                   "<local-command-stdout>don't use pip</local-command-stdout>"),
        # a decoy: 特别 / 别的 / 不用担心 must NOT trip the guards
        _human(day(1, 9, 9), S["1"], cwd,
               "这个方案特别好，别的事情先不用担心，级别也够了。"),
        _assistant(day(1, 9, 10), S["1"], cwd, "Noting the build scratch."),
        _tool(day(1, 9, 11), S["1"], cwd, "t1b", "Write",
              {"file_path": str(mem / "notes.md"),
               "content": "---\nname: notes\ndescription: scratch notes about the "
                          "build\n---\n\nScratch.\n"}),
        _result(day(1, 9, 12), S["1"], cwd, "t1b",
                {"type": "update", "filePath": str(mem / "notes.md"),
                 "content": "---\nname: notes\ndescription: scratch notes about the "
                            "build\n---\n\nScratch.\n",
                 "originalFile": "---\nname: notes\n---\n\nold\n"}),
    ]

    # ---- S2: pip again, a different session -------------------------------
    s2 = [
        _human(day(2, 10), S["2"], cwd, "把 flask 装上，我要起一个服务。"),
        _assistant(day(2, 10, 1), S["2"], cwd, "Installing flask."),
        _tool(day(2, 10, 2), S["2"], cwd, "t2a", "Bash",
              {"command": "pip install flask && python -m flask run"}),
        _result(day(2, 10, 3), S["2"], cwd, "t2a",
                {"stdout": "ok", "stderr": "", "interrupted": False}),
        _human(day(2, 10, 4), S["2"], cwd, "我说过了，别用 pip，改用 uv 装。"),
        _assistant(day(2, 10, 5), S["2"], cwd, "Sorry — switching to uv."),
        _tool(day(2, 10, 6), S["2"], cwd, "t2b", "Bash", {"command": "uv add flask"}),
        _result(day(2, 10, 7), S["2"], cwd, "t2b",
                {"stdout": "ok", "stderr": "", "interrupted": False}),
    ]

    # ---- S3 / S4: the protected path --------------------------------------
    s3 = [
        _human(day(3, 11), S["3"], cwd, "把发布流程整理一下。"),
        _assistant(day(3, 11, 1), S["3"], cwd, "Editing the release notes."),
        _tool(day(3, 11, 2), S["3"], cwd, "t3a", "Edit",
              {"file_path": str(protected), "old_string": "Do not edit.",
               "new_string": "Do not edit. Release on Friday."}),
        _result(day(3, 11, 3), S["3"], cwd, "t3a",
                {"filePath": str(protected), "oldString": "Do not edit.",
                 "newString": "Do not edit. Release on Friday.", "replaceAll": False}),
        _human(day(3, 11, 4), S["3"], cwd,
               "不要修改 protected.md 这个文件，它是我手写的。"),
        _human(day(3, 11, 5), S["3"], cwd, "发布流程就先这样吧。"),
    ]
    s4 = [
        _human(day(4, 12), S["4"], cwd, "再更新一下发布说明。"),
        _assistant(day(4, 12, 1), S["4"], cwd, "Rewriting the release notes."),
        _tool(day(4, 12, 2), S["4"], cwd, "t4a", "Write",
              {"file_path": str(protected), "content": "rewritten\n"}),
        _result(day(4, 12, 3), S["4"], cwd, "t4a",
                {"type": "update", "filePath": str(protected),
                 "content": "rewritten\n", "originalFile": "Do not edit.\n"}),
        _human(day(4, 12, 4), S["4"], cwd,
               "又改了？不要修改 protected.md 这个文件，我说过它是手写的。"),
        _human(day(4, 12, 5), S["4"], cwd, "谢谢。"),
    ]

    # ---- S5 / S6: the english curl topic ----------------------------------
    s5 = [
        _human(day(5, 13), S["5"], cwd, "fetch the changelog for me"),
        _assistant(day(5, 13, 1), S["5"], cwd, "Fetching it."),
        _tool(day(5, 13, 2), S["5"], cwd, "t5a", "Bash",
              {"command": "curl -s https://example.invalid/changelog"}),
        _result(day(5, 13, 3), S["5"], cwd, "t5a",
                {"stdout": "ok", "stderr": "", "interrupted": False}),
        _human(day(5, 13, 4), S["5"], cwd, "stop using curl, use httpie instead"),
    ]
    s6 = [
        _human(day(6, 14), S["6"], cwd, "grab the release notes too"),
        _assistant(day(6, 14, 1), S["6"], cwd, "Grabbing them."),
        _tool(day(6, 14, 2), S["6"], cwd, "t6a", "Bash",
              {"command": "curl -O https://example.invalid/notes.txt"}),
        _result(day(6, 14, 3), S["6"], cwd, "t6a",
                {"stdout": "ok", "stderr": "", "interrupted": False}),
        _human(day(6, 14, 4), S["6"], cwd,
               "don't use curl again, i said httpie instead"),
        _human(day(6, 14, 5), S["6"], cwd, "thanks, that works"),
    ]

    # ---- S7: uncorrected, but it does run `npm install` --------------------
    # It exists so an over-broad rule (regex on "install" rather than "pip") has
    # somewhere to misfire: that is the case the birth gate must reject.
    s7 = [
        _human(day(7, 15), S["7"], cwd, "set up the web front end"),
        _assistant(day(7, 15, 1), S["7"], cwd, "Installing the node deps."),
        _tool(day(7, 15, 2), S["7"], cwd, "t7a", "Bash",
              {"command": "npm install express"}),
        _result(day(7, 15, 3), S["7"], cwd, "t7a",
                {"stdout": "ok", "stderr": "", "interrupted": False}),
        _human(day(7, 15, 4), S["7"], cwd, "great, ship it"),
    ]

    for n, recs in (("1", s1), ("2", s2), ("3", s3), ("4", s4), ("5", s5),
                    ("6", s6), ("7", s7)):
        write_jsonl(str(home / "projects" / SLUG / f"{S[n]}.jsonl"), recs)

    class Tree:
        pass

    t = Tree()
    t.home = str(home)
    t.workspace = str(workspace)
    t.memory = str(mem)
    t.protected = str(protected)
    t.notes = str(mem / "notes.md")
    t.slug = SLUG
    t.sessions = dict(S)
    t.state_dir = str(tmp_path / "state")
    return t


# --------------------------------------------------------------------------
# tree fingerprinting, for the "nothing outside tmp_path was written" checks
# --------------------------------------------------------------------------

def tree_fingerprint(root: str) -> dict[str, str]:
    """``{relative path: sha256}`` for every regular file under ``root``."""
    out: dict[str, str] = {}
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(dirnames)
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


@pytest.fixture
def real_home_canary():
    """Records the state of the user's real ``~/.claude`` and ``~/.precedent``.

    Call the returned object at the end of a test: it raises if either one was
    created, removed or had its directory mtime moved.
    """
    paths = [os.path.expanduser("~/.claude"), os.path.expanduser("~/.precedent")]
    before = {p: (os.path.exists(p),
                  os.path.getmtime(p) if os.path.exists(p) else None) for p in paths}

    def check():
        for p in paths:
            exists = os.path.exists(p)
            mtime = os.path.getmtime(p) if exists else None
            assert (exists, mtime) == before[p], (
                f"precedent touched {p}: {before[p]} -> {(exists, mtime)}")
    return check


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# a general session builder, for the temporal-gate and revert scenarios
# --------------------------------------------------------------------------

def build_home(tmp_path, name: str, spec: dict, slug: str = "-tmp-gate",
               cwd: str | None = None):
    """Write a synthetic Claude home from ``{session_id: [event, ...]}``.

    An event is one of::

        ("human",     ts, text)
        ("assistant", ts, text)
        ("tool",      ts, tool_name, input_dict)
        ("expansion", ts, text)

    Everything lands under ``tmp_path``; nothing else is touched.
    """
    home = tmp_path / name
    proj = home / "projects" / slug
    proj.mkdir(parents=True, exist_ok=True)
    workspace = cwd or str(tmp_path / "ws")
    os.makedirs(workspace, exist_ok=True)
    for session_id, events in spec.items():
        recs = []
        n = 0
        for ev in events:
            kind, ts = ev[0], ev[1]
            if kind == "human":
                recs.append(_human(ts, session_id, workspace, ev[2]))
            elif kind == "assistant":
                recs.append(_assistant(ts, session_id, workspace, ev[2]))
            elif kind == "expansion":
                recs.append(_expansion(ts, session_id, workspace, ev[2],
                                       marker="sourceToolUseID"))
            elif kind == "tool":
                n += 1
                tid = f"tu{n}"
                recs.append(_tool(ts, session_id, workspace, tid, ev[2], ev[3]))
                recs.append(_result(ts, session_id, workspace, tid,
                                    {"stdout": "ok", "stderr": "",
                                     "interrupted": False}))
            else:                                        # pragma: no cover
                raise AssertionError(f"unknown event {kind!r}")
        write_jsonl(str(proj / f"{session_id}.jsonl"), recs)
    return str(home)


def sid(n: int) -> str:
    """A well-formed session id from a small integer."""
    c = str(n % 10)
    return f"{c * 8}-{c * 4}-4{c * 3}-8{c * 3}-{c * 12}"


@pytest.fixture
def home_builder(tmp_path):
    counter = {"n": 0}

    def make(spec, **kw):
        counter["n"] += 1
        return build_home(tmp_path, f"claude{counter['n']}", spec, **kw)
    return make


@pytest.fixture
def fake_claude(tmp_path):
    """A PATH shim standing in for the `claude` binary.

    ``fake_claude(answer_text, cost=0.01, returncode=0)`` writes a script that
    prints ``{"result": <answer_text>, "total_cost_usd": …}`` and records the
    argv it was called with, so a test can assert on the flags.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    argv_log = tmp_path / "claude-argv.json"
    prompt_log = tmp_path / "claude-prompt.txt"

    def make(answer: str, cost: float = 0.0123, returncode: int = 0,
             stdout_override: str | None = None):
        payload = json.dumps({"type": "result", "subtype": "success",
                              "is_error": False, "result": answer,
                              "total_cost_usd": cost}, ensure_ascii=False)
        body = (
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"json.dump(sys.argv[1:], open({json.dumps(str(argv_log))}, 'w'))\n"
            "args = sys.argv[1:]\n"
            "if '-p' in args:\n"
            f"    open({json.dumps(str(prompt_log))}, 'w', encoding='utf-8')"
            ".write(args[args.index('-p') + 1])\n"
            + (f"sys.stdout.write({json.dumps(stdout_override)})\n"
               if stdout_override is not None
               else f"sys.stdout.write({json.dumps(payload)})\n")
            + f"sys.exit({returncode})\n")
        path = bindir / "claude"
        path.write_text(body, encoding="utf-8")
        os.chmod(path, 0o755)
        return str(path)

    make.bindir = str(bindir)
    make.argv_log = str(argv_log)
    make.prompt_log = str(prompt_log)
    return make


# --------------------------------------------------------------------------
# C-Accept fixtures: cassettes (a terminal verification command) and failures
# --------------------------------------------------------------------------

def _error_result(ts, session, cwd, tid, text):
    """A ``tool_result`` the model was handed as an error."""
    return _rec("user", ts, session, cwd,
                message={"content": [{"type": "tool_result", "tool_use_id": tid,
                                      "content": text, "is_error": True}]},
                toolUseResult={"stderr": text, "stdout": "",
                               "interrupted": False, "isImage": False})


def build_home_v2(tmp_path, name: str, spec: dict, slug: str = "-tmp-exam",
                  cwd: str | None = None):
    """``build_home`` plus ``("tool_err", ts, tool, input, error_text)``.

    Kept separate from ``build_home`` so the existing fixtures keep their exact
    byte layout and the tests that fingerprint them do not move.
    """
    home = tmp_path / name
    proj = home / "projects" / slug
    proj.mkdir(parents=True, exist_ok=True)
    workspace = cwd or str(tmp_path / "ws")
    os.makedirs(workspace, exist_ok=True)
    for session_id, events in spec.items():
        recs = []
        n = 0
        for ev in events:
            kind, ts = ev[0], ev[1]
            if kind == "human":
                recs.append(_human(ts, session_id, workspace, ev[2]))
            elif kind == "assistant":
                recs.append(_assistant(ts, session_id, workspace, ev[2]))
            elif kind == "expansion":
                recs.append(_expansion(ts, session_id, workspace, ev[2],
                                       marker="sourceToolUseID"))
            elif kind == "tool":
                n += 1
                tid = f"tu{n}"
                recs.append(_tool(ts, session_id, workspace, tid, ev[2], ev[3]))
                recs.append(_result(ts, session_id, workspace, tid,
                                    {"stdout": "ok", "stderr": "",
                                     "interrupted": False}))
            elif kind == "tool_err":
                n += 1
                tid = f"tu{n}"
                recs.append(_tool(ts, session_id, workspace, tid, ev[2], ev[3]))
                recs.append(_error_result(ts, session_id, workspace, tid, ev[4]))
            else:                                        # pragma: no cover
                raise AssertionError(f"unknown event {kind!r}")
        write_jsonl(str(proj / f"{session_id}.jsonl"), recs)
    return str(home)


@pytest.fixture
def exam_builder(tmp_path):
    """Build a synthetic home whose sessions end in a verification command."""
    counter = {"n": 0}

    def make(spec, **kw):
        counter["n"] += 1
        return build_home_v2(tmp_path, f"examhome{counter['n']}", spec, **kw)
    return make


@pytest.fixture
def exam_home(tmp_path, exam_builder):
    """Three cassette sessions in one workspace, all ending in `pytest -q`."""
    ws = tmp_path / "repo"
    ws.mkdir(exist_ok=True)

    def sess(n, day):
        return [
            ("human", f"2026-09-{day:02d}T09:00:00.000Z", f"fix the parser, run the tests"),
            ("assistant", f"2026-09-{day:02d}T09:00:01.000Z", "Editing."),
            ("tool", f"2026-09-{day:02d}T09:00:02.000Z", "Edit",
             {"file_path": str(ws / "parser.py"), "old_string": "a",
              "new_string": "b"}),
            ("tool", f"2026-09-{day:02d}T09:00:03.000Z", "Bash",
             {"command": ".venv/bin/python -m pytest -q"}),
            ("human", f"2026-09-{day:02d}T09:00:09.000Z", "thanks"),
        ]

    spec = {sid(n): sess(n, 10 + n) for n in (1, 2, 3)}
    home = exam_builder(spec, cwd=str(ws))

    class T:
        pass
    t = T()
    t.home = home
    t.workspace = str(ws)
    t.state_dir = str(tmp_path / "state2")
    t.sessions = [sid(n) for n in (1, 2, 3)]
    return t
