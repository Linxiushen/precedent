# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""BUNDLE EVALUATOR — the deterministic grader behind ``skill_proposal_evaluate``.

OpenClaw asks every registered evaluator the same question before a skill
proposal is applied: *is this bundle safe to write?*  This module answers it
with regex, ``sha256``, ``compile()`` and set algebra — **no model call, no
network, no clock-dependent behaviour** — so the same bundle always gets the
same verdict and a verdict can be replayed from the event JSON alone.

The seam (verified against OpenClaw main, 2026-09-16)
----------------------------------------------------
The plugin registers ``api.on("skill_proposal_evaluate", …)`` and shells out to
``precedent evaluate-bundle --stdin --json``.  The event it forwards is::

    {"correlationId": "...",
     "proposal": {"id", "kind": "create"|"update", "revision",
                  "revisionSha256", "targetCurrentSha256"?},
     "skill":    {"name", "skillKey", "description", "source"?},
     "candidate": BundleSnapshot,
     "baseline":  BundleSnapshot?,          # present on update proposals
     "reason": "created"|"revised"|"manual"|"apply"}

    BundleSnapshot = {"skillMd": File, "files": [File], "treeSha256": str}
    File           = {"path", "content", "encoding": "utf8"|"base64",
                      "sha256", "sizeBytes"}

and the result document written back on stdout is::

    {"summary"?, "findings"?: [{"ruleId", "severity", "message",
                                "file"?, "line"?}],
     "metrics"?, "evaluatorVersion"?, "mode"?, "decision"?, "decisionReason"?}

**Why this never raises.**  On a thrown error or a timeout OpenClaw records an
*attributed error outcome* — it does not block.  Only a completed
``decision: "block"`` vetoes an apply.  An evaluator that crashes therefore
fails **open**: the proposal lands unreviewed and the audit trail says the
grader was broken, which is exactly the shape of failure this project exists to
prevent.  So :func:`evaluate_event` converts every internal failure into a
valid result document carrying ``decision: "block"`` and a ``decisionReason``
that names the exception (``fail_closed=True``, the default), and the CLI exits
``0`` whenever it managed to write a document.  Turning that off
(``--no-fail-closed``) is available for debugging and lets the exception out.

**Why stdout is JSON and nothing else.**  The TypeScript shim parses stdout
blind.  Every diagnostic, every human rendering, every traceback goes to
stderr.  A single stray ``print`` would turn a working gate into an attributed
error outcome, so the rule is absolute — and enforced rather than intended:
:func:`precedent.cli._grade` runs the whole evaluation with ``sys.stdout``
redirected to ``sys.stderr``, so a check that prints (or one that tries to
write ``{"decision": "pass"}`` itself) cannot splice anything into the document.
The only write to the real stdout is the finished document.

The checks
----------
Seven families, each finding carrying a stable ``ruleId`` the plugin can
allowlist, suppress or count over time:

``structure/*``   every ``files[].path`` stays inside the bundle, SKILL.md
                  parses, the frontmatter declares ``name`` and
                  ``description``, the declared name matches ``skill.name``,
                  every referenced file is *in the bundle*, a script with a
                  shebang compiles, base64 decodes, no file carries invisible
                  or bidirectional control characters, nothing was silently
                  truncated, and the bundle's own digests recompute.  The path
                  check is the only one here whose subject is the envelope
                  rather than the payload: applying a proposal *writes* those
                  paths, so ``../../../../etc/cron.d/evil`` is a finding
                  whatever the file it carries contains.
``dlp/*``         :mod:`precedent.scrub` over every text file, and over the
                  *logical* lines as well, so a credential split across a
                  shell line continuation cannot hide in the seam between two
                  physical ones.  The matched bytes are **never** echoed back:
                  a finding carries the kind, the file and the line, and
                  nothing else.
``evidence/*``    a line that claims something was verified has to cite
                  something; a placeholder token inside such a line is the
                  hermes#89963 failure — template text codified as
                  "Verified 2026-08-04" — and is CRITICAL.
``scope/*``       absolute home paths, ``localhost:PORT``, machine hostnames
                  and dates-as-facts in a skill that never says where it
                  applies.
``baseline/*``    the SafeEvolve invariant, updates only: a revision may not
                  silently DROP a verification or reversibility requirement its
                  ancestor asserted — nor *reformat* it away, which is why a
                  replacement has to share two content tokens with what it
                  replaces and not merely one.  Each dropped requirement is
                  listed individually and is CRITICAL.
``risk/*``        updates only: destructive verbs, network egress hosts and
                  credential reads the baseline did not have.
``size/*``        SKILL.md over 25 KiB or the bundle over 1 MiB — retrieval
                  precision collapses as the pool grows (*Demystifying Agent
                  Skills*), so bloat is a defect, not a style note.

The severity model, stated once so it stays coherent
----------------------------------------------------
``critical`` = *do not apply this*: an integrity mismatch, a SKILL.md that does
not parse, a script that does not compile, a live credential, a fabricated
verification claim, a dropped safety requirement, or a risk shape that reads
like exfiltration.  ``warn`` = *revise and resubmit*: it is wrong, not
dangerous.  ``info`` = *noted*, it changes no decision.

``DECISION``: any critical -> ``block``; else any warn -> ``revise``; else
``pass``.

Honest limitations
------------------
* ``treeSha256`` — per-file ``sha256`` is unambiguous (the digest of the file's
  bytes) and is enforced exactly.  The *tree* digest has no wire-format
  specification in the seam, so :func:`tree_sha256_variants` recomputes the
  three canonical shapes a sane producer would use and only reports a mismatch
  when **none** of them agrees.  A content change moves all three, so tamper
  detection is preserved; an unknown fourth encoding would be a false
  ``critical``, which is the direction this gate is allowed to be wrong in.
* Shell syntax is checked with ``bash -n`` when ``bash`` is on PATH and skipped
  with an ``info`` finding when it is not.  ``-n`` reads and parses without
  executing; nothing from the bundle is ever run.
* The requirement extractor is lexical.  It finds obligations phrased as
  sentences; it cannot see one that lives only in a diagram or a table header.
* The DLP pass is a net for *shapes*, not a proof of absence.  A credential
  split across a line continuation is caught because the logical line is
  scanned too; one assembled by string concatenation (``"sk-" + tail``) or read
  out of a variable is not, and no deterministic checker can chase arbitrary
  string algebra.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from . import __version__
from .mine import content_tokens, jaccard
from .scrub import findings as dlp_findings
from .scrub import scrub, scrub_obj

__all__ = [
    "BUNDLE_BYTES_WARN",
    "DECISIONS",
    "SEVERITIES",
    "SKILL_MD_BYTES_WARN",
    "BundleError",
    "evaluate_event",
    "evaluator_version",
    "event_from_dirs",
    "render_human",
    "requirement_sentences",
    "risk_facts",
    "snapshot_from_dir",
    "tree_sha256",
    "tree_sha256_variants",
    "unsafe_path_reason",
]

SEVERITIES = ("info", "warn", "critical")
DECISIONS = ("pass", "revise", "block")

#: severity -> rank, for ordering and for the decision rule
_RANK = {"info": 0, "warn": 1, "critical": 2}

#: *Demystifying Agent Skills*: retrieval precision collapses as pools grow, so
#: a skill that will not fit in a reviewer's head is a defect of the skill.
SKILL_MD_BYTES_WARN = 25 * 1024
BUNDLE_BYTES_WARN = 1024 * 1024

#: how much of one file we are willing to read into a check
MAX_TEXT_CHARS = 2_000_000
#: how long ``bash -n`` may take before we give up on it
BASH_TIMEOUT_S = 5.0


class BundleError(Exception):
    """A malformed event or snapshot — raised only when ``fail_closed`` is off."""


def evaluator_version() -> str:
    return f"precedent/{__version__}"


# --------------------------------------------------------------------------
# digests
# --------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _short(digest) -> str:
    """A digest short enough that the DLP pass will not mistake it for a secret.

    :mod:`precedent.scrub` masks any 32+ character hex run, on purpose.  A
    finding that quoted a full sha256 would come out as ``[redacted:token]``
    and tell the reader nothing, so every digest in a message is 12 characters.
    """
    return str(digest or "")[:12]


def tree_sha256(files: list[dict]) -> str:
    """The canonical tree digest: ``sha256("<path>\\0<sha256>\\n" … )``, sorted.

    ``files`` are ``{path, sha256}`` dicts, SKILL.md included.  Sorting by path
    makes the digest independent of the order the producer walked the tree in.
    """
    body = "".join(f"{f['path']}\0{f['sha256']}\n"
                   for f in sorted(files, key=lambda f: f["path"]))
    return _sha256(body.encode("utf-8"))


def tree_sha256_variants(files: list[dict]) -> set[str]:
    """Every tree digest a sane producer might have written for ``files``.

    The seam does not specify the encoding, so a mismatch is only reported when
    none of these agrees — see the module docstring.  All three are functions
    of the same (path, sha256) pairs, so any content change moves all of them.
    """
    ordered = sorted(files, key=lambda f: f["path"])
    out = {tree_sha256(ordered)}
    # newline-separated "path sha" lines
    out.add(_sha256("".join(f"{f['path']} {f['sha256']}\n"
                            for f in ordered).encode("utf-8")))
    # the concatenated digests alone, no paths
    out.add(_sha256("".join(f["sha256"] for f in ordered).encode("utf-8")))
    return out


# --------------------------------------------------------------------------
# snapshots: reading one out of a directory, and normalising one off the wire
# --------------------------------------------------------------------------

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache",
              ".mypy_cache", ".DS_Store"}


def _file_record(path: str, rel: str) -> dict:
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
        encoding, content = "utf8", text
    except UnicodeDecodeError:
        encoding = "base64"
        content = base64.b64encode(raw).decode("ascii")
    return {"path": rel.replace(os.sep, "/"), "content": content,
            "encoding": encoding, "sha256": _sha256(raw), "sizeBytes": len(raw)}


def snapshot_from_dir(path: str) -> dict:
    """Build a ``BundleSnapshot`` from a directory on disk (read-only).

    Used by ``--dir`` / ``--baseline-dir`` and by the tests, so a bundle can be
    graded without an OpenClaw event to hand.  Nothing is written, nothing is
    followed out of the tree: symlinks are skipped rather than resolved.
    """
    root = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(root):
        raise BundleError(f"not a directory: {path}")
    files: list[dict] = []
    skill_md: dict | None = None
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS
                             and not os.path.islink(os.path.join(dirpath, d)))
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, root)
            rec = _file_record(full, rel)
            if rec["path"] == "SKILL.md":
                skill_md = rec
            else:
                files.append(rec)
    if skill_md is None:
        raise BundleError(f"no SKILL.md in {path}")
    return {"skillMd": skill_md, "files": files,
            "treeSha256": tree_sha256([skill_md] + files)}


def event_from_dirs(directory: str, baseline_dir: str | None = None, *,
                    skill_name: str | None = None,
                    reason: str = "manual") -> dict:
    """Synthesise an OpenClaw event from one (or two) directories on disk."""
    candidate = snapshot_from_dir(directory)
    name = skill_name or os.path.basename(
        os.path.abspath(os.path.expanduser(directory)))
    event: dict = {
        "proposal": {"id": f"dir:{name}",
                     "kind": "update" if baseline_dir else "create",
                     "revision": 1, "revisionSha256": candidate["treeSha256"]},
        "skill": {"name": name, "skillKey": name,
                  "description": _frontmatter(candidate["skillMd"]["content"])[0]
                  .get("description", "") if candidate["skillMd"] else "",
                  "source": "directory"},
        "candidate": candidate,
        "reason": reason,
    }
    if baseline_dir:
        event["baseline"] = snapshot_from_dir(baseline_dir)
        event["proposal"]["targetCurrentSha256"] = event["baseline"]["treeSha256"]
    return event


# --------------------------------------------------------------------------
# the parsed view a check sees
# --------------------------------------------------------------------------

class _File:
    """One bundle file, decoded once so every check reads the same bytes."""

    __slots__ = ("path", "encoding", "declared_sha", "declared_size", "raw",
                 "text", "decode_error", "truncated")

    def __init__(self, rec: dict):
        if not isinstance(rec, dict):
            raise BundleError(f"a bundle file is not an object: {type(rec).__name__}")
        self.path = str(rec.get("path") or "")
        self.encoding = str(rec.get("encoding") or "utf8")
        self.declared_sha = rec.get("sha256")
        self.declared_size = rec.get("sizeBytes")
        content = rec.get("content")
        content = "" if content is None else str(content)
        self.decode_error: str | None = None
        self.truncated = False
        self.raw: bytes = b""
        self.text: str | None = None
        if self.encoding == "base64":
            try:
                self.raw = base64.b64decode(content, validate=True)
            except (binascii.Error, ValueError) as exc:
                self.decode_error = str(exc)
        else:
            self.raw = content.encode("utf-8", "surrogatepass")
            self.text = content[:MAX_TEXT_CHARS]
            # A file too large to read in full is graded on the part we read,
            # and `structure/file-truncated` says so: silence would mean the
            # bytes past the cap had been checked and found clean.
            self.truncated = len(content) > MAX_TEXT_CHARS

    @property
    def size(self) -> int:
        return len(self.raw)

    def lines(self) -> list[str]:
        return (self.text or "").splitlines()


class _Snapshot:
    __slots__ = ("skill_md", "files", "declared_tree", "by_path")

    def __init__(self, snap: dict | None):
        snap = snap or {}
        if not isinstance(snap, dict):
            raise BundleError("a BundleSnapshot is not an object")
        raw_md = snap.get("skillMd")
        self.skill_md = _File(raw_md) if isinstance(raw_md, dict) else None
        if self.skill_md is not None and not self.skill_md.path:
            self.skill_md.path = "SKILL.md"
        raw_files = snap.get("files") or []
        if not isinstance(raw_files, list):
            raise BundleError("BundleSnapshot.files is not a list")
        self.files = [_File(f) for f in raw_files]
        self.declared_tree = snap.get("treeSha256")
        self.by_path = {f.path: f for f in self.all()}

    def all(self) -> list[_File]:
        return ([self.skill_md] if self.skill_md else []) + self.files

    def texts(self) -> list[_File]:
        """Every file we can read as text — base64 payloads are never scanned."""
        return [f for f in self.all() if f.text is not None]

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.all())

    def joined_text(self) -> str:
        return "\n".join(f.text or "" for f in self.texts())


def _finding(rule_id: str, severity: str, message: str,
             file: str | None = None, line: int | None = None) -> dict:
    out = {"ruleId": rule_id, "severity": severity, "message": message}
    if file:
        out["file"] = file
    if line:
        out["line"] = int(line)
    return out


# --------------------------------------------------------------------------
# 1. structure
# --------------------------------------------------------------------------

def _frontmatter(text: str) -> tuple[dict, str]:
    """``({key: value}, error)`` for a SKILL.md frontmatter block.

    Deliberately the same shape as :func:`precedent.improve._frontmatter`: a
    line-oriented reader, not a YAML parser.  A skill frontmatter is
    ``key: value`` with the occasional nested block, and the packages here ship
    without PyYAML on purpose — a grader that needs a parser with a CVE history
    to decide whether to block is not a safety control.  Nested blocks are
    recorded by their top-level key so ``metadata:`` does not read as an error.
    """
    if not text.startswith("---"):
        return {}, "the file does not start with a `---` frontmatter block"
    end = text.find("\n---", 3)
    if end < 0:
        return {}, "the frontmatter block is never closed with `---`"
    fm: dict = {}
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in (" ", "\t", "-"):          # a nested block or list item
            continue
        if ":" not in line:
            return fm, f"frontmatter line is not `key: value`: {line[:60]!r}"
        k, v = line.split(":", 1)
        fm[k.strip().lower()] = v.strip().strip("'\"")
    return fm, ""


_MD_LINK = re.compile(r"\[[^\]]{0,200}\]\(([^)\s]{1,300})\)")
# A bundle-relative reference.  Unlike improve.py's linter this one does *not*
# exclude a leading backtick: a skill writes `scripts/run.py` in code font far
# more often than in bare prose, and those are exactly the files that have to
# be in the bundle.
_REL_REF = re.compile(r"(?<![\w/.\-])((?:scripts|references|assets|templates|"
                      r"examples|data|docs)/[\w.\-/]+\.[A-Za-z0-9]{1,8})")
_SHEBANG_PY = re.compile(r"^#!.*\bpython[0-9.]*\b")
_SHEBANG_SH = re.compile(r"^#!.*\b(?:ba|z|k|da)?sh\b")


def _referenced_paths(text: str) -> list[tuple[str, int]]:
    """``[(target, line)]`` for every in-bundle reference a SKILL.md makes."""
    out: list[tuple[str, int]] = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in _MD_LINK.finditer(line):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#", "<")):
                continue
            out.append((target.split("#", 1)[0].strip(), n))
        for m in _REL_REF.finditer(line):
            out.append((m.group(1), n))
    seen: set[str] = set()
    uniq: list[tuple[str, int]] = []
    for target, n in out:
        if target and target not in seen:
            seen.add(target)
            uniq.append((target, n))
    return uniq


def _normalise_ref(target: str) -> str | None:
    """A bundle-relative path, or ``None`` when the reference leaves the bundle.

    ``..`` is resolved segment by segment rather than stripped: a sibling-skill
    link (``../lark-shared/SKILL.md``) has to come out as "outside the bundle",
    not as a missing local file.
    """
    t = target.strip()
    if not t or os.path.isabs(target) or target.startswith("~"):
        return None
    parts: list[str] = []
    for seg in t.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None               # escapes the bundle root
            parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts) or None


#: a bundle file path is a *relative* path inside the skill directory.  Both
#: separators count as separators: the path is applied on whatever platform the
#: host runs on, and ``..\\..\\`` escapes exactly as ``../../`` does.
_PATH_SEP_RX = re.compile(r"[\\/]+")
_WIN_DRIVE_RX = re.compile(r"^[A-Za-z]:")
_CTRL_IN_PATH_RX = re.compile(r"[\x00-\x1f\x7f]")


def unsafe_path_reason(path: str) -> str | None:
    """Why ``path`` must not be written, or ``None`` when it is bundle-relative.

    Applying a proposal means writing every ``files[].path`` under the skill
    directory.  A path that leaves it is an arbitrary file write and the file's
    *content* is beside the point: ``../../../../etc/cron.d/evil`` is a finding
    when the file it carries is empty.  This is the one check whose subject is
    the envelope rather than the payload, which is exactly why it has to exist
    — every other check here reads content, and content is not what an escaping
    path attacks with.
    """
    p = str(path or "")
    if not p.strip():
        return "the path is empty"
    if _CTRL_IN_PATH_RX.search(p):
        return "the path contains a control character"
    if p.startswith("~"):
        return "the path starts at a home directory (`~`)"
    if p.startswith("/") or p.startswith("\\") or _WIN_DRIVE_RX.match(p):
        return "the path is absolute"
    segments = [seg for seg in _PATH_SEP_RX.split(p) if seg not in ("", ".")]
    if any(seg == ".." for seg in segments):
        return "the path escapes the bundle root with `..`"
    if not segments:
        return "the path names no file"
    return None


#: an explicit bidi *override* — the *Trojan Source* shape.  LRO and RLO force
#: a direction regardless of what the characters are, so the rendered line can
#: differ from the line that runs and the human who approved the skill and the
#: model that loads it read different text.  There is no honest use for one in
#: a skill, which is what separates these two from the pair below.
_BIDI_OVERRIDE_RX = re.compile("[\u202d\u202e]")
#: the other bidi controls: embeddings, the pop, and the isolates.  These are
#: ordinary typography in a right-to-left document, so they are reported rather
#: than refused — but they are still the same mechanism, and a skill that has
#: them without any RTL text in it is worth a second look.
_BIDI_RX = re.compile("[\u202a-\u202c\u2066-\u2069]")
#: zero-width and other invisible formatting characters.  Defensible in prose in
#: a few scripts, never in a command — and they defeat every lexical check in
#: this module, which is the point of putting one there.
_INVISIBLE_RX = re.compile("[\u200b-\u200f\u2060-\u2064\ufeff\u00ad]")


def _codepoints(text: str, rx: "re.Pattern") -> str:
    """``U+202E, U+200B`` — what was found, named rather than quoted."""
    return ", ".join(sorted({f"U+{ord(c):04X}" for c in rx.findall(text)}))


def _hidden_character_findings(f: "_File") -> list[dict]:
    """``structure/bidi-control`` and ``structure/invisible-character`` for one file."""
    out: list[dict] = []
    text = f.text or ""
    if text.startswith("\ufeff"):        # a BOM at offset 0 is a BOM, not a hole
        text = " " + text[1:]
    for rx, rule_id, severity, what in (
        (_BIDI_OVERRIDE_RX, "structure/bidi-override", "critical",
         "an explicit bidirectional override; the line renders differently "
         "from the way it parses, so the text reviewed is not the text that "
         "runs"),
        (_BIDI_RX, "structure/bidi-control", "warn",
         "a bidirectional embedding or isolate; ordinary in a right-to-left "
         "document and the same mechanism a text-hiding attack uses, so it is "
         "reported rather than refused"),
        (_INVISIBLE_RX, "structure/invisible-character", "warn",
         "an invisible formatting character; it is unreadable in review and it "
         "splits words that every check here matches on"),
    ):
        m = rx.search(text)
        if not m:
            continue
        out.append(_finding(
            rule_id, severity,
            f"{f.path} contains {what} ({_codepoints(text, rx)})",
            f.path, text.count("\n", 0, m.start()) + 1))
    return out


def _check_shell_syntax(src: bytes, name: str) -> tuple[bool, str]:
    """``(ok, detail)`` from ``bash -n``.  ``-n`` parses; it never executes."""
    bash = shutil.which("bash")
    if not bash:
        return True, "no-bash"
    tmp = tempfile.mkdtemp(prefix="precedent-bundle-")
    try:
        # The bundle does not get to name a file on this machine.  basename()
        # already contains `../`, but a NUL, a leading dot-dot or an empty stem
        # would each turn an open() into something other than "write a script
        # into my own temp dir", so the name is rebuilt from a safe alphabet.
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name or ""))[:64]
        target = os.path.join(tmp, safe.strip(".") or "script.sh")
        with open(target, "wb") as fh:
            fh.write(src)
        proc = subprocess.run([bash, "-n", target], capture_output=True,
                              text=True, timeout=BASH_TIMEOUT_S)
        if proc.returncode == 0:
            return True, ""
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, (detail[-1] if detail else f"bash -n exited {proc.returncode}")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return True, f"unavailable: {type(exc).__name__}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_structure(ctx: dict) -> list[dict]:
    cand: _Snapshot = ctx["candidate"]
    skill = ctx["event"].get("skill") or {}
    out: list[dict] = []

    # --- the paths, before any question about content ----------------------
    at_path: dict[str, set[str]] = {}
    for f in cand.all():
        at_path.setdefault(f.path, set()).add(_sha256(f.raw))
        reason = unsafe_path_reason(f.path)
        if reason:
            out.append(_finding(
                "structure/unsafe-path", "critical",
                f"{f.path!r} is not a path inside the bundle: {reason}. Applying "
                f"this proposal would write outside the skill directory, so what "
                f"the file contains is beside the point", f.path))
        if f.truncated:
            out.append(_finding(
                "structure/file-truncated", "warn",
                f"{f.path} is larger than the {MAX_TEXT_CHARS} character read "
                f"limit; everything past it was NOT graded, so a clean verdict "
                f"covers only the part that was read", f.path))
    for path, digests in sorted(at_path.items()):
        # Listing one file twice — a producer that puts SKILL.md in `files` as
        # well as in `skillMd`, say — is redundant, not wrong, and a gate that
        # fired on it would be wrong about every proposal from that producer.
        # Two *different* files claiming one path is the finding.
        if len(digests) > 1:
            out.append(_finding(
                "structure/duplicate-path", "warn",
                f"{path!r} is carried {len(digests)} times in this bundle with "
                f"different contents; on apply one copy overwrites the other "
                f"and which one survives is the producer's iteration order, "
                f"not a decision anyone made", path))
    for f in cand.texts():
        out.extend(_hidden_character_findings(f))

    # --- the digests the producer handed us --------------------------------
    for f in cand.all():
        if f.decode_error is not None:
            out.append(_finding(
                "structure/base64-undecodable", "critical",
                f"{f.path}: the base64 payload does not decode "
                f"({f.decode_error[:80]})", f.path))
            continue
        if f.declared_sha and _sha256(f.raw) != str(f.declared_sha):
            out.append(_finding(
                "structure/file-hash-mismatch", "critical",
                f"{f.path}: declared sha256 {_short(f.declared_sha)}… but the "
                f"content hashes to {_short(_sha256(f.raw))}… — what you asked "
                f"me to grade is not what you handed me", f.path))
        if (isinstance(f.declared_size, int) and f.declared_size != f.size
                and f.decode_error is None):
            out.append(_finding(
                "structure/file-size-mismatch", "warn",
                f"{f.path}: declared sizeBytes {f.declared_size} but the "
                f"content is {f.size} bytes", f.path))
    if cand.declared_tree:
        pairs = [{"path": f.path, "sha256": _sha256(f.raw)} for f in cand.all()]
        if str(cand.declared_tree) not in tree_sha256_variants(pairs):
            out.append(_finding(
                "structure/tree-hash-mismatch", "critical",
                f"the snapshot declares treeSha256 {_short(cand.declared_tree)}… "
                f"but the {len(pairs)} files it carries hash to "
                f"{_short(tree_sha256(pairs))}… under every canonical encoding "
                f"— the bundle is not internally consistent"))
    else:
        out.append(_finding(
            "structure/tree-hash-absent", "info",
            "the snapshot declares no treeSha256, so the bundle's integrity as "
            "a whole could not be recomputed; the per-file digests it did "
            "declare were still checked exactly"))

    # --- SKILL.md ----------------------------------------------------------
    if cand.skill_md is None or cand.skill_md.text is None:
        out.append(_finding("structure/skill-md-missing", "critical",
                            "the candidate snapshot carries no readable "
                            "SKILL.md, so there is nothing to grade"))
        return out
    text = cand.skill_md.text
    md_path = cand.skill_md.path
    fm, err = _frontmatter(text)
    if err:
        out.append(_finding("structure/frontmatter-invalid", "critical",
                            f"{md_path}: {err}", md_path, 1))
    else:
        for key in ("name", "description"):
            if not fm.get(key):
                out.append(_finding(
                    "structure/frontmatter-incomplete", "warn",
                    f"{md_path}: the frontmatter declares no `{key}`; the skill "
                    f"cannot be retrieved without one", md_path, 1))
        declared = fm.get("name")
        claimed = skill.get("name")
        if declared and claimed and declared != claimed:
            out.append(_finding(
                "structure/name-mismatch", "warn",
                f"{md_path} declares name {declared!r} but the proposal is for "
                f"skill {claimed!r}", md_path, 1))

    # --- references --------------------------------------------------------
    present = set(cand.by_path)
    for target, line in _referenced_paths(text):
        rel = _normalise_ref(target)
        if rel is None:
            # A sibling-skill link (`../lark-shared/SKILL.md`) is a supported,
            # common shape.  It is not a defect — it is simply outside what a
            # bundle evaluator can see, and saying so is `info`, not a verdict.
            out.append(_finding(
                "structure/reference-escapes-bundle", "info",
                f"{md_path} references {target!r}, which is outside the bundle; "
                f"this evaluator cannot check that it exists", md_path, line))
            continue
        if rel not in present:
            out.append(_finding(
                "structure/missing-reference", "warn",
                f"{md_path} references {rel!r}, which is not in the bundle",
                md_path, line))

    # --- scripts -----------------------------------------------------------
    for f in cand.all():
        if f.text is None:
            continue
        first = f.text.splitlines()[0] if f.text else ""
        ext = os.path.splitext(f.path)[1].lower()
        is_py = bool(_SHEBANG_PY.match(first)) or ext == ".py"
        is_sh = bool(_SHEBANG_SH.match(first)) or ext in (".sh", ".bash", ".zsh")
        if is_py:
            try:
                compile(f.text, f.path, "exec")
            except SyntaxError as exc:
                out.append(_finding(
                    "structure/script-syntax", "critical",
                    f"{f.path}: not valid Python — {exc.msg} (line {exc.lineno})",
                    f.path, exc.lineno or 1))
            except ValueError as exc:                      # NUL bytes and friends
                out.append(_finding(
                    "structure/script-syntax", "critical",
                    f"{f.path}: not valid Python — {type(exc).__name__}", f.path))
        elif is_sh:
            ok, detail = _check_shell_syntax(f.raw, f.path)
            if not ok:
                out.append(_finding(
                    "structure/script-syntax", "critical",
                    f"{f.path}: not valid shell — {detail[:160]}", f.path))
            elif detail == "no-bash":
                out.append(_finding(
                    "structure/script-unchecked", "info",
                    f"{f.path}: no `bash` on PATH, so its syntax was not "
                    f"checked", f.path))
    return out


# --------------------------------------------------------------------------
# 2. DLP — never echo the match
# --------------------------------------------------------------------------

#: the scrub rule -> (ruleId, severity).  The four named credential shapes are
#: unambiguous; the generic 32+ character net is "a digest or a key, and it
#: cannot tell", so it warns rather than blocking a release over a checksum.
_DLP_RULES = {
    "token_sk": ("dlp/secret", "critical"),
    "token_gh": ("dlp/secret", "critical"),
    "token_slack": ("dlp/secret", "critical"),
    "token_aws": ("dlp/secret", "critical"),
    "hex_blob": ("dlp/opaque-blob", "warn"),
    "b64_blob": ("dlp/opaque-blob", "warn"),
    "email": ("dlp/pii", "warn"),
    "phone_cn": ("dlp/pii", "warn"),
    "phone_intl": ("dlp/pii", "warn"),
    "wechat": ("dlp/pii", "warn"),
    "ipv4": ("dlp/pii", "warn"),
    "home": ("dlp/home-path", "info"),
}

_DLP_MESSAGE = {
    "dlp/secret": "a credential ({kind}) appears here; it must not ship in a "
                  "skill bundle",
    "dlp/opaque-blob": "a 32+ character opaque run appears here — a digest or a "
                       "key, and a deterministic check cannot tell which",
    "dlp/pii": "personal data ({kind}) appears here",
    "dlp/home-path": "an absolute home path appears here; the username travels "
                     "with the skill",
}


def _is_path_not_blob(match: str) -> bool:
    """``True`` when a generic base64 hit is really a slash-separated path.

    ``/`` is in the base64 alphabet, so :mod:`precedent.scrub`'s generic net
    matches ``apis/approval/v4/instances/subscription`` exactly as it matches a
    token.  Scrub is right to be conservative — it is redacting a quote — but a
    *gate* that turns every URL path in a skill into a finding teaches people
    to ignore it.  A path's segments are short; a credential is one long run.
    """
    if "/" not in match:
        return False
    return max((len(seg) for seg in match.split("/")), default=0) < 24


def _continued_lines(lines: list[str]):
    """``(first_line, joined)`` for every run of backslash-continued lines.

    ``export T="sk-a\\`` + ``bcdef…"`` is one string at runtime and two lines on
    disk, and a line-at-a-time DLP pass sees neither half of it.  Only runs that
    were actually continued are yielded, so an ordinary file costs one string
    comparison per line and produces nothing.
    """
    buf: list[str] | None = None
    start = 0
    for n, line in enumerate(lines, 1):
        if line.endswith("\\"):
            if buf is None:
                buf, start = [], n
            buf.append(line[:-1])
            continue
        if buf is not None:
            buf.append(line)
            yield start, "".join(buf)
            buf = None
    if buf is not None:
        yield start, "".join(buf)


def check_dlp(ctx: dict) -> list[dict]:
    cand: _Snapshot = ctx["candidate"]
    out: list[dict] = []
    seen: set[tuple] = set()

    def record(text: str, path: str, n: int) -> None:
        for hit in dlp_findings(text):
            mapped = _DLP_RULES.get(hit["rule"])
            if not mapped:                                  # pragma: no cover
                continue
            if hit["rule"] == "b64_blob" and _is_path_not_blob(hit["match"]):
                continue
            rule_id, severity = mapped
            key = (rule_id, path, n, hit["kind"])
            if key in seen:
                continue
            seen.add(key)
            # the match itself is deliberately absent from the message
            out.append(_finding(
                rule_id, severity,
                _DLP_MESSAGE[rule_id].format(kind=hit["kind"]), path, n))

    for f in cand.texts():
        lines = f.lines()
        for n, line in enumerate(lines, 1):
            record(line, f.path, n)
        # and once more over the logical lines, so a secret cannot hide in the
        # seam between two physical ones
        for start, joined in _continued_lines(lines):
            record(joined, f.path, start)
    return out


# --------------------------------------------------------------------------
# 3. evidence discipline — hermes#89963
# --------------------------------------------------------------------------

#: what counts as *claiming* something was verified.
#:
#: Only assertions of a completed check qualify.  English keeps the past
#: participles; ``check``/``pass`` are left out because "checked the box" and
#: "passed the flag" are not claims.  Chinese needs the same care in the other
#: direction: bare ``确认`` is an instruction far more often than a claim
#: ("需用户确认", "确认后再执行"), so the claim *forms* — ``已确认``, ``确认过``,
#: ``确认完毕`` — are what is matched.  Treating the bare verb as a claim made
#: every command table in a real skill a critical finding, which is how a gate
#: gets switched off.
_CLAIM_RX = re.compile(
    r"(?i)\b(?:verified|tested|validated|confirmed|benchmarked|reproduced)\b"
    r"|已(?:验证|确认|测试|核实|核对|检查)"
    r"|(?:验证|确认|测试|核实|核对)(?:过|完毕|通过|无误)"
    r"|(?:验证|确认|测试)结果")

#: what counts as citing something: a command, a path, a date, a URL, an issue
_CITE_RX = re.compile(
    r"`[^`\n]{2,}`"                                  # an inline command/path
    r"|\b\d{4}-\d{2}-\d{2}\b"                        # a date
    r"|https?://\S+"                                 # a URL
    r"|(?<![\w`])[\w.\-]+/[\w.\-/]+\.[A-Za-z0-9]{1,8}"   # a path with a suffix
    r"|(?<![\w`])[\w.\-]{2,}\.(?:py|sh|md|json|ts|tsx|js|toml|yaml|yml|txt)\b"
    r"|#\d{2,}"                                      # an issue reference
    r"|\bv?\d+\.\d+(?:\.\d+)?\b")                    # a version

_PLACEHOLDER_RX = re.compile(
    r"\bTODO\b|\bFIXME\b|\bTBD\b|\bXXX\b"
    r"|(?<![A-Za-z])N{3,}(?![A-Za-z])"
    r"|(?<![A-Za-z])[Xx]{3,}(?![A-Za-z])"
    r"|\bHH:MM(?::SS)?\b"
    r"|\bYYYY[-/]MM[-/]DD\b"
    r"|<your[-_ ][^>\n]{0,40}>"
    r"|<[A-Z][A-Z0-9_]{1,38}>"
    r"|\{\{[^}\n]{1,40}\}\}"
    r"|_{3,}"
    # the Chinese placeholder words, minus their noun forms: a skill that
    # *discusses* 占位符 or 待定项 is writing about placeholders, not leaving one
    r"|(?:占位|待填|待补充|待定)(?![符项者性的])")


_FENCE_RX = re.compile(r"^\s*(?:```|~~~)")
_CODE_SPAN_RX = re.compile(r"`[^`\n]*`")


def prose_lines(f: "_File"):
    """``(n, raw, prose)`` per line — ``prose`` is ``""`` inside a code fence.

    Two checks need the same distinction and need it made the same way.  A
    claim lives in prose; its *citation* may well be the command in backticks,
    so the raw line is handed over too and the caller picks.  ``<ISO>`` inside
    a command template is a parameter, not a placeholder somebody forgot to
    fill in — dropping code spans before looking for holes is what stops a
    reference table from reading as a fabricated verification.
    """
    fenced = False
    for n, line in enumerate(f.lines(), 1):
        if _FENCE_RX.match(line):
            fenced = not fenced
            yield n, line, ""
            continue
        yield n, line, ("" if fenced else _CODE_SPAN_RX.sub(" ", line))


def check_evidence(ctx: dict) -> list[dict]:
    cand: _Snapshot = ctx["candidate"]
    out: list[dict] = []
    for f in cand.texts():
        for n, line, prose in prose_lines(f):
            stripped = prose.strip()
            if not stripped or not _CLAIM_RX.search(stripped):
                continue
            hole = _PLACEHOLDER_RX.search(stripped)
            if hole:
                out.append(_finding(
                    "evidence/placeholder-claim", "critical",
                    f"this line claims something was verified and contains the "
                    f"placeholder {hole.group(0)[:24]!r} — template text "
                    f"codified as a verification is the hermes#89963 failure",
                    f.path, n))
                continue
            # the citation may be the code the prose pass just removed
            if not _CITE_RX.search(line):
                out.append(_finding(
                    "evidence/uncited-claim", "warn",
                    "this line claims something was verified but cites nothing "
                    "— name the command, the path or the date", f.path, n))
    return out


# --------------------------------------------------------------------------
# 4. scope leakage
# --------------------------------------------------------------------------

_SCOPE_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("scope/home-path", r"/(?:Users|home)/[A-Za-z0-9._\-]+/"
                        r"|[A-Za-z]:\\Users\\[A-Za-z0-9._\-]+",
     "an absolute home path"),
    ("scope/localhost-port",
     r"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])\s*:\s*\d{2,5}\b",
     "a localhost port"),
    ("scope/hostname",
     r"\b[A-Za-z0-9][A-Za-z0-9\-]{1,62}\."
     r"(?:local|lan|internal|intranet|corp|localdomain)\b",
     "a machine hostname"),
    ("scope/dated-fact", r"\b20\d{2}-\d{2}-\d{2}\b", "a date stated as a fact"),
)
_SCOPE_RX = [(rid, re.compile(rx), what) for rid, rx, what in _SCOPE_PATTERNS]

#: a line (or the line above it) saying "this is an example" / "yours will
#: differ" / "on my machine" is a scope qualifier: the fact is presented as
#: local, so it is not leaking as a universal truth.
_QUALIFIER_RX = re.compile(
    r"(?i)\bexamples?\b|\be\.g\.|\bfor instance\b|\bsample\b|\bplaceholder\b"
    r"|\byour own\b|\byours? will\b|\breplace (?:this|it|with)\b"
    r"|\bon (?:my|this) machine\b|\blocal(?:ly| only)\b"
    r"|示例|举例|例如|比如|替换|改成你|本机|仅本地|仅供参考")

#: a frontmatter key that already says where the skill applies
_SCOPE_KEYS = ("scope", "applies-to", "applies_to", "environment", "env",
               "machine", "host")


def _scope_declared(cand: _Snapshot) -> bool:
    if cand.skill_md is None or cand.skill_md.text is None:
        return False
    fm, _ = _frontmatter(cand.skill_md.text)
    return any(fm.get(k) for k in _SCOPE_KEYS)


def check_scope(ctx: dict) -> list[dict]:
    cand: _Snapshot = ctx["candidate"]
    if _scope_declared(cand):
        return []
    out: list[dict] = []
    for f in cand.texts():
        lines = f.lines()
        for n, line, prose in prose_lines(f):
            if not line.strip():
                continue
            prev = lines[n - 2] if n >= 2 else ""
            if _QUALIFIER_RX.search(line) or _QUALIFIER_RX.search(prev):
                continue
            for rule_id, rx, what in _SCOPE_RX:
                # A date inside a command example is an argument, not an
                # assertion, so `dated-fact` reads prose only.  A home path,
                # a localhost port or a hostname pins the skill to one machine
                # wherever it appears — code included.
                subject = prose if rule_id == "scope/dated-fact" else line
                if rx.search(subject):
                    out.append(_finding(
                        rule_id, "warn",
                        f"{what} appears here and the skill never says where it "
                        f"applies; it will be loaded on machines where this is "
                        f"false", f.path, n))
    return out


# --------------------------------------------------------------------------
# 5. baseline invariant — the SafeEvolve rule
# --------------------------------------------------------------------------

#: verification and reversibility verbs -> the obligation they express.
#:
#: Mapping surface forms onto one canonical name is what lets "back up the
#: file" and "take a backup of the file" count as the same requirement — and,
#: usefully, lets a Chinese requirement be answered by an English rewrite of
#: it.  Latin keys match on a word boundary; CJK keys match as substrings,
#: which is how Chinese reads.
_REQ_VERBS_EN: dict[str, str] = {
    "verify": "verify", "verifies": "verify", "verified": "verify",
    "verifying": "verify", "verification": "verify",
    "check": "check", "checks": "check", "checked": "check",
    "test": "test", "tests": "test", "tested": "test", "testing": "test",
    "confirm": "confirm", "confirms": "confirm", "confirmed": "confirm",
    "validate": "verify", "validates": "verify", "validated": "verify",
    "backup": "backup", "back up": "backup", "snapshot": "backup",
    "rollback": "rollback", "roll back": "rollback", "revert": "rollback",
    "undo": "rollback", "restore": "rollback",
    "dry-run": "dryrun", "dry run": "dryrun", "preview": "dryrun",
    "diff": "diff", "review": "review",
}
_REQ_VERBS_ZH: dict[str, str] = {
    "确认": "confirm", "验证": "verify", "检查": "check", "核对": "check",
    "复核": "check", "测试": "test", "备份": "backup", "快照": "backup",
    "回滚": "rollback", "撤销": "rollback", "还原": "rollback",
    "恢复": "rollback", "试运行": "dryrun", "预演": "dryrun",
}

_REQ_VERB_RX = re.compile(
    r"(?i)(?<![A-Za-z])(?:"
    + "|".join(sorted((v.replace(" ", r"\s+").replace("-", r"[-\s]")
                       for v in _REQ_VERBS_EN), key=len, reverse=True))
    + r")(?![A-Za-z])"
    r"|" + "|".join(_REQ_VERBS_ZH))

#: what turns a sentence that mentions a verb into an *obligation*
_MODAL_RX = re.compile(
    r"(?i)\bmust\b|\bshould\b|\balways\b|\bnever\b|\brequired\b|\brequire[sd]?\b"
    r"|\bdo not\b|\bdon't\b|\bensure\b|\bmake sure\b|\bbefore\b|\bafter\b"
    r"|\bfirst\b|\bnot allowed\b|\bonly (?:after|once)\b"
    r"|必须|务必|需要|应当|应该|请先|先|一定要|不要|禁止|严禁|才能|之前|之后")

_BULLET_RX = re.compile(r"^\s*(?:[-*+]|\d+[.)]|#{1,6})\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|(?<=[。！？；])")
_MD_NOISE = re.compile(r"[`*_~>|\[\]()]+")


def _verbs_in(text: str) -> set[str]:
    """The canonical obligations a sentence expresses (``{"verify", "backup"}``)."""
    found: set[str] = set()
    low = text.lower()
    for surface, canon in _REQ_VERBS_EN.items():
        pattern = surface.replace(" ", r"\s+").replace("-", r"[-\s]")
        if re.search(r"(?<![a-z])" + pattern + r"(?![a-z])", low):
            found.add(canon)
    for surface, canon in _REQ_VERBS_ZH.items():
        if surface in text:
            found.add(canon)
    return found


def _normalise_sentence(s: str) -> str:
    return " ".join(_MD_NOISE.sub(" ", s).lower().split())


def requirement_sentences(text: str) -> list[dict]:
    """``[{sentence, norm, verbs, tokens, line}]`` — the obligations in ``text``.

    A sentence qualifies when it names a verification or reversibility verb
    *and* reads as an instruction: it carries a modal, or it is a bullet /
    heading that opens with one of the verbs.  Prose that merely mentions the
    word ("the tests live in tests/") is not an obligation and is not extracted.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for n, line in enumerate((text or "").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            continue
        bullet = bool(_BULLET_RX.match(line))
        body = _BULLET_RX.sub("", line).strip()
        for sentence in _SENTENCE_SPLIT.split(body):
            sentence = sentence.strip()
            if len(sentence) < 4 or not _REQ_VERB_RX.search(sentence):
                continue
            opens_with_verb = bool(_REQ_VERB_RX.match(sentence))
            if not (_MODAL_RX.search(sentence)
                    or (bullet and opens_with_verb) or opens_with_verb):
                continue
            norm = _normalise_sentence(sentence)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            out.append({"sentence": sentence[:300], "norm": norm,
                        "verbs": _verbs_in(sentence),
                        "tokens": content_tokens(sentence), "line": n})
    return out


#: how much of a baseline requirement a candidate sentence has to keep before
#: it counts as a rewording rather than a removal.
#:
#: Two measures, because one of them is biased by language.  Jaccard punishes a
#: rewrite that adds words, and Chinese is tokenised into character bigrams, so
#: an honest rewording of a Chinese requirement scores systematically lower
#: than the same rewording in English.  Containment ( |A n B| / min ) does not
#: care that the replacement is longer.  Either one, plus a shared canonical
#: verb, is enough.
#:
#: Both thresholds are deliberately generous.  The asymmetry matters: calling a
#: rewording a removal blocks a good revision and costs a human one look;
#: missing a real removal is the failure this rule exists to prevent.
REPLACEMENT_SIMILARITY = 0.4
REPLACEMENT_CONTAINMENT = 0.6

#: how many content tokens a *containment* match has to rest on.
#:
#: Containment divides by the smaller set, which is what lets a longer rewrite
#: count — and, on a one-token candidate, what lets anything count.  A bare
#: ``## Verify`` heading contains "verify" completely, scores 1.0 against "you
#: must verify the output before applying the change", and cancels it.  That is
#: a requirement removed by *reformatting*, which is precisely the move this
#: rule exists to catch, so containment now has to rest on two shared tokens.
#: Jaccard needs no such guard: a one-token candidate cannot reach 0.4.
REPLACEMENT_MIN_SHARED = 2


def _containment(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _replaced_by(req: dict, candidates: list[dict]) -> bool:
    for other in candidates:
        if other["norm"] == req["norm"]:
            return True
        if not (req["verbs"] & other["verbs"]):
            continue
        if jaccard(req["tokens"], other["tokens"]) >= REPLACEMENT_SIMILARITY:
            return True
        shared = req["tokens"] & other["tokens"]
        if (len(shared) >= REPLACEMENT_MIN_SHARED
                and _containment(req["tokens"],
                                 other["tokens"]) >= REPLACEMENT_CONTAINMENT):
            return True
    return False


def check_baseline(ctx: dict) -> list[dict]:
    base: _Snapshot | None = ctx["baseline"]
    if base is None:
        return []
    cand: _Snapshot = ctx["candidate"]
    base_reqs = requirement_sentences(base.joined_text())
    cand_reqs = requirement_sentences(cand.joined_text())
    ctx["metrics"]["baseline.requirements"] = len(base_reqs)
    dropped = [r for r in base_reqs if not _replaced_by(r, cand_reqs)]
    ctx["metrics"]["baseline.requirementsDropped"] = len(dropped)
    md = cand.skill_md.path if cand.skill_md else None
    return [_finding(
        "baseline/dropped-requirement", "critical",
        f"the revision drops a requirement its ancestor asserted, with nothing "
        f"in its place: {r['sentence']!r} (baseline line {r['line']})", md)
        for r in dropped]


# --------------------------------------------------------------------------
# 6. risk delta
# --------------------------------------------------------------------------

_DESTRUCTIVE: tuple[tuple[str, str], ...] = (
    # the same shape as rules.ASK_BEFORE_PRESETS["rm_rf"], on purpose: one
    # definition of "recursive delete" across the enforcement and the grader
    ("rm -rf", r"(?i)(?<![A-Za-z0-9_.\-])rm\b[^\n]{0,60}?\s-[A-Za-z]{0,6}[rR]"),
    ("git push --force",
     r"(?i)git\s+push\b[^\n]{0,200}?(?:--force(?!-with-lease)"
     r"|(?<![A-Za-z0-9_])-f(?![A-Za-z0-9_]))"),
    ("git reset --hard", r"(?i)git\s+reset\s+[^\n]{0,80}--hard"),
    ("curl | sh", r"(?i)(?:curl|wget)\b[^\n|]{0,200}\|[^\n]{0,40}?(?:ba|z)?sh\b"),
    ("chmod 777", r"(?i)chmod\s+(?:-[A-Za-z]+\s+)*(?:0?777|a\+rwx)\b"),
    ("sudo", r"(?i)(?<![A-Za-z0-9_.\-])sudo(?![A-Za-z0-9_\-])"),
    ("dd of=", r"(?i)(?<![A-Za-z0-9_.\-])dd\s+[^\n]{0,80}\bof="),
    ("mkfs", r"(?i)(?<![A-Za-z0-9_.\-])mkfs(?:\.[a-z0-9]+)?\b"),
    ("drop table", r"(?i)\bdrop\s+(?:table|database)\b"),
)
_DESTRUCTIVE_RX = [(n, re.compile(rx)) for n, rx in _DESTRUCTIVE]

_CREDENTIAL: tuple[tuple[str, str], ...] = (
    ("process.env", r"\bprocess\.env\b"),
    ("os.environ", r"\bos\.environ\b"),
    ("os.getenv", r"\bos\.getenv\b|\bgetenv\("),
    ("~/.aws", r"(?:~|\$HOME|/(?:Users|home)/[A-Za-z0-9._\-]+)/\.aws\b"),
    ("~/.ssh", r"(?:~|\$HOME|/(?:Users|home)/[A-Za-z0-9._\-]+)/\.ssh\b"),
    ("~/.netrc", r"(?:~|\$HOME)/\.netrc\b"),
    ("credentials file", r"(?i)\bcredentials?\.(?:json|ini|env|yaml|yml)\b"),
)
_CREDENTIAL_RX = [(n, re.compile(rx)) for n, rx in _CREDENTIAL]

_URL_HOST = re.compile(r"(?i)\bhttps?://([A-Za-z0-9.\-]{1,253})")

#: hosts that are documentation, not egress: RFC 2606 / RFC 6761 reserved names
#: and the loopback.  A localhost reference is a *scope* problem, not an
#: exfiltration one, and it is reported there.
_NON_EGRESS = {"localhost", "127.0.0.1", "0.0.0.0", "::1",
               "example.com", "example.org", "example.net", "example.edu",
               "example.invalid", "invalid", "test", "localdomain"}


def risk_facts(snap: _Snapshot) -> dict[str, dict[str, tuple[str, int]]]:
    """``{family: {fact: (file, line)}}`` — the risk surface of a snapshot."""
    out: dict[str, dict[str, tuple[str, int]]] = {
        "destructive": {}, "egress": {}, "credential": {}}
    for f in snap.texts():
        for n, line in enumerate(f.lines(), 1):
            if not line.strip():
                continue
            for name, rx in _DESTRUCTIVE_RX:
                if rx.search(line) and name not in out["destructive"]:
                    out["destructive"][name] = (f.path, n)
            for name, rx in _CREDENTIAL_RX:
                if rx.search(line) and name not in out["credential"]:
                    out["credential"][name] = (f.path, n)
            for m in _URL_HOST.finditer(line):
                host = m.group(1).lower().rstrip(".")
                if host in _NON_EGRESS or host.endswith(".example.com"):
                    continue
                if host not in out["egress"]:
                    out["egress"][host] = (f.path, n)
    return out


def check_risk(ctx: dict) -> list[dict]:
    base: _Snapshot | None = ctx["baseline"]
    if base is None:
        return []
    cand: _Snapshot = ctx["candidate"]
    now, before = risk_facts(cand), risk_facts(base)
    new = {fam: {k: v for k, v in now[fam].items() if k not in before[fam]}
           for fam in now}
    ctx["metrics"]["risk.newDestructive"] = len(new["destructive"])
    ctx["metrics"]["risk.newEgressHosts"] = len(new["egress"])
    ctx["metrics"]["risk.newCredentialReads"] = len(new["credential"])

    out: list[dict] = []
    for name, (path, line) in sorted(new["destructive"].items()):
        out.append(_finding(
            "risk/new-destructive-verb", "warn",
            f"the revision introduces {name!r}, which the baseline did not use",
            path, line))
    for host, (path, line) in sorted(new["egress"].items()):
        out.append(_finding(
            "risk/new-egress-host", "warn",
            f"the revision sends traffic to {host!r}, which the baseline did "
            f"not contact", path, line))
    for name, (path, line) in sorted(new["credential"].items()):
        out.append(_finding(
            "risk/new-credential-read", "warn",
            f"the revision reads credentials via {name!r}, which the baseline "
            f"did not", path, line))
    if new["egress"] and (new["destructive"] or new["credential"]):
        host = sorted(new["egress"])[0]
        path, line = new["egress"][host]
        paired = sorted(new["credential"]) or sorted(new["destructive"])
        out.append(_finding(
            "risk/egress-combination", "critical",
            f"the revision adds a new egress host ({host!r}) *and* new "
            f"{'credential reads' if new['credential'] else 'destructive verbs'} "
            f"({', '.join(paired)}); separately each is a warning, together "
            f"they are the shape of exfiltration", path, line))
    return out


# --------------------------------------------------------------------------
# 7. size / bloat
# --------------------------------------------------------------------------

def check_size(ctx: dict) -> list[dict]:
    cand: _Snapshot = ctx["candidate"]
    out: list[dict] = []
    if cand.skill_md is not None and cand.skill_md.size > SKILL_MD_BYTES_WARN:
        out.append(_finding(
            "size/skill-md", "warn",
            f"SKILL.md is {cand.skill_md.size} bytes, over the "
            f"{SKILL_MD_BYTES_WARN} byte budget; retrieval precision falls as "
            f"the pool grows, so length is a cost the whole skill set pays",
            cand.skill_md.path))
    total = cand.total_bytes
    if total > BUNDLE_BYTES_WARN:
        out.append(_finding(
            "size/bundle", "warn",
            f"the bundle is {total} bytes across {len(cand.all())} files, over "
            f"the {BUNDLE_BYTES_WARN} byte budget"))
    return out


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

#: name -> check.  The order is the order findings are produced in, before the
#: severity sort; ``CHECKS`` is public so a test can inject a failing check.
CHECKS: list[tuple[str, object]] = [
    ("structure", check_structure),
    ("dlp", check_dlp),
    ("evidence", check_evidence),
    ("scope", check_scope),
    ("baseline", check_baseline),
    ("risk", check_risk),
    ("size", check_size),
]


def _sort_key(f: dict) -> tuple:
    return (-_RANK.get(f.get("severity"), 0), f.get("file") or "",
            f.get("line") or 0, f.get("ruleId") or "")


def _decide(findings: list[dict]) -> tuple[str, str]:
    worst = max((_RANK.get(f.get("severity"), 0) for f in findings), default=-1)
    if worst == 2:
        crit = [f for f in findings if f["severity"] == "critical"]
        head = crit[0]
        extra = f" (and {len(crit) - 1} more)" if len(crit) > 1 else ""
        return "block", f"{head['ruleId']}: {head['message']}{extra}"
    if worst == 1:
        warns = [f for f in findings if f["severity"] == "warn"]
        head = warns[0]
        extra = f" (and {len(warns) - 1} more)" if len(warns) > 1 else ""
        return "revise", f"{head['ruleId']}: {head['message']}{extra}"
    return "pass", ("no finding above `info`: structure, DLP, evidence, scope, "
                    "risk and size all clear")


def evaluate_event(event: dict, *, fail_closed: bool = True,
                   checks: list | None = None) -> dict:
    """Grade one ``skill_proposal_evaluate`` event; return the result document.

    With ``fail_closed`` (the default) this never raises: any exception — a
    malformed event, a check that blows up, a decoding failure — becomes a
    ``critical`` finding and a ``decision: "block"`` whose ``decisionReason``
    names the exception.  OpenClaw does not block on a thrown error, so an
    evaluator that lets one escape has silently stopped being a gate.
    """
    started = time.perf_counter()
    metrics: dict = {}
    findings: list[dict] = []
    failures: list[str] = []
    mode = "static"
    try:
        if not isinstance(event, dict):
            raise BundleError(
                f"the event is not a JSON object but a {type(event).__name__}")
        candidate = _Snapshot(event.get("candidate"))
        baseline = (_Snapshot(event["baseline"])
                    if isinstance(event.get("baseline"), dict) else None)
        mode = "baseline-comparison" if baseline else "static"
        ctx = {"event": event, "candidate": candidate, "baseline": baseline,
               "metrics": metrics}
        metrics["bundle.files"] = len(candidate.all())
        metrics["bundle.bytes"] = candidate.total_bytes
        metrics["bundle.skillMdBytes"] = (
            candidate.skill_md.size if candidate.skill_md else 0)
        if baseline is not None:
            metrics["baseline.files"] = len(baseline.all())
            metrics["baseline.bytes"] = baseline.total_bytes
        for name, fn in (checks if checks is not None else CHECKS):
            try:
                findings.extend(fn(ctx) or [])
            except Exception as exc:                       # noqa: BLE001
                if not fail_closed:
                    raise
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
                findings.append(_finding(
                    "internal/check-failed", "critical",
                    f"the `{name}` check raised {type(exc).__name__}: {exc}; a "
                    f"bundle that cannot be graded is not approved"))
    except Exception as exc:                               # noqa: BLE001
        if not fail_closed:
            raise
        failures.append(f"evaluate: {type(exc).__name__}: {exc}")
        findings.append(_finding(
            "internal/evaluator-failed", "critical",
            f"the evaluator raised {type(exc).__name__}: {exc} before it could "
            f"grade the bundle"))

    findings.sort(key=_sort_key)
    counts = {s: sum(1 for f in findings if f["severity"] == s)
              for s in SEVERITIES}
    decision, reason = _decide(findings)
    if failures:
        decision = "block"
        reason = ("fail-closed: " + "; ".join(failures))[:600]
    metrics.update({
        "findings.total": len(findings),
        "findings.critical": counts["critical"],
        "findings.warn": counts["warn"],
        "findings.info": counts["info"],
        "checks.run": len(checks if checks is not None else CHECKS),
        "checks.failed": len(failures),
        "elapsedMs": int(round((time.perf_counter() - started) * 1000)),
    })
    top = sorted({f["ruleId"] for f in findings
                  if f["severity"] in ("critical", "warn")})[:4]
    summary = (f"{decision}: {counts['critical']} critical, {counts['warn']} "
               f"warn, {counts['info']} info over "
               f"{metrics.get('bundle.files', 0)} files "
               f"({metrics.get('bundle.bytes', 0)} bytes, {mode})")
    if top:
        summary += " — " + ", ".join(top)
    doc = {"summary": summary, "findings": findings, "metrics": metrics,
           "evaluatorVersion": evaluator_version(), "mode": mode,
           "decision": decision, "decisionReason": reason}
    # Second, independent DLP pass over the finished document.  The checks are
    # written never to quote a match; this is the belt to that pair of braces,
    # and it is why a planted secret cannot reach the caller even through an
    # exception message.
    return scrub_obj(doc)


def render_human(doc: dict) -> str:
    """A short human rendering — for stderr only; stdout is JSON and only JSON."""
    lines = [f"precedent evaluate-bundle -> {doc.get('decision')}",
             f"  {doc.get('summary')}"]
    for f in doc.get("findings") or []:
        where = f.get("file") or ""
        if f.get("line"):
            where += f":{f['line']}"
        lines.append(f"  [{f['severity']:<8}] {f['ruleId']}"
                     + (f"  ({where})" if where else ""))
        lines.append(f"             {scrub(f['message'])}")
    lines.append(f"  reason: {doc.get('decisionReason')}")
    return "\n".join(lines) + "\n"


def load_event(stream) -> dict:
    """Read one JSON event from a text stream, with a useful error on failure."""
    raw = stream.read()
    if not (raw or "").strip():
        raise BundleError("no event on stdin (expected the "
                          "skill_proposal_evaluate JSON)")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BundleError(f"stdin is not valid JSON: {exc}") from exc
