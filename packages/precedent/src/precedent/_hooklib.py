# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""THE HOOK RUNTIME — this file is both a module and the installed hook library.

``precedent hooks install --apply`` copies this file **verbatim** into
``<state>/hooks/_plib.py``; the six generated hook scripts are three lines each
and delegate here.  That is deliberate: the code Claude Code runs in-process is
the same code the test suite imports and unit-tests, so there is no second copy
to drift.

Hard constraints, all of them load-bearing:

* **stdlib only, no package imports.**  It has to run under whatever ``python3``
  is on the user's PATH, from a directory that is not an installed package.
* **never break Claude Code.**  :func:`guard_main` catches everything, exits 0,
  and writes the traceback to ``<state>/hooklog.jsonl``; a hook that wedges the
  agent when its own state file is torn is worse than no hook at all.
* **deny/ask only on a CONFIRMED precedent.**  A rule blocks only when its
  ``status`` is exactly ``"active"``.  The ownership guard is itself such a
  rule (``kind == "ownership-guard"``, written by ``precedent own``), so the
  invariant holds for it too: no confirmation, no ``ask``.
* **snapshot before the write.**  PreToolUse runs *before* the tool, which is
  the only moment the pre-image of a governed file still exists; it goes to
  ``<state>/blobs/<sha256>`` and the candidate record names the blob.

Everything else is allow + log.
"""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import os
import re
import sys
import time

__all__ = [
    "DECIDING_EVENTS", "GOVERNED_KINDS", "SCHEMA_VERSION", "agent_of", "classify_path", "configure",
    "decide", "diff_summary", "guard_main", "handle_instructions_loaded",
    "handle_post_tool_use", "handle_pre_tool_use", "handle_session_start",
    "handle_stop", "handle_user_prompt_submit", "owner_of", "search_evaluated",
    "snapshot_file", "write_targets",
]

SCHEMA_VERSION = 1

#: Set by :func:`configure` from the values baked into each generated script
#: (``PRECEDENT_STATE_DIR`` / ``PRECEDENT_CLAUDE_HOME`` still win at run time,
#: which is what lets the test suite point the whole thing at ``tmp_path``).
STATE_DIR = ""
CLAUDE_HOME = ""

MAX_SUBJECT = 4096            # chars of tool input a regex may ever see
BUDGET_MS = 50.0              # per-pattern time budget before quarantine
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_DIFF_LINES = 4000         # above this, counts only — no line diff
MAX_SESSION_LOG_BYTES = 4 * 1024 * 1024
MAX_TARGETS = 16              # governed paths handled per tool call (then logged)

GOVERNED_KINDS = ("memory", "claude_md", "rules", "skills")

TOOL_FIELDS = {
    "Bash": ("command",), "Edit": ("file_path",), "Write": ("file_path",),
    "MultiEdit": ("file_path",), "NotebookEdit": ("notebook_path", "file_path"),
    "Agent": ("prompt",), "Workflow": ("script",), "WebFetch": ("url",),
    "Skill": ("skill",),
}

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash")


def configure(state_dir: str, claude_home: str = "") -> None:
    global STATE_DIR, CLAUDE_HOME
    STATE_DIR = os.environ.get("PRECEDENT_STATE_DIR") or state_dir or ""
    CLAUDE_HOME = (os.environ.get("PRECEDENT_CLAUDE_HOME") or claude_home
                   or os.environ.get("CLAUDE_CONFIG_DIR")
                   or os.path.expanduser("~/.claude"))


# --------------------------------------------------------------------------
# paths + append-only io (every one of these is best-effort: never raise)
# --------------------------------------------------------------------------

def spath(*parts: str) -> str:
    return os.path.join(STATE_DIR, *parts)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def append_jsonl(path: str, row: dict, cap: int = MAX_LOG_BYTES) -> None:
    try:
        try:
            if os.path.getsize(path) > cap:
                os.replace(path, path + ".1")
        except OSError:
            pass
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def log(row: dict) -> None:
    """One line in ``hooklog.jsonl`` — the hook-health log."""
    row.setdefault("ts", now_iso())
    append_jsonl(spath("hooklog.jsonl"), row)


def session_receipt_path(session: str) -> str:
    safe = "".join(c for c in str(session or "unknown")
                   if c.isalnum() or c in "-_.")[:120] or "unknown"
    return spath("receipts", safe + ".jsonl")


def session_log(session: str, row: dict) -> None:
    """One line in ``receipts/<session>.jsonl`` — the per-session event log."""
    row.setdefault("ts", now_iso())
    append_jsonl(session_receipt_path(session), row, cap=MAX_SESSION_LOG_BYTES)


def read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def read_text(path: str, limit: int = MAX_SNAPSHOT_BYTES):
    try:
        if os.path.getsize(path) > limit:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


# --------------------------------------------------------------------------
# payload helpers
# --------------------------------------------------------------------------

def session_of(payload: dict) -> str:
    for k in ("session_id", "sessionId", "session"):
        v = payload.get(k)
        if isinstance(v, str) and v:
            return v
    return "unknown"


def agent_of(payload: dict):
    """``("foreground" | "subagent", agent_id | None)``.

    Claude Code identifies a subagent turn by an ``agent_id`` (older builds:
    ``agentId`` / ``subagent_id``, or an explicit ``isSidechain``).  Anything
    that names an agent is treated as a subagent, because the failure mode we
    are guarding is exactly "the 02:17 background fork rewrote a file the user
    had verified" — so unknown-but-agent-shaped counts as subagent.
    """
    for k in ("agent_id", "agentId", "subagent_id", "subagentId"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return "subagent", v.strip()
    for k in ("is_sidechain", "isSidechain", "is_subagent"):
        if payload.get(k) is True:
            atype = payload.get("agent_type") or payload.get("agentType")
            return "subagent", (str(atype) if atype else None)
    atype = payload.get("agent_type") or payload.get("agentType")
    if isinstance(atype, str) and atype.strip() and atype.strip() != "main":
        return "subagent", atype.strip()
    return "foreground", None


# --------------------------------------------------------------------------
# governed trees
# --------------------------------------------------------------------------

def _norm(p: str) -> str:
    try:
        return os.path.normpath(os.path.expanduser(p))
    except Exception:
        return str(p)


_REALPATH_CACHE = {}


def _realpath(path: str) -> str:
    """``os.path.realpath`` with a per-process cache.  Never raises."""
    if path in _REALPATH_CACHE:
        return _REALPATH_CACHE[path]
    try:
        out = os.path.realpath(path)
    except Exception:
        out = path
    if len(_REALPATH_CACHE) < 512:
        _REALPATH_CACHE[path] = out
    return out


def _classify_norm(p: str, home: str):
    """The classification of one already-normalised pair.  No filesystem io."""
    parts = p.split(os.sep)
    if os.path.basename(p) in ("CLAUDE.md", "CLAUDE.local.md"):
        return "claude_md"
    if home and p.startswith(home + os.sep):
        rest = p[len(home) + 1:].split(os.sep)
        if len(rest) >= 4 and rest[0] == "projects" and rest[2] == "memory":
            return "memory"
        if rest and rest[0] == "skills":
            return "skills"
        if rest and rest[0] == "rules":
            return "rules"
        if rest and rest[0] == "memory":
            return "memory"
    for i, seg in enumerate(parts[:-1]):
        if seg == ".claude":
            nxt = parts[i + 1]
            if nxt == "rules":
                return "rules"
            if nxt == "skills":
                return "skills"
            if nxt == "memory":
                return "memory"
    if "memory" in parts[:-1] and "projects" in parts:
        return "memory"
    return None


def classify_path(path: str, claude_home: str = None):
    """Which governed tree ``path`` belongs to, or ``None``.

    The governed trees are the learned state a self-evolving harness writes to
    itself: project memory, ``CLAUDE.md``, ``.claude/rules``, ``.claude/skills``
    (project **and** user scope).  Nothing else is snapshotted or gated.

    Symlinks are resolved on **both** sides before we give up, because both
    directions are real and both are silent failures:

    * a ``~/.claude`` that is itself a symlink (every dotfile manager does this)
      while the tool call carries the resolved path — the whole governed tree
      would otherwise be invisible to the hook;
    * a symlink pointing *into* the governed tree, which is a one-line way to
      write a user-owned skill without the path ever looking like one.

    The cheap textual test runs first and answers almost every call; the
    ``realpath`` pass is a cached fallback taken only when it says ``None``.
    """
    if not path or not isinstance(path, str):
        return None
    home = _norm(claude_home if claude_home is not None else CLAUDE_HOME)
    p = _norm(path)
    got = _classify_norm(p, home)
    if got:
        return got
    real_home = _norm(_realpath(home)) if home else home
    real_p = _norm(_realpath(p))
    for cand_p in (p, real_p):
        for cand_home in (home, real_home):
            if cand_p == p and cand_home == home:
                continue                       # already tried, above
            got = _classify_norm(cand_p, cand_home)
            if got:
                return got
    return None


_HEREDOC = re.compile(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?[\s\S]*?^\1\s*$",
                      re.MULTILINE)


def _blank_heredocs(cmd: str) -> str:
    """Blank heredoc bodies: a command that *writes a file containing* a path
    is not a write to that path (this cost four false positives in receipts)."""
    try:
        return _HEREDOC.sub(lambda m: "<<" + m.group(1) + "\n" + m.group(1), cmd)
    except Exception:
        return cmd


_PATHISH = re.compile(r"[A-Za-z0-9_@%+=:,./~-]*[/][A-Za-z0-9_@%+=:,./~.-]*")
_WRITE_VERBS = ("tee", "cp", "mv", "rm", "install", "truncate", "touch", "dd")

#: How far back from an occurrence of the path we look for the write construct.
#: The command is one shell line, so the construct is adjacent in practice; the
#: cap is what keeps the scan linear.
CTX_CHARS = 512
MAX_BASH_TOKENS = 32          # distinct governed-looking tokens per command
MAX_BASH_HITS = 8             # occurrences of one token we bother to look at

_REDIRECT_TAIL = re.compile(r">>?\s*['\"]?\Z")
_VAR_TAIL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=['\"]?\Z")
_VERB_RX = re.compile(r"\b(" + "|".join(_WRITE_VERBS) + r")\b")
_TEE_RX = re.compile(r"\btee\b")
_SED_RX = re.compile(r"\bsed\b")
_ANY_WRITER_RX = re.compile(r">|\btee\b|\bsed\b\s+-i|\bopen\s*\(")
_SEGMENT_CHARS = "|;&"


def _left_context(body: str, at: int) -> tuple[str, str]:
    """``(context, segment)`` immediately to the left of ``body[at]``.

    ``context`` is the raw ``CTX_CHARS`` before the token (what a redirect or a
    ``VAR=`` assignment has to end with); ``segment`` is that same window cut at
    the last ``|``, ``;`` or ``&``, i.e. the part of the pipeline the token is
    actually an argument of.  This is the linear replacement for the old
    ``\btee\b[^|;&]*?<path>`` searches, whose two lazy unbounded gaps made one
    8 KB command cost tens of seconds — comfortably past the 5 s hook timeout,
    on the hot path of every Bash call.
    """
    ctx = body[max(0, at - CTX_CHARS):at]
    cut = max(ctx.rfind(c) for c in _SEGMENT_CHARS)
    return ctx, ctx[cut + 1:] if cut >= 0 else ctx


#: Reported in this order when one path appears more than once with different
#: constructs, so the answer does not depend on where in the command it first
#: shows up (which is what searching the whole string used to give us).
_HOW_PRIORITY = ("redirect", "tee", "sed -i", "verb", "var")


def _how_at(body: str, at: int, whole_has_writer: bool):
    ctx, seg = _left_context(body, at)
    if _REDIRECT_TAIL.search(ctx):
        return "redirect"
    if _TEE_RX.search(seg):
        return "tee"
    m = _SED_RX.search(seg)
    if m and seg.find("-i", m.end()) >= 0:
        return "sed -i"
    if _VERB_RX.search(seg):
        return "verb"
    if whole_has_writer and _VAR_TAIL.search(ctx):
        return "var"
    return None


def _how_written(body: str, tok: str, whole_has_writer: bool):
    """The write construct ``tok`` sits in, or ``None``.  Linear in ``body``."""
    found = set()
    start = 0
    for _ in range(MAX_BASH_HITS):
        at = body.find(tok, start)
        if at < 0:
            break
        start = at + 1
        how = _how_at(body, at, whole_has_writer)
        if how:
            found.add(how)
            if how == _HOW_PRIORITY[0]:
                break
    for how in _HOW_PRIORITY:
        if how in found:
            return how
    return None


def bash_write_targets(cmd: str, claude_home: str = None):
    """Governed paths a Bash command appears to *write*, with a confidence.

    Only tokens that are already governed are considered, so this is cheap: the
    path regex runs over the command once, and each governed token is then
    located with :meth:`str.find` and judged from a bounded window of the text
    immediately before it.  Same three-level confidence as the receipts change
    feed (``high`` inside the write construct, ``medium`` assigned to a variable
    next to a writer, ``low`` only inside inline code).

    Everything here is bounded on purpose — 8 KB of command, ``MAX_BASH_TOKENS``
    distinct governed tokens, ``MAX_BASH_HITS`` occurrences each,
    ``CTX_CHARS`` of left context — because this runs inside ``PreToolUse``,
    before every Bash call the agent makes.  Detection is a heuristic; a
    heuristic that can hang the agent is worse than no heuristic.
    """
    out = []
    if not isinstance(cmd, str) or not cmd:
        return out
    body = _blank_heredocs(cmd[:8192])
    seen = set()
    cands = []
    for m in _PATHISH.finditer(body):
        raw = m.group(0).strip().strip("'\"")
        cands.append(raw)
        if "=" in raw:
            # `M=/path/to/memory.md && …` — the token the shell assigns is the
            # path, and that is how agents actually reach a governed tree.
            cands.append(raw.rsplit("=", 1)[1])
    whole_has_writer = bool(_ANY_WRITER_RX.search(body))
    for tok in cands:
        if not tok or tok in seen or len(tok) > 512:
            continue
        seen.add(tok)
        if len(seen) > MAX_BASH_TOKENS:
            log({"event": "bash_targets_truncated", "tokens": len(cands),
                 "cap": MAX_BASH_TOKENS})
            break
        if classify_path(tok, claude_home) is None:
            continue
        how = _how_written(body, tok, whole_has_writer)
        if how:
            out.append((tok, how))
    return out


def write_targets(tool_name: str, tool_input: dict, claude_home: str = None):
    """``[(path, how)]`` — the governed files this tool call would write."""
    if not isinstance(tool_input, dict):
        return []
    if tool_name == "Bash":
        return bash_write_targets(tool_input.get("command") or "", claude_home)
    out = []
    for field in ("file_path", "notebook_path", "path"):
        v = tool_input.get(field)
        if isinstance(v, str) and v and classify_path(v, claude_home):
            out.append((v, "tool"))
            break
    return out


# --------------------------------------------------------------------------
# snapshot + diff summary
# --------------------------------------------------------------------------

def snapshot_file(path: str) -> dict:
    """Content-address the file **before** the write.  Never raises."""
    info = {"existed": False, "sha256": None, "bytes": 0, "blob": None}
    try:
        if not os.path.isfile(path):
            return info
        size = os.path.getsize(path)
        info["existed"] = True
        info["bytes"] = size
        if size > MAX_SNAPSHOT_BYTES:
            info["skipped"] = "too large (%d bytes)" % size
            return info
        with open(path, "rb") as fh:
            body = fh.read()
        sha = hashlib.sha256(body).hexdigest()
        info["sha256"] = sha
        blob = spath("blobs", sha)
        if not os.path.exists(blob):
            os.makedirs(os.path.dirname(blob), exist_ok=True)
            tmp = blob + ".tmp%d" % os.getpid()
            with open(tmp, "wb") as fh:
                fh.write(body)
            os.replace(tmp, blob)
        info["blob"] = blob
    except Exception as exc:
        info["error"] = "%s: %s" % (type(exc).__name__, exc)
    return info


def _apply_edits(before: str, tool_name: str, tool_input: dict):
    if tool_name == "Write":
        c = tool_input.get("content")
        return c if isinstance(c, str) else None
    if tool_name == "Edit":
        old, new = tool_input.get("old_string"), tool_input.get("new_string")
        if isinstance(old, str) and isinstance(new, str) and before is not None:
            if tool_input.get("replace_all"):
                return before.replace(old, new)
            return before.replace(old, new, 1)
        return None
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list) or before is None:
            return None
        cur = before
        for e in edits[:200]:
            if not isinstance(e, dict):
                continue
            old, new = e.get("old_string"), e.get("new_string")
            if isinstance(old, str) and isinstance(new, str):
                cur = cur.replace(old, new, -1 if e.get("replace_all") else 1)
        return cur
    return None


def diff_summary(path: str, tool_name: str, tool_input: dict) -> dict:
    """``+N −M lines`` for the write that is about to happen.  Best effort."""
    out = {"tool": tool_name}
    if tool_name == "Bash":
        # There is nothing to diff against: the command decides what happens,
        # and reading a 4 MB pre-image to print its byte count costs the hook
        # budget for no information.  The snapshot already has the bytes.
        cmd = str(tool_input.get("command") or "")
        out["summary"] = "bash: " + (cmd[:160] + ("…" if len(cmd) > 160 else ""))
        return out
    before = read_text(path)
    out["beforeBytes"] = len(before.encode("utf-8")) if before is not None else 0
    after = _apply_edits(before, tool_name, tool_input)
    if after is None:
        out["summary"] = "%s (no diff available)" % tool_name
        return out
    out["afterBytes"] = len(after.encode("utf-8"))
    b = (before or "").splitlines()
    a = after.splitlines()
    out["beforeLines"], out["afterLines"] = len(b), len(a)
    if max(len(a), len(b)) > MAX_DIFF_LINES:
        out["summary"] = "%d → %d lines (too large to diff)" % (len(b), len(a))
        return out
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, b, a, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    out["addedLines"], out["removedLines"] = added, removed
    out["summary"] = "+%d −%d lines" % (added, removed)
    return out


# --------------------------------------------------------------------------
# ownership
# --------------------------------------------------------------------------

def owners() -> dict:
    data = read_json(spath("owners.json"), default=None)
    return data if isinstance(data, dict) else {}


def agent_created() -> dict:
    data = read_json(spath("agent_created.json"), default=None)
    if isinstance(data, dict) and isinstance(data.get("paths"), dict):
        return data["paths"]
    return {}


def owner_of(path: str, owners_doc: dict = None, created: dict = None):
    """``(owner, why)`` where owner is ``user`` | ``agent`` | ``unknown``.

    Order: an explicit ``precedent own`` declaration (exact path, then the
    longest matching glob) beats everything.  Otherwise a file the change feed
    has never seen an agent create, and which exists, is the user's — that is
    the honest default, and it is the conservative one: it produces an *ask*,
    not a deny, and only for a subagent.
    """
    od = owners() if owners_doc is None else owners_doc
    p = _norm(path)
    rp = _norm(_realpath(p))
    # A declaration and a write can name the same file by different routes (one
    # through a symlinked ~/.claude, one not).  Try both spellings before
    # falling through to the defaults, which are deliberately conservative.
    keys = [p, path] + ([rp] if rp != p else [])
    paths = od.get("paths") if isinstance(od.get("paths"), dict) else {}
    for k in keys:
        ent = paths.get(k)
        if isinstance(ent, dict) and ent.get("owner") in ("user", "agent"):
            return ent["owner"], "owners.json"
    best = None
    for pat, ent in paths.items():
        if not isinstance(ent, dict) or ent.get("owner") not in ("user", "agent"):
            continue
        if not ("*" in pat or "?" in pat):
            continue
        npat = _norm(pat)
        if fnmatch.fnmatch(p, npat) or (rp != p and fnmatch.fnmatch(rp, npat)):
            if best is None or len(npat) > len(best[0]):
                best = (npat, ent["owner"])
    if best:
        return best[1], "owners.json glob %s" % best[0]
    cr = agent_created() if created is None else created
    if any(k in cr for k in keys):
        return "agent", "created by an agent (change feed)"
    if os.path.exists(p):
        return "user", "never created by an agent, and it exists"
    return "agent", "does not exist yet — this call creates it"


# --------------------------------------------------------------------------
# the DSL v1 evaluator
# --------------------------------------------------------------------------

def rules() -> list:
    """The confirmed rules, or ``[]``.

    A *missing* ``precedents.json`` is the normal state before the first
    ``precedent confirm`` and is silent.  A file that exists but will not parse
    is an enforcement outage, so it gets a line in the hook log — that is what
    ``precedent report`` turns into a STARVATION alarm.  Either way the hook
    allows: a gate that bricks the agent when its own state file is torn is
    worse than no gate.
    """
    path = spath("precedents.json")
    data = read_json(path, default=None)
    if data is None:
        if os.path.exists(path):
            log({"event": "precedents_unreadable", "path": path})
        return []
    rs = data.get("precedents") if isinstance(data, dict) else data
    if not isinstance(rs, list):
        log({"event": "precedents_unreadable", "path": path,
             "note": "no precedents list"})
        return []
    return [r for r in rs if isinstance(r, dict)]


def active_rules() -> list:
    return [r for r in rules() if r.get("status") == "active"]


def field_value(tool_input, field):
    if not isinstance(tool_input, dict) or field not in tool_input:
        return None
    v = tool_input[field]
    if v is None:
        return None
    if isinstance(v, str):
        return v or None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    try:
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(v)


_SLOW = set()


def search_evaluated(rx, subject):
    """``(match, evaluated)`` under a length cap, a time budget and a quarantine.

    CPython's ``re`` holds the GIL and ignores signals mid-match, so this is
    reject-then-quarantine, not a hard deadline: patterns are linted before they
    are ever stored, the subject is capped, and one overrun disables the pattern
    for the rest of this process (fail-open).

    ``evaluated`` is False when the pattern was skipped.  ``input_regex_absent``
    inverts the answer, so it must be able to tell "did not match" from "was not
    asked": inverting the second one would turn one quarantined pattern into a
    hook that denies every call.

    Truncation counts as "was not asked" in the negative direction only: the
    subject is capped at :data:`MAX_SUBJECT`, so a miss in a capped subject is
    "not in the first 4 KB", and inverting *that* denies a compliant call — a
    real ``Workflow`` script carried its ``model: 'opus'`` at offset 5,778 of
    19,739 characters.  A hit inside the cap is still a hit.
    """
    full_len = len(subject)
    subject = subject[:MAX_SUBJECT]
    truncated = full_len > len(subject)
    if rx.pattern in _SLOW:
        log({"event": "regex_quarantined", "pattern": rx.pattern[:120]})
        return None, False
    t0 = time.time()
    out = rx.search(subject)
    ms = (time.time() - t0) * 1000
    if ms > BUDGET_MS:
        _SLOW.add(rx.pattern)
        log({"event": "regex_slow", "pattern": rx.pattern[:120], "ms": round(ms, 2)})
    if out is None and truncated:
        log({"event": "regex_truncated", "pattern": rx.pattern[:120],
             "subjectChars": len(subject), "fullChars": full_len})
        return None, False
    return out, True


def bounded_search(rx, subject):
    """``rx.search`` under a length cap, a time budget and a quarantine."""
    return search_evaluated(rx, subject)[0]


def matcher_fires(m, tool_input):
    field = m.get("field") or ""
    value = field_value(tool_input, field)
    mtype = m.get("type")
    if mtype == "input_field_missing":
        return value is None
    if mtype == "input_field_equals":
        if value is None:
            return False
        want = m.get("value")
        if isinstance(want, bool):
            eq = (value.lower() in ("true", "1", "yes")) is want
        elif m.get("case_sensitive"):
            eq = value == str(want)
        else:
            eq = value.lower() == str(want).lower()
        return (not eq) if m.get("negate") else eq
    if mtype in ("input_regex", "input_regex_absent"):
        absent = mtype == "input_regex_absent"
        if value is None:
            return absent           # missing cannot match a "must match" rule
        try:
            rx = re.compile(m["regex"])
        except Exception:
            return False
        hit, evaluated = search_evaluated(rx, value)
        if not evaluated:
            return False            # unknown -> fail open, in both directions
        return (hit is None) if absent else (hit is not None)
    return False


def scope_ok(rule, cwd):
    if (rule.get("scope") or "project") == "global":
        return True
    glob = rule.get("cwd_glob")
    if not glob:
        return True
    if not cwd:
        return False
    cwd = os.path.normpath(cwd)
    pats = glob if isinstance(glob, list) else [glob]
    for pat in pats:
        p = os.path.normpath(os.path.expanduser(str(pat)))
        if fnmatch.fnmatch(cwd, p) or fnmatch.fnmatch(cwd, p.rstrip("/*") + "/*") \
                or cwd == p.rstrip("/*"):
            return True
    return False


def normalise(rule, tool_name):
    ms = rule.get("matchers")
    if isinstance(ms, list) and ms:
        return ms
    rx = rule.get("input_regex")
    if not rx:
        return []
    field = rule.get("field") or (TOOL_FIELDS.get(tool_name) or ("command",))[0]
    return [{"type": "input_regex", "field": field, "regex": rx}]


def reason(rule):
    text = "[precedent %s] %s" % (rule.get("id", "?"), rule.get("message", ""))
    quote = (rule.get("quote") or "").strip()
    date = (rule.get("quoteDate") or "").strip()
    if quote and quote not in text:
        text += " -- from your correction on %s: %s" % (
            date or "an earlier session", quote)
    return text


def decide(payload, rule_list=None):
    """The DSL half of PreToolUse.  ``(hookSpecificOutput | None, rule id)``."""
    if payload.get("hook_event_name") not in (None, "", "PreToolUse"):
        return None, None
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    cwd = payload.get("cwd") or payload.get("workingDirectory")
    for rule in (rules() if rule_list is None else rule_list):
        if rule.get("status") != "active":
            continue                      # only a CONFIRMED precedent may block
        if rule.get("kind") == "ownership-guard":
            continue                      # handled by the ownership pass
        if rule.get("hook", "PreToolUse") != "PreToolUse":
            continue
        tools = [t.strip() for t in str(rule.get("tool", "")).split("|") if t.strip()]
        if not tools or (tool_name not in tools and "*" not in tools):
            continue
        if not scope_ok(rule, cwd):
            continue
        ms = normalise(rule, tool_name)
        if not ms:
            continue
        results = [matcher_fires(m, tool_input) for m in ms if isinstance(m, dict)]
        hit = any(results) if rule.get("match") == "any" else (results and all(results))
        if not hit:
            continue
        action = rule.get("action", "deny")
        if action == "log":
            log({"event": "match", "rule": rule.get("id"), "action": "log",
                 "tool": tool_name, "session": session_of(payload)})
            continue
        if action not in ("deny", "ask"):
            action = "deny"
        return {"hookEventName": "PreToolUse", "permissionDecision": action,
                "permissionDecisionReason": reason(rule)}, rule.get("id")
    return None, None


# --------------------------------------------------------------------------
# PreToolUse: ownership
# --------------------------------------------------------------------------

def _candidate_id(parts) -> str:
    return "w-" + hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]


def ownership_pass(payload, rule_list=None, blocked_by=None):
    """Snapshot, record a candidate, and decide.

    Returns ``(hookSpecificOutput | None, [candidate records])``.  The *ask* is
    returned only when (a) the write comes from a **subagent**, (b) the target
    is **user-owned**, and (c) an ownership guard exists in ``precedents.json``
    with ``status == "active"`` — which is what ``precedent own <path> --user``
    writes.  Without that confirmed rule this pass is pure observation.

    ``blocked_by`` is the id of a confirmed precedent that has already denied
    this call.  The write is still recorded (it is evidence either way) but it
    is recorded as *blocked*, not as something pending your decision: the
    docket is for things that still need one.
    """
    tool_name = payload.get("tool_name") or ""
    if tool_name not in WRITE_TOOLS:
        return None, []
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None, []
    targets = write_targets(tool_name, tool_input)
    if not targets:
        return None, []

    kind, agent_id = agent_of(payload)
    session = session_of(payload)
    guard = None
    for r in (rule_list if rule_list is not None else rules()):
        if r.get("kind") == "ownership-guard" and r.get("status") == "active":
            guard = r
            break

    od, cr = owners(), agent_created()
    records, decision = [], None
    if len(targets) > MAX_TARGETS:
        # "Starvation is never silent" applies to our own bookkeeping too: a
        # governed write we decline to record has to say so.
        log({"event": "targets_truncated", "n": len(targets), "cap": MAX_TARGETS,
             "dropped": [t[0] for t in targets[MAX_TARGETS:]][:8],
             "session": session})
    for path, how in targets[:MAX_TARGETS]:
        governed = classify_path(path)
        snap = snapshot_file(path)
        owner, why = owner_of(path, od, cr)
        ask = (kind == "subagent" and owner == "user" and guard is not None)
        rec = {
            "schemaVersion": SCHEMA_VERSION, "kind": "write",
            "ts": now_iso(), "session": session, "agent": kind,
            "agentId": agent_id, "tool": tool_name, "how": how,
            "path": _norm(path), "governed": governed,
            "owner": owner, "ownerWhy": why,
            "cwd": payload.get("cwd"),
            "snapshot": snap,
            "diff": diff_summary(path, tool_name, tool_input),
            "decision": ("deny" if blocked_by else "ask" if ask else "allow"),
            "status": "blocked" if blocked_by else "pending",
        }
        if blocked_by:
            rec["blockedBy"] = blocked_by
        rec["id"] = _candidate_id([rec["session"], rec["path"], rec["ts"],
                                   rec["tool"], rec["diff"].get("summary")])
        append_jsonl(spath("candidates.jsonl"), rec)
        session_log(session, {"event": "candidate", "id": rec["id"],
                              "path": rec["path"], "agent": kind,
                              "decision": rec["decision"]})
        records.append(rec)
        if ask and decision is None and not blocked_by:
            decision = {
                "hookEventName": "PreToolUse", "permissionDecision": "ask",
                "permissionDecisionReason":
                    "[precedent %s] %s\n  %s (%s) — %s\n  快照 %s\n  "
                    "确认: precedent docket confirm %s   拒绝: precedent docket "
                    "reject %s" % (
                        guard.get("id", "p-own-guard"),
                        guard.get("message", "subagent write to a user-owned artifact"),
                        rec["path"], governed, why,
                        (snap.get("sha256") or "(none)")[:12],
                        rec["id"], rec["id"]),
            }
    return decision, records


def handle_pre_tool_use(payload):
    rule_list = rules()                       # one read, two passes
    out, rule_id = decide(payload, rule_list)
    own_out, records = ownership_pass(payload, rule_list,
                                      blocked_by=rule_id if out else None)
    if out is None and own_out is not None:
        out, rule_id = own_out, "p-own-guard"
    if records:
        log({"event": "candidates", "n": len(records),
             "paths": [r["path"] for r in records][:4],
             "session": session_of(payload)})
    return out, rule_id


# --------------------------------------------------------------------------
# the other five events
# --------------------------------------------------------------------------

def handle_post_tool_use(payload):
    """Change-feed capture: what a Write/Edit/Bash call actually touched."""
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    targets = write_targets(tool_name, tool_input)
    if not targets:
        return None
    kind, agent_id = agent_of(payload)
    session = session_of(payload)
    resp = payload.get("tool_response")
    ok = True
    if isinstance(resp, dict):
        ok = not (resp.get("is_error") or resp.get("interrupted"))
    if len(targets) > MAX_TARGETS:
        log({"event": "targets_truncated", "hook": "PostToolUse",
             "n": len(targets), "cap": MAX_TARGETS, "session": session})
    for path, how in targets[:MAX_TARGETS]:
        p = _norm(path)
        row = {"schemaVersion": SCHEMA_VERSION, "event": "change",
               "ts": now_iso(), "session": session, "agent": kind,
               "agentId": agent_id, "tool": tool_name, "how": how, "path": p,
               "governed": classify_path(p), "ok": bool(ok),
               "exists": os.path.exists(p),
               "bytes": (os.path.getsize(p) if os.path.isfile(p) else 0),
               "cwd": payload.get("cwd")}
        append_jsonl(spath("changes.jsonl"), row)
        session_log(session, {"event": "change", "path": p, "tool": tool_name,
                              "agent": kind})
    return None


def handle_user_prompt_submit(payload):
    """A human turn: the one origin precedent treats as trusted."""
    text = payload.get("prompt")
    if not isinstance(text, str):
        text = payload.get("user_prompt") or payload.get("message") or ""
        if not isinstance(text, str):
            text = ""
    session = session_of(payload)
    row = {"schemaVersion": SCHEMA_VERSION, "event": "human_turn",
           "ts": now_iso(), "session": session, "cwd": payload.get("cwd"),
           "chars": len(text),
           "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
           "preview": text.strip()[:120], "origin": "human", "trusted": True}
    append_jsonl(spath("origins.jsonl"), row)
    session_log(session, dict(row))
    return None


def handle_session_start(payload):
    """A live receipt that this session began, and under what."""
    session = session_of(payload)
    row = {"schemaVersion": SCHEMA_VERSION, "event": "session_start",
           "ts": now_iso(), "session": session, "cwd": payload.get("cwd"),
           "source": payload.get("source") or payload.get("trigger"),
           "transcript": payload.get("transcript_path"),
           "claudeHome": CLAUDE_HOME,
           "activeRules": _n_active_rules()}
    session_log(session, row)
    return None


def _n_active_rules() -> int:
    try:
        return len(active_rules())
    except Exception:
        return 0


def _instruction_items(payload):
    """Every file-ish thing an InstructionsLoaded payload might carry."""
    items = []
    for key in ("files", "instructions", "loaded_files", "loadedFiles",
                "instruction_files", "memory_files"):
        v = payload.get(key)
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict):
                    items.append(it)
                elif isinstance(it, str):
                    items.append({"path": it})
        elif isinstance(v, dict):
            for k, val in v.items():
                items.append({"path": k,
                              "content": val if isinstance(val, str) else None})
    return items


def _norm_ws(text: str) -> str:
    return " ".join(text.split())


def _load_status(path: str, content):
    """The receipts vocabulary, decided from what the payload actually proves."""
    if not path or not os.path.exists(path):
        return "missing_on_disk", None
    if not isinstance(content, str) or not content:
        return "loaded_declared", None      # listed as loaded; bytes unproven
    body = read_text(path)
    if body is None:
        return "loaded_declared", None
    want, got = _norm_ws(body), _norm_ws(content)
    if not want:
        return "loaded_complete", 1.0
    if want in got:
        return "loaded_complete", 1.0
    units = [u for u in (p.strip() for p in body.split("\n\n")) if u]
    if units:
        hit = sum(1 for u in units if _norm_ws(u) in got)
        if hit == len(units):
            return "loaded_complete", 1.0
        if hit:
            return "loaded_truncated", round(hit / float(len(units)), 3)
    return "not_loaded", 0.0


def handle_instructions_loaded(payload):
    """The live half of a load receipt: what reached the model, this session."""
    session = session_of(payload)
    items = _instruction_items(payload)
    if not items:
        session_log(session, {"event": "instructions_loaded", "ts": now_iso(),
                              "session": session, "n": 0,
                              "note": "payload carried no file list"})
        return None
    for it in items[:200]:
        path = it.get("path") or it.get("file") or it.get("filePath") or \
            it.get("file_path") or ""
        content = it.get("content") or it.get("text")
        status, cov = _load_status(_norm(path) if path else "", content)
        session_log(session, {
            "schemaVersion": SCHEMA_VERSION, "event": "instructions_loaded",
            "ts": now_iso(), "session": session, "path": _norm(path) if path else "",
            "type": it.get("type"), "status": status, "coverage": cov,
            "bytesLoaded": len(content) if isinstance(content, str) else None,
            "bytesOnDisk": (os.path.getsize(path)
                            if path and os.path.isfile(path) else None),
            "source": "live:InstructionsLoaded"})
    return None


def _tail_lines(path: str, cap: int = 512 * 1024):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > cap:
                fh.seek(size - cap)
                fh.readline()
            raw = fh.read()
    except OSError:
        return []
    out = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def handle_stop(payload):
    """Funnel counters for this session, from its own (small) receipt file."""
    session = session_of(payload)
    rows = _tail_lines(session_receipt_path(session))
    counts = {"humanTurns": 0, "candidates": 0, "asks": 0, "changes": 0,
              "instructionsLoaded": 0, "loadedComplete": 0,
              "loadedTruncated": 0, "notLoaded": 0, "fires": 0}
    for r in rows:
        ev = r.get("event")
        if ev == "human_turn":
            counts["humanTurns"] += 1
        elif ev == "candidate":
            counts["candidates"] += 1
            if r.get("decision") == "ask":
                counts["asks"] += 1
        elif ev == "change":
            counts["changes"] += 1
        elif ev == "fire":
            counts["fires"] += 1
        elif ev == "instructions_loaded":
            counts["instructionsLoaded"] += 1
            st = r.get("status")
            if st == "loaded_complete":
                counts["loadedComplete"] += 1
            elif st == "loaded_truncated":
                counts["loadedTruncated"] += 1
            elif st == "not_loaded":
                counts["notLoaded"] += 1
    row = {"schemaVersion": SCHEMA_VERSION, "event": "session_stop",
           "ts": now_iso(), "session": session, "cwd": payload.get("cwd"),
           "activeRules": _n_active_rules(), "counts": counts}
    append_jsonl(spath("funnel.jsonl"), row)
    session_log(session, {"event": "session_stop", "counts": counts})
    return None


# --------------------------------------------------------------------------
# the wrapper every generated script goes through
# --------------------------------------------------------------------------

#: The events whose hook may answer with a permission decision.  Everything
#: else writes evidence and stays silent on stdout.
DECIDING_EVENTS = ("PreToolUse",)

HANDLERS = {
    "PreToolUse": handle_pre_tool_use,
    "PostToolUse": handle_post_tool_use,
    "UserPromptSubmit": handle_user_prompt_submit,
    "SessionStart": handle_session_start,
    "InstructionsLoaded": handle_instructions_loaded,
    "Stop": handle_stop,
}


def guard_main(event: str, argv=None) -> int:
    """Read stdin, run the handler, print at most one JSON object, exit 0.

    On **any** internal exception: exit 0, nothing on stdout, and the error in
    ``hooklog.jsonl``.  That is the whole contract.
    """
    t0 = time.time()
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            payload = {}
        handler = HANDLERS.get(event)
        if handler is None:
            return 0
        result = handler(payload)
        out, rule_id = result if isinstance(result, tuple) else (result, None)
    except Exception as exc:
        log({"event": "error", "hook": event,
             "error": "%s: %s" % (type(exc).__name__, exc)})
        if os.environ.get("PRECEDENT_HOOK_DEBUG"):
            import traceback
            traceback.print_exc()
        return 0                         # exit 0, no stdout: never wedge the agent
    ms = round((time.time() - t0) * 1000, 2)
    session = session_of(payload) if isinstance(payload, dict) else "unknown"
    if out is None:
        log({"event": "allow" if event == "PreToolUse" else event.lower(),
             "hook": event, "ms": ms, "session": session})
        # Only the events that can carry a permission decision answer with an
        # empty object.  For UserPromptSubmit and SessionStart, Claude Code
        # treats a hook's stdout as text to put in front of the model; printing
        # anything at all there — even "{}" — is a change to the prompt we have
        # no reason to make.  Silence is the unambiguous no-op for every event.
        if event in DECIDING_EVENTS:
            sys.stdout.write("{}\n")
    else:
        log({"event": out.get("permissionDecision", event.lower()),
             "hook": event, "rule": rule_id, "ms": ms, "session": session})
        session_log(session, {"event": "fire", "rule": rule_id,
                              "decision": out.get("permissionDecision")})
        sys.stdout.write(json.dumps({"hookSpecificOutput": out},
                                    ensure_ascii=False) + "\n")
    return 0
