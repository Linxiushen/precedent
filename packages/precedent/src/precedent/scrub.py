# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""DLP — the deterministic scrub every quote and snippet passes through.

The miner's whole value is that it quotes you *verbatim*: "you said this, on
this date, in this session".  That is also its whole hazard.  Real transcripts
carry real phone numbers, real WeChat ids, real API keys — and `precedent mine`
/ `report` / `docket` copy them into markdown that people paste into issues and
commit to public repositories.  This happened: a phone number and a WeChat id
reached ``DEMO.md`` and had to be redacted by hand afterwards.  By hand, once,
is not a control.

So every quote, tool summary, revert command and rendered report goes through
:func:`scrub` first.  Four properties make it a control rather than a gesture:

**Deterministic.**  Pure regex, no model, no network, no clock.  The same text
always yields the same output, so a scrubbed report is reproducible and
diffable, and a test can assert a planted secret never appears.

**Applied at the source and at the sink.**  :mod:`precedent.mine` scrubs each
quote as it is built (so the JSON output and anything downstream inherits it)
*and* the markdown renderers scrub the finished document (so a field nobody
thought about — a path in a docket title, a candidate's message — is covered
too).  Two independent passes, because the one thing a redaction pass must not
have is a single point of failure.

**Allowlisted where an id is clearly not PII.**  Session UUIDs, ``p-…`` /
``t-…`` ids and arXiv numbers are the tool's own vocabulary; masking them would
make the output useless and teach people to turn the scrubber off.  They are
matched *first*, so no later pattern can claim them.

**Conservative where it is unsure.**  A 32-character hex or base64 run inside a
quote gets masked even though it might be a harmless digest: in quoted user
text the cost of masking a digest is a less pretty report, and the cost of not
masking a token is a live credential in a git history.

What it deliberately does not do
--------------------------------
It is not a classifier and does not pretend to catch everything: a name, an
address, a licence plate or an API key in a shape nobody has seen yet all pass
straight through.  It covers the shapes that are *mechanically recognisable*,
and :func:`findings` exists so a caller can show what was masked rather than
silently changing bytes.

Home paths are rewritten to ``~/`` rather than masked: ``~`` is still a working
path on the machine the report came from, so the output stays actionable while
the username stops travelling with it.
"""

from __future__ import annotations

import re

__all__ = ["PLACEHOLDERS", "findings", "scrub", "scrub_lines", "scrub_obj"]

#: What each rule replaces its match with.  ``None`` means "leave it alone"
#: (the allowlist); a callable gets the :class:`re.Match`.
PLACEHOLDERS = {
    "email": "[redacted:email]",
    "phone": "[redacted:phone]",
    "wechat": "[redacted:wechat-id]",
    "token": "[redacted:token]",
    "ip": "[redacted:ip]",
    "home": "~",
}

# --------------------------------------------------------------------------
# the rules, in priority order — the first alternative that matches at a given
# position wins, which is why the allowlist comes first
# --------------------------------------------------------------------------

_RULES: list[tuple[str, str]] = [
    # ---- allowlist: the tool's own identifiers, and citations -------------
    # a session id (UUID v4 as Claude Code writes it)
    ("keep_uuid",
     r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
    # precedent ids (p-1a2b3c4d) and topic ids (t-1a2b3c4d)
    ("keep_precedent_id", r"(?<![A-Za-z0-9])[pt]-[0-9a-f]{8}(?![0-9a-z])"),
    # arXiv identifiers, with or without the scheme and version suffix
    ("keep_arxiv", r"(?:arXiv:)?(?<![\d.])\d{4}\.\d{4,5}(?:v\d+)?(?![\d.])"),

    # ---- home directories: rewritten, not masked --------------------------
    ("home", r"(?<![A-Za-z0-9._~-])/(?:Users|home)/[A-Za-z0-9][A-Za-z0-9._-]*"),

    # ---- credentials ------------------------------------------------------
    ("token_sk", r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}"),
    ("token_gh", r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{16,}"),
    ("token_slack", r"(?<![A-Za-z0-9])xox[abprse]-[A-Za-z0-9-]{10,}"),
    ("token_aws", r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])"),

    # ---- contact details --------------------------------------------------
    ("email", r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}"
              r"(?:\.[A-Za-z0-9-]{1,63}){1,4}"),
    # WeChat / QQ ids only after the keyword that makes them identifiable: a
    # bare "linxiu_2024" is not recognisably an account, "微信 linxiu_2024" is.
    ("wechat",
     r"(?:微信(?:号)?|WeChat|wechat|WECHAT|(?<![A-Za-z0-9])[Ww][Xx](?![A-Za-z0-9])|"
     r"(?<![A-Za-z0-9])[Qq][Qq](?![A-Za-z0-9]))"
     r"\s*(?:号码|号|账号|ID|id|Id)?\s*(?:[:：=]|是|叫)?\s*"
     r"(?P<wxid>[A-Za-z][-_A-Za-z0-9]{5,19}|[0-9]{5,12})"),
    # mainland mobile numbers
    ("phone_cn", r"(?<![0-9])1[3-9][0-9]{9}(?![0-9])"),
    # international, in the shapes people actually type
    ("phone_intl",
     r"(?<![0-9+])\+[0-9]{1,3}[ .-]?(?:\([0-9]{1,4}\)[ .-]?)?"
     r"[0-9]{2,4}(?:[ .-]?[0-9]{2,4}){1,3}(?![0-9])"),

    # ---- network ----------------------------------------------------------
    ("ipv4",
     r"(?<![0-9.])(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}"
     r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])(?![0-9.])"),

    # ---- long opaque runs: the generic-secret net -------------------------
    ("hex_blob", r"(?<![A-Za-z0-9])[0-9a-fA-F]{32,}(?![A-Za-z0-9])"),
    ("b64_blob", r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{32,}={0,2}(?![A-Za-z0-9+/=])"),
]

#: rule name -> the placeholder key in :data:`PLACEHOLDERS`
_KIND = {
    "home": "home",
    "token_sk": "token", "token_gh": "token", "token_slack": "token",
    "token_aws": "token", "hex_blob": "token", "b64_blob": "token",
    "email": "email",
    "wechat": "wechat",
    "phone_cn": "phone", "phone_intl": "phone",
    "ipv4": "ip",
}

_RX = re.compile(
    "|".join(f"(?P<{name}>{pat})" for name, pat in _RULES))


def _replace(m: "re.Match") -> str:
    name = m.lastgroup
    # a named inner group (``wxid``) can win ``lastgroup``; walk back to the
    # outer rule so the keyword is preserved and only the id is masked
    if name == "wxid":
        name = "wechat"
    if name is None or name.startswith("keep_"):        # pragma: no cover
        return m.group(0)
    kind = _KIND.get(name)
    if kind is None:                                    # pragma: no cover
        return m.group(0)
    if name == "wechat":
        whole = m.group(0)
        ident = m.group("wxid")
        # keep the keyword ("微信：") so the reader can see *what* was masked
        return whole[:whole.rindex(ident)] + PLACEHOLDERS["wechat"]
    return PLACEHOLDERS[kind]


def scrub(text):
    """Mask the personal data and credentials in ``text``.

    Non-strings are returned unchanged, so this is safe to map over mixed
    payloads.  ``None`` stays ``None``: a caller that wants ``""`` should say so.
    """
    if not isinstance(text, str) or not text:
        return text
    return _RX.sub(_replace, text)


def scrub_lines(lines):
    """:func:`scrub` every string in an iterable, keeping the order."""
    return [scrub(x) for x in lines]


def scrub_obj(obj, _depth: int = 0):
    """Recursively :func:`scrub` every string in a JSON-shaped structure.

    Dict *keys* are left alone: they are schema, not content, and rewriting one
    would silently change the shape of a record.
    """
    if _depth > 12:                                     # pragma: no cover
        return obj
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, list):
        return [scrub_obj(v, _depth + 1) for v in obj]
    if isinstance(obj, tuple):                          # pragma: no cover
        return tuple(scrub_obj(v, _depth + 1) for v in obj)
    if isinstance(obj, dict):
        return {k: scrub_obj(v, _depth + 1) for k, v in obj.items()}
    return obj


def findings(text) -> list[dict]:
    """``[{kind, match, start, end}]`` — what :func:`scrub` would mask, and where.

    For showing a user why their report changed, and for tests that want to
    assert on the *classification* rather than on the output bytes.
    """
    out: list[dict] = []
    if not isinstance(text, str) or not text:
        return out
    for m in _RX.finditer(text):
        name = m.lastgroup
        if name == "wxid":
            name = "wechat"
        if not name or name.startswith("keep_"):
            continue
        kind = _KIND.get(name)
        if kind is None:                                # pragma: no cover
            continue
        out.append({"kind": kind, "rule": name, "match": m.group(0),
                    "start": m.start(), "end": m.end()})
    return out
