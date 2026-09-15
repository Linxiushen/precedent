# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Top-level scan: wire the parsers together into one result object."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import SCHEMA_VERSION, __version__
from .artifacts import discover_artifacts
from .changefeed import (Mutation, OutOfBand, build_change_feed, corroborate,
                         detect_out_of_band)
from .funnel import FunnelRow, build_funnel
from .paths import PathClassifier
from .receipts import SessionReceipt, build_receipts
from .transcripts import SessionData, find_sessions, load_session

__all__ = ["ScanResult", "scan"]


@dataclass
class ScanResult:
    claude_home: str
    generated_at: str
    project_filter: str | None
    last_n: int | None
    include_subagents: bool
    project_slugs: list[str] = field(default_factory=list)
    cwds: dict[str, str | None] = field(default_factory=dict)
    sessions_found: int = 0
    sessions_scanned: int = 0
    session_receipts: list[SessionReceipt] = field(default_factory=list)
    mutations: list[Mutation] = field(default_factory=list)
    out_of_band: list[OutOfBand] = field(default_factory=list)
    history_events: list[dict] = field(default_factory=list)
    funnel: list[FunnelRow] = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "tool": {"name": "receipts", "version": __version__},
            "scan": {
                "claudeHome": self.claude_home,
                "generatedAt": self.generated_at,
                "projectFilter": self.project_filter,
                "lastN": self.last_n,
                "includeSubagents": self.include_subagents,
                "projectSlugs": self.project_slugs,
                "cwds": self.cwds,
                "sessionsFound": self.sessions_found,
                "sessionsScanned": self.sessions_scanned,
                "readOnly": True,
            },
            "sessions": [s.to_dict() for s in self.session_receipts],
            "changeFeed": [m.to_dict() for m in self.mutations],
            "outOfBand": [o.to_dict() for o in self.out_of_band],
            "fileHistoryEvents": self.history_events,
            "artifacts": [f.to_dict() for f in self.funnel],
            "limitations": self.limitations,
        }


def scan(claude_home: str, project_filter: str | None = None, last_n: int | None = None,
         include_subagents: bool = True, now: datetime | None = None) -> ScanResult:
    home = os.path.realpath(os.path.expanduser(claude_home))
    now = now or datetime.now(timezone.utc)
    result = ScanResult(
        claude_home=home, generated_at=now.isoformat(), project_filter=project_filter,
        last_n=last_n, include_subagents=include_subagents,
    )

    found = find_sessions(home, project_filter)
    result.sessions_found = len(found)

    sessions: list[SessionData] = [
        load_session(slug, path, include_subagents=include_subagents) for slug, path in found
    ]
    sessions.sort(key=lambda s: (s.started_at or "", s.session_id))
    if last_n is not None and last_n > 0:
        sessions = sessions[-last_n:]
    result.sessions_scanned = len(sessions)

    slugs: list[str] = []
    cwds: dict[str, str | None] = {}
    for s in sessions:
        if s.project_slug not in cwds:
            slugs.append(s.project_slug)
        if s.cwd and not cwds.get(s.project_slug):
            cwds[s.project_slug] = s.cwd
        cwds.setdefault(s.project_slug, None)
    result.project_slugs = slugs
    result.cwds = cwds

    artifacts = discover_artifacts(home, slugs, cwds)
    result.artifacts = artifacts

    classifier = PathClassifier(home, cwds)
    mutations = build_change_feed(sessions, classifier)
    history_events = corroborate(mutations, sessions, classifier)
    result.mutations = mutations
    result.history_events = history_events

    result.session_receipts = build_receipts(sessions, artifacts, mutations)
    result.out_of_band = detect_out_of_band(artifacts, mutations,
                                            history_events=history_events)
    result.funnel = build_funnel(artifacts, result.session_receipts, mutations,
                                 result.out_of_band, now=now)
    result.limitations = _limitations(result)
    return result


def _limitations(result: ScanResult) -> list[str]:
    lim: list[str] = []
    no_ps = [s for s in result.session_receipts if not s.has_prompt_snapshot]
    if no_ps:
        lim.append(
            f"{len(no_ps)}/{len(result.session_receipts)} scanned session(s) carry no "
            "`prompt_snapshot` attachment; for those, non-skill load status is "
            "`unknown`, not `not_loaded` — the transcript simply does not record "
            "what the system prompt contained: "
            + ", ".join(s.session_id[:8] for s in no_ps[:8]))
    no_sl = [s for s in result.session_receipts if not s.has_skill_listing]
    if no_sl:
        lim.append(
            f"{len(no_sl)} scanned session(s) carry no `skill_listing` attachment; "
            "skill load status there is `unknown`.")
    unknown_actor = [o for o in result.out_of_band
                     if o.reason in ("no_attributed_write",
                                     "harness_recorded_write_not_attributed")]
    if unknown_actor:
        lim.append(
            f"{len(unknown_actor)} artifact(s) have no attributed write in the scanned "
            "sessions. That is expected for hand-written or vendor-shipped files and "
            "for sessions whose transcripts were deleted or are outside --project/--last.")
    mem_rows = [r for r in result.funnel if r.kind == "memory" and r.exists]
    if mem_rows and not any(r.n_sessions_loaded for r in mem_rows):
        lim.append(
            f"None of the {len(mem_rows)} memory entry *bodies* was ever observed in a "
            "recorded context: Claude Code loads MEMORY.md (the index) every session and "
            "pulls an entry's body only when it recalls it. `not_loaded` on an entry "
            "therefore means 'this session never recalled it', not 'memory is broken'.")
    lim.append(
        "Load detection is textual: it proves the artifact's text is present in a "
        "recorded context blob. It cannot prove the model attended to it. Absence of "
        "a `<cc-memory>` tag or Skill invocation is weak evidence of non-use, not proof.")
    low = [m for m in result.mutations if m.confidence == "low"]
    lim.append(
        "Bash mutations are detected by pattern (redirect / tee / sed -i / heredoc+writer "
        "/ rm|mv|cp) and their before/after content is not recoverable from the "
        "transcript; the out-of-band check falls back to mtime for those. A command that "
        "merely *quotes* such a write inside `python -c \"…\"` cannot be distinguished "
        "from one that runs it, so those rows are marked `confidence: low`"
        + (f" ({len(low)} here)." if low else " (none here)."))
    if result.last_n or result.project_filter:
        lim.append(
            "`--last` / `--project` narrow the session set, so `nSessionsEligible` and "
            "the citation counts are relative to the scanned sessions, not to the "
            "machine's whole history.")
    lim.append("This tool never writes under the Claude home directory it reads.")
    return lim
