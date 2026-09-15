# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Markdown rendering of a :class:`~receipts.scan.ScanResult`.

Every claim carries the session id and the timestamp, plus a ``file:line``
locator into the transcript, so any line of the report can be checked with::

    sed -n '<line>p' ~/.claude/projects/<slug>/<session-id>.jsonl
"""

from __future__ import annotations

import os

__all__ = ["render_markdown"]

_STATUS_MARK = {
    "loaded_complete": "OK",
    "loaded_truncated": "TRUNCATED",
    "not_loaded": "NOT LOADED",
    "missing_on_disk": "MISSING",
    "unknown": "unknown",
}


def _short(sid: str | None) -> str:
    return (sid or "?")[:8]


def _cell(text: str | None, limit: int = 70) -> str:
    if not text:
        return "—"
    t = " ".join(str(text).split())
    t = t.replace("|", "\\|")
    if len(t) > limit:
        t = t[:limit - 1] + "…"
    return t


def _label(artifact_id: str, limit: int = 52) -> str:
    """Shorten an artifact id for a table cell without losing which one it is.

    ``memory:-Users-you-code-Someproject------/microcat-project.md``
    becomes ``memory:…Microduck------/microcat-project.md``.
    """
    if len(artifact_id) <= limit:
        return artifact_id
    kind, _, rest = artifact_id.partition(":")
    keep = limit - len(kind) - 2
    if keep < 12:
        return artifact_id[:limit - 1] + "…"
    return f"{kind}:…{rest[-keep:]}"


def _ts(ts: str | None) -> str:
    if not ts:
        return "—"
    return ts.replace("T", " ").replace("Z", "").split(".")[0]


def render_markdown(result, last_n: int | None = None) -> str:
    L: list[str] = []
    n_sess = len(result.session_receipts)
    shown = result.session_receipts[-last_n:] if last_n else result.session_receipts
    L.append("# Learned-state receipts")
    L.append("")
    L.append(f"- Claude home: `{result.claude_home}` (read-only scan; nothing was written there)")
    L.append(f"- Generated: {result.generated_at}")
    L.append(f"- Sessions: {result.sessions_scanned} scanned of {result.sessions_found} found"
             + (f", filter `--project {result.project_filter}`" if result.project_filter else ""))
    L.append(f"- Projects: {len(result.project_slugs)} — "
             + ", ".join(f"`{s}`" for s in result.project_slugs[:6])
             + (" …" if len(result.project_slugs) > 6 else ""))
    L.append(f"- Artifacts tracked: {len(result.funnel)}; "
             f"mutations: {len(result.mutations)}; out-of-band findings: {len(result.out_of_band)}")
    L.append("")
    L.extend(_summary_block(result))
    L.append("")
    L.extend(_receipts_section(shown, n_sess))
    L.append("")
    L.extend(_changefeed_section(result))
    L.append("")
    L.extend(_artifacts_section(result))
    L.append("")
    L.append("## Limitations")
    L.append("")
    for lim in result.limitations:
        L.append(f"- {lim}")
    L.append("")
    return "\n".join(L)


def _summary_block(result) -> list[str]:
    totals: dict[str, int] = {}
    for sr in result.session_receipts:
        for k, v in sr.counts().items():
            totals[k] = totals.get(k, 0) + v
    never = [r for r in result.funnel if r.never_cited and r.exists]
    unmeasured = [r for r in result.funnel if r.unmeasured]
    stale = [r for r in result.funnel if not r.exists]
    trunc = [r for r in result.funnel if r.n_sessions_truncated]
    dupes = [r for r in result.funnel if r.near_duplicates]
    L = ["## Headline", ""]
    L.append("| signal | value |")
    L.append("|---|---|")
    L.append(f"| (session × artifact) receipts | {sum(totals.values())} |")
    for k in ("loaded_complete", "loaded_truncated", "not_loaded", "missing_on_disk", "unknown"):
        if totals.get(k):
            L.append(f"| … {k} | {totals[k]} |")
    high = [o for o in result.out_of_band if o.severity == "high"]
    info = [o for o in result.out_of_band if o.severity != "high"]
    corrob = sum(1 for m in result.mutations if m.corroborated)
    medium = sum(1 for m in result.mutations if m.confidence == "medium")
    low = sum(1 for m in result.mutations if m.confidence == "low")
    L.append(f"| learned-state mutations attributed | {len(result.mutations)} "
             f"({medium} heuristic Bash, {low} low-confidence, "
             f"{corrob} corroborated by file-history) |")
    L.append(f"| **out-of-band changes (unexplained)** | {len(high)} |")
    L.append(f"| unattributed / informational findings | {len(info)} |")
    L.append(f"| artifacts never cited (citable kinds) | {len(never)} |")
    L.append(f"| artifacts with no eligible session yet (unmeasured) | {len(unmeasured)} |")
    L.append(f"| artifacts truncated in >=1 session | {len(trunc)} |")
    L.append(f"| stale index entries / missing files | {len(stale)} |")
    L.append(f"| near-duplicate artifacts | {len(dupes)} |")
    return L


def _receipts_section(shown, n_total: int) -> list[str]:
    L = [f"## Receipts (last {len(shown)} of {n_total} sessions)", ""]
    if not shown:
        L.append("_No sessions in scope._")
        return L
    for sr in reversed(shown):
        L.append(f"### `{_short(sr.session_id)}` · {sr.project_slug}")
        L.append("")
        L.append(f"- session id: `{sr.session_id}`")
        L.append(f"- transcript: `{sr.transcript}`")
        L.append(f"- window: {_ts(sr.started_at)} → {_ts(sr.ended_at)} UTC · "
                 f"cwd `{sr.cwd or '—'}` · branch `{sr.git_branch or '—'}` · "
                 f"cli {', '.join(sr.versions) or '—'}")
        L.append(f"- model(s): {', '.join(sr.models) or '—'} · "
                 f"{sr.n_assistant_turns} assistant turns · {sr.n_human_prompts} human prompts"
                 + (f" · {sr.n_subagent_transcripts} subagent transcripts" if sr.n_subagent_transcripts else ""))
        u = sr.usage
        if any(u.values()):
            L.append(f"- tokens: in {u['input_tokens']:,} · out {u['output_tokens']:,} · "
                     f"cache-read {u['cache_read_input_tokens']:,} · "
                     f"cache-write {u['cache_creation_input_tokens']:,}")
        L.append(f"- context records present: prompt_snapshot={sr.has_prompt_snapshot} · "
                 f"instructions={sr.has_instructions} · skill_listing={sr.has_skill_listing}")
        counts = sr.counts()
        L.append("- receipt counts: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "—"))
        L.append("")
        interesting = [a for a in sr.artifacts if a.status != "unknown"]
        noteworthy = [a for a in interesting if a.status != "loaded_complete"]
        rows = noteworthy or interesting[:8]
        if rows:
            L.append("| status | kind | artifact | coverage | evidence (file:line) | at |")
            L.append("|---|---|---|---|---|---|")
            for a in rows[:25]:
                cov = "—"
                if a.coverage:
                    cov = (f"{a.coverage.units_found}/{a.coverage.units_total} "
                           f"{a.coverage.method} ({a.coverage.fraction:.0%})")
                L.append(f"| **{_STATUS_MARK.get(a.status, a.status)}** | {a.kind} | "
                         f"`{_cell(a.name, 46)}` | {cov} | "
                         f"`{a.locator() or '—'}` | {_ts(a.evidence_ts or a.ts)} |")
            if len(rows) > 25:
                L.append(f"| … | | {len(rows) - 25} more rows in the JSON | | | |")
            L.append("")
        n_ok = counts.get("loaded_complete", 0)
        if not noteworthy and n_ok:
            L.append(f"_All {n_ok} determinable artifacts loaded complete._")
            L.append("")
        unlisted = [a for a in sr.artifacts
                    if any("is not among" in n for n in a.notes)]
        if unlisted:
            loc = unlisted[0].locator() or "—"
            names = ", ".join(f"`{a.name}`" for a in unlisted[:6])
            more = f" … +{len(unlisted) - 6}" if len(unlisted) > 6 else ""
            L.append(f"- warning · {len(unlisted)} skill(s) present on disk today were "
                     f"NOT in this session's skill listing (installed after the "
                     f"session, or the listing dropped them): {names}{more} (`{loc}`)")
        for a in sr.artifacts:
            for note in a.notes:
                if "is not among" in note:
                    continue
                if any(w in note for w in ("cap", "stale", "orphan", "is NOT what")):
                    L.append(f"- warning · `{a.name}`: {note} "
                             f"(`{a.locator() or a.path}`)")
            if a.missing_index_lines:
                preview = "; ".join(f"L{n}: {t.strip()[:60]}" for n, t in a.missing_index_lines[:3])
                L.append(f"- warning · `{a.name}`: {len(a.missing_index_lines)} index line(s) "
                         f"did not reach context — {preview}")
        if sr.cited_artifacts:
            names: dict[str, int] = {}
            for c in sr.cited_artifacts:
                names[c["name"]] = names.get(c["name"], 0) + 1
            top = ", ".join(f"`{k}`×{v}" for k, v in sorted(names.items(), key=lambda kv: -kv[1])[:10])
            L.append(f"- cited/invoked this session: {top}")
        if sr.written_artifacts:
            L.append("- learned state written this session: "
                     + ", ".join(f"`{w}`" for w in sr.written_artifacts[:10]))
        L.append("")
    return L


def _changefeed_section(result) -> list[str]:
    L = ["## Change feed", ""]
    if not result.mutations:
        L.append("_No learned-state mutations found in the scanned sessions._")
    else:
        L.append("Chronological. `locator` is `<transcript file>:<line>` — "
                 "`sed -n '<line>p'` on that file shows the raw record.")
        L.append("")
        L.append("| when (UTC) | session | tool/op | artifact | change | conf | locator |")
        L.append("|---|---|---|---|---|---|---|")
        for m in result.mutations:
            conf = m.confidence + ("+fh" if m.corroborated else "")
            L.append(f"| {_ts(m.ts)} | `{_short(m.session_id)}` | {m.tool}/{m.op} | "
                     f"`{_cell(_label(m.artifact_id), 54)}` | {m.change_type}"
                     f"{' (subagent)' if m.is_sidechain else ''} | {conf} | `{m.locator()}` |")
        L.append("")
        L.append("<details><summary>Per-mutation detail (before → after, stated reason)</summary>")
        L.append("")
        for m in result.mutations:
            L.append(f"**{_ts(m.ts)} · `{_short(m.session_id)}` · {m.tool}/{m.op} · "
                     f"`{m.artifact_id}`** (`{m.locator()}`)")
            L.append("")
            if m.reason:
                L.append(f"- stated reason (assistant text at line {m.reason_line_no}, "
                         f"{_ts(m.reason_ts)}): {_cell(m.reason, 300)}")
            if m.detail:
                L.append(f"- command: `{_cell(m.detail, 240)}`")
            if m.before_excerpt:
                L.append(f"- before: `{_cell(m.before_excerpt, 200)}`")
            if m.after_excerpt:
                L.append(f"- after: `{_cell(m.after_excerpt, 200)}`")
            L.append("")
        L.append("</details>")
    L.append("")
    L.append("### Out-of-band changes (on-disk state no scanned session accounts for)")
    L.append("")
    high = [o for o in result.out_of_band if o.severity == "high"]
    info = [o for o in result.out_of_band if o.severity != "high"]

    def _table(items):
        rows = ["| artifact | reason | last attributed write | detail |", "|---|---|---|---|"]
        for o in items:
            lw = "—"
            if o.last_write_ts:
                lw = (f"{_ts(o.last_write_ts)} by `{_short(o.last_write_session)}` "
                      f"(`{o.last_write_locator}`)")
            rows.append(f"| `{_cell(o.artifact_id, 46)}` | **{o.reason}** | {lw} | "
                        f"{_cell(o.detail, 90)} |")
        return rows

    if not high:
        L.append("_No unexplained change: every artifact with a recorded write matches "
                 "what the feed says it should contain._")
    else:
        L.extend(_table(high))
    L.append("")
    if info:
        by_reason: dict[str, list] = {}
        for o in info:
            by_reason.setdefault(o.reason, []).append(o)
        L.append(f"<details><summary>Informational: {len(info)} unattributed / "
                 "expected findings</summary>")
        L.append("")
        for reason, items in sorted(by_reason.items()):
            L.append(f"**{reason}** ({len(items)}) — {_cell(items[0].detail, 160)}")
            L.append("")
            L.extend(_table(items[:30]))
            if len(items) > 30:
                L.append(f"| … {len(items) - 30} more | | | |")
            L.append("")
        L.append("</details>")
    if result.history_events:
        L.append("")
        L.append(f"_Claude Code's own `file-history-delta` markers cover "
                 f"{len(result.history_events)} learned-state write(s); "
                 f"{sum(1 for m in result.mutations if m.corroborated)} feed entries "
                 "are corroborated by one._")
    return L


def _artifacts_section(result) -> list[str]:
    L = ["## Artifacts: activation funnel + warnings", ""]
    L.append("`eligible` = scanned sessions in scope that started after the artifact "
             "existed. `loaded` counts complete+truncated. `cited` counts sessions with a "
             "`<cc-memory>` tag (memory) or a Skill invocation (skills); `n/a` where no "
             "citation signal exists.")
    L.append("")
    L.append("| kind | artifact | project | bytes | age (d) | created by | eligible | loaded | trunc | cited | last cited | writes |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in result.funnel:
        cited = "n/a" if not r.citable else str(r.n_sessions_cited)
        proj = (r.project_slug or ("user" if r.scope == "user" else "—"))
        if len(proj) > 18:
            proj = "…" + proj[-17:]
        L.append(
            f"| {r.kind} | `{_cell(r.name, 40)}` | `{proj}` | {r.size_bytes} | "
            f"{r.age_days if r.age_days is not None else '—'} | "
            f"`{_short(r.created_by_session) if r.created_by_session else '—'}` | "
            f"{r.n_sessions_eligible} | {r.n_sessions_loaded} | {r.n_sessions_truncated} | "
            f"{cited} | {_ts(r.last_cited)} | {r.n_writes} |")
    L.append("")
    warned = [r for r in result.funnel if r.warnings]
    L.append("### Warnings")
    L.append("")
    if not warned:
        L.append("_None._")
        return L
    buckets: dict[str, list[str]] = {
        "truncated index / over cap": [], "stale index entries": [], "orphans": [],
        "near-duplicates": [], "never cited": [], "no eligible session yet": [],
        "out-of-band": [], "other": [],
    }
    seen_pairs: set[tuple[str, str]] = set()
    for r in warned:
        for w in r.warnings:
            wl = w.lower()
            if "near-duplicate of" in w:
                other = w.split("near-duplicate of", 1)[1].split("(")[0].strip()
                pair = tuple(sorted((r.artifact_id, other)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
            if "cap" in wl:
                key = "truncated index / over cap"
            elif "stale" in wl or "missing on disk" in wl:
                key = "stale index entries"
            elif "orphan" in wl:
                key = "orphans"
            elif "duplicate" in wl:
                key = "near-duplicates"
            elif "never cited" in wl:
                key = "never cited"
            elif "no eligible session" in wl:
                key = "no eligible session yet"
            elif "out-of-band" in wl:
                key = "out-of-band"
            else:
                key = "other"
            buckets[key].append(f"`{r.artifact_id}` — {w}")
    for key, items in buckets.items():
        if not items:
            continue
        L.append(f"**{key}** ({len(items)})")
        L.append("")
        for it in items[:40]:
            L.append(f"- {it}")
        if len(items) > 40:
            L.append(f"- … {len(items) - 40} more (see JSON)")
        L.append("")
    return L
