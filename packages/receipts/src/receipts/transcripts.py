# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Streaming reader for Claude Code session transcripts.

Layout (verified on Claude Code 2.1.x)::

    ~/.claude/projects/<project-slug>/<session-id>.jsonl          main session
    ~/.claude/projects/<project-slug>/<session-id>/subagents/**/*.jsonl

Records are one JSON object per line with a ``type`` field.  The ones this
module cares about:

``assistant``
    ``message.content[]`` blocks of type ``text`` / ``thinking`` / ``tool_use``.
    ``message.usage`` carries token counts; ``message.model`` the served model;
    ``attributionSkill`` names the skill a turn was attributed to.
``user``
    ``message.content[]`` ``tool_result`` blocks plus a top-level
    ``toolUseResult`` with the structured result (Write: ``{type, filePath,
    content, originalFile}``; Edit: ``{filePath, oldString, newString, ...}``).
    Human turns carry ``origin.kind == "human"``.
``attachment``
    ``attachment.type`` selects the payload, and the top-level ``rendered[]``
    array holds the ``<system-reminder>`` text that actually entered context:

      * ``prompt_snapshot``  – ``systemPrompt: [str]``, the rendered system prompt
      * ``instructions``     – ``files: [{path, type, content}]``, the authoritative
                               record of which instruction/memory file was loaded
      * ``skill_listing``    – ``names``, ``skillCount``, ``content``
      * ``invoked_skills``   – ``skills: [{name, path, content}]``
      * ``edited_text_file`` – ``filename``, ``snippet`` (a change notice, not a load)

``file-history-delta``
    Claude Code's own first-write backup marker: ``trackingPath`` plus
    ``backup.backupTime``.  It is emitted the first time a session touches a
    tracked file, so it corroborates a write but is not a complete change log.

Files are streamed line by line; nothing is ever written.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

__all__ = [
    "CC_MEMORY_RE",
    "Evidence",
    "SessionData",
    "ToolCall",
    "find_sessions",
    "iter_records",
    "load_session",
    "parse_ts",
]

CC_MEMORY_RE = re.compile(r"<cc-memory\s+filenames=\"([^\"]*)\"\s*>")
"""Adherence signal: the assistant wraps a sentence that *uses* a memory."""

_TEMPLATE_CITATION = "{comma separated memory file names}"

# Attachment types whose rendered text is boilerplate, never artifact content.
_NOISE_ATTACHMENTS = frozenset({
    "total_tokens_reminder", "batching_reminder_sent", "silent_turn_reminder",
    "date", "model", "environment", "deferred_tools_delta", "deferred_tools_record",
    "agent_listing_delta", "auto_mode", "ultra_effort_enter", "ultra_effort_exit",
    "remote_session_change", "read_truncation_notice", "queued_command",
    "command_permissions", "session_context", "thinking_stripped", "task_status",
    "edited_text_file", "compact_file_reference",
})

_MIN_EVIDENCE_CHARS = 120
_MAX_EVIDENCE_PER_SESSION = 400
_MAX_REASON_CHARS = 300


def parse_ts(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (``...Z`` accepted) into an aware datetime."""
    if not value:
        return None
    try:
        s = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iter_records(path: str) -> Iterator[tuple[int, dict]]:
    """Yield ``(1-based line number, record)`` for every parseable line."""
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line[0] != "{":
                continue
            try:
                rec = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(rec, dict):
                yield line_no, rec


@dataclass
class Evidence:
    """A blob of text that demonstrably entered the model's context."""

    kind: str                 # prompt_snapshot | instructions | skill_listing | invoked_skills | attachment:<t>
    ts: str | None
    line_no: int
    source: str               # transcript path
    path: str | None = None   # for `instructions`, the file whose content this is
    text: str = ""

    def locator(self) -> str:
        return f"{os.path.basename(self.source)}:{self.line_no}"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "ts": self.ts, "lineNo": self.line_no,
            "source": self.source, "path": self.path, "chars": len(self.text),
        }


@dataclass
class ToolCall:
    id: str | None
    name: str
    input: dict
    ts: str | None
    line_no: int
    source: str
    is_sidechain: bool = False
    agent_file: str | None = None
    order: int = 0
    result: dict | None = None


@dataclass
class AssistantText:
    order: int
    ts: str | None
    line_no: int
    source: str
    text: str


@dataclass
class SessionData:
    session_id: str
    project_slug: str
    path: str
    cwd: str | None = None
    git_branch: str | None = None
    versions: set[str] = field(default_factory=set)
    models: set[str] = field(default_factory=set)
    started_at: str | None = None
    ended_at: str | None = None
    n_assistant_turns: int = 0
    n_human_prompts: int = 0
    usage: dict = field(default_factory=lambda: {
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    })
    evidence: list[Evidence] = field(default_factory=list)
    instructions_files: list[dict] = field(default_factory=list)
    skill_listings: list[dict] = field(default_factory=list)
    skill_listing_names: set[str] = field(default_factory=set)
    skill_listing_text: str = ""
    skill_listing_evidence: Evidence | None = None
    invoked_skills: set[str] = field(default_factory=set)
    skill_invocations: list[dict] = field(default_factory=list)
    memory_citations: list[dict] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_texts: list[AssistantText] = field(default_factory=list)
    subagent_files: list[str] = field(default_factory=list)
    file_history: list[dict] = field(default_factory=list)
    has_prompt_snapshot: bool = False
    has_instructions: bool = False
    has_skill_listing: bool = False

    def sort_key(self) -> str:
        return self.started_at or ""


def find_sessions(claude_home: str, project_filter: str | None = None) -> list[tuple[str, str]]:
    """Return ``(project_slug, transcript_path)`` for every main session.

    Subagent transcripts (``<session-id>/subagents/**``) are folded into their
    parent session by :func:`load_session`, not listed separately.
    """
    root = os.path.join(claude_home, "projects")
    out: list[tuple[str, str]] = []
    if not os.path.isdir(root):
        return out
    for slug in sorted(os.listdir(root)):
        if project_filter and project_filter not in slug:
            continue
        pdir = os.path.join(root, slug)
        if not os.path.isdir(pdir):
            continue
        try:
            names = sorted(os.listdir(pdir))
        except OSError:
            continue
        for name in names:
            if name.endswith(".jsonl") and os.path.isfile(os.path.join(pdir, name)):
                out.append((slug, os.path.join(pdir, name)))
    return out


def _subagent_files(transcript: str) -> list[str]:
    base = transcript[:-len(".jsonl")]
    out: list[str] = []
    for root in (os.path.join(base, "subagents"),):
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in sorted(filenames):
                if fn.endswith(".jsonl"):
                    out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _content_blocks(rec: dict) -> list[dict]:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return []
    c = msg.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    if isinstance(c, list):
        return [b for b in c if isinstance(b, dict)]
    return []


def _rendered_text(rec: dict) -> str:
    parts: list[str] = []
    rendered = rec.get("rendered")
    if isinstance(rendered, list):
        for e in rendered:
            if isinstance(e, dict) and isinstance(e.get("content"), str):
                parts.append(e["content"])
            elif isinstance(e, str):
                parts.append(e)
    return "\n".join(parts)


def load_session(slug: str, transcript: str, include_subagents: bool = True) -> SessionData:
    """Parse one session (and, optionally, its subagent transcripts)."""
    sess = SessionData(session_id=os.path.basename(transcript)[:-len(".jsonl")],
                       project_slug=slug, path=transcript)
    order = 0
    pending: dict[str, ToolCall] = {}

    files = [(transcript, False)]
    if include_subagents:
        subs = _subagent_files(transcript)
        sess.subagent_files = subs
        files.extend((s, True) for s in subs)

    for source, is_sub in files:
        for line_no, rec in iter_records(source):
            rtype = rec.get("type")
            ts = rec.get("timestamp")
            if not is_sub:
                if rec.get("sessionId"):
                    sess.session_id = rec["sessionId"]
                if rec.get("cwd") and not sess.cwd:
                    sess.cwd = rec["cwd"]
                if rec.get("gitBranch") and not sess.git_branch:
                    sess.git_branch = rec["gitBranch"]
                if rec.get("version"):
                    sess.versions.add(str(rec["version"]))
                if ts:
                    if sess.started_at is None or ts < sess.started_at:
                        sess.started_at = ts
                    if sess.ended_at is None or ts > sess.ended_at:
                        sess.ended_at = ts

            if rtype == "file-history-delta":
                tracking = rec.get("trackingPath")
                if tracking:
                    backup = rec.get("backup") if isinstance(rec.get("backup"), dict) else {}
                    sess.file_history.append({
                        "path": tracking, "ts": rec.get("timestamp") or backup.get("backupTime"),
                        "version": backup.get("version"), "lineNo": line_no, "source": source,
                    })
                continue

            if rtype == "attachment":
                _handle_attachment(sess, rec, line_no, source)
                continue

            if rtype == "assistant":
                order += 1
                sess.n_assistant_turns += 1
                msg = rec.get("message") or {}
                if msg.get("model"):
                    sess.models.add(str(msg["model"]))
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    for k in sess.usage:
                        v = usage.get(k)
                        if isinstance(v, int):
                            sess.usage[k] += v
                attribution = rec.get("attributionSkill")
                if attribution:
                    sess.skill_invocations.append({
                        "skill": str(attribution), "ts": ts, "lineNo": line_no,
                        "source": source, "via": "attributionSkill",
                    })
                for b in _content_blocks(rec):
                    bt = b.get("type")
                    if bt == "text":
                        text = b.get("text") or ""
                        if text.strip():
                            sess.assistant_texts.append(AssistantText(
                                order, ts, line_no, source, text[:2000]))
                        for m in CC_MEMORY_RE.finditer(text):
                            names = [n.strip() for n in m.group(1).split(",") if n.strip()]
                            if not names or _TEMPLATE_CITATION in m.group(1):
                                continue
                            sess.memory_citations.append({
                                "files": names, "ts": ts, "lineNo": line_no,
                                "source": source,
                            })
                    elif bt == "tool_use":
                        tc = ToolCall(
                            id=b.get("id"), name=str(b.get("name") or ""),
                            input=b.get("input") if isinstance(b.get("input"), dict) else {},
                            ts=ts, line_no=line_no, source=source,
                            is_sidechain=bool(rec.get("isSidechain")) or is_sub,
                            agent_file=source if is_sub else None, order=order,
                        )
                        sess.tool_calls.append(tc)
                        if tc.id:
                            pending[tc.id] = tc
                        if tc.name == "Skill":
                            skill = (tc.input or {}).get("skill")
                            if skill:
                                sess.skill_invocations.append({
                                    "skill": str(skill), "ts": ts, "lineNo": line_no,
                                    "source": source, "via": "Skill tool_use",
                                })
                continue

            if rtype == "user":
                origin = rec.get("origin")
                if isinstance(origin, dict) and origin.get("kind") == "human":
                    sess.n_human_prompts += 1
                tur = rec.get("toolUseResult")
                for b in _content_blocks(rec):
                    if b.get("type") == "tool_result":
                        tc = pending.get(b.get("tool_use_id") or "")
                        if tc is not None and isinstance(tur, dict):
                            tc.result = tur
                continue

    sess.assistant_texts.sort(key=lambda a: (a.source, a.line_no))
    sess.tool_calls.sort(key=lambda t: (t.source != transcript, t.source, t.line_no))
    return sess


def _handle_attachment(sess: SessionData, rec: dict, line_no: int, source: str) -> None:
    att = rec.get("attachment")
    if not isinstance(att, dict):
        return
    atype = str(att.get("type") or "")
    ts = rec.get("timestamp")

    if atype == "prompt_snapshot":
        sess.has_prompt_snapshot = True
        sp = att.get("systemPrompt")
        if isinstance(sp, list):
            text = "\n".join(s for s in sp if isinstance(s, str))
        elif isinstance(sp, str):
            text = sp
        else:
            text = ""
        if text:
            _add_evidence(sess, Evidence("prompt_snapshot", ts, line_no, source, None, text))
        return

    if atype == "instructions":
        sess.has_instructions = True
        for f in att.get("files") or []:
            if not isinstance(f, dict):
                continue
            entry = {
                "path": f.get("path"), "type": f.get("type"),
                "chars": len(f.get("content") or ""), "ts": ts,
                "lineNo": line_no, "source": source,
                "reason": att.get("reason"), "changed": att.get("changed"),
            }
            sess.instructions_files.append(entry)
            _add_evidence(sess, Evidence("instructions", ts, line_no, source,
                                         f.get("path"), f.get("content") or ""))
        return

    if atype == "skill_listing":
        sess.has_skill_listing = True
        names = att.get("names")
        name_set = {str(n) for n in names} if isinstance(names, list) else set()
        content = att.get("content")
        content = content if isinstance(content, str) else ""
        sess.skill_listing_names.update(name_set)
        ev = Evidence("skill_listing", ts, line_no, source, None, content)
        # A session can be shown several listings (skills installed mid-session,
        # a compaction re-render). Keep them all so a receipt can point at the
        # listing that actually carried the skill, not just the last one.
        sess.skill_listings.append({
            "names": name_set, "content": content, "evidence": ev,
            "isInitial": bool(att.get("isInitial")), "skillCount": att.get("skillCount"),
            "ts": ts, "lineNo": line_no,
        })
        if content:
            sess.skill_listing_text = content
            sess.skill_listing_evidence = ev
            _add_evidence(sess, ev)
        return

    if atype == "invoked_skills":
        for s in att.get("skills") or []:
            if isinstance(s, dict) and s.get("name"):
                sess.invoked_skills.add(str(s["name"]))
                sess.skill_invocations.append({
                    "skill": str(s["name"]), "ts": ts, "lineNo": line_no,
                    "source": source, "via": "invoked_skills attachment",
                })
        return

    if atype in _NOISE_ATTACHMENTS:
        return

    text = _rendered_text(rec)
    if len(text) >= _MIN_EVIDENCE_CHARS:
        _add_evidence(sess, Evidence(f"attachment:{atype}", ts, line_no, source, None, text))


def _add_evidence(sess: SessionData, ev: Evidence) -> None:
    if not ev.text:
        return
    if len(sess.evidence) >= _MAX_EVIDENCE_PER_SESSION:
        return
    sess.evidence.append(ev)
