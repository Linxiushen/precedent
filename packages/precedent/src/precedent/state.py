# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""State directory, Claude-home detection and the write guard.

Everything ``precedent`` remembers lives under ``~/.precedent`` (overridable
with ``--state-dir``; the tests always point it at ``tmp_path``)::

    ~/.precedent/
      config.json                 claude_home + created_at
      ledger.jsonl                acceptor's sha256 hash-chained ledger
      precedents.json             CONFIRMED rules — the only file the hook reads
      candidates.json             compiled-but-unconfirmed rules + birth-gate counts
      scans/receipts-<ts>.json    `precedent scan` output, timestamped
      snapshots/<id>.json         content-addressed manifests
      blobs/<sha256>              file bodies, deduplicated
      hooks/pre_tool_use.py       the hook script (written only by `hooks install --apply`)
      hooklog.jsonl               every hook decision + every hook error
      rejected.jsonl              LLM drafts the schema/gate rejected (negative examples)
      spend.jsonl                 one line per `claude -p` call: model, budget, cost
      backups/settings-<ts>.json  the settings.json as it was before we merged
      hooks/installed.json        what `hooks install --apply` wrote (drift baseline)
      candidates.jsonl            governed-write candidates (the ownership hook)
      owners.json                 `precedent own` — user- vs agent-owned artifacts
      agent_created.json          paths an agent created, per the change feed
      receipts/<session>.jsonl    LIVE load receipts + per-session hook events
      changes.jsonl               live change feed (PostToolUse)
      origins.jsonl               human turns (UserPromptSubmit) = trusted origin
      funnel.jsonl                per-session funnel counters (Stop)
      docket.json                 docket decisions: confirm / reject / snooze

The one hard rule: **nothing under the Claude home is ever written** — with a
single, explicit, backed-up exception.  :func:`guard_not_under_claude_home` is
called by every write path in this class, so a bug that aimed a write at
``~/.claude`` raises instead of corrupting a real home.  The exception is
``precedent hooks install/uninstall --apply``, which writes ``settings.json``
through :func:`precedent.hooks.write_settings` after
:func:`precedent.hooks.backup_settings` has copied the old file into
``<state>/backups/``; that path never goes through ``StateDir``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from acceptor import Ledger

from . import SCHEMA_VERSION, __version__

__all__ = [
    "DEFAULT_CLAUDE_HOME",
    "DEFAULT_STATE_DIR",
    "ClaudeHomeWriteRefused",
    "StateDir",
    "detect_claude_home",
    "guard_not_under_claude_home",
    "is_real_claude_home",
    "now_iso",
    "stamp",
]

DEFAULT_CLAUDE_HOME = "~/.claude"
DEFAULT_STATE_DIR = "~/.precedent"


class ClaudeHomeWriteRefused(RuntimeError):
    """Raised when a write would land inside the Claude home being read."""


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def stamp(now: datetime | None = None) -> str:
    """A filesystem-safe UTC timestamp, e.g. ``20260915T101530Z``."""
    dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def detect_claude_home(explicit: str | None = None) -> str:
    """Resolve the Claude home: ``--claude-home`` > ``$CLAUDE_CONFIG_DIR`` > ``~/.claude``."""
    if explicit:
        return _real(explicit)
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return _real(env)
    return _real(DEFAULT_CLAUDE_HOME)


def is_real_claude_home(path: str) -> bool:
    """True when ``path`` is (or is inside) the *user's own* ``~/.claude``.

    Used by ``undo --apply`` and ``hooks install --apply`` to refuse to touch a
    production home without ``--i-know``.  A synthetic home under ``tmp_path``
    is not one, which is what makes the destructive paths testable at all.
    """
    real_home = _real(DEFAULT_CLAUDE_HOME)
    target = _real(path)
    return target == real_home or target.startswith(real_home + os.sep)


def guard_not_under_claude_home(path: str, claude_home: str) -> str:
    """Return the resolved ``path``, or raise if it is inside ``claude_home``."""
    target = _real(path)
    home = _real(claude_home)
    if target == home or target.startswith(home + os.sep):
        raise ClaudeHomeWriteRefused(
            f"refusing to write {target!r}: it is inside the Claude home {home!r}, "
            "which precedent only ever reads")
    return target


@dataclass
class StateDir:
    """The ``~/.precedent`` tree.  Every write goes through :meth:`write_json`."""

    root: str
    claude_home: str

    @classmethod
    def open(cls, state_dir: str | None = None, claude_home: str | None = None,
             create: bool = False) -> "StateDir":
        root = _real(state_dir or DEFAULT_STATE_DIR)
        home = detect_claude_home(claude_home)
        # A state dir inside the Claude home would make every later write a
        # violation; catch it at open() rather than at the first write.
        guard_not_under_claude_home(root, home)
        st = cls(root=root, claude_home=home)
        if create:
            st.ensure()
        return st

    # ---- layout ---------------------------------------------------------
    @property
    def config_path(self) -> str:
        return os.path.join(self.root, "config.json")

    @property
    def ledger_path(self) -> str:
        return os.path.join(self.root, "ledger.jsonl")

    @property
    def precedents_path(self) -> str:
        return os.path.join(self.root, "precedents.json")

    @property
    def candidates_path(self) -> str:
        return os.path.join(self.root, "candidates.json")

    @property
    def scans_dir(self) -> str:
        return os.path.join(self.root, "scans")

    @property
    def snapshots_dir(self) -> str:
        return os.path.join(self.root, "snapshots")

    @property
    def blobs_dir(self) -> str:
        return os.path.join(self.root, "blobs")

    @property
    def hooks_dir(self) -> str:
        return os.path.join(self.root, "hooks")

    @property
    def backups_dir(self) -> str:
        """Timestamped copies of every ``settings.json`` we were about to edit."""
        return os.path.join(self.root, "backups")

    @property
    def hooklog_path(self) -> str:
        return os.path.join(self.root, "hooklog.jsonl")

    @property
    def rejected_path(self) -> str:
        """LLM drafts the schema or the birth gate threw out, with reasons."""
        return os.path.join(self.root, "rejected.jsonl")

    @property
    def spend_path(self) -> str:
        return os.path.join(self.root, "spend.jsonl")

    @property
    def hook_script_path(self) -> str:
        return os.path.join(self.hooks_dir, "pre_tool_use.py")

    def hook_script(self, name: str) -> str:
        """Path of one generated hook script, e.g. ``post_tool_use.py``."""
        return os.path.join(self.hooks_dir, name)

    @property
    def install_receipt_path(self) -> str:
        """What ``hooks install --apply`` actually wrote — the drift baseline."""
        return os.path.join(self.hooks_dir, "installed.json")

    # ---- B-Enforce: ownership, live receipts, the change feed ------------
    @property
    def write_candidates_path(self) -> str:
        """Append-only governed-write candidates, written by the PreToolUse hook."""
        return os.path.join(self.root, "candidates.jsonl")

    @property
    def owners_path(self) -> str:
        """``precedent own`` — who owns which governed artifact."""
        return os.path.join(self.root, "owners.json")

    @property
    def agent_created_path(self) -> str:
        """Index of paths the change feed shows an agent created (never a human)."""
        return os.path.join(self.root, "agent_created.json")

    @property
    def receipts_dir(self) -> str:
        """Live per-session receipts written by the SessionStart/InstructionsLoaded hooks."""
        return os.path.join(self.root, "receipts")

    @property
    def changes_path(self) -> str:
        """Live change feed captured by the PostToolUse hook."""
        return os.path.join(self.root, "changes.jsonl")

    @property
    def origins_path(self) -> str:
        """Human turns recorded by the UserPromptSubmit hook (trusted origin)."""
        return os.path.join(self.root, "origins.jsonl")

    @property
    def funnel_path(self) -> str:
        """Per-session funnel counters written by the Stop hook."""
        return os.path.join(self.root, "funnel.jsonl")

    @property
    def docket_path(self) -> str:
        """Docket decisions: confirmed / rejected / snoozed, with their reasons."""
        return os.path.join(self.root, "docket.json")

    def session_receipt_path(self, session_id: str) -> str:
        safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_.")[:120]
        return os.path.join(self.receipts_dir, (safe or "unknown") + ".jsonl")

    def ensure(self) -> None:
        for d in (self.root, self.scans_dir, self.snapshots_dir, self.blobs_dir,
                  self.hooks_dir, self.backups_dir, self.receipts_dir):
            guard_not_under_claude_home(d, self.claude_home)
            os.makedirs(d, exist_ok=True)

    # ---- guarded io -----------------------------------------------------
    def path(self, *parts: str) -> str:
        return guard_not_under_claude_home(os.path.join(self.root, *parts),
                                           self.claude_home)

    def write_text(self, path: str, text: str, mode: int | None = None) -> str:
        target = guard_not_under_claude_home(path, self.claude_home)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
        if mode is not None:
            os.chmod(target, mode)
        return target

    def write_json(self, path: str, payload) -> str:
        return self.write_text(
            path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def read_json(self, path: str, default=None):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    # ---- records --------------------------------------------------------
    def write_config(self, now: datetime | None = None) -> dict:
        cfg = self.read_json(self.config_path, default=None) or {}
        cfg.update({
            "schemaVersion": SCHEMA_VERSION,
            "tool": {"name": "precedent", "version": __version__},
            "claudeHome": self.claude_home,
            "stateDir": self.root,
            "updatedAt": now_iso(now),
        })
        cfg.setdefault("createdAt", cfg["updatedAt"])
        self.write_json(self.config_path, cfg)
        return cfg

    def ledger(self, strict: bool = False) -> Ledger:
        """Open the hash-chained ledger (``acceptor.Ledger``).

        ``strict=False``: v0 writes lifecycle *events*, not gate certificates,
        so the anti-rewind certificate checks have nothing to chew on yet.
        """
        guard_not_under_claude_home(self.ledger_path, self.claude_home)
        self.ensure()
        return Ledger(self.ledger_path, strict=strict)

    def precedents(self) -> list[dict]:
        data = self.read_json(self.precedents_path, default=None)
        if isinstance(data, dict):
            rules = data.get("precedents")
            return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        return []

    def write_precedents(self, rules: list[dict], now: datetime | None = None) -> str:
        return self.write_json(self.precedents_path, {
            "schemaVersion": SCHEMA_VERSION,
            "updatedAt": now_iso(now),
            "precedents": rules,
        })

    def candidates(self) -> list[dict]:
        data = self.read_json(self.candidates_path, default=None)
        if isinstance(data, dict) and isinstance(data.get("candidates"), list):
            return [c for c in data["candidates"] if isinstance(c, dict)]
        return []

    def write_candidates(self, cands: list[dict], now: datetime | None = None) -> str:
        return self.write_json(self.candidates_path, {
            "schemaVersion": SCHEMA_VERSION,
            "updatedAt": now_iso(now),
            "candidates": cands,
        })

    def latest_scan(self) -> dict | None:
        try:
            names = sorted(n for n in os.listdir(self.scans_dir)
                           if n.startswith("receipts-") and n.endswith(".json"))
        except OSError:
            return None
        if not names:
            return None
        return self.read_json(os.path.join(self.scans_dir, names[-1]))
