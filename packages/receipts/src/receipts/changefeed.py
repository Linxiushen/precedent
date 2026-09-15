# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Chronological feed of mutations to learned state, with attribution.

A mutation is any tool call in a transcript that writes a learned-state file:

* ``Write``        – ``input.file_path`` / ``input.content``; the structured
  ``toolUseResult`` also carries ``type`` (create|update) and ``originalFile``.
* ``Edit``         – ``old_string`` / ``new_string`` / ``replace_all``.
* ``NotebookEdit`` – ``notebook_path`` / ``new_source``.
* ``Bash``         – a command containing a redirect, ``tee``, ``sed -i``, a
  heredoc feeding a writer, an ``rm``/``mv``/``cp``, or an inline
  ``open(path, "w")`` that targets a learned-state path.  Relative paths are
  resolved against any ``cd`` in the same command line, because that is how
  these writes actually appear in the wild.

Each mutation is attributed to (session, timestamp, tool) and annotated with the
assistant's stated reason: the nearest preceding assistant text block in the same
transcript, truncated to 300 characters, carrying its own line number so the
claim can be grepped.

The out-of-band check compares what the feed *says* the file should contain with
what is actually on disk (and with the file's mtime).  A divergence means some
actor other than the recorded sessions touched it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import timedelta

from .paths import PathClassifier
from .textmatch import normalize, strip_frontmatter
from .transcripts import SessionData, ToolCall, parse_ts

__all__ = ["Mutation", "OutOfBand", "build_change_feed", "corroborate", "detect_out_of_band"]

_MAX_REASON_CHARS = 300
_EXCERPT_CHARS = 400
MTIME_TOLERANCE_S = 120

_WRITE_TOOLS = {"Write", "Edit", "NotebookEdit", "MultiEdit"}

_CD = re.compile(r"\bcd\s+(?:'([^']*)'|\"([^\"]*)\"|([^\\s;&|]+))")
_TOKEN = re.compile(r"'([^'\\s]+)'|\"([^\"\\s]+)\"|([A-Za-z0-9_@~./\\-]*[A-Za-z0-9_.\\-]+)")
_SED_I = re.compile(r"\bsed\s+(?:-[A-Za-z]*\s+)*-i")
_HEREDOC = re.compile(r"<<-?\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?")
_PY_WRITE = re.compile(r"\.write_text\(|\.writelines\(|\.write\(|open\([^)]*['\"][wax]\+?b?['\"]")
_MAX_TOKEN_CHARS = 512

#: ``python3 -c "…"`` / ``node -e '…'`` — inline code passed as one argument.
#: A learned-state path that appears ONLY inside such an argument is usually a
#: command being quoted, demonstrated or tested rather than executed against the
#: agent's own state, so those mutations are reported at low confidence.
_INLINE_CODE = re.compile(
    r"""\b(?:python3?|node|ruby|perl|osascript)\s+(?:-[A-Za-z]+\s+)*-[ce]\s+"""
    r"""(?:'[^']*'|"(?:[^"\\]|\\.)*")""")


def _only_inside_inline_code(cmd: str, token: str) -> bool:
    """True when every occurrence of ``token`` sits inside an inline-code argument."""
    spans = [(m.start(), m.end()) for m in _INLINE_CODE.finditer(cmd)]
    if not spans:
        return False
    pos = cmd.find(token)
    if pos == -1:
        return False
    while pos != -1:
        if not any(a <= pos < b for a, b in spans):
            return False
        pos = cmd.find(token, pos + 1)
    return True


#: ops whose target is unambiguous (the path sits inside the write construct)
STRONG_OPS = frozenset({"sed -i", "redirect", "tee", "rm", "mv", "cp", "truncate"})


def _esc(token: str) -> str:
    return re.escape(token)


def _redirect_to(cmd: str, token: str) -> bool:
    return bool(re.search(r">>?\s*(?:['\"])?" + _esc(token) + r"(?:['\"])?(?:\s|$|;|&|\|)", cmd))


def _verb_on(cmd: str, token: str, verbs: tuple[str, ...]) -> str | None:
    for v in verbs:
        if re.search(r"\b" + v + r"\b[^;&|\n]*?(?:['\"])?" + _esc(token), cmd):
            return v
    return None


def _assigned_to_var(cmd: str, token: str) -> bool:
    """``M=/path/to/memory.md`` or ``p='competitions.md'`` — the dominant way a
    heredoc script names the file it is about to rewrite."""
    return bool(re.search(r"(?:^|[\s;&|(])[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?:['\"])?"
                          + _esc(token) + r"(?:['\"])?(?:\s|$|;|&|\||\))", cmd))


def bash_write_ops(cmd: str, tokens: list[str]) -> dict[str, str]:
    """For each candidate path token, the write operation detected (if any).

    A token only counts as written when it sits *inside* a write construct
    (``> path``, ``tee path``, ``sed -i ... path``, ``rm/mv/cp path``) or when it
    is assigned to a shell/python variable in a command that also contains a
    writer (``M=<path> && python3 - "$M" <<'EOF' ... open(p,'w') ... EOF``).
    Merely appearing somewhere in the command — inside a replacement string, a
    ``find`` pattern or a log line — is not enough.
    """
    ops: dict[str, str] = {}
    has_heredoc = bool(_HEREDOC.search(cmd))
    has_py_write = bool(_PY_WRITE.search(cmd))
    for tok in tokens:
        op: str | None = None
        if _SED_I.search(cmd) and re.search(r"\bsed\b[^;&|\n]*" + _esc(tok), cmd):
            op = "sed -i"
        elif _redirect_to(cmd, tok):
            op = "redirect"
        elif re.search(r"\btee\b[^;&|\n]*" + _esc(tok), cmd):
            op = "tee"
        else:
            v = _verb_on(cmd, tok, ("rm", "mv", "cp", "truncate"))
            if v:
                op = v
            elif _assigned_to_var(cmd, tok):
                if has_py_write:
                    op = "heredoc+python" if has_heredoc else "python-write"
                elif _SED_I.search(cmd):
                    op = "sed -i (via var)"
                elif re.search(r">>?\s*[\"']?\$\{?[A-Za-z_]", cmd):
                    op = "redirect (via var)"
        if op:
            ops[tok] = op
    return ops


def _candidate_tokens(cmd: str) -> list[str]:
    toks: list[str] = []
    for m in _TOKEN.finditer(cmd):
        t = m.group(1) or m.group(2) or m.group(3)
        if not t or len(t) < 3 or len(t) > _MAX_TOKEN_CHARS:
            continue
        if any(ch in t for ch in " \t\n\r"):
            continue
        if t.endswith((".md", ".json", ".yaml", ".yml", ".py", ".sh", ".txt", ".toml")) \
           or "/memory/" in t or "/.claude/skills/" in t or t.endswith("CLAUDE.md"):
            toks.append(t)
    seen: set[str] = set()
    out = []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _cd_dirs(cmd: str) -> list[str]:
    out = []
    for m in _CD.finditer(cmd):
        d = m.group(1) or m.group(2) or m.group(3)
        if d and d not in ("-", ".."):
            out.append(d)
    return out


@dataclass
class Mutation:
    ts: str | None
    session_id: str
    project_slug: str
    transcript: str
    line_no: int
    tool: str
    op: str
    path: str
    artifact_id: str
    artifact_kind: str
    change_type: str                 # create | update | delete | unknown
    before_excerpt: str | None = None
    after_excerpt: str | None = None
    after_full: str | None = None    # only for Write, used by the out-of-band check
    edit_new: str | None = None      # only for Edit
    detail: str | None = None        # Bash command
    reason: str | None = None
    reason_line_no: int | None = None
    reason_ts: str | None = None
    is_sidechain: bool = False
    agent_file: str | None = None
    confidence: str = "high"          # high | medium | low — see bash_write_ops
    corroborated: bool = False        # a file-history-delta backs this write

    def locator(self) -> str:
        return f"{os.path.basename(self.transcript)}:{self.line_no}"

    def to_dict(self) -> dict:
        return {
            "ts": self.ts, "sessionId": self.session_id, "projectSlug": self.project_slug,
            "transcript": self.transcript, "lineNo": self.line_no, "tool": self.tool,
            "op": self.op, "path": self.path, "artifactId": self.artifact_id,
            "artifactKind": self.artifact_kind, "changeType": self.change_type,
            "beforeExcerpt": self.before_excerpt, "afterExcerpt": self.after_excerpt,
            "detail": self.detail, "reason": self.reason,
            "reasonLineNo": self.reason_line_no, "reasonTs": self.reason_ts,
            "isSidechain": self.is_sidechain, "agentFile": self.agent_file,
            "confidence": self.confidence, "corroborated": self.corroborated,
        }


def _excerpt(text: str | None, limit: int = _EXCERPT_CHARS) -> str | None:
    if text is None:
        return None
    t = text.strip()
    if len(t) <= limit:
        return t
    return t[:limit] + f"… (+{len(t) - limit} chars)"


def _nearest_reason(sess: SessionData, tc: ToolCall) -> tuple[str | None, int | None, str | None]:
    best = None
    for at in sess.assistant_texts:
        if at.source != tc.source:
            continue
        if at.line_no >= tc.line_no:
            continue
        if best is None or at.line_no > best.line_no:
            best = at
    if best is None:
        return None, None, None
    text = " ".join(best.text.split())
    if len(text) > _MAX_REASON_CHARS:
        text = text[:_MAX_REASON_CHARS] + "…"
    return text, best.line_no, best.ts


def build_change_feed(sessions: list[SessionData], classifier: PathClassifier) -> list[Mutation]:
    """Walk every session's tool calls and emit the learned-state mutations."""
    muts: list[Mutation] = []
    for sess in sessions:
        for tc in sess.tool_calls:
            for m in _mutations_for_call(sess, tc, classifier):
                muts.append(m)
    muts.sort(key=lambda m: (m.ts or "", m.transcript, m.line_no))
    return muts


def _mutations_for_call(sess: SessionData, tc: ToolCall, classifier: PathClassifier) -> list[Mutation]:
    inp = tc.input or {}
    res = tc.result if isinstance(tc.result, dict) else {}
    out: list[Mutation] = []

    def emit(pc, op, change_type, before=None, after=None, after_full=None,
             edit_new=None, detail=None, confidence="high"):
        reason, rline, rts = _nearest_reason(sess, tc)
        out.append(Mutation(
            ts=tc.ts, session_id=sess.session_id, project_slug=sess.project_slug,
            transcript=tc.source, line_no=tc.line_no, tool=tc.name, op=op,
            path=pc.path, artifact_id=pc.artifact_id, artifact_kind=pc.kind,
            change_type=change_type, before_excerpt=_excerpt(before),
            after_excerpt=_excerpt(after), after_full=after_full, edit_new=edit_new,
            detail=detail, reason=reason, reason_line_no=rline, reason_ts=rts,
            is_sidechain=tc.is_sidechain, agent_file=tc.agent_file,
            confidence=confidence,
        ))

    if tc.name in _WRITE_TOOLS:
        target = inp.get("file_path") or inp.get("notebook_path") or res.get("filePath")
        pc = classifier.classify(str(target or ""), sess.cwd)
        if pc is None:
            return out
        if tc.name == "Write":
            content = inp.get("content")
            if not isinstance(content, str):
                content = res.get("content") if isinstance(res.get("content"), str) else None
            original = res.get("originalFile") if isinstance(res.get("originalFile"), str) else None
            ctype = res.get("type")
            change_type = {"create": "create", "update": "update"}.get(str(ctype or ""), None)
            if change_type is None:
                change_type = "update" if original else "create"
            emit(pc, "write", change_type, before=original, after=content, after_full=content)
        elif tc.name in ("Edit", "MultiEdit"):
            old = inp.get("old_string")
            new = inp.get("new_string")
            if not isinstance(old, str):
                old = res.get("oldString") if isinstance(res.get("oldString"), str) else None
            if not isinstance(new, str):
                new = res.get("newString") if isinstance(res.get("newString"), str) else None
            emit(pc, "edit", "update", before=old, after=new, edit_new=new)
        else:
            emit(pc, "notebook_edit", "update", after=str(inp.get("new_source") or "")[:_EXCERPT_CHARS])
        return out

    if tc.name == "Bash":
        cmd = inp.get("command")
        if not isinstance(cmd, str) or not cmd:
            return out
        dirs = _cd_dirs(cmd)
        tokens = _candidate_tokens(cmd)
        resolved: dict[str, object] = {}
        for tok in tokens:
            pc = classifier.classify(tok, sess.cwd)
            if pc is None:
                for d in dirs:
                    base = d if os.path.isabs(d) else os.path.join(sess.cwd or "", d)
                    pc = classifier.classify(tok, base)
                    if pc is not None:
                        break
            if pc is not None:
                resolved[tok] = pc
        if not resolved:
            return out
        ops = bash_write_ops(cmd, list(resolved))
        for tok, pc in resolved.items():
            op = ops.get(tok)
            if not op:
                continue
            change_type = "delete" if op == "rm" else "update"
            if _only_inside_inline_code(cmd, tok):
                confidence = "low"
            elif op in STRONG_OPS:
                confidence = "high"
            else:
                confidence = "medium"
            emit(pc, f"bash:{op}", change_type,
                 detail=" ".join(cmd.split())[:600], confidence=confidence)
        return out
    return out


#: findings that mean "someone other than the recorded sessions changed this"
HIGH_SEVERITY = frozenset({
    "content_mismatch", "edit_not_present", "missing_after_write",
    "mtime_after_last_write",
})


@dataclass
class OutOfBand:
    artifact_id: str
    path: str
    reason: str
    detail: str
    mtime: float | None = None
    last_write_ts: str | None = None
    last_write_session: str | None = None
    last_write_locator: str | None = None

    @property
    def severity(self) -> str:
        return "high" if self.reason in HIGH_SEVERITY else "info"

    def to_dict(self) -> dict:
        return {
            "artifactId": self.artifact_id, "path": self.path, "reason": self.reason,
            "severity": self.severity,
            "detail": self.detail, "mtime": self.mtime,
            "lastWriteTs": self.last_write_ts, "lastWriteSessionId": self.last_write_session,
            "lastWriteLocator": self.last_write_locator,
        }


def corroborate(mutations: list[Mutation], sessions, classifier: PathClassifier) -> list[dict]:
    """Cross-check the feed against Claude Code's own ``file-history-delta`` markers.

    Returns the learned-state history events; each mutation within 10 minutes of
    one for the same path is marked ``corroborated``.
    """
    events: list[dict] = []
    for sess in sessions:
        for ev in sess.file_history:
            pc = classifier.classify(str(ev.get("path") or ""), sess.cwd)
            if pc is None:
                continue
            events.append({**ev, "artifactId": pc.artifact_id,
                           "sessionId": sess.session_id, "path": pc.path})
    by_path: dict[str, list] = {}
    for ev in events:
        by_path.setdefault(os.path.normpath(ev["path"]), []).append(ev)
    for m in mutations:
        for ev in by_path.get(os.path.normpath(m.path), ()):
            a, b = parse_ts(m.ts), parse_ts(ev.get("ts"))
            if a is not None and b is not None and abs((a - b).total_seconds()) <= 600:
                m.corroborated = True
                break
    return events


def detect_out_of_band(artifacts, mutations: list[Mutation],
                       tolerance_s: int = MTIME_TOLERANCE_S,
                       history_events: list[dict] | None = None) -> list[OutOfBand]:
    """Find learned-state files whose on-disk state no session accounts for."""
    by_path: dict[str, list[Mutation]] = {}
    for m in mutations:
        by_path.setdefault(os.path.normpath(m.path), []).append(m)
    hist_paths = {os.path.normpath(e["path"]) for e in (history_events or [])}

    findings: list[OutOfBand] = []
    for art in artifacts:
        path = os.path.normpath(art.path)
        muts = sorted(by_path.get(path, []), key=lambda m: (m.ts or "", m.line_no))
        if not art.exists:
            if muts and muts[-1].change_type != "delete":
                last = muts[-1]
                findings.append(OutOfBand(
                    art.id, art.path, "missing_after_write",
                    "file was written by a recorded session but is gone from disk",
                    art.mtime, last.ts, last.session_id, last.locator()))
            continue
        if not muts:
            if path in hist_paths:
                findings.append(OutOfBand(
                    art.id, art.path, "harness_recorded_write_not_attributed",
                    "Claude Code recorded a file-history backup for this file but no "
                    "tool call in the scanned transcripts explains it",
                    art.mtime))
            else:
                findings.append(OutOfBand(
                    art.id, art.path, "no_attributed_write",
                    "exists on disk but no scanned session wrote it — actor unknown "
                    "(hand edit, unscanned session, or a tool this scan cannot see)",
                    art.mtime))
            continue

        last = muts[-1]
        content = art.content or ""
        norm_disk = normalize(content)
        if last.tool == "Write" and isinstance(last.after_full, str):
            if normalize(last.after_full) != norm_disk:
                _, body_written = strip_frontmatter(last.after_full)
                _, body_disk = strip_frontmatter(content)
                if body_written.strip() and normalize(body_written) == normalize(body_disk):
                    findings.append(OutOfBand(
                        art.id, art.path, "frontmatter_stamped_by_harness",
                        "body matches the last attributed Write; only the YAML "
                        "frontmatter differs — Claude Code stamps node_type / "
                        "originSessionId / modified after the write",
                        art.mtime, last.ts, last.session_id, last.locator()))
                else:
                    findings.append(OutOfBand(
                        art.id, art.path, "content_mismatch",
                        "on-disk content differs from the last attributed Write",
                        art.mtime, last.ts, last.session_id, last.locator()))
                continue
        elif last.tool in ("Edit", "MultiEdit") and isinstance(last.edit_new, str) and last.edit_new.strip():
            if normalize(last.edit_new) not in norm_disk:
                findings.append(OutOfBand(
                    art.id, art.path, "edit_not_present",
                    "the last attributed Edit's new text is not in the file any more",
                    art.mtime, last.ts, last.session_id, last.locator()))
                continue

        last_dt = parse_ts(last.ts)
        if last_dt is not None and art.mtime is not None:
            from datetime import datetime, timezone
            mtime_dt = datetime.fromtimestamp(art.mtime, tz=timezone.utc)
            if mtime_dt > last_dt + timedelta(seconds=tolerance_s):
                findings.append(OutOfBand(
                    art.id, art.path, "mtime_after_last_write",
                    f"mtime {mtime_dt.isoformat()} is later than the last attributed "
                    f"write at {last.ts}",
                    art.mtime, last.ts, last.session_id, last.locator()))
    return findings
