# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Load receipts: for each (session, artifact), did it actually reach context?

Statuses
--------
``loaded_complete``   every comparable unit of the artifact was found in what
                      the session was shown.
``loaded_truncated``  some of it was found and some was not — the failure mode
                      behind claude-code#82056 / #92998.
``not_loaded``        the session had a usable record of its context and the
                      artifact is not in it.
``missing_on_disk``   the artifact is referenced (e.g. by a MEMORY.md line) but
                      the file does not exist.
``unknown``           the session's transcript carries no record of the context
                      for this class of artifact, so nothing can be claimed.

Evidence precedence (best first):

1. ``instructions`` attachment naming the exact path — authoritative.
2. ``prompt_snapshot.systemPrompt`` — the rendered system prompt.
3. ``skill_listing`` — for skills, the listing entry IS the load.
4. any other non-boilerplate attachment rendered into a ``<system-reminder>``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .artifacts import (INDEX_MAX_BYTES, INDEX_MAX_LINES, Artifact, KIND_CLAUDE_MD,
                        KIND_MEMORY, KIND_MEMORY_INDEX, KIND_RULE, KIND_SKILL)
from .textmatch import Coverage, Haystack, classify_lines, classify_text
from .transcripts import Evidence, SessionData

__all__ = ["ArtifactReceipt", "SessionReceipt", "build_receipts"]

STATUS_ORDER = {
    "loaded_complete": 0, "loaded_truncated": 1, "not_loaded": 2,
    "missing_on_disk": 3, "unknown": 4,
}


@dataclass
class ArtifactReceipt:
    session_id: str
    ts: str | None
    artifact_id: str
    kind: str
    name: str
    path: str
    status: str
    coverage: Coverage | None = None
    evidence_kind: str | None = None
    evidence_line_no: int | None = None
    evidence_ts: str | None = None
    evidence_source: str | None = None
    notes: list[str] = field(default_factory=list)
    missing_index_lines: list[tuple[int, str]] = field(default_factory=list)

    def locator(self) -> str | None:
        if self.evidence_source is None or self.evidence_line_no is None:
            return None
        return f"{os.path.basename(self.evidence_source)}:{self.evidence_line_no}"

    def to_dict(self) -> dict:
        return {
            "sessionId": self.session_id, "ts": self.ts, "artifactId": self.artifact_id,
            "kind": self.kind, "name": self.name, "path": self.path,
            "status": self.status,
            "coverage": self.coverage.to_dict() if self.coverage else None,
            "evidence": {
                "kind": self.evidence_kind, "lineNo": self.evidence_line_no,
                "ts": self.evidence_ts, "source": self.evidence_source,
                "locator": self.locator(),
            },
            "notes": self.notes,
            "missingIndexLines": [{"lineNo": n, "text": t} for n, t in self.missing_index_lines],
        }


@dataclass
class SessionReceipt:
    session_id: str
    project_slug: str
    transcript: str
    cwd: str | None
    git_branch: str | None
    started_at: str | None
    ended_at: str | None
    versions: list[str]
    models: list[str]
    n_assistant_turns: int
    n_human_prompts: int
    usage: dict
    has_prompt_snapshot: bool
    has_instructions: bool
    has_skill_listing: bool
    n_subagent_transcripts: int
    artifacts: list[ArtifactReceipt] = field(default_factory=list)
    cited_artifacts: list[dict] = field(default_factory=list)
    written_artifacts: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for a in self.artifacts:
            c[a.status] = c.get(a.status, 0) + 1
        return c

    def to_dict(self) -> dict:
        return {
            "sessionId": self.session_id, "projectSlug": self.project_slug,
            "transcript": self.transcript, "cwd": self.cwd, "gitBranch": self.git_branch,
            "startedAt": self.started_at, "endedAt": self.ended_at,
            "versions": self.versions, "models": self.models,
            "assistantTurns": self.n_assistant_turns, "humanPrompts": self.n_human_prompts,
            "usage": self.usage,
            "contextRecords": {
                "promptSnapshot": self.has_prompt_snapshot,
                "instructions": self.has_instructions,
                "skillListing": self.has_skill_listing,
                "subagentTranscripts": self.n_subagent_transcripts,
            },
            "statusCounts": self.counts(),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "citedArtifacts": self.cited_artifacts,
            "writtenArtifacts": self.written_artifacts,
        }


def _in_scope(art: Artifact, sess: SessionData) -> bool:
    if art.scope == "user":
        return True
    if art.project_slug is not None:
        return art.project_slug == sess.project_slug
    # project-scoped artifact discovered from a cwd (CLAUDE.md, rules)
    if sess.cwd and art.path.startswith(os.path.realpath(sess.cwd) + os.sep):
        return True
    return sess.cwd is not None and art.path.startswith(sess.cwd + os.sep)


class _SessionContext:
    """Lazily-built haystacks over a session's evidence."""

    def __init__(self, sess: SessionData) -> None:
        self.sess = sess
        self._hay: dict[int, Haystack] = {}

    def hay(self, i: int) -> Haystack:
        h = self._hay.get(i)
        if h is None:
            h = Haystack(self.sess.evidence[i].text)
            self._hay[i] = h
        return h

    def ordered(self) -> list[int]:
        rank = {"instructions": 0, "prompt_snapshot": 1, "skill_listing": 2}
        idx = list(range(len(self.sess.evidence)))
        idx.sort(key=lambda i: rank.get(self.sess.evidence[i].kind, 3))
        return idx

    def hay_for_evidence(self, ev: Evidence) -> Haystack:
        for i, e in enumerate(self.sess.evidence):
            if e is ev:
                return self.hay(i)
        return Haystack(ev.text)

    def for_path(self, path: str) -> list[int]:
        target = os.path.normpath(path)
        return [i for i, e in enumerate(self.sess.evidence)
                if e.path and os.path.normpath(e.path) == target]


def _best_text_coverage(ctx: _SessionContext, body: str,
                        candidates: list[int]) -> tuple[Coverage | None, Evidence | None]:
    best: tuple[Coverage, Evidence] | None = None
    for i in candidates:
        cov = classify_text(body, ctx.hay(i))
        ev = ctx.sess.evidence[i]
        if cov.status == "not_loaded":
            continue
        if best is None or cov.chars_found > best[0].chars_found:
            best = (cov, ev)
        if cov.status == "loaded_complete":
            break
    if best is None:
        return None, None
    return best


def _receipt_for_memory_index(ctx: _SessionContext, art: Artifact,
                              sess: SessionData) -> ArtifactReceipt:
    notes = list(art.notes)
    entries = art.index_entries
    beyond = [e for e in entries if e.beyond_cap]
    if beyond:
        notes.append(
            f"{len(beyond)} index line(s) past the {INDEX_MAX_LINES}-line cap "
            f"(first at line {beyond[0].line_no}) — silently dropped")
    if art.size_bytes > INDEX_MAX_BYTES:
        notes.append(f"index is {art.size_bytes} B, past the {INDEX_MAX_BYTES} B cap")

    if not art.exists:
        return ArtifactReceipt(sess.session_id, sess.started_at, art.id, art.kind,
                               art.name, art.path, "missing_on_disk", notes=notes)

    direct = ctx.for_path(art.path)
    candidates = direct or ctx.ordered()
    lines = [(e.line_no, e.raw) for e in entries]
    best: tuple[Coverage, Evidence, list] | None = None
    for i in candidates:
        cov, missing = classify_lines(lines, ctx.hay(i))
        ev = ctx.sess.evidence[i]
        if cov.units_found == 0:
            continue
        if best is None or cov.units_found > best[0].units_found:
            best = (cov, ev, missing)
        if cov.status == "loaded_complete":
            break
    if best is not None:
        cov, ev, missing = best
        return ArtifactReceipt(
            sess.session_id, sess.started_at, art.id, art.kind, art.name, art.path,
            cov.status, cov, ev.kind, ev.line_no, ev.ts, ev.source, notes,
            [(n, t) for n, t in missing])

    if direct or sess.has_prompt_snapshot or sess.has_instructions:
        ev = ctx.sess.evidence[candidates[0]] if candidates else None
        return ArtifactReceipt(
            sess.session_id, sess.started_at, art.id, art.kind, art.name, art.path,
            "not_loaded", None, ev.kind if ev else None, ev.line_no if ev else None,
            ev.ts if ev else None, ev.source if ev else None, notes,
            [(e.line_no, e.raw) for e in entries])
    return ArtifactReceipt(sess.session_id, sess.started_at, art.id, art.kind,
                           art.name, art.path, "unknown", notes=notes + [
                               "session has no prompt_snapshot and no instructions "
                               "attachment — cannot tell what loaded"])


def _receipt_for_skill(ctx: _SessionContext, art: Artifact, sess: SessionData) -> ArtifactReceipt:
    notes = list(art.notes)
    if not sess.has_skill_listing:
        return ArtifactReceipt(sess.session_id, sess.started_at, art.id, art.kind,
                               art.name, art.path, "unknown", notes=notes + [
                                   "session has no skill_listing attachment"])

    # A session can be shown more than one listing. Find the listing that
    # actually carried this skill, newest first; fall back to the newest listing
    # as the evidence for a negative result.
    listings = sess.skill_listings or []
    carrier = next((l for l in reversed(listings) if art.name in l["names"]), None)
    latest = listings[-1] if listings else None
    if carrier is None:
        ev = latest["evidence"] if latest else sess.skill_listing_evidence
        shown = len(latest["names"]) if latest else len(sess.skill_listing_names)
        return ArtifactReceipt(
            sess.session_id, sess.started_at, art.id, art.kind, art.name, art.path,
            "not_loaded", None, "skill_listing", ev.line_no if ev else None,
            ev.ts if ev else None, ev.source if ev else None,
            notes + [f"'{art.name}' is not among the {shown} skills this session was "
                     "shown (installed after the session, or removed since)"])

    ev = carrier["evidence"]
    if len(listings) > 1:
        kind = "initial" if carrier["isInitial"] else "delta (installed mid-session)"
        notes.append(f"session was shown {len(listings)} skill listings; this receipt "
                     f"cites the {kind} listing at line {carrier['lineNo']} "
                     f"({carrier['skillCount']} skill(s))")
    desc = art.description or ""
    cov = None
    status = "loaded_complete"
    if desc:
        cov = classify_text(desc, ctx.hay_for_evidence(ev), min_section_chars=30)
        if cov.status == "not_loaded":
            status = "loaded_truncated"
            notes.append("listed, but the SKILL.md description on disk today is NOT what "
                         "this session was shown — the skill was edited after this "
                         "session, or the listing trimmed it")
        elif cov.status == "loaded_truncated":
            status = "loaded_truncated"
            notes.append("only part of the SKILL.md description reached the listing")
    notes.append("a listed skill contributes its name+description; the SKILL.md body "
                 "loads only on invocation")
    if art.name in sess.invoked_skills:
        notes.append("body was loaded this session (invoked_skills attachment)")
    return ArtifactReceipt(
        sess.session_id, sess.started_at, art.id, art.kind, art.name, art.path,
        status, cov, "skill_listing", ev.line_no, ev.ts, ev.source, notes)


def _receipt_for_text(ctx: _SessionContext, art: Artifact, sess: SessionData) -> ArtifactReceipt:
    notes = list(art.notes)
    if not art.exists:
        return ArtifactReceipt(sess.session_id, sess.started_at, art.id, art.kind,
                               art.name, art.path, "missing_on_disk", notes=notes)
    body = art.body or art.content or ""
    direct = ctx.for_path(art.path)
    candidates = direct or ctx.ordered()
    cov, ev = _best_text_coverage(ctx, body, candidates)
    if cov is not None and ev is not None:
        return ArtifactReceipt(sess.session_id, sess.started_at, art.id, art.kind,
                               art.name, art.path, cov.status, cov, ev.kind,
                               ev.line_no, ev.ts, ev.source, notes)
    if direct:
        e = ctx.sess.evidence[direct[0]]
        return ArtifactReceipt(sess.session_id, sess.started_at, art.id, art.kind,
                               art.name, art.path, "not_loaded", None, e.kind,
                               e.line_no, e.ts, e.source, notes)
    if sess.has_prompt_snapshot:
        e = next((ctx.sess.evidence[i] for i in candidates
                  if ctx.sess.evidence[i].kind == "prompt_snapshot"), None)
        return ArtifactReceipt(sess.session_id, sess.started_at, art.id, art.kind,
                               art.name, art.path, "not_loaded", None,
                               "prompt_snapshot", e.line_no if e else None,
                               e.ts if e else None, e.source if e else None, notes)
    return ArtifactReceipt(sess.session_id, sess.started_at, art.id, art.kind,
                           art.name, art.path, "unknown", notes=notes + [
                               "session has no prompt_snapshot — cannot tell what loaded"])


def build_receipts(sessions: list[SessionData], artifacts: list[Artifact],
                   mutations) -> list[SessionReceipt]:
    """One :class:`SessionReceipt` per session, covering every in-scope artifact."""
    writes_by_session: dict[str, set[str]] = {}
    first_write: dict[str, str] = {}
    for m in mutations:
        writes_by_session.setdefault(m.session_id, set()).add(m.artifact_id)
        if m.ts and (m.artifact_id not in first_write or m.ts < first_write[m.artifact_id]):
            first_write[m.artifact_id] = m.ts

    by_name_memory: dict[tuple[str, str], Artifact] = {}
    for a in artifacts:
        if a.kind == KIND_MEMORY and a.project_slug:
            by_name_memory[(a.project_slug, a.name)] = a

    out: list[SessionReceipt] = []
    for sess in sessions:
        ctx = _SessionContext(sess)
        sr = SessionReceipt(
            session_id=sess.session_id, project_slug=sess.project_slug,
            transcript=sess.path, cwd=sess.cwd, git_branch=sess.git_branch,
            started_at=sess.started_at, ended_at=sess.ended_at,
            versions=sorted(sess.versions), models=sorted(sess.models),
            n_assistant_turns=sess.n_assistant_turns, n_human_prompts=sess.n_human_prompts,
            usage=dict(sess.usage), has_prompt_snapshot=sess.has_prompt_snapshot,
            has_instructions=sess.has_instructions, has_skill_listing=sess.has_skill_listing,
            n_subagent_transcripts=len(sess.subagent_files),
        )
        for art in artifacts:
            if not _in_scope(art, sess):
                continue
            if art.kind == KIND_MEMORY_INDEX:
                r = _receipt_for_memory_index(ctx, art, sess)
            elif art.kind == KIND_SKILL:
                r = _receipt_for_skill(ctx, art, sess)
            elif art.kind in (KIND_MEMORY, KIND_CLAUDE_MD, KIND_RULE):
                r = _receipt_for_text(ctx, art, sess)
            else:  # pragma: no cover - defensive
                continue
            if r.status in ("not_loaded", "loaded_truncated"):
                created = first_write.get(art.id) or art.origin_modified
                when = r.evidence_ts or sr.started_at
                if created and when and created > when:
                    r.notes.append(
                        f"artifact did not exist yet when this context was rendered "
                        f"(first recorded write {created}, context {when})")
            sr.artifacts.append(r)
        sr.artifacts.sort(key=lambda a: (STATUS_ORDER.get(a.status, 9), a.kind, a.name))

        for cite in sess.memory_citations:
            for fn in cite["files"]:
                art = by_name_memory.get((sess.project_slug, fn))
                sr.cited_artifacts.append({
                    "artifactId": art.id if art else f"memory:{sess.project_slug}/{fn}",
                    "name": fn, "kind": "memory", "via": "cc-memory tag",
                    "ts": cite["ts"], "lineNo": cite["lineNo"], "source": cite["source"],
                    "resolved": art is not None,
                })
        for inv in sess.skill_invocations:
            sr.cited_artifacts.append({
                "artifactId": f"skill:{inv['skill']}", "name": inv["skill"],
                "kind": "skill", "via": inv["via"], "ts": inv["ts"],
                "lineNo": inv["lineNo"], "source": inv["source"], "resolved": True,
            })
        sr.written_artifacts = sorted(writes_by_session.get(sess.session_id, ()))
        out.append(sr)
    return out
