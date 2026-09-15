# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Content-addressed snapshots of the learned-state tree, and ``undo --session``.

No tar, no zip: every file body is copied to ``blobs/<sha256>`` (deduplicated
across snapshots) and a JSON manifest in ``snapshots/`` records the tree.  That
makes a snapshot greppable, diffable and partially restorable, and makes two
snapshots of an unchanged tree cost one manifest.

What counts as learned state::

    <claude home>/CLAUDE.md
    <claude home>/skills/**
    <claude home>/projects/<slug>/memory/**
    <cwd>/CLAUDE.md                     for every working directory seen
    <cwd>/.claude/skills/**
    <cwd>/.claude/rules/**

``undo --session <id>`` asks the receipts change feed which of those files that
session mutated, finds the newest snapshot taken **before** the session
started, and restores those files — and only those files — to their
pre-session bytes.  It is **dry-run by default**; ``--apply`` refuses to touch
a real ``~/.claude`` unless ``--i-know`` is passed, which is why the whole path
is testable against a synthetic home.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .state import StateDir, is_real_claude_home, now_iso, stamp

__all__ = [
    "MAX_BLOB_BYTES",
    "Manifest",
    "UndoPlan",
    "apply_undo",
    "learned_state_roots",
    "plan_undo",
    "take_snapshot",
]

#: Anything larger is recorded in the manifest but its body is not stored.
#: Learned state is Markdown; a 32 MiB "memory file" is a bug, not a memory.
MAX_BLOB_BYTES = 4 * 1024 * 1024

_SKIP_DIR_NAMES = {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache"}


def learned_state_roots(claude_home: str, cwds: list[str] | None = None,
                        slugs: list[str] | None = None) -> list[str]:
    """Every path that is part of the agent's learned state, existing or not."""
    home = os.path.realpath(os.path.expanduser(claude_home))
    roots = [os.path.join(home, "CLAUDE.md"), os.path.join(home, "skills")]
    projects = os.path.join(home, "projects")
    found_slugs = list(slugs or [])
    if not found_slugs and os.path.isdir(projects):
        try:
            found_slugs = sorted(os.listdir(projects))
        except OSError:                                   # pragma: no cover - defensive
            found_slugs = []
    for slug in found_slugs:
        roots.append(os.path.join(projects, slug, "memory"))
    for cwd in sorted(set(cwds or [])):
        if not cwd:
            continue
        c = os.path.realpath(os.path.expanduser(cwd))
        roots.extend([os.path.join(c, "CLAUDE.md"),
                      os.path.join(c, ".claude", "skills"),
                      os.path.join(c, ".claude", "rules")])
    out: list[str] = []
    for r in roots:
        if r not in out:
            out.append(r)
    return out


def _iter_files(root: str):
    if os.path.isfile(root) and not os.path.islink(root):
        yield root
        return
    if not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIR_NAMES)
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            yield p


def sha256_file(path: str) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 16)
            if not chunk:
                break
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


@dataclass
class Manifest:
    id: str
    created_at: str
    claude_home: str
    roots: list[str]
    files: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    @property
    def n_bytes(self) -> int:
        return sum(int(f.get("size") or 0) for f in self.files)

    def by_path(self) -> dict[str, dict]:
        return {f["path"]: f for f in self.files}

    def to_dict(self) -> dict:
        from . import SCHEMA_VERSION
        return {
            "schemaVersion": SCHEMA_VERSION,
            "id": self.id,
            "createdAt": self.created_at,
            "claudeHome": self.claude_home,
            "roots": self.roots,
            "nFiles": len(self.files),
            "nBytes": self.n_bytes,
            "files": self.files,
            "skipped": self.skipped,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        return cls(id=d.get("id", ""), created_at=d.get("createdAt", ""),
                   claude_home=d.get("claudeHome", ""), roots=list(d.get("roots") or []),
                   files=[f for f in (d.get("files") or []) if isinstance(f, dict)],
                   skipped=list(d.get("skipped") or []))


def take_snapshot(state: StateDir, roots: list[str] | None = None,
                  cwds: list[str] | None = None, slugs: list[str] | None = None,
                  now: datetime | None = None, dry_run: bool = False) -> Manifest:
    """Hash every learned-state file, copy new bodies into ``blobs/``.

    Writes only under the state dir, so it needs no ``--apply`` guard.
    """
    now = now or datetime.now(timezone.utc)
    roots = roots if roots is not None else learned_state_roots(
        state.claude_home, cwds=cwds, slugs=slugs)
    man = Manifest(id="", created_at=now_iso(now), claude_home=state.claude_home,
                   roots=roots)
    seen: set[str] = set()
    for root in roots:
        for path in _iter_files(root):
            if path in seen:
                continue
            seen.add(path)
            try:
                digest, size = sha256_file(path)
                mtime = os.path.getmtime(path)
            except OSError as exc:                        # pragma: no cover - defensive
                man.skipped.append({"path": path, "reason": f"unreadable: {exc}"})
                continue
            if size > MAX_BLOB_BYTES:
                man.skipped.append({"path": path, "reason": "over MAX_BLOB_BYTES",
                                    "size": size, "sha256": digest})
                continue
            man.files.append({"path": path, "sha256": digest, "size": size,
                              "mtime": mtime})
            if not dry_run:
                blob = os.path.join(state.blobs_dir, digest)
                if not os.path.exists(blob):
                    with open(path, "rb") as src:
                        data = src.read()
                    state.ensure()
                    target = state.path("blobs", digest)
                    with open(target, "wb") as dst:
                        dst.write(data)
    tree_hash = hashlib.sha256(
        "\n".join(f"{f['sha256']}  {f['path']}" for f in man.files).encode("utf-8")
    ).hexdigest()[:12]
    man.id = f"snap-{stamp(now)}-{tree_hash}"
    if not dry_run:
        state.write_json(os.path.join(state.snapshots_dir, man.id + ".json"),
                         man.to_dict())
    return man


def list_snapshots(state: StateDir) -> list[Manifest]:
    try:
        names = sorted(n for n in os.listdir(state.snapshots_dir) if n.endswith(".json"))
    except OSError:
        return []
    out = []
    for n in names:
        d = state.read_json(os.path.join(state.snapshots_dir, n))
        if isinstance(d, dict) and d.get("files") is not None:
            out.append(Manifest.from_dict(d))
    out.sort(key=lambda m: (m.created_at, m.id))
    return out


def newest_before(state: StateDir, when: str | None) -> Manifest | None:
    """Newest snapshot strictly older than ``when`` (ISO-8601)."""
    snaps = list_snapshots(state)
    if not when:
        return snaps[-1] if snaps else None
    older = [m for m in snaps if m.created_at and m.created_at < when]
    return older[-1] if older else None


# --------------------------------------------------------------------------
# undo
# --------------------------------------------------------------------------

@dataclass
class UndoPlan:
    session_id: str
    session_started_at: str | None
    snapshot_id: str | None
    snapshot_created_at: str | None
    actions: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def changes(self) -> list[dict]:
        return [a for a in self.actions if a["action"] in ("restore", "recreate", "delete")]

    def to_dict(self) -> dict:
        return {
            "sessionId": self.session_id,
            "sessionStartedAt": self.session_started_at,
            "snapshotId": self.snapshot_id,
            "snapshotCreatedAt": self.snapshot_created_at,
            "nTouched": len(self.actions),
            "nChanges": len(self.changes),
            "actions": self.actions,
            "notes": self.notes,
        }


def plan_undo(state: StateDir, session_id: str, scan_result) -> UndoPlan:
    """Which files that session touched, and what restoring them would mean.

    ``scan_result`` is a :class:`receipts.scan.ScanResult`; its change feed is
    the only source of "which files did session X mutate".
    """
    started: str | None = None
    for sr in scan_result.session_receipts:
        if sr.session_id == session_id:
            started = sr.started_at
            break
    touched: list[str] = []
    for m in scan_result.mutations:
        if m.session_id == session_id and m.path not in touched:
            touched.append(m.path)
    touched.sort()

    snap = newest_before(state, started)
    plan = UndoPlan(session_id=session_id, session_started_at=started,
                    snapshot_id=snap.id if snap else None,
                    snapshot_created_at=snap.created_at if snap else None)
    if started is None:
        plan.notes.append(
            f"session {session_id} is not in the scan — check --project/--last, "
            "or the transcript was deleted")
    if snap is None:
        plan.notes.append(
            "no snapshot older than the session start: nothing can be restored. "
            "Run `precedent snapshot` before unattended writes, not after.")
    if not touched:
        plan.notes.append("the change feed records no learned-state mutation for "
                          "this session — nothing to undo")

    index = snap.by_path() if snap else {}
    for path in touched:
        entry = index.get(path)
        exists = os.path.isfile(path)
        current = sha256_file(path)[0] if exists else None
        if entry is None:
            action = "delete" if exists else "noop"
            reason = ("created by this session (absent from the pre-session snapshot)"
                      if exists else "absent then and now")
            plan.actions.append({"path": path, "action": action, "reason": reason,
                                 "currentSha256": current, "snapshotSha256": None})
            continue
        if not exists:
            plan.actions.append({"path": path, "action": "recreate",
                                 "reason": "deleted by this session",
                                 "currentSha256": None,
                                 "snapshotSha256": entry["sha256"]})
            continue
        if current == entry["sha256"]:
            plan.actions.append({"path": path, "action": "unchanged",
                                 "reason": "already identical to the snapshot",
                                 "currentSha256": current,
                                 "snapshotSha256": entry["sha256"]})
            continue
        blob = os.path.join(state.blobs_dir, entry["sha256"])
        if not os.path.exists(blob):
            plan.actions.append({"path": path, "action": "blocked",
                                 "reason": f"blob {entry['sha256'][:12]}… is missing "
                                           "from the store",
                                 "currentSha256": current,
                                 "snapshotSha256": entry["sha256"]})
            continue
        plan.actions.append({"path": path, "action": "restore",
                             "reason": "content differs from the pre-session snapshot",
                             "currentSha256": current,
                             "snapshotSha256": entry["sha256"]})

    # conflict check: did a LATER session touch any of these too?
    later: dict[str, list[str]] = {}
    for m in scan_result.mutations:
        if m.session_id == session_id or m.path not in touched:
            continue
        if started and (m.ts or "") > started:
            later.setdefault(m.path, [])
            if m.session_id not in later[m.path]:
                later[m.path].append(m.session_id)
    for path, sessions in sorted(later.items()):
        plan.notes.append(
            f"CONFLICT: {os.path.basename(path)} was also written after "
            f"{session_id[:8]} by {', '.join(s[:8] for s in sessions)} — "
            "restoring it would discard that work too")
    return plan


class UndoRefused(RuntimeError):
    """Raised when ``--apply`` would touch a real ``~/.claude`` without ``--i-know``."""


def apply_undo(state: StateDir, plan: UndoPlan, i_know: bool = False) -> list[dict]:
    """Execute the plan.  Guarded; never called by ``--dry-run`` (the default)."""
    targets = [a["path"] for a in plan.changes]
    risky = [p for p in targets if is_real_claude_home(p)]
    if risky and not i_know:
        raise UndoRefused(
            f"{len(risky)} target(s) are inside your real ~/.claude "
            f"(e.g. {risky[0]}). precedent v0 will not write there. "
            "Pass --i-know if you really mean it.")
    done: list[dict] = []
    for action in plan.changes:
        path, kind = action["path"], action["action"]
        if kind in ("restore", "recreate"):
            blob = os.path.join(state.blobs_dir, action["snapshotSha256"])
            with open(blob, "rb") as fh:
                data = fh.read()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
            done.append(dict(action, result="restored", bytes=len(data)))
        elif kind == "delete":
            try:
                os.remove(path)
                done.append(dict(action, result="deleted"))
            except OSError as exc:                        # pragma: no cover - defensive
                done.append(dict(action, result=f"failed: {exc}"))
    return done
