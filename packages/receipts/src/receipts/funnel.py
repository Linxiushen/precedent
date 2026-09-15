# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Activation funnel per artifact: created -> eligible -> loaded -> cited.

The point of the funnel is the last column.  hermes#96704 reported a production
store of 182 skills with "many use_count=0"; Demystifying Skills measured skill
retrieval precision collapsing from 29.6% to 3.3% at 100 skills.  An artifact
that is eligible in every session, loaded in every session, and cited in none is
paying context rent for nothing.

Citation evidence used here is deliberately narrow and mechanical:

* memory  -> ``<cc-memory filenames="...">`` tags in assistant text (the model is
  instructed to emit these whenever it uses a memory).
* skill   -> a ``Skill`` tool_use, an ``attributionSkill`` on an assistant turn,
  or an ``invoked_skills`` attachment.

There is no citation signal for CLAUDE.md / rules, so those rows report
``cited = n/a`` rather than a misleading zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .artifacts import Artifact, KIND_MEMORY, KIND_SKILL
from .textmatch import jaccard, tokenize
from .transcripts import parse_ts

__all__ = ["DUPLICATE_THRESHOLD", "FunnelRow", "build_funnel", "find_near_duplicates"]

DUPLICATE_THRESHOLD = 0.6
_CITABLE = {KIND_MEMORY, KIND_SKILL}


@dataclass
class FunnelRow:
    artifact_id: str
    kind: str
    name: str
    path: str
    scope: str
    project_slug: str | None
    exists: bool
    size_bytes: int
    created_by_session: str | None = None
    created_at: str | None = None
    created_evidence: str | None = None
    last_modified: str | None = None
    last_modified_by_session: str | None = None
    age_days: float | None = None
    n_sessions_eligible: int = 0
    n_sessions_loaded: int = 0
    n_sessions_truncated: int = 0
    n_sessions_unknown: int = 0
    n_sessions_cited: int = 0
    last_cited: str | None = None
    last_cited_locator: str | None = None
    citable: bool = True
    never_cited: bool = False
    unmeasured: bool = False
    n_writes: int = 0
    warnings: list[str] = field(default_factory=list)
    near_duplicates: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "artifactId": self.artifact_id, "kind": self.kind, "name": self.name,
            "path": self.path, "scope": self.scope, "projectSlug": self.project_slug,
            "existsOnDisk": self.exists, "sizeBytes": self.size_bytes,
            "createdBySessionId": self.created_by_session, "createdAt": self.created_at,
            "createdEvidence": self.created_evidence,
            "lastModified": self.last_modified,
            "lastModifiedBySessionId": self.last_modified_by_session,
            "ageDays": self.age_days,
            "nSessionsEligible": self.n_sessions_eligible,
            "nSessionsLoaded": self.n_sessions_loaded,
            "nSessionsTruncated": self.n_sessions_truncated,
            "nSessionsUnknown": self.n_sessions_unknown,
            "nSessionsCited": None if not self.citable else self.n_sessions_cited,
            "lastCited": self.last_cited, "lastCitedLocator": self.last_cited_locator,
            "citable": self.citable, "neverCited": self.never_cited,
            "unmeasured": self.unmeasured,
            "nWrites": self.n_writes, "warnings": self.warnings,
            "nearDuplicates": self.near_duplicates,
        }


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def build_funnel(artifacts: list[Artifact], session_receipts, mutations,
                 out_of_band, now: datetime | None = None) -> list[FunnelRow]:
    now = now or datetime.now(timezone.utc)
    rows: dict[str, FunnelRow] = {}
    for art in artifacts:
        age = None
        if art.mtime is not None:
            age = round((now - datetime.fromtimestamp(art.mtime, tz=timezone.utc)).total_seconds() / 86400.0, 2)
        rows[art.id] = FunnelRow(
            artifact_id=art.id, kind=art.kind, name=art.name, path=art.path,
            scope=art.scope, project_slug=art.project_slug, exists=art.exists,
            size_bytes=art.size_bytes, last_modified=_iso(art.mtime), age_days=age,
            citable=art.kind in _CITABLE, warnings=list(art.notes),
        )
        if art.origin_session:
            rows[art.id].created_by_session = art.origin_session
            rows[art.id].created_evidence = "frontmatter metadata.originSessionId"
        if art.origin_modified:
            rows[art.id].created_at = art.origin_modified

    # attribution from the change feed wins over frontmatter
    for m in sorted(mutations, key=lambda m: (m.ts or "", m.line_no)):
        row = rows.get(m.artifact_id)
        if row is None:
            continue
        row.n_writes += 1
        if m.change_type == "create" and (row.created_evidence != "change feed"):
            row.created_by_session = m.session_id
            row.created_at = m.ts
            row.created_evidence = "change feed"
        row.last_modified_by_session = m.session_id

    # eligibility / load counts
    for sr in session_receipts:
        started = parse_ts(sr.started_at)
        for ar in sr.artifacts:
            row = rows.get(ar.artifact_id)
            if row is None:
                continue
            created = parse_ts(row.created_at)
            if created is not None and started is not None and started < created:
                continue  # session predates the artifact: not eligible
            row.n_sessions_eligible += 1
            if ar.status == "loaded_complete":
                row.n_sessions_loaded += 1
            elif ar.status == "loaded_truncated":
                row.n_sessions_loaded += 1
                row.n_sessions_truncated += 1
            elif ar.status == "unknown":
                row.n_sessions_unknown += 1

    # citations
    cited_sessions: dict[str, set[str]] = {}
    for sr in session_receipts:
        for c in sr.cited_artifacts:
            aid = c["artifactId"]
            if aid not in rows and c["kind"] == "skill":
                continue
            cited_sessions.setdefault(aid, set()).add(sr.session_id)
            row = rows.get(aid)
            if row is None:
                continue
            ts = c.get("ts")
            if ts and (row.last_cited is None or ts > row.last_cited):
                row.last_cited = ts
                row.last_cited_locator = f"{c['source'].rsplit('/', 1)[-1]}:{c['lineNo']}"
    for aid, sessions in cited_sessions.items():
        row = rows.get(aid)
        if row is not None:
            row.n_sessions_cited = len(sessions)

    oob_by_id: dict[str, list] = {}
    for f in out_of_band:
        oob_by_id.setdefault(f.artifact_id, []).append(f)

    for row in rows.values():
        if row.exists and row.n_sessions_eligible == 0:
            # created after every scanned session started: nobody could have used
            # it yet, so "never cited" would be an unfair verdict, not a finding.
            row.unmeasured = True
            row.warnings.append(
                "no eligible session yet — it was created after every scanned "
                "session started, so activation is unmeasured")
        elif row.citable and row.n_sessions_cited == 0:
            row.never_cited = True
            if row.n_sessions_loaded:
                row.warnings.append(
                    f"never cited in {row.n_sessions_eligible} eligible session(s) "
                    f"despite loading in {row.n_sessions_loaded}")
            elif row.exists:
                row.warnings.append("never cited and never observed loading")
        if not row.exists:
            row.warnings.append("referenced but missing on disk")
        for f in oob_by_id.get(row.artifact_id, ()):
            row.warnings.append(f"out-of-band: {f.reason} — {f.detail}")

    ordered = sorted(rows.values(), key=lambda r: (r.kind, r.scope, r.name))
    dupes = find_near_duplicates(artifacts)
    for a_id, b_id, score, a_desc, b_desc in dupes:
        for src, dst, desc in ((a_id, b_id, b_desc), (b_id, a_id, a_desc)):
            row = rows.get(src)
            if row is None:
                continue
            row.near_duplicates.append({
                "artifactId": dst, "jaccard": round(score, 3),
                "otherDescription": (desc or "")[:160],
            })
            row.warnings.append(f"near-duplicate of {dst} (description Jaccard {score:.2f})")
    return ordered


def find_near_duplicates(artifacts: list[Artifact],
                         threshold: float = DUPLICATE_THRESHOLD):
    """Pairs of same-kind artifacts whose normalized descriptions overlap.

    Artifacts that do not exist on disk are skipped: a stale index entry has no
    real description to compare, and "de-duplicate a file that isn't there" is
    not advice anyone can act on.
    """
    items = [(a, tokenize(a.description or a.name)) for a in artifacts
             if a.kind in (KIND_MEMORY, KIND_SKILL) and a.exists
             and (a.description or "").strip()]
    out = []
    for i in range(len(items)):
        a, ta = items[i]
        for j in range(i + 1, len(items)):
            b, tb = items[j]
            if a.kind != b.kind:
                continue
            score = jaccard(ta, tb)
            if score >= threshold:
                out.append((a.id, b.id, score, a.description, b.description))
    return out
