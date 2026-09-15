# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Discovery of learned-state artifacts on disk, plus MEMORY.md index parsing.

Everything here is read-only.  Nothing in this package opens a file for writing
under the Claude home directory.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .textmatch import strip_frontmatter

__all__ = [
    "INDEX_MAX_BYTES",
    "INDEX_MAX_LINES",
    "Artifact",
    "IndexEntry",
    "discover_artifacts",
    "parse_frontmatter",
    "parse_index",
    "project_dir_for_slug",
    "read_text",
]

INDEX_MAX_LINES = 200
"""Documented MEMORY.md index cap: entries beyond this are silently dropped."""

INDEX_MAX_BYTES = 25 * 1024
"""Documented MEMORY.md index byte cap."""

KIND_MEMORY_INDEX = "memory_index"
KIND_MEMORY = "memory"
KIND_SKILL = "skill"
KIND_CLAUDE_MD = "claude_md"
KIND_RULE = "rule"


def read_text(path: str, limit: int = 4 * 1024 * 1024) -> str | None:
    """Read a text file, or return ``None`` if it is unreadable/absent."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# minimal YAML frontmatter subset
# --------------------------------------------------------------------------

_KV = re.compile(r"^(\s*)([A-Za-z0-9_.\-]+)\s*:\s*(.*)$")


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        inner = v[1:-1]
        if v[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
        return inner
    return v


def parse_frontmatter(text: str) -> dict:
    """Parse the subset of YAML that Claude Code writes into SKILL.md / memory files.

    Supports ``key: value``, quoted scalars, one level of nesting, block scalars
    (``|``, ``>``, ``>-``, ``|-``) and simple inline ``[a, b]`` lists.  Anything
    it cannot parse is ignored rather than raising — this is a forensic tool, not
    a validator.
    """
    fm_text, _ = strip_frontmatter(text or "")
    if not fm_text:
        return {}
    out: dict = {}
    stack: list[tuple[int, dict]] = [(-1, out)]

    def assign(indent: int, key: str, value) -> None:
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        container = stack[-1][1]
        container[key] = value
        if isinstance(value, dict):
            stack.append((indent, value))

    lines = fm_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _KV.match(line)
        if not m:
            i += 1
            continue
        indent, key, rest = len(m.group(1)), m.group(2), m.group(3).strip()
        if rest in ("|", ">", ">-", "|-", "|+", ">+"):
            folded = rest.startswith(">")
            block: list[str] = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                block.append(nxt.strip())
                i += 1
            assign(indent, key, (" " if folded else "\n").join(block).strip())
            continue
        if rest == "":
            j = i + 1
            nested = False
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip():
                    j += 1
                    continue
                nested = (len(nxt) - len(nxt.lstrip())) > indent
                break
            assign(indent, key, {} if nested else "")
            i += 1
            continue
        if rest.startswith("[") and rest.endswith("]"):
            assign(indent, key, [_unquote(x) for x in rest[1:-1].split(",") if x.strip()])
            i += 1
            continue
        assign(indent, key, _unquote(rest))
        i += 1
    return out


# --------------------------------------------------------------------------
# MEMORY.md index
# --------------------------------------------------------------------------

_INDEX_LINK = re.compile(r"^\s*[-*+]\s*\[([^\]]*)\]\(([^)]+)\)\s*(?:[—–-]\s*(.*))?$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")


@dataclass
class IndexEntry:
    line_no: int          # 1-based
    raw: str
    title: str | None
    target: str | None    # relative link target, if the line is a markdown link
    hook: str | None
    beyond_cap: bool = False
    exists: bool = True

    def to_dict(self) -> dict:
        return {
            "lineNo": self.line_no,
            "raw": self.raw,
            "title": self.title,
            "target": self.target,
            "hook": self.hook,
            "beyondCap": self.beyond_cap,
            "existsOnDisk": self.exists,
        }


def parse_index(text: str, memory_dir: str | None = None) -> list[IndexEntry]:
    """Parse MEMORY.md into entries, flagging lines past the documented cap."""
    entries: list[IndexEntry] = []
    for i, raw in enumerate(( text or "").splitlines(), start=1):
        if not raw.strip():
            continue
        m = _INDEX_LINK.match(raw)
        if m:
            title, target, hook = m.group(1), m.group(2).strip(), (m.group(3) or "").strip() or None
        else:
            b = _BULLET.match(raw)
            title, target, hook = (b.group(1)[:80] if b else raw.strip()[:80]), None, None
        exists = True
        if target and memory_dir and not target.startswith(("http://", "https://")):
            exists = os.path.isfile(os.path.join(memory_dir, target))
        entries.append(IndexEntry(i, raw.rstrip(), title, target, hook,
                                  beyond_cap=i > INDEX_MAX_LINES, exists=exists))
    return entries


# --------------------------------------------------------------------------
# artifacts
# --------------------------------------------------------------------------

@dataclass
class Artifact:
    id: str
    kind: str
    name: str
    path: str
    scope: str                    # "project" | "user"
    project_slug: str | None = None
    exists: bool = True
    size_bytes: int = 0
    mtime: float | None = None
    content: str | None = None
    body: str | None = None
    description: str | None = None
    frontmatter: dict = field(default_factory=dict)
    origin_session: str | None = None
    origin_modified: str | None = None
    index_entries: list[IndexEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "path": self.path,
            "scope": self.scope,
            "projectSlug": self.project_slug,
            "existsOnDisk": self.exists,
            "sizeBytes": self.size_bytes,
            "mtime": self.mtime,
            "description": self.description,
            "originSessionId": self.origin_session,
            "originModified": self.origin_modified,
            "notes": self.notes,
        }


def project_dir_for_slug(claude_home: str, slug: str) -> str:
    return os.path.join(claude_home, "projects", slug)


def _skill_artifact(skill_dir: str, scope: str, project_slug: str | None) -> Artifact | None:
    md = os.path.join(skill_dir, "SKILL.md")
    name = os.path.basename(skill_dir.rstrip(os.sep))
    text = read_text(md)
    if text is None:
        return None
    fm = parse_frontmatter(text)
    _, body = strip_frontmatter(text)
    try:
        st = os.stat(md)
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, None
    aid = f"skill:{name}" if scope == "user" else f"skill:{project_slug}:{name}"
    return Artifact(
        id=aid, kind=KIND_SKILL, name=str(fm.get("name") or name), path=md, scope=scope,
        project_slug=project_slug, exists=True, size_bytes=size, mtime=mtime,
        content=text, body=body, description=str(fm.get("description") or "") or None,
        frontmatter=fm,
    )


def _memory_artifacts(claude_home: str, slug: str) -> list[Artifact]:
    mem_dir = os.path.join(project_dir_for_slug(claude_home, slug), "memory")
    if not os.path.isdir(mem_dir):
        return []
    out: list[Artifact] = []
    index_path = os.path.join(mem_dir, "MEMORY.md")
    index_text = read_text(index_path)
    entries: list[IndexEntry] = []
    if index_text is not None:
        entries = parse_index(index_text, mem_dir)
        st = os.stat(index_path)
        notes: list[str] = []
        if len(index_text.splitlines()) > INDEX_MAX_LINES:
            notes.append(f"index has {len(index_text.splitlines())} lines (> {INDEX_MAX_LINES} cap)")
        if st.st_size > INDEX_MAX_BYTES:
            notes.append(f"index is {st.st_size} bytes (> {INDEX_MAX_BYTES} cap)")
        out.append(Artifact(
            id=f"memory_index:{slug}", kind=KIND_MEMORY_INDEX, name="MEMORY.md",
            path=index_path, scope="project", project_slug=slug, exists=True,
            size_bytes=st.st_size, mtime=st.st_mtime, content=index_text,
            body=index_text, description=None, index_entries=entries, notes=notes,
        ))
    else:
        out.append(Artifact(
            id=f"memory_index:{slug}", kind=KIND_MEMORY_INDEX, name="MEMORY.md",
            path=index_path, scope="project", project_slug=slug, exists=False,
            notes=["MEMORY.md does not exist"],
        ))

    referenced = {e.target for e in entries if e.target}
    try:
        files = sorted(f for f in os.listdir(mem_dir) if f.endswith(".md") and f != "MEMORY.md")
    except OSError:
        files = []
    for fname in files:
        fpath = os.path.join(mem_dir, fname)
        text = read_text(fpath)
        if text is None:
            continue
        fm = parse_frontmatter(text)
        _, body = strip_frontmatter(text)
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        st = os.stat(fpath)
        notes = [] if fname in referenced else ["orphan: not linked from MEMORY.md"]
        out.append(Artifact(
            id=f"memory:{slug}/{fname}", kind=KIND_MEMORY, name=fname, path=fpath,
            scope="project", project_slug=slug, exists=True, size_bytes=st.st_size,
            mtime=st.st_mtime, content=text, body=body,
            description=str(fm.get("description") or "") or None, frontmatter=fm,
            origin_session=str(meta.get("originSessionId") or "") or None,
            origin_modified=str(meta.get("modified") or "") or None,
            notes=notes,
        ))

    # stale index entries -> synthesize a missing_on_disk artifact so the receipt
    # says which entry points at nothing.
    for e in entries:
        if e.target and not e.exists and not e.target.startswith(("http://", "https://")):
            out.append(Artifact(
                id=f"memory:{slug}/{e.target}", kind=KIND_MEMORY, name=e.target,
                path=os.path.join(mem_dir, e.target), scope="project", project_slug=slug,
                exists=False, description=e.hook,
                notes=[f"stale: MEMORY.md line {e.line_no} links to a file that does not exist"],
            ))
    return out


def _instruction_artifacts(cwd: str | None, claude_home: str) -> list[Artifact]:
    out: list[Artifact] = []
    candidates: list[tuple[str, str, str]] = []
    user_md = os.path.realpath(os.path.join(claude_home, "CLAUDE.md"))
    candidates.append((user_md, "user", f"claude_md:{user_md}"))
    if cwd:
        proj_md = os.path.realpath(os.path.join(cwd, "CLAUDE.md"))
        if proj_md != user_md:
            candidates.append((proj_md, "project", f"claude_md:{proj_md}"))
    for path, scope, aid in candidates:
        text = read_text(path)
        if text is None:
            continue
        st = os.stat(path)
        out.append(Artifact(
            id=aid, kind=KIND_CLAUDE_MD, name=os.path.basename(path), path=path,
            scope=scope, exists=True, size_bytes=st.st_size, mtime=st.st_mtime,
            content=text, body=text,
            description=(text.strip().splitlines() or [""])[0][:200] or None,
        ))
    if cwd:
        rules_dir = os.path.join(cwd, ".claude", "rules")
        if os.path.isdir(rules_dir):
            try:
                names = sorted(n for n in os.listdir(rules_dir) if n.endswith(".md"))
            except OSError:
                names = []
            for n in names:
                p = os.path.join(rules_dir, n)
                text = read_text(p)
                if text is None:
                    continue
                st = os.stat(p)
                fm = parse_frontmatter(text)
                _, body = strip_frontmatter(text)
                out.append(Artifact(
                    id=f"rule:{p}", kind=KIND_RULE, name=n, path=p, scope="project",
                    exists=True, size_bytes=st.st_size, mtime=st.st_mtime,
                    content=text, body=body,
                    description=str(fm.get("description") or "") or None, frontmatter=fm,
                ))
    return out


def discover_artifacts(claude_home: str, slugs: list[str], cwds: dict[str, str | None]) -> list[Artifact]:
    """Enumerate every learned-state artifact reachable from ``claude_home``.

    ``cwds`` maps project slug -> the working directory observed in that
    project's transcripts (used to find project CLAUDE.md, rules and skills).
    """
    arts: list[Artifact] = []
    seen: set[str] = set()

    def add(a: Artifact | None) -> None:
        if a is None or a.id in seen:
            return
        seen.add(a.id)
        arts.append(a)

    user_skills = os.path.join(claude_home, "skills")
    if os.path.isdir(user_skills):
        for name in sorted(os.listdir(user_skills)):
            d = os.path.join(user_skills, name)
            if os.path.isdir(d):
                add(_skill_artifact(d, "user", None))

    for a in _instruction_artifacts(None, claude_home):
        add(a)

    for slug in slugs:
        for a in _memory_artifacts(claude_home, slug):
            add(a)
        cwd = cwds.get(slug)
        for a in _instruction_artifacts(cwd, claude_home):
            add(a)
        if cwd:
            proj_skills = os.path.realpath(os.path.join(cwd, ".claude", "skills"))
            if proj_skills == os.path.realpath(user_skills):
                # the project's cwd IS the Claude home's parent: same directory,
                # already enumerated as user-scope skills.
                continue
            if os.path.isdir(proj_skills):
                for name in sorted(os.listdir(proj_skills)):
                    d = os.path.join(proj_skills, name)
                    if os.path.isdir(d):
                        add(_skill_artifact(d, "project", slug))
    return arts
