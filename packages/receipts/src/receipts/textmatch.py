# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Whitespace-normalized containment tests.

The only question this module answers is: *does this artifact's text actually
appear in what the model was shown?*  Everything is compared after collapsing
runs of whitespace, because the renderer reflows and re-indents content on the
way into the system prompt.

Three increasingly forgiving tests, in the order they are applied:

1. exact  — the whole normalized artifact body is a substring of the context.
2. sections — the body is split into paragraph/heading sections and each is
   looked up independently.  A partial hit is the signature of *truncation*.
3. lcs — a >=200 character longest-common-substring test (implemented as a
   rolling-hash k-gram intersection), used as a last resort so that heavily
   reflowed or partially quoted content is still reported as "something got
   through" rather than "nothing loaded".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator

__all__ = [
    "LCS_MIN_CHARS",
    "Coverage",
    "Haystack",
    "classify_lines",
    "classify_text",
    "normalize",
    "sections",
    "strip_frontmatter",
    "tokenize",
    "jaccard",
]

LCS_MIN_CHARS = 200
"""Length of the common substring that counts as 'part of this loaded'."""

_WS = re.compile(r"\s+")
_PARA = re.compile(r"\n\s*\n")
_HEADING = re.compile(r"^#{1,6}\s")
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)

_MOD = (1 << 61) - 1
_BASE = 1_000_003


def normalize(text: str | None) -> str:
    """Collapse every whitespace run to a single space and strip the ends."""
    if not text:
        return ""
    return _WS.sub(" ", text).strip()


def strip_frontmatter(text: str) -> tuple[str, str]:
    """Split a leading ``---`` YAML frontmatter block off ``text``.

    Returns ``(frontmatter_text, body)``; ``frontmatter_text`` is ``""`` when
    there is none.  Frontmatter is stripped before load comparisons because the
    renderer generally does not echo it into context.
    """
    m = _FRONTMATTER.match(text or "")
    if not m:
        return "", text or ""
    return m.group(1), (text or "")[m.end():]


def sections(text: str, min_chars: int = 40) -> list[str]:
    """Split ``text`` into comparable chunks (paragraphs, split at headings).

    Chunks whose normalized form is shorter than ``min_chars`` are dropped as
    noise (rule lines, stray punctuation) unless that would drop everything, in
    which case the short chunks are kept so a small artifact is still testable.
    """
    raw = text or ""
    chunks: list[str] = []
    for para in _PARA.split(raw):
        buf: list[str] = []
        for line in para.splitlines():
            if _HEADING.match(line.strip()) and buf:
                chunks.append("\n".join(buf))
                buf = [line]
            else:
                buf.append(line)
        if buf:
            chunks.append("\n".join(buf))
    norm = [normalize(c) for c in chunks]
    norm = [c for c in norm if c]
    kept = [c for c in norm if len(c) >= min_chars]
    return kept or norm


def _rolling_hashes(s: str, k: int) -> Iterator[tuple[int, int]]:
    n = len(s)
    if n < k:
        return
    h = 0
    for i in range(k):
        h = (h * _BASE + ord(s[i])) % _MOD
    power = pow(_BASE, k - 1, _MOD)
    yield 0, h
    for i in range(k, n):
        h = (h - ord(s[i - k]) * power) % _MOD
        h = (h * _BASE + ord(s[i])) % _MOD
        yield i - k + 1, h


class Haystack:
    """A normalized blob of context text with a cached k-gram index."""

    __slots__ = ("norm", "_grams", "_k")

    def __init__(self, text: str, k: int = LCS_MIN_CHARS) -> None:
        self.norm = normalize(text)
        self._k = k
        self._grams: set[int] | None = None

    def __len__(self) -> int:
        return len(self.norm)

    def contains(self, needle_norm: str) -> bool:
        return bool(needle_norm) and needle_norm in self.norm

    def _gram_set(self) -> set[int]:
        if self._grams is None:
            self._grams = {h for _, h in _rolling_hashes(self.norm, self._k)}
        return self._grams

    def has_common_substring(self, needle_norm: str) -> bool:
        """True when a common substring of >= ``k`` characters exists."""
        k = self._k
        if len(needle_norm) < k or len(self.norm) < k:
            return False
        grams = self._gram_set()
        for pos, h in _rolling_hashes(needle_norm, k):
            if h in grams and needle_norm[pos:pos + k] in self.norm:
                return True
        return False


@dataclass
class Coverage:
    """Result of testing one artifact body against one context blob."""

    status: str  # loaded_complete | loaded_truncated | not_loaded
    method: str  # exact | sections | lcs | lines | none
    units_total: int = 0
    units_found: int = 0
    chars_total: int = 0
    chars_found: int = 0
    missing: list[str] = field(default_factory=list)

    @property
    def fraction(self) -> float:
        if not self.chars_total:
            return 0.0
        return self.chars_found / self.chars_total

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "method": self.method,
            "unitsTotal": self.units_total,
            "unitsFound": self.units_found,
            "charsTotal": self.chars_total,
            "charsFound": self.chars_found,
            "fraction": round(self.fraction, 4),
            "missing": self.missing[:5],
        }


_COMPLETE_FRACTION = 0.95


def classify_text(content: str, hay: Haystack, min_section_chars: int = 40) -> Coverage:
    """Decide whether ``content`` reached the context represented by ``hay``."""
    body_norm = normalize(content)
    if not body_norm:
        return Coverage("not_loaded", "none")
    if hay.contains(body_norm):
        n = len(body_norm)
        return Coverage("loaded_complete", "exact", 1, 1, n, n)

    secs = sections(content, min_chars=min_section_chars)
    total_chars = sum(len(s) for s in secs)
    found = [s for s in secs if hay.contains(s)]
    found_chars = sum(len(s) for s in found)
    missing = [s for s in secs if not hay.contains(s)]
    if found:
        frac = found_chars / total_chars if total_chars else 0.0
        status = "loaded_complete" if frac >= _COMPLETE_FRACTION else "loaded_truncated"
        return Coverage(
            status, "sections", len(secs), len(found), total_chars, found_chars,
            [m[:160] for m in missing],
        )

    if hay.has_common_substring(body_norm):
        return Coverage(
            "loaded_truncated", "lcs", len(secs), 0, len(body_norm), LCS_MIN_CHARS,
            [m[:160] for m in missing[:3]],
        )
    return Coverage("not_loaded", "sections", len(secs), 0, total_chars, 0,
                    [m[:160] for m in missing[:3]])


def classify_lines(lines: Iterable[tuple[int, str]], hay: Haystack) -> tuple[Coverage, list[tuple[int, str]]]:
    """Line-by-line coverage, used for the MEMORY.md index.

    ``lines`` is an iterable of ``(line_number, raw_line)``.  Returns the
    coverage plus the list of ``(line_number, raw_line)`` that did NOT make it
    into the context — the concrete thing a user greps for after filing
    "my newest memory entry silently vanished".
    """
    items = [(no, raw) for no, raw in lines if normalize(raw)]
    if not items:
        return Coverage("not_loaded", "lines"), []
    missing: list[tuple[int, str]] = []
    found_chars = 0
    total_chars = 0
    n_found = 0
    for no, raw in items:
        n = normalize(raw)
        total_chars += len(n)
        if hay.contains(n):
            n_found += 1
            found_chars += len(n)
        else:
            missing.append((no, raw))
    if n_found == len(items):
        status = "loaded_complete"
    elif n_found == 0:
        status = "not_loaded"
    else:
        status = "loaded_truncated"
    cov = Coverage(status, "lines", len(items), n_found, total_chars, found_chars,
                   [f"L{no}: {raw.strip()[:140]}" for no, raw in missing[:5]])
    return cov, missing


_TOKEN = re.compile(r"[A-Za-z0-9_]+|[㐀-䶿一-鿿]")


def tokenize(text: str | None) -> set[str]:
    """Lowercased ASCII word runs plus individual CJK characters."""
    if not text:
        return set()
    return {t.lower() for t in _TOKEN.findall(text)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)
