# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Classification of filesystem paths as learned-state artifacts."""

from __future__ import annotations

import os
import re

__all__ = ["PathClass", "PathClassifier"]


class PathClass:
    __slots__ = ("kind", "artifact_id", "name", "project_slug", "scope", "path")

    def __init__(self, kind: str, artifact_id: str, name: str,
                 project_slug: str | None, scope: str, path: str) -> None:
        self.kind = kind
        self.artifact_id = artifact_id
        self.name = name
        self.project_slug = project_slug
        self.scope = scope
        self.path = path

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PathClass({self.kind!r}, {self.artifact_id!r})"


_SKILL_DIR = re.compile(r"(?:^|/)\.claude/skills/([A-Za-z0-9._@\-]{1,64})(?:/|$)")


_MAX_PATH_CHARS = 512


def _looks_like_path(token: str) -> bool:
    """Reject quoted prose that merely mentions a learned-state directory."""
    if not token or len(token) > _MAX_PATH_CHARS:
        return False
    if any(ch in token for ch in " \t\n\r\x00"):
        return False
    return True


class PathClassifier:
    """Maps an absolute path to the artifact id used everywhere else.

    Constructed from the resolved Claude home plus the working directories seen
    in the transcripts, so that project-local ``CLAUDE.md`` / ``.claude/rules``
    / ``.claude/skills`` are recognised too.
    """

    def __init__(self, claude_home: str, cwds: dict[str, str | None] | None = None) -> None:
        self.claude_home = os.path.realpath(os.path.expanduser(claude_home))
        self.projects_root = os.path.join(self.claude_home, "projects")
        self.user_skills_root = os.path.join(self.claude_home, "skills")
        self.cwds: dict[str, str] = {}
        for slug, cwd in (cwds or {}).items():
            if cwd:
                self.cwds[os.path.realpath(cwd)] = slug

    def normalize(self, path: str, cwd: str | None = None) -> str | None:
        if not path:
            return None
        p = os.path.expanduser(path)
        if not os.path.isabs(p):
            if not cwd:
                return None
            p = os.path.join(cwd, p)
        return os.path.normpath(p)

    def classify(self, path: str, cwd: str | None = None) -> PathClass | None:
        if not _looks_like_path(path):
            return None
        p = self.normalize(path, cwd)
        if not p:
            return None
        base = os.path.basename(p)

        # ~/.claude/projects/<slug>/memory/...
        if p.startswith(self.projects_root + os.sep):
            rel = p[len(self.projects_root) + 1:].split(os.sep)
            if len(rel) >= 3 and rel[1] == "memory":
                slug = rel[0]
                fname = rel[-1]
                if fname == "MEMORY.md" and len(rel) == 3:
                    return PathClass("memory_index", f"memory_index:{slug}", "MEMORY.md",
                                     slug, "project", p)
                if fname.endswith(".md"):
                    return PathClass("memory", f"memory:{slug}/{fname}", fname,
                                     slug, "project", p)
            return None

        # ~/.claude/skills/<name>/**
        if p.startswith(self.user_skills_root + os.sep):
            name = p[len(self.user_skills_root) + 1:].split(os.sep)[0]
            return PathClass("skill", f"skill:{name}", name, None, "user", p)

        # ~/.claude/CLAUDE.md
        if p == os.path.join(self.claude_home, "CLAUDE.md"):
            return PathClass("claude_md", f"claude_md:{p}", "CLAUDE.md", None, "user", p)

        # Everything below is project-local. A path outside every working
        # directory this scan knows about is out of scope: it is some other
        # repo's file, or — more often — a path quoted inside a command string.
        slug = self._slug_for(p)
        if slug is None:
            return None

        # project-local .claude/skills/<name>/**
        m = _SKILL_DIR.search(p.replace(os.sep, "/"))
        if m:
            name = m.group(1)
            return PathClass("skill", f"skill:{slug}:{name}", name, slug, "project", p)

        if base == "CLAUDE.md":
            return PathClass("claude_md", f"claude_md:{p}", "CLAUDE.md", slug, "project", p)

        norm = p.replace(os.sep, "/")
        if "/.claude/rules/" in norm and base.endswith(".md"):
            return PathClass("rule", f"rule:{p}", base, slug, "project", p)
        return None

    def _slug_for(self, path: str) -> str | None:
        best: tuple[int, str] | None = None
        for cwd, slug in self.cwds.items():
            if path == cwd or path.startswith(cwd + os.sep):
                if best is None or len(cwd) > best[0]:
                    best = (len(cwd), slug)
        return best[1] if best else None
