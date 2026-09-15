# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""ENFORCER — render, install, uninstall and audit the Claude Code hook suite.

Six events, one library, one settings block::

    <state>/hooks/_plib.py                 precedent._hooklib, copied verbatim
    <state>/hooks/pre_tool_use.py          PreToolUse   — precedents + ownership
    <state>/hooks/post_tool_use.py         PostToolUse  — change-feed capture
    <state>/hooks/user_prompt_submit.py    UserPromptSubmit — trusted origin
    <state>/hooks/session_start.py         SessionStart — live receipts
    <state>/hooks/instructions_loaded.py   InstructionsLoaded — live receipts
    <state>/hooks/stop.py                  Stop — funnel counters
    <state>/hooks/installed.json           the receipt `hooks status` diffs against

Each generated script is three lines of logic: bake the state dir, import the
library next to it, delegate.  The library is
:mod:`precedent._hooklib` **byte for byte**, so the code Claude Code runs is the
code the test suite imports — there is no second implementation to drift.

``hooks install`` is **dry-run by default** and prints the exact merge.  With
``--apply`` it is the only command in ``precedent`` that writes a
``settings.json``, under four constraints:

* a **timestamped backup** of the file goes to ``<state>/backups/`` first;
* the merge is **non-destructive** (existing hooks are kept) and **idempotent**;
* the write is atomic (``os.replace`` of a temp file in the same directory);
* the user's real ``~/.claude`` additionally requires ``--i-know``.

Every entry we write is marker-tagged twice — the command points into *this*
state dir's ``hooks/`` and carries ``--precedent-hook <Event>`` — so
``hooks uninstall --apply`` removes exactly ours and nothing else, and
``hooks status`` can report drift against ``installed.json``.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shlex

from . import __version__
from .state import now_iso, stamp

__all__ = [
    "HOOK_EVENTS",
    "MARKER",
    "OWNERSHIP_TOOLS",
    "SettingsUnreadable",
    "backup_path_for",
    "backup_settings",
    "hooks_block",
    "hooks_key_was_ours",
    "install",
    "foreign_entries_in_our_hooks_dir",
    "is_precedent_entry",
    "merge_settings",
    "settings_diff",
    "settings_text",
    "plib_source",
    "read_settings",
    "render_hook_script",
    "render_plan",
    "render_scripts",
    "render_status",
    "status",
    "uninstall",
    "uninstall_settings",
    "write_settings",
]

class SettingsUnreadable(RuntimeError):
    """The settings.json we were asked to merge into cannot be merged safely.

    Raised when the file exists but is not valid JSON, is not a JSON object, or
    carries a ``hooks`` value that is not an object.  Merging in any of those
    cases would mean *replacing* the user's file with ours rather than adding to
    it, so ``install --apply`` refuses and says why.  (``uninstall`` never had
    to: it only ever removes, so an unparseable file is simply left alone.)
    """

    def __init__(self, path: str, why: str):
        super().__init__(f"refusing to merge into {path}: {why}")
        self.path = path
        self.why = why


#: What :func:`read_settings` found: ``absent`` | ``ok`` | ``unreadable``.
SETTINGS_ABSENT, SETTINGS_OK, SETTINGS_UNREADABLE = "absent", "ok", "unreadable"

_STATE_DIR_SENTINEL = "__PRECEDENT_STATE_DIR__"
_CLAUDE_HOME_SENTINEL = "__PRECEDENT_CLAUDE_HOME__"

#: The marker every entry we write carries, in addition to pointing into this
#: state dir.  ``grep -n precedent-hook ~/.claude/settings.json`` finds ours.
MARKER = "--precedent-hook"

#: Always watched for ownership, whatever the confirmed rules happen to match:
#: write permission is enforced by **path**, not by tool (hermes#99729 — "the
#: approval gate is a convention; write_file / patch / terminal reach the skills
#: directory directly").
OWNERSHIP_TOOLS = ("Bash", "Edit", "MultiEdit", "NotebookEdit", "Write")

#: ``(event, script, timeout seconds, what it does)`` — the whole suite.
HOOK_EVENTS = (
    ("PreToolUse", "pre_tool_use.py", 5,
     "confirmed precedents (deny/ask) + ownership of governed writes"),
    ("PostToolUse", "post_tool_use.py", 5,
     "change-feed capture: what a Write/Edit/Bash call actually touched"),
    ("UserPromptSubmit", "user_prompt_submit.py", 5,
     "record the human turn — the one origin precedent treats as trusted"),
    ("SessionStart", "session_start.py", 10,
     "live receipt: this session started, with N active rules"),
    ("InstructionsLoaded", "instructions_loaded.py", 10,
     "live receipt: which instruction files reached the model, whole or truncated"),
    ("Stop", "stop.py", 10,
     "funnel counters for the session (turns, candidates, asks, loads)"),
)

_POST_MATCHER = "Write|Edit|Bash"

_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
# Copyright 2026 The precedent authors.
# SPDX-License-Identifier: Apache-2.0
"""precedent {event} hook -- GENERATED by `precedent hooks install`, do not edit.

{what}

Contract (all six scripts, enforced in _plib.guard_main):
  * deny / ask ONLY when a rule with status == "active" matches;
  * everything else is allow ({{}}) + a line in <state>/hooklog.jsonl;
  * ANY internal exception -> exit 0, NO stdout, the error in hooklog.jsonl.

    echo '{{"hook_event_name":"{event}", ...}}' | python3 {script}
"""

import os
import sys

STATE_DIR = os.environ.get("PRECEDENT_STATE_DIR") or "{state_dir}"
CLAUDE_HOME = os.environ.get("PRECEDENT_CLAUDE_HOME") or "{claude_home}"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _plib as P                      # precedent._hooklib, copied verbatim
except Exception:
    sys.exit(0)                            # no library -> allow, silently

P.configure(STATE_DIR, CLAUDE_HOME)
sys.exit(P.guard_main("{event}", sys.argv[1:]))
'''


def plib_source() -> str:
    """The body of ``_plib.py``: :mod:`precedent._hooklib`, byte for byte."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_hooklib.py")
    with open(here, "r", encoding="utf-8") as fh:
        return fh.read()


def _esc(value: str) -> str:
    return json.dumps(str(value))[1:-1]


def render_scripts(state_dir: str, claude_home: str = "") -> dict[str, str]:
    """``{filename: body}`` for the whole suite, state dir baked in."""
    out = {"_plib.py": plib_source()}
    for event, script, _timeout, what in HOOK_EVENTS:
        out[script] = _SCRIPT_TEMPLATE.format(
            event=event, script=script, what=what,
            state_dir=_esc(state_dir), claude_home=_esc(claude_home))
    return out


def render_hook_script(state_dir: str, claude_home: str = "") -> str:
    """The PreToolUse script body (the one users ask to see)."""
    return render_scripts(state_dir, claude_home)["pre_tool_use.py"]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _cmd(script_path: str, event: str) -> str:
    return f"python3 {shlex.quote(script_path)} {MARKER} {event}"


def pre_tool_matcher(rules: list[dict]) -> str:
    """One matcher covering every tool an active rule names, plus ownership.

    A single entry, not one per rule: two entries whose matchers both match
    ``Bash`` would run the hook twice for one call, log it twice, and answer
    twice.  ``*`` in any rule swallows the rest.
    """
    tools: set[str] = set(OWNERSHIP_TOOLS)
    for r in rules:
        for t in str(r.get("tool") or "").split("|"):
            t = t.strip()
            if t == "*":
                return "*"
            if t:
                tools.add(t)
    return "|".join(sorted(tools))


def hooks_block(rules: list[dict], hooks_dir: str) -> dict:
    """The ``hooks`` object precedent merges into ``settings.json``."""
    def entry(event: str, script: str, timeout: int, matcher: str | None) -> dict:
        e: dict = {}
        if matcher is not None:
            e["matcher"] = matcher
        e["hooks"] = [{"type": "command",
                       "command": _cmd(os.path.join(hooks_dir, script), event),
                       "timeout": timeout}]
        return e

    block: dict[str, list] = {}
    for event, script, timeout, _what in HOOK_EVENTS:
        if event == "PreToolUse":
            matcher: str | None = pre_tool_matcher(rules)
        elif event == "PostToolUse":
            matcher = _POST_MATCHER
        else:
            matcher = None
        block[event] = [entry(event, script, timeout, matcher)]
    return block


def _same(a: dict, b: dict) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def read_settings(path: str) -> tuple[dict | None, str, str]:
    """``(document, state, why)`` for a settings.json we are about to merge.

    ``state`` is ``absent`` (no such file — we may create it), ``ok`` (a JSON
    object we can merge into), or ``unreadable``.  The distinction is the whole
    point: :func:`json.load` failing and the file not existing look identical to
    a ``default=None`` reader, and treating the first like the second is how a
    hand-edited ``settings.json`` gets *replaced* by ours instead of merged
    into.  Nothing here writes.
    """
    if not os.path.exists(path):
        return None, SETTINGS_ABSENT, ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError as exc:
        return None, SETTINGS_UNREADABLE, f"cannot be read ({exc.strerror or exc})"
    except ValueError as exc:
        return None, SETTINGS_UNREADABLE, f"is not valid JSON ({exc})"
    if not isinstance(doc, dict):
        return None, SETTINGS_UNREADABLE, (
            f"is a JSON {type(doc).__name__}, not an object")
    hooks = doc.get("hooks")
    if "hooks" in doc and hooks is not None and not isinstance(hooks, dict):
        return doc, SETTINGS_UNREADABLE, (
            f'its "hooks" value is a JSON {type(hooks).__name__}, not an object')
    return doc, SETTINGS_OK, ""


def merge_settings(existing: dict | None, block: dict) -> dict:
    """Non-destructive, idempotent merge.

    Ours are replaced (a changed matcher must not leave the old entry behind);
    everyone else's are untouched, in order.  A ``hooks`` value that is not an
    object cannot be merged into without discarding it, so this raises
    :class:`SettingsUnreadable` rather than silently dropping user data.
    """
    merged = json.loads(json.dumps(existing)) if isinstance(existing, dict) else {}
    hooks = merged.get("hooks")
    if hooks is None:
        hooks = merged["hooks"] = {}
    if not isinstance(hooks, dict):
        raise SettingsUnreadable(
            "settings.json",
            f'its "hooks" value is a JSON {type(hooks).__name__}, not an object')
    hooks_dir = None
    for entries in block.values():
        if entries:
            hooks_dir = _entry_hooks_dir(entries[0])
            if hooks_dir:
                break
    for event, entries in block.items():
        current = hooks.get(event)
        if not isinstance(current, list):
            current = []
        kept = [c for c in current
                if not (isinstance(c, dict) and hooks_dir
                        and is_precedent_entry(c, hooks_dir))]
        hooks[event] = kept + [e for e in entries]
    # An entry of ours under an event this build no longer writes (the suite
    # changed between versions) is still ours, and it still runs.  Sweep it out
    # here rather than leaving `hooks status` to report it forever.
    if hooks_dir:
        for event in list(hooks):
            if event in block or not isinstance(hooks.get(event), list):
                continue
            kept = [c for c in hooks[event]
                    if not is_precedent_entry(c, hooks_dir)]
            if len(kept) != len(hooks[event]):
                hooks[event] = kept
    return merged


def _entry_hooks_dir(entry: dict) -> str | None:
    for h in entry.get("hooks") or []:
        cmd = str(h.get("command", "")) if isinstance(h, dict) else ""
        if MARKER in cmd:
            try:
                path = shlex.split(cmd)[1]
            except (ValueError, IndexError):
                continue
            return os.path.dirname(path)
    return None


def is_precedent_entry(entry, hooks_dir: str) -> bool:
    """True when this settings hook entry is one **we** wrote.

    Both marks are required, not either: the command must point into *this*
    state dir's ``hooks/`` **and** carry ``--precedent-hook``.  Requiring only
    the directory would make every command that merely names a file in there
    ours — including a script the user put next to ours on purpose — and
    ``uninstall`` would delete their entry.  Requiring only the marker would
    make a second precedent install (another ``--state-dir``) ours to remove.
    See :func:`foreign_entries_in_our_hooks_dir` for what we then refuse to
    touch but still report.
    """
    if not isinstance(entry, dict):
        return False
    inside = os.path.join(hooks_dir, "")
    for h in entry.get("hooks") or []:
        if not isinstance(h, dict):
            continue
        cmd = str(h.get("command", ""))
        if inside in cmd and MARKER in cmd:
            return True
    return False


def foreign_entries_in_our_hooks_dir(settings, hooks_dir: str) -> list[dict]:
    """Entries that run something from our hooks dir but are **not** ours.

    We will not remove them (they are not marker-tagged, so they are not ours
    to remove), but leaving them unmentioned would be the silent half of a
    drift: they run out of a directory ``hooks install`` overwrites.
    """
    out: list[dict] = []
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    if not isinstance(hooks, dict):
        return out
    inside = os.path.join(hooks_dir, "")
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict) or is_precedent_entry(e, hooks_dir):
                continue
            for h in e.get("hooks") or []:
                if isinstance(h, dict) and inside in str(h.get("command", "")):
                    out.append({"event": event, "entry": e})
                    break
    return out


def uninstall_settings(existing: dict | None, hooks_dir: str,
                       drop_empty_hooks: bool = False) -> tuple[dict, int]:
    """Remove only our entries.  Returns ``(settings, n_removed)``.

    "Only ours" is literal: an event key we took nothing out of is left exactly
    as it was, including an empty list the user happened to have there.

    ``drop_empty_hooks`` removes the now-empty ``hooks`` object as well.  The
    caller passes it only when the install receipt recorded that there was no
    ``hooks`` key before we merged — i.e. when leaving ``"hooks": {}`` behind
    would mean uninstall did not round-trip.  Without that evidence the key
    stays, because deleting one we might not have created is the one
    destructive thing in an operation whose whole contract is "remove only
    ours".
    """
    merged = json.loads(json.dumps(existing)) if isinstance(existing, dict) else {}
    hooks = merged.get("hooks")
    if not isinstance(hooks, dict):
        return merged, 0
    removed = 0
    for event in list(hooks):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not is_precedent_entry(e, hooks_dir)]
        n = len(entries) - len(kept)
        if n == 0:
            continue                       # nothing of ours here: do not touch it
        removed += n
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if drop_empty_hooks and removed and not hooks:
        merged.pop("hooks", None)
    return merged, removed


# --------------------------------------------------------------------------
# the only writes that leave the state dir
# --------------------------------------------------------------------------

def backup_path_for(state, settings_path: str, now=None) -> str | None:
    """The exact path :func:`backup_settings` would write — without writing it.

    ``--dry-run`` prints this, so "where does my old file go" is a path you can
    read before you decide, not a ``<ts>`` placeholder you have to trust.  It is
    the *same* naming logic, collision suffix included, so the only way the
    printed path and the written one differ is if a second backup lands in the
    same second between the dry run and the apply.
    """
    if not os.path.isfile(settings_path):
        return None
    # Two --apply runs inside the same second must not share a filename: the
    # second backup would overwrite the first, and the file it overwrote is the
    # only copy of what the user had before the first merge.
    base = f"settings-{stamp(now)}"
    dest = state.path("backups", base + ".json")
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = state.path("backups", f"{base}-{n}.json")
    return dest


def backup_settings(state, settings_path: str, now=None) -> str | None:
    """Copy ``settings.json`` into ``<state>/backups/`` before we touch it."""
    dest = backup_path_for(state, settings_path, now=now)
    if dest is None:
        return None
    with open(settings_path, "r", encoding="utf-8") as fh:
        body = fh.read()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(body)
    state.write_json(dest[:-len(".json")] + ".meta.json", {
        "source": settings_path, "backedUpAt": now_iso(now), "bytes": len(body)})
    return dest


def settings_text(payload: dict) -> str:
    """The exact bytes ``--apply`` writes.  One definition, so the diff the dry
    run prints and the file the apply writes cannot drift apart."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def settings_diff(settings_path: str, merged: dict) -> list[str]:
    """Unified diff from what is on disk **now** to the exact bytes ``--apply``
    would write.  Byte level on purpose: if the merge also reformats a file the
    user indented by hand, that shows up here rather than as a surprise.
    """
    try:
        with open(settings_path, "r", encoding="utf-8") as fh:
            before = fh.read()
        before_label = settings_path
    except OSError:
        before, before_label = "", settings_path + "  (does not exist)"
    after = settings_text(merged)
    return list(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"{before_label}  (now, sha256 {sha256_text(before)[:12]}…)"
                 if before else f"{before_label}",
        tofile=f"{settings_path}  (after --apply, sha256 "
               f"{sha256_text(after)[:12]}…)",
        n=3, lineterm=""))


def write_settings(settings_path: str, payload: dict) -> str:
    """Atomically write ``settings.json``, keeping the file's own permissions.

    The single function in ``precedent`` allowed to write inside a Claude home,
    reached only from ``hooks install/uninstall --apply`` after
    :func:`backup_settings`.  The temp file carries this process's pid so two
    concurrent runs cannot hand each other a half-written file, and the mode of
    the file we are replacing is carried over — a settings.json the user had
    locked down to ``0600`` must not come back world-readable.
    """
    os.makedirs(os.path.dirname(settings_path) or ".", exist_ok=True)
    mode = None
    try:
        mode = os.stat(settings_path).st_mode & 0o7777
    except OSError:
        pass
    tmp = "%s.precedent.%d.tmp" % (settings_path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(settings_text(payload))
        fh.flush()
        os.fsync(fh.fileno())
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, settings_path)
    return settings_path


def write_scripts(state) -> list[str]:
    """Write the six scripts and the library under ``<state>/hooks/``."""
    state.ensure()
    written = []
    for name, body in render_scripts(state.root, state.claude_home).items():
        written.append(state.write_text(state.hook_script(name), body,
                                        mode=0o644 if name == "_plib.py" else 0o755))
    return written


def install(state, rules: list[dict], settings_path: str | None = None,
            write_settings_file: bool = False, now=None) -> dict:
    """``--apply``: write the scripts, and (optionally) merge settings.json.

    Returns ``{"scripts": [...], "settings": path|None, "backup": path|None,
    "changed": bool, "receipt": path}``.  Idempotent: a second run writes the
    same scripts and leaves an already-merged settings.json byte-identical.
    """
    scripts = write_scripts(state)
    out = {"scripts": scripts, "settings": None, "backup": None, "changed": False,
           "receipt": None, "refused": None}
    block = hooks_block(rules, state.hooks_dir)
    if write_settings_file and settings_path:
        existing, kind, why = read_settings(settings_path)
        out["settings"] = settings_path
        if kind == SETTINGS_UNREADABLE:
            # Merging here would mean *replacing* the file, not adding to it.
            # Refuse, loudly, and leave the scripts on disk: the user can fix
            # the JSON (or paste the block by hand) and re-run.
            out["refused"] = why
            out["receipt"] = _write_receipt(state, rules, block, out, now=now)
            return out
        merged = merge_settings(existing, block)
        # Remember it now: after the merge there is no way to tell a hooks key
        # we created from one the user already had, and `uninstall` needs to
        # know to round-trip the file back to what it was.  The answer is
        # STICKY: on a second --apply the key exists *because of the first one*,
        # and overwriting the receipt with "it existed" would leave a
        # `"hooks": {}` scar behind on uninstall.
        out["hooks_key_existed"] = (
            False if hooks_key_was_ours(state)
            else bool(isinstance(existing, dict) and "hooks" in existing))
        if json.dumps(existing, sort_keys=True) != json.dumps(merged, sort_keys=True):
            out["backup"] = backup_settings(state, settings_path, now=now)
            write_settings(settings_path, merged)
            out["changed"] = True
    out["receipt"] = _write_receipt(state, rules, block, out, now=now)
    return out


def _write_receipt(state, rules, block, out, now=None) -> str:
    """``installed.json`` — the baseline ``hooks status`` reports drift against."""
    bodies = render_scripts(state.root, state.claude_home)
    receipt = {
        "schemaVersion": 1,
        "tool": {"name": "precedent", "version": __version__},
        "installedAt": now_iso(now),
        "claudeHome": state.claude_home,
        "stateDir": state.root,
        "settingsPath": out.get("settings"),
        "settingsWritten": bool(out.get("changed")),
        "settingsRefused": out.get("refused"),
        "hooksKeyExisted": out.get("hooks_key_existed"),
        "backup": out.get("backup"),
        "scripts": {name: {"sha256": sha256_text(body), "bytes": len(body)}
                    for name, body in bodies.items()},
        "block": block,
        "rules": [{"id": r.get("id"), "tool": r.get("tool"),
                   "action": r.get("action"), "kind": r.get("kind", "precedent")}
                  for r in rules],
    }
    return state.write_json(state.install_receipt_path, receipt)


def hooks_key_was_ours(state) -> bool:
    """Did *this* install create the ``hooks`` object in settings.json?

    Read from the install receipt, which is written by ``--apply`` before
    anything else can edit the file.  No receipt (or an older one that predates
    the field) answers ``False``, so the conservative behaviour — leave the key
    alone — is what happens when we do not know.
    """
    receipt = state.read_json(state.install_receipt_path, default=None)
    return isinstance(receipt, dict) and receipt.get("hooksKeyExisted") is False


def uninstall(state, settings_path: str, now=None) -> dict:
    """``hooks uninstall --apply``: remove only our entries, after a backup.

    An unreadable settings.json is not an error here — there is nothing of ours
    to find in a file we cannot parse, so we remove nothing and say so.
    """
    existing, kind, why = read_settings(settings_path)
    if kind == SETTINGS_UNREADABLE:
        return {"settings": settings_path, "backup": None, "removed": 0,
                "changed": False, "refused": why}
    merged, removed = uninstall_settings(
        existing, state.hooks_dir,
        drop_empty_hooks=hooks_key_was_ours(state))
    out = {"settings": settings_path, "backup": None, "removed": removed,
           "changed": False, "refused": None}
    if removed == 0 or json.dumps(existing, sort_keys=True) == json.dumps(
            merged, sort_keys=True):
        return out
    out["backup"] = backup_settings(state, settings_path, now=now)
    write_settings(settings_path, merged)
    out["changed"] = True
    try:
        if os.path.isfile(state.install_receipt_path):
            os.remove(state.install_receipt_path)
    except OSError:                                       # pragma: no cover
        pass
    return out


# --------------------------------------------------------------------------
# status / drift
# --------------------------------------------------------------------------

def status(state, rules: list[dict], settings_path: str) -> dict:
    """What is installed, and every way it differs from what should be.

    Drift is the fourth STARVATION alarm in ``precedent report``: a gate that
    was installed once and then silently stopped matching (rule added, script
    from an older build, entry hand-edited) is exactly the failure mode that ran
    for eight weeks in hermes#105770.
    """
    bodies = render_scripts(state.root, state.claude_home)
    receipt = state.read_json(state.install_receipt_path, default=None)
    rec_scripts = (receipt or {}).get("scripts") or {}

    scripts = []
    for name, body in bodies.items():
        path = state.hook_script(name)
        on_disk = _sha256_file(path)
        want = sha256_text(body)
        was = (rec_scripts.get(name) or {}).get("sha256")
        if on_disk is None:
            state_ = "missing"
        elif on_disk == want:
            state_ = "current"
        elif was and on_disk == was:
            state_ = "older-build"
        else:
            state_ = "edited"
        scripts.append({"name": name, "path": path, "state": state_,
                        "sha256": on_disk, "expected": want})

    settings, settings_kind, settings_why = read_settings(settings_path)
    hooks = (settings or {}).get("hooks") if isinstance(settings, dict) else None
    expected = hooks_block(rules, state.hooks_dir)
    found, missing, extra = [], [], []
    for event, entries in expected.items():
        current = (hooks or {}).get(event) if isinstance(hooks, dict) else None
        current = [e for e in current if isinstance(e, dict)] if isinstance(current, list) else []
        ours = [e for e in current if is_precedent_entry(e, state.hooks_dir)]
        for want in entries:
            if any(_same(want, e) for e in ours):
                found.append(event)
            else:
                missing.append({"event": event, "entry": want})
        for e in ours:
            if not any(_same(e, want) for want in entries):
                extra.append({"event": event, "entry": e})
    if isinstance(hooks, dict):
        for event, current in hooks.items():
            if event in expected or not isinstance(current, list):
                continue
            for e in current:
                if isinstance(e, dict) and is_precedent_entry(e, state.hooks_dir):
                    extra.append({"event": event, "entry": e})

    # The matcher that matters is the one that is *installed*, not the one this
    # build would install: a rule added after the last `install --apply` is
    # accepted but not activated, and that gap is the whole point of the funnel.
    installed_matcher = None
    if isinstance(hooks, dict):
        for e in hooks.get("PreToolUse") or []:
            if isinstance(e, dict) and is_precedent_entry(e, state.hooks_dir):
                installed_matcher = str(e.get("matcher") or "")
                break
    expected_matcher = pre_tool_matcher(rules)
    matcher = installed_matcher if installed_matcher is not None else expected_matcher
    covered = set(matcher.split("|"))
    uncovered = []
    if installed_matcher is not None:
        for r in rules:
            if r.get("status", "active") != "active":
                continue
            for t in str(r.get("tool") or "").split("|"):
                t = t.strip()
                if t and t not in covered and "*" not in covered:
                    uncovered.append({"rule": r.get("id"), "tool": t})

    drift: list[str] = []
    for s in scripts:
        if s["state"] == "missing":
            drift.append(f"script missing: {s['name']} — run `hooks install --apply`")
        elif s["state"] == "older-build":
            drift.append(f"script {s['name']} is from an older precedent build "
                         f"— re-run `hooks install --apply`")
        elif s["state"] == "edited":
            drift.append(f"script {s['name']} does not match this build and does "
                         f"not match installed.json — edited by hand?")
    if settings_kind == SETTINGS_UNREADABLE:
        drift.append(f"settings.json at {settings_path} {settings_why} — "
                     f"`hooks install --apply` will refuse to merge into it")
    elif settings is None:
        drift.append(f"settings.json not readable at {settings_path}")
    if missing:
        drift.append(f"settings.json is missing {len(missing)} of our entries: "
                     + ", ".join(sorted({m['event'] for m in missing})))
    if extra:
        drift.append(f"settings.json carries {len(extra)} stale precedent "
                     f"entr{'y' if len(extra) == 1 else 'ies'} (a matcher or a "
                     f"script path changed): "
                     + ", ".join(sorted({e['event'] for e in extra})))
    if uncovered:
        drift.append(f"{len(uncovered)} active rule(s) name a tool the installed "
                     f"PreToolUse matcher does not cover: "
                     + ", ".join(f"{u['rule']}:{u['tool']}" for u in uncovered[:4]))
    foreign = foreign_entries_in_our_hooks_dir(settings or {}, state.hooks_dir)
    if foreign:
        drift.append(
            f"{len(foreign)} settings.json entr"
            f"{'y' if len(foreign) == 1 else 'ies'} run something out of "
            f"{state.hooks_dir}/ without our marker ({MARKER}) — precedent will "
            f"neither update nor remove "
            f"{'it' if len(foreign) == 1 else 'them'}: "
            + ", ".join(sorted({f['event'] for f in foreign})))
    if receipt is None and not found:
        drift.append("not installed (no installed.json, no entries in settings.json)")

    return {
        "installed": bool(found) and receipt is not None,
        "receipt": receipt,
        "settingsPath": settings_path,
        "settingsExists": os.path.isfile(settings_path),
        "settingsReadable": settings_kind != SETTINGS_UNREADABLE,
        "foreign": foreign,
        "scripts": scripts,
        "entriesFound": len(found), "entriesExpected": sum(
            len(v) for v in expected.values()),
        "missing": missing, "extra": extra,
        "matcher": matcher, "expectedMatcher": expected_matcher,
        "activeRules": len([r for r in rules if r.get("status", "active") == "active"]),
        "uncoveredTools": uncovered,
        "drift": drift,
    }


def render_status(st: dict) -> str:
    L = ["# precedent hooks status", ""]
    rec = st.get("receipt") or {}
    L.append(f"installed          : {'yes' if st['installed'] else 'no'}"
             + (f"  (at {rec.get('installedAt')}, precedent "
                f"{(rec.get('tool') or {}).get('version')})" if rec else ""))
    L.append(f"settings.json      : {st['settingsPath']}"
             + ("  (present)" if st["settingsExists"] and
                st.get("settingsReadable", True)
                else "  (UNREADABLE)" if st["settingsExists"]
                else "  (MISSING)"))
    L.append(f"entries            : {st['entriesFound']}/{st['entriesExpected']} ours")
    L.append(f"PreToolUse matcher : {st['matcher']}"
             + ("" if st["matcher"] == st.get("expectedMatcher")
                else f"   (this build would install: {st.get('expectedMatcher')})"))
    L.append(f"active rules       : {st['activeRules']}")
    L.append("")
    L.append("scripts:")
    for s in st["scripts"]:
        L.append(f"  {s['state']:<11} {s['name']:<22} "
                 f"{(s['sha256'] or '-')[:12]}  {s['path']}")
    L.append("")
    if st["drift"]:
        L.append(f"DRIFT ({len(st['drift'])}):")
        for d in st["drift"]:
            L.append(f"  ! {d}")
    else:
        L.append("drift              : none — what is installed is what this build "
                 "would write")
    L.append("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# the dry-run plan
# --------------------------------------------------------------------------

_EXAMPLE_DENY = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "[precedent p-1a2b3c4d] 用 uv，不要用 pip "
                                    "-- from your correction on 2026-09-12: "
                                    "不要用 pip，用 uv",
    }
}


def render_plan(state, rules: list[dict], settings_path: str,
                apply: bool = False, uninstall: bool = False,
                show_scripts: bool = False, now=None) -> tuple[str, dict]:
    """Human-readable plan + the settings object that would be written."""
    bodies = render_scripts(state.root, state.claude_home)
    block = hooks_block(rules, state.hooks_dir)
    existing, kind, why = read_settings(settings_path)
    refused = why if kind == SETTINGS_UNREADABLE else None
    if refused:
        merged, removed, idempotent = existing, 0, True
    elif uninstall:
        merged, removed = uninstall_settings(
            existing, state.hooks_dir, drop_empty_hooks=hooks_key_was_ours(state))
        idempotent = json.dumps(existing, sort_keys=True) == json.dumps(
            merged, sort_keys=True)
    else:
        merged, removed = merge_settings(existing, block), 0
        idempotent = json.dumps(existing, sort_keys=True) == json.dumps(
            merged, sort_keys=True)

    L: list[str] = []
    L.append(f"# precedent hooks {'uninstall' if uninstall else 'install'} claude-code"
             + ("  (--apply)" if apply else "  (--dry-run)"))
    L.append("")
    L.append(f"claude home          : {state.claude_home}")
    L.append(f"state dir            : {state.root}")
    L.append(f"confirmed precedents : {len(rules)}")
    for r in rules:
        if r.get("kind") == "ownership-guard":
            L.append(f"  - {r.get('id')}  {r.get('tool')}  {r.get('action')}  "
                     f"[ownership guard] {r.get('message', '')[:60]}")
            continue
        ms = r.get("matchers") or [{"type": "input_regex",
                                    "regex": r.get("input_regex", "")}]
        desc = "; ".join(
            (m.get("regex") if m.get("type") == "input_regex"
             else f"{m.get('type')}({m.get('field')}"
                  + (f"={m.get('value')}" if "value" in m else "") + ")")
            for m in ms if isinstance(m, dict))
        L.append(f"  - {r.get('id')}  {r.get('tool')}  {r.get('action')}  "
                 f"[{r.get('match', 'all')}] {desc}")
    if not rules and not uninstall:
        L.append("  (none — `precedent confirm <id>` first; the block below still "
                 "installs the suite, which records candidates and receipts but "
                 "denies nothing)")
    L.append("")
    if not uninstall:
        L.append("## the six hooks")
        L.append("")
        L.append("| event | script | timeout | what it does |")
        L.append("|---|---|---|---|")
        for event, script, timeout, what in HOOK_EVENTS:
            L.append(f"| {event} | `{script}` | {timeout}s | {what} |")
        L.append("")
        L.append(f"PreToolUse matcher   : `{pre_tool_matcher(rules)}`  "
                 f"(tools named by active rules ∪ {'|'.join(OWNERSHIP_TOOLS)} "
                 f"for ownership)")
        L.append(f"PostToolUse matcher  : `{_POST_MATCHER}`")
        L.append("")
    L.append("## files precedent would write (all of them inside the state dir)")
    L.append("")
    for name, body in bodies.items():
        L.append(f"  {state.hook_script(name)}")
        L.append(f"      {len(body.splitlines())} lines, {len(body)} bytes, "
                 f"sha256 {sha256_text(body)[:16]}…")
    L.append(f"  {state.install_receipt_path}")
    L.append("      the install receipt `precedent hooks status` diffs against")
    L.append("")
    L.append(f"settings.json        : {settings_path}")
    if refused:
        L.append(f"  !! REFUSED         : this file {refused}")
        L.append("  !!                   precedent will NOT touch it — merging "
                 "into a file it cannot parse")
        L.append("  !!                   would mean replacing your settings, not "
                 "adding to them.")
        L.append("  !!                   Fix the JSON and re-run, or use "
                 "`--scripts-only` and paste the")
        L.append("  !!                   hooks block below in by hand.")
    if uninstall:
        L.append(f"  entries to remove  : {removed}")
    backup_preview = backup_path_for(state, settings_path, now=now)
    if refused or idempotent:
        # Nothing is written, so nothing is backed up: say which of the two it
        # is rather than printing a path that will never exist.
        L.append("  backup             : none — "
                 + ("precedent refuses to touch this file" if refused
                    else "nothing to change, so nothing to back up"))
    elif backup_preview is None:
        L.append(f"  backup             : none — {settings_path} does not exist "
                 f"yet, so there is nothing to back up")
    else:
        L.append(f"  backup             : {backup_preview}")
        L.append( "                       (the exact path this run would copy "
                  "your current file to, before any change)")
    L.append(f"  marker             : every entry we write contains "
             f"`{MARKER}` and points into {state.hooks_dir}/")
    L.append(f"  idempotent         : "
             + ("yes — nothing to change" if idempotent
                else "no — this run changes the file"))
    L.append("")
    if refused:
        L.append("## settings.json — precedent would write NOTHING")
        L.append("")
        L.append(f"The file at {settings_path} {refused}, so there is no merge "
                 "to show: your file stays exactly as it is.")
    else:
        diff = settings_diff(settings_path, merged)
        L.append("## settings.json — the exact diff")
        L.append("")
        if not diff:
            L.append("(empty — the file on disk is already byte-identical to "
                     "what this run would write)")
        else:
            L.append("```diff")
            L.extend(diff)
            L.append("```")
        L.append("")
        L.append("## settings.json — the exact result precedent would write")
        L.append("")
        L.append("```json")
        L.append(settings_text(merged).rstrip("\n"))
        L.append("```")
    if not uninstall:
        L.append("")
        L.append("## just the hooks block")
        L.append("")
        L.append("```json")
        L.append(json.dumps({"hooks": block}, ensure_ascii=False, indent=2))
        L.append("```")
        L.append("")
        L.append("## what the PreToolUse hook answers when a precedent matches")
        L.append("")
        L.append("```json")
        L.append(json.dumps(_EXAMPLE_DENY, ensure_ascii=False, indent=2))
        L.append("```")
        L.append("")
        L.append("No match → `{}` and exit 0. Any internal exception → exit 0, "
                 "no stdout, and the error in `<state>/hooklog.jsonl`.")
        if show_scripts:
            for name, body in bodies.items():
                L.append("")
                L.append(f"## {state.hook_script(name)}")
                L.append("")
                L.append("```python")
                L.append(body.rstrip("\n"))
                L.append("```")
        else:
            L.append("")
            L.append("(Re-run with `--show-scripts` to print every generated "
                     "script in full.)")
    return "\n".join(L) + "\n", merged
