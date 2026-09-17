# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""SHARE — the one-screen card you can paste into an issue without leaking.

``precedent report`` is a full digest with quotes, paths and rule bodies in it;
it is for you.  This module is the other thing people actually do with an audit
tool: they want to show somebody the four numbers that made them look twice.

So ``precedent audit --share`` prints a card built to be pasteable, and the
anonymisation is **structural, not a filter**:

**The card is built from counts, never from text.**  :func:`build_card` returns
integers, booleans, and strings drawn from three closed vocabularies — the five
artifact kinds receipts knows, the five alarm codes ``audit.py`` can raise, and
the labels this module invents (``project-A``, ``s1``).  No transcript text, no
path, no artifact name and no id is ever *put into* the card, so there is
nothing for a redaction pass to miss.  A redaction pass can only be as good as
its patterns; a card with no free-form strings in it cannot leak a shape nobody
thought of.

**The names are replaced, not dropped.**  A card that said "3 projects" and
stopped would be unreadable.  Projects become ``project-A``, ``project-B`` … in
scan order, sessions become ``s1``, ``s2`` … in start order, so the reader can
still see that all eleven never-cited artifacts sit in one project and that the
truncation happens in one session.  The mapping exists only in memory; the
local (non-``--share``) view prints it, which is what makes the anonymised view
useful rather than mysterious.

**And then it is checked anyway.**  :func:`leaks` runs the finished card back
through :mod:`precedent.scrub` — the same DLP pass every quote goes through —
and adds the three shapes scrub deliberately *allowlists* because they are the
tool's own vocabulary elsewhere: session UUIDs, absolute paths, and long hex
runs.  Finally it is handed the actual secrets from this machine (the Claude
home, the state dir, every project slug, every session id, every artifact path
and name) and asserts none of them is a substring of the card.  ``--share``
runs this by default and **refuses to print** if anything survives, because a
sharing feature that leaks on an unforeseen input is worse than no sharing
feature.
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from receipts.transcripts import parse_ts

from . import SCHEMA_VERSION, __version__
from . import i18n
from .i18n import t
from .scrub import findings as scrub_findings

__all__ = [
    "CARD_ID", "ALARM_CODES", "ARTIFACT_KINDS", "SCHEMA_FIELDS", "build_card",
    "card_chars", "card_words", "foreign_tokens", "leaks", "project_labels",
    "render_card", "secrets_of", "session_labels", "shareable", "verify_checks",
]

#: The card's own schema id, so a pasted card can be identified years later.
CARD_ID = "precedent/audit-share/v1"

#: The only artifact kinds that may appear in a card.  Anything receipts grows
#: later shows up as ``other`` rather than as a new free-form string.
ARTIFACT_KINDS = ("skill", "memory", "memory_index", "claude_md", "rule")

#: The only alarm codes that may appear in a card (see :mod:`precedent.audit`).
ALARM_CODES = ("PENDING", "HOOK", "NOT_INSTALLED", "NO_FIRES", "DRIFT")

_KIND_LABEL = {"skill": "skill", "memory": "memory", "memory_index": "MEMORY.md",
               "claude_md": "CLAUDE.md", "rule": "rule", "other": "other"}

_WINDOW_DAYS = 7


# --------------------------------------------------------------------------
# the label alphabets
# --------------------------------------------------------------------------

def _letters(n: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA — a label alphabet with no upper bound."""
    out = ""
    n += 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def project_labels(slugs) -> dict:
    """``{slug: 'project-A'}`` in the order given (receipts yields scan order).

    ``None`` — a global artifact that belongs to no project — maps to
    ``global``, which is a category, not a name.
    """
    out: dict = {}
    i = 0
    for slug in slugs:
        if slug in out:
            continue
        if slug is None:
            out[None] = "global"
            continue
        out[slug] = f"project-{_letters(i)}"
        i += 1
    return out


def session_labels(session_ids) -> dict:
    """``{session id: 's1'}`` in the order given (receipts yields start order)."""
    out: dict = {}
    for sid in session_ids:
        if sid not in out:
            out[sid] = f"s{len(out) + 1}"
    return out


# --------------------------------------------------------------------------
# the card
# --------------------------------------------------------------------------

def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.0f}%" if d else "n/a"


def build_card(scan_result, state, precedents=None, now: datetime | None = None,
               days: int = _WINDOW_DAYS, settings_path: str | None = None) -> dict:
    """The counts-only card, plus a ``local`` block ``--share`` drops.

    Everything outside ``local`` is an int, a bool, or a member of one of the
    three closed vocabularies at the top of this module.
    """
    from .audit import funnel, hook_health
    from .docket import build_entries
    from .hooks import status as hooks_status_fn
    from .report import unattended_writes

    now = now or datetime.now(timezone.utc)
    rows = list(scan_result.funnel)
    precedents = state.precedents() if precedents is None else precedents
    active = [r for r in precedents if r.get("status", "active") == "active"]

    plabels = project_labels(
        list(scan_result.project_slugs)
        + [r.project_slug for r in rows if r.project_slug is not None])
    slabels = session_labels(
        [s.session_id for s in scan_result.session_receipts])

    never = [r for r in rows if r.never_cited and r.exists]
    citable = [r for r in rows if r.citable and r.exists]
    trunc = [r for r in rows if r.n_sessions_truncated]
    stale = [r for r in rows if not r.exists]
    dupes = [r for r in rows if r.near_duplicates]

    by_kind: dict[str, int] = {}
    for r in rows:
        key = r.kind if r.kind in ARTIFACT_KINDS else "other"
        by_kind[key] = by_kind.get(key, 0) + 1

    # which sessions the truncation happened in, as labels
    trunc_sessions = sorted(
        {slabels[s.session_id]
         for s in scan_result.session_receipts
         for a in s.artifacts
         if str(getattr(a, "status", "")) == "loaded_truncated"
         and s.session_id in slabels},
        key=lambda x: int(x[1:]))

    uw = unattended_writes(scan_result, now=now, days=days)
    cutoff = now - timedelta(days=days)
    uw_sessions = sorted(
        {slabels[m.session_id] for m in scan_result.mutations
         if m.session_id in slabels
         and (parse_ts(m.ts) or cutoff) >= cutoff},
        key=lambda x: int(x[1:]))

    entries = build_entries(state, now=now, include_decided=False)
    pending = [e for e in entries if e["status"] == "pending"]
    hstatus = hooks_status_fn(state, active,
                              settings_path or f"{state.claude_home}/settings.json")
    health = hook_health(state, now=now)
    fun = funnel(state, entries, hstatus, health=health, now=now)

    from .audit import alarms as alarm_rows_fn
    codes = sorted({a["code"] for a in
                    alarm_rows_fn(state, entries, fun, health, hstatus, now=now)
                    if a["code"] in ALARM_CODES})

    # per project
    per: dict = {}
    for r in rows:
        label = plabels.get(r.project_slug, "global")
        p = per.setdefault(label, {"label": label, "artifacts": 0,
                                   "neverCited": 0, "sessions": 0})
        p["artifacts"] += 1
        if r.never_cited and r.exists:
            p["neverCited"] += 1
    sess_per: dict = {}
    for s in scan_result.session_receipts:
        label = plabels.get(getattr(s, "project_slug", None), "global")
        sess_per[label] = sess_per.get(label, 0) + 1
    for label, n in sess_per.items():
        per.setdefault(label, {"label": label, "artifacts": 0, "neverCited": 0,
                               "sessions": 0})["sessions"] = n
    by_project = sorted(per.values(),
                        key=lambda p: (p["label"] == "global", -p["artifacts"],
                                       p["label"]))

    card = {
        "schemaVersion": SCHEMA_VERSION,
        "card": CARD_ID,
        "tool": {"name": "precedent", "version": __version__},
        "generatedOn": now.astimezone(timezone.utc).date().isoformat(),
        "corpus": {
            "sessions": int(scan_result.sessions_scanned),
            "sessionsFound": int(scan_result.sessions_found),
            "projects": len([k for k in plabels if k is not None]),
            "artifacts": len(rows),
            "citable": len(citable),
            "byKind": {k: by_kind[k] for k in sorted(by_kind)},
        },
        "findings": {
            "neverCited": {"n": len(never), "of": len(citable),
                           "pct": _pct(len(never), len(citable))},
            "unattendedWrites": {"days": int(days), "n": int(uw["total"]),
                                 "bypass": int(uw["bash_bypass"]),
                                 "subagent": int(uw["sidechain"]),
                                 "sessions": uw_sessions},
            "truncated": {"n": len(trunc), "sessions": len(trunc_sessions),
                          "sessionLabels": trunc_sessions},
            "staleIndex": {"n": len(stale)},
            "nearDuplicates": {"n": len(dupes),
                               "pairs": sum(len(r.near_duplicates)
                                            for r in dupes) // 2},
        },
        "enforcement": {
            "active": len(active),
            "activated": len(fun["activated"]),
            "fired": len(fun["attributed"]),
            "docketPending": len(pending),
            "installed": bool(fun["installed"]),
        },
        "alarms": codes,
        "byProject": by_project,
        # not part of the shareable card: dropped by --share, printed locally
        "local": {
            "claudeHome": scan_result.claude_home,
            "stateDir": state.root,
            "projects": [{"label": lbl, "slug": slug}
                         for slug, lbl in plabels.items() if slug is not None],
        },
    }
    return card


def shareable(card: dict) -> dict:
    """The card with the ``local`` block removed — what ``--share`` emits."""
    return {k: v for k, v in card.items() if k != "local"}


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

_RULE = "─" * 68


def render_card(card: dict, lang: str | None = None, local: bool = False,
                verified: int | None = None) -> str:
    """The one-screen card.  ``local=True`` adds the block ``--share`` drops."""
    lang = lang or i18n.get_lang()
    c, f, e = card["corpus"], card["findings"], card["enforcement"]
    mix = " / ".join(f"{_KIND_LABEL.get(k, k)} {v}"
                     for k, v in sorted(c["byKind"].items(),
                                        key=lambda kv: (-kv[1], kv[0])))
    labels = ["never_cited", "unattended", "truncated", "stale", "duplicates",
              "enforced"]
    width = max(i18n.display_width(t(f"audit.label.{k}", lang)) for k in labels)

    def row(key: str, value: str) -> str:
        return f"  {i18n.pad(t(f'audit.label.{key}', lang), width)}  {value}"

    where = ""
    if f["truncated"]["sessionLabels"]:
        where = t("audit.in_sessions", lang,
                  labels=", ".join(f["truncated"]["sessionLabels"]))

    heads = ["corpus", "alarms", "reproduce", "verified"]
    hw = max(i18n.display_width(t(f"audit.label.{k}", lang)) for k in heads)

    def head(key: str, value: str) -> str:
        return f"{i18n.pad(t('audit.label.' + key, lang), hw)}: {value}"

    L = [t("audit.title", lang), _RULE,
         head("corpus", t("audit.corpus", lang, sessions=c["sessions"],
                          projects=c["projects"], artifacts=c["artifacts"],
                          session_w=i18n.plural(c["sessions"], "session", lang),
                          project_w=i18n.plural(c["projects"], "project", lang),
                          learned_w=i18n.plural(c["artifacts"],
                                                "learned_artifact", lang)))]
    if mix:
        L.append(" " * (hw + 2) + mix)
    L.append("")
    L.append(row("never_cited", t("audit.value.never_cited", lang,
                                  n=f["neverCited"]["n"], of=f["neverCited"]["of"],
                                  pct=f["neverCited"]["pct"])))
    L.append(row("unattended", t("audit.value.unattended", lang,
                                 n=f["unattendedWrites"]["n"],
                                 days=f["unattendedWrites"]["days"],
                                 bypass=f["unattendedWrites"]["bypass"],
                                 sub=f["unattendedWrites"]["subagent"])))
    L.append(row("truncated", t(
        "audit.value.truncated", lang, n=f["truncated"]["n"],
        sessions=f["truncated"]["sessions"], where=where,
        file_w=i18n.plural(f["truncated"]["n"], "instruction_file", lang),
        session_w=i18n.plural(f["truncated"]["sessions"], "session", lang))))
    L.append(row("stale", t(
        "audit.value.stale", lang, n=f["staleIndex"]["n"],
        entry_w=i18n.plural(f["staleIndex"]["n"], "index_entry", lang))))
    L.append(row("duplicates", t(
        "audit.value.duplicates", lang, n=f["nearDuplicates"]["n"],
        pairs=f["nearDuplicates"]["pairs"],
        artifact_w=i18n.plural(f["nearDuplicates"]["n"], "artifact", lang),
        pair_w=i18n.plural(f["nearDuplicates"]["pairs"], "pair", lang))))
    L.append(row("enforced", t(
        "audit.value.enforced", lang, active=e["active"],
        activated=e["activated"], fired=e["fired"], pending=e["docketPending"],
        precedent_w=i18n.plural(e["active"], "precedent", lang))))
    L.append("")
    if card["byProject"]:
        L.append(t("audit.byproject", lang))
        for p in card["byProject"]:
            L.append(t("audit.byproject.row", lang, label=i18n.pad(p["label"], 11),
                       artifacts=p["artifacts"], never=p["neverCited"],
                       sessions=p["sessions"],
                       artifact_w=i18n.plural(p["artifacts"], "artifact", lang),
                       session_w=i18n.plural(p["sessions"], "session", lang)))
        L.append("")
    L.append(head("alarms",
                  t("audit.alarms", lang, codes=" ".join(card["alarms"]))
                  if card["alarms"] else t("audit.alarms.none", lang)))
    L.append(_RULE)
    L.append(t("audit.footer.local" if local else "audit.footer", lang))
    L.append(head("reproduce",
                  t("audit.reproduce", lang, version=card["tool"]["version"])))
    if verified is not None:
        L.append(head("verified", t("audit.verified", lang, n=verified,
                                    langs=len(i18n.LANGS))))
    if local:
        loc = card.get("local") or {}
        L.append("")
        L.append(t("audit.local.head", lang))
        L.append(t("audit.local.home", lang, path=loc.get("claudeHome", "")))
        L.append(t("audit.local.state", lang, path=loc.get("stateDir", "")))
        for p in loc.get("projects", []):
            L.append(t("audit.local.project", lang, label=p["label"], slug=p["slug"]))
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

#: Shapes :mod:`precedent.scrub` deliberately allowlists (they are the tool's
#: own vocabulary in a *report*) but which have no business in a *card*.
_CARD_RULES = [
    ("session_uuid",
     r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
    ("home_path", r"~/[^\s`'\"]+"),
    ("abs_path", r"(?<![\w.])/[A-Za-z0-9._][A-Za-z0-9._-]*(?:/[A-Za-z0-9._-]+)+"),
    ("win_path", r"(?<![A-Za-z])[A-Za-z]:\\[^\s]+"),
    ("hex_run", r"(?<![A-Za-z0-9])[0-9a-fA-F]{8,}(?![A-Za-z0-9])"),
    ("rule_id", r"(?<![A-Za-z0-9])[pt]-[0-9a-f]{8}(?![0-9a-z])"),
]
_CARD_RX = re.compile("|".join(f"(?P<{n}>{p})" for n, p in _CARD_RULES))

# --------------------------------------------------------------------------
# the card's own closed vocabulary, derived from the string table
# --------------------------------------------------------------------------
#
# The three passes above are *blocklists*: they look for shapes known to be
# dangerous.  A blocklist can only fail one way — quietly — so the card also
# gets the opposite check, an allowlist.  Every word and every non-ASCII letter
# the card is permitted to contain comes from exactly four places: the ``audit.``
# rows of :data:`precedent.i18n.STRINGS` that ``--share`` renders, the counted
# nouns in :data:`precedent.i18n.PLURALS`, the two closed code vocabularies at
# the top of this module, and the label alphabets (``project-A``, ``s1``).
# Anything else in a rendered card is, by construction, free-form text that got
# in — which is the failure this whole module exists to prevent.
#
# Deriving it from the table rather than hand-listing it is the point: a string
# id added to ``i18n`` widens the vocabulary automatically, and a *value* that
# reached the card does not.

_WORD_RX = re.compile(r"[A-Za-z][A-Za-z0-9_.'-]*")

#: What a path is built out of.  The coarse split keeps a hyphenated name
#: whole (``…/_workspaces/northwind-treasury`` -> ``northwind-treasury``);
#: the fine one goes on to the identifier parts (``northwind``, ``treasury``),
#: because a leak shows whichever of the two the renderer happened to hold.
_PATH_RX = re.compile(r"[/\\\s]+")
_SEGMENT_RX = re.compile(r"[/\\\-_.\s]+")
_PLACEHOLDER_RX = re.compile(r"\{[^{}]*\}")


def _norm(word: str) -> str:
    """Lowercase, minus the sentence punctuation a word can end a clause with.

    ``ids.`` and ``ids`` are the same word; ``CLAUDE.md`` and ``precedent.scrub``
    keep their dots because the dot is not at the end.
    """
    return word.rstrip(".-'").lower()


#: ``audit.`` ids that ``--share`` never renders: the local-only block (which
#: does print paths, on purpose) and the stderr refusal.  Excluded so their
#: words — "Claude", "home", "state" — do not silently widen the allowlist.
_NOT_SHAREABLE = (".local", ".refused")

#: Every key name in the card's JSON.  The JSON is verified too, and a field
#: name is schema rather than content; a test asserts a built card uses no key
#: outside this set, so the list cannot drift away from the shape.
SCHEMA_FIELDS = frozenset([
    "schemaVersion", "card", "tool", "name", "version", "generatedOn",
    "corpus", "sessions", "sessionsFound", "projects", "artifacts", "citable",
    "byKind", "findings", "neverCited", "n", "of", "pct", "unattendedWrites",
    "days", "bypass", "subagent", "truncated", "sessionLabels", "staleIndex",
    "nearDuplicates", "pairs", "enforcement", "active", "activated", "fired",
    "docketPending", "installed", "alarms", "byProject", "label",
    "local", "claudeHome", "stateDir", "slug",
])


def _is_letter(ch: str) -> bool:
    """A non-ASCII letter — the alphabet a word-based check cannot see."""
    return ord(ch) > 127 and unicodedata.category(ch).startswith("L")


def _table_rows(lang: str):
    from . import i18n as _i18n
    for key, row in _i18n.STRINGS.items():
        if not key.startswith("audit."):
            continue
        if any(skip in key for skip in _NOT_SHAREABLE):
            continue
        yield _PLACEHOLDER_RX.sub(" ", row.get(lang, ""))
    for forms in _i18n.PLURALS.values():
        for form in forms.get(lang, ()):
            yield form


def card_words(lang: str | None = None, schema: bool = False) -> frozenset:
    """Every lowercase word a rendered card may contain, in ``lang``.

    ``lang=None`` means "in any language we ship", which is what the secret
    filter wants: a project named ``memory`` is not a leak in either.
    ``schema=True`` adds the JSON field names, which are schema rather than
    content and so are not evidence of a leak either — but which must *not*
    widen the allowlist the rendered text is checked against.
    """
    from . import i18n as _i18n
    langs = _i18n.LANGS if lang is None else (lang,)
    out: set[str] = set()
    for one in langs:
        for text in _table_rows(one):
            out.update(_norm(w) for w in _WORD_RX.findall(text))
    out.update(k.lower() for k in ARTIFACT_KINDS)
    out.update(c.lower() for c in ALARM_CODES)
    out.update(v.lower() for v in _KIND_LABEL.values())
    if schema:
        out.update(f.lower() for f in SCHEMA_FIELDS)
    # the label alphabets, the tool's own name, and the two one-letter words
    # "n/a" tokenises into
    out.update(["precedent", "project", "global", "other", "n", "a", "s"])
    return frozenset(out)


def card_chars(lang: str | None = None) -> frozenset:
    """Every non-ASCII letter a rendered card may contain, in ``lang``."""
    from . import i18n as _i18n
    langs = _i18n.LANGS if lang is None else (lang,)
    out: set[str] = set()
    for one in langs:
        for text in _table_rows(one):
            out.update(ch for ch in text if _is_letter(ch))
    return frozenset(out)


#: ``project-A``, ``project-AB``, ``s1`` — positional labels, and the version.
_LABEL_RX = re.compile(r"^(?:project-[A-Z]+|s[0-9]+|v[0-9]+)$")


def foreign_tokens(text: str, lang: str | None = None) -> list[dict]:
    """Everything in ``text`` that is not in the card's own vocabulary.

    The complement of :func:`leaks`: that one asks "does this look like a
    secret?", this one asks "is this one of the ~200 words the card is allowed
    to say?".  A leak has to defeat both, and the shapes they miss do not
    overlap — a customer name in an alphabet nobody wrote a regex for is
    invisible to the first check and obvious to this one.

    It reads *words*, not numbers: a card is mostly integers and there is no
    allowlist for those.  Numeric shapes are the blocklist's job (a phone
    number and a long hex run are both :func:`leaks` findings), which is why
    both checks run.
    """
    allowed_words = card_words(lang)
    allowed_chars = card_chars(lang)
    out: list[dict] = []
    for m in _WORD_RX.finditer(text):
        word = m.group(0)
        if _LABEL_RX.match(word) or _norm(word) in allowed_words:
            continue
        out.append({"kind": "foreign_word", "rule": "vocabulary",
                    "start": m.start(), "match": word})
    for i, ch in enumerate(text):
        if _is_letter(ch) and ch not in allowed_chars:
            out.append({"kind": "foreign_letter", "rule": "vocabulary",
                        "start": i, "match": ch})
    out.sort(key=lambda f: f["start"])
    return out


#: For splitting the card's own words into fragments.  Deliberately *every*
#: non-alphanumeric character, not the path separators: ``machine's`` has to
#: yield ``machine`` or a user working under a directory called ``machine``
#: gets their card refused by the receipt line's own possessive.  That is not a
#: hypothetical — it is how this function got written.
_FRAGMENT_RX = re.compile(r"[^0-9A-Za-z]+")


def _with_fragments(words) -> frozenset:
    """``{"claude.md", "machine's"}`` -> also ``claude``, ``md``, ``machine``…

    The secret filter needs the *fragments* of the card's own words, not just
    the words: the card prints ``CLAUDE.md`` as the name of a kind, so a home
    directory called ``claude-home`` contributes the fragment ``claude``, which
    would then be "found" inside the card's own legend.  A control that refuses
    every card on every machine is a control that gets turned off.
    """
    out = set(words)
    for word in list(words):
        for part in _FRAGMENT_RX.split(word):
            if len(part) >= 2:
                out.add(part.lower())
    return frozenset(out)


#: Words that are part of the card's own fixed vocabulary, so a project slug or
#: artifact name that happens to equal one of them is not evidence of a leak.
_VOCABULARY = _with_fragments(card_words(None, schema=True))

#: Shortest string worth testing for literally.  A three-character project name
#: is a substring of half the schema and would refuse every card.
_MIN_SECRET = 4

#: …and a *derived* fragment — one path or slug component, split out of a
#: longer secret — has to be longer still before it earns a literal test.
_MIN_SEGMENT = 6

#: Below this length a secret is matched only at a word boundary.  ``kind`` as
#: a bare substring lives inside ``byKind``; as a word it does not.
_LOOSE_MATCH_MIN = 12


def secrets_of(scan_result, state=None) -> list[str]:
    """Every string from this machine that must not be in the card.

    The Claude home, the state dir, each project slug, each session id, each
    session cwd, each artifact path and each artifact name — **and each of
    their components**.  The components matter because a leak is rarely a whole
    path: a renderer that printed a project's *name* rather than its slug would
    emit ``northwind-treasury``, which is not a substring test the full slug
    ``-Users-jdoe-src-northwind-treasury`` would ever fail.  So the slug is
    split on the separators that build it and every part long enough to mean
    something is tested for on its own.

    Short strings and the card's own vocabulary are dropped, so the membership
    test cannot cry wolf on ``skill``, on a two-letter project, or on the word
    ``memory`` in someone's directory name.
    """
    out: set[str] = set()

    def add(value, minimum: int = _MIN_SECRET) -> None:
        if not isinstance(value, str):
            return
        value = value.strip()
        if len(value) >= minimum and _norm(value) not in _VOCABULARY:
            out.add(value)

    def add_parts(value) -> None:
        """The whole string, its path components, and their identifier parts."""
        add(value)
        if isinstance(value, str):
            for part in _PATH_RX.split(value):
                add(part, minimum=_MIN_SEGMENT)
            for part in _SEGMENT_RX.split(value):
                add(part, minimum=_MIN_SEGMENT)

    add_parts(getattr(scan_result, "claude_home", None))
    if state is not None:
        add_parts(getattr(state, "root", None))
        add_parts(getattr(state, "claude_home", None))
    for slug in getattr(scan_result, "project_slugs", []):
        add_parts(slug)
    for _slug, cwd in (getattr(scan_result, "cwds", {}) or {}).items():
        add_parts(cwd)
    for sess in getattr(scan_result, "session_receipts", []):
        add(getattr(sess, "session_id", None))
    for r in getattr(scan_result, "funnel", []):
        add_parts(getattr(r, "path", None))
        add_parts(getattr(r, "name", None))
        add(getattr(r, "artifact_id", None))
    add(os.path.basename(os.path.expanduser("~")))
    return sorted(out)


def leaks(text: str, secrets=()) -> list[dict]:
    """Everything in ``text`` that must not have reached a shareable card.

    Three passes, deliberately overlapping:

    1. :func:`precedent.scrub.findings` — the same DLP rules every quote in
       every report goes through (emails, phone numbers, tokens, IPs, home
       directories).
    2. :data:`_CARD_RULES` — the shapes scrub *allowlists* elsewhere because
       they are the tool's own ids, and which a card must still never contain.
    3. the literal ``secrets`` from this machine.

    Each finding carries a ``kind`` and the offset; the *content* is included
    only for the caller's own logging, never for printing to a stranger.
    """
    out: list[dict] = []
    for f in scrub_findings(text):
        out.append({"kind": f["kind"], "rule": f["rule"], "start": f["start"],
                    "match": f["match"]})
    for m in _CARD_RX.finditer(text):
        out.append({"kind": m.lastgroup, "rule": m.lastgroup, "start": m.start(),
                    "match": m.group(0)})
    lowered = text.lower()
    for secret in secrets:
        needle = secret.lower()
        if len(needle) >= _LOOSE_MATCH_MIN:
            idx = lowered.find(needle)
        else:
            # A short secret is only a finding at a word boundary.  ``kind`` is
            # a substring of ``byKind`` and ``claude`` of ``claude_md``: matched
            # loosely, every card on every machine would be refused, and a check
            # that always fires is a check nobody keeps switched on.
            m = re.search(rf"(?<![0-9A-Za-z_]){re.escape(needle)}(?![0-9A-Za-z_])",
                          lowered)
            idx = m.start() if m else -1
        if idx >= 0:
            out.append({"kind": "literal", "rule": "machine_secret", "start": idx,
                        "match": secret})
    out.sort(key=lambda f: (f["start"], f["kind"]))
    return out


def verify_checks(langs=None) -> int:
    """How many independent checks the ``--share`` verification ran.

    The number on the card's last line, and it is an accounting, not a slogan::

        renderings    = one per language, plus the JSON
        per rendering = len(_CARD_RULES) shape patterns
                      + the precedent.scrub pass (one call, many rules, counted
                        once, because that is what the card can honestly say)
        plus          = one vocabulary check per *text* rendering (the JSON's
                        field names are schema, so the allowlist skips it)

    The literal :func:`secrets_of` tests are deliberately **not** in the count.
    There is one per component of every path on the machine, so counting them
    would make the printed card differ between two people running the same
    command on the same tree — and a receipt whose number moves with your
    directory layout is not a receipt.  The card's last line names them
    instead; :func:`secrets_of` is how you count them.
    """
    from . import i18n as _i18n
    langs = tuple(langs if langs is not None else _i18n.LANGS)
    per = len(_CARD_RULES) + 1
    return per * (len(langs) + 1) + len(langs)
