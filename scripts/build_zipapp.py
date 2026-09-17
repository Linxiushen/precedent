#!/usr/bin/env python3
# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""Build ``precedent.pyz`` — the whole tool as one file, reproducibly.

    python3 scripts/build_zipapp.py            # -> dist/precedent.pyz
    python3 scripts/build_zipapp.py --check    # build twice, compare, no output
    python3 precedent.pyz init                 # …and that is the install

All three packages are standard library only, so "install" can be a download.
The zipapp carries ``receipts``, ``acceptor`` and ``precedent`` plus a
``__main__.py``, and runs on any CPython 3.11+ with nothing installed — no pip,
no venv, no network, no root.

**Why this is hand-rolled rather than ``python -m zipapp``.**  ``zipapp`` walks
the source tree with :func:`os.walk` and stamps each entry with the file's
mtime, so the same tree produces a different archive on every checkout — and an
artifact whose hash changes for no reason is an artifact nobody can verify.
This builder fixes the three things that vary:

* **order** — entries are written in sorted path order, not directory order;
* **time** — every entry gets ``1980-01-01 00:00:00``, the zero of the zip
  format (zip cannot store a date before it);
* **mode** — every entry gets ``0644`` and ``create_system = 3`` (Unix), so the
  umask and the platform of the machine that built it do not leak in.

What is left is zlib, which is deterministic for a given level. So: the same
tree, the same ``--level``, any machine → the same ``sha256``. The builder
prints two digests and they mean different things::

    contents sha256   over (path, sha256(bytes)) for every entry, sorted
    artifact sha256   over the .pyz file itself

The first is interpreter- and compressor-independent: it is the honest answer
to "is this the same code?".  The second is what you check a download against.
``--check`` builds twice into temporary files and fails unless both match.

Excluded by construction: tests, ``__pycache__``, ``.pyc``, dotfiles, and
anything that is not ``.py`` under the three ``src`` trees.  Nothing outside
the repository is read and nothing outside ``--out`` is written.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import stat
import sys
import tempfile
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = ("receipts", "acceptor", "precedent")
DEFAULT_OUT = os.path.join(REPO, "dist", "precedent.pyz")

#: zip cannot represent a timestamp before 1980; this is its zero.
FIXED_DATE = (1980, 1, 1, 0, 0, 0)
FIXED_MODE = 0o644
UNIX = 3

SHEBANG = b"#!/usr/bin/env python3\n"

MAIN = '''# Copyright 2026 The Precedent authors.
# SPDX-License-Identifier: Apache-2.0
"""precedent.pyz — the whole harness in one file, no install.

    python3 precedent.pyz --version
    python3 precedent.pyz init
    python3 precedent.pyz audit --share

``receipts``, ``acceptor`` and ``precedent`` are all inside this archive and
all standard library only, so nothing is imported from site-packages and
nothing needs to be.  If a *newer* copy of any of the three is installed on the
interpreter running this file, the archive still wins: sys.path[0] is the
zipapp itself.
"""

import sys

if sys.version_info < (3, 11):                      # pragma: no cover - guard
    sys.stderr.write(
        "precedent.pyz needs Python 3.11 or newer; this is %d.%d.\\n"
        "Install one (uv python install 3.12) and re-run, or use pipx.\\n"
        % sys.version_info[:2])
    raise SystemExit(2)

from precedent.cli import main                      # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
'''


def sources() -> list[tuple[str, bytes]]:
    """``[(archive path, bytes)]`` for everything that goes in, sorted.

    Only ``.py`` files under ``packages/<pkg>/src/<pkg>/``.  A package that
    ever grows a data file has to be added here deliberately — silently
    shipping half a package is the failure this explicitness prevents.
    """
    out: list[tuple[str, bytes]] = []
    for pkg in PACKAGES:
        root = os.path.join(REPO, "packages", pkg, "src", pkg)
        if not os.path.isdir(root):
            raise SystemExit(f"build_zipapp: no such package tree: {root}")
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames
                                 if d != "__pycache__" and not d.startswith("."))
            for fn in sorted(filenames):
                if not fn.endswith(".py") or fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, os.path.dirname(root))
                with open(full, "rb") as fh:
                    out.append((rel.replace(os.sep, "/"), fh.read()))
    out.append(("__main__.py", MAIN.encode("utf-8")))
    out.sort(key=lambda item: item[0])
    return out


def contents_digest(entries: list[tuple[str, bytes]]) -> str:
    """sha256 over ``path\\0sha256(bytes)\\n`` for every entry, in sorted order.

    Independent of the zip format, the compressor and the interpreter — two
    builds with the same contents digest contain the same code even if the two
    ``.pyz`` files differ byte for byte.
    """
    h = hashlib.sha256()
    for path, body in entries:
        h.update(path.encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(body).hexdigest().encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def build_bytes(entries: list[tuple[str, bytes]], level: int = 9) -> bytes:
    """The whole ``.pyz`` as bytes: shebang + a deterministic zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=level) as zf:
        for path, body in entries:
            info = zipfile.ZipInfo(path, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = UNIX
            info.external_attr = (stat.S_IFREG | FIXED_MODE) << 16
            zf.writestr(info, body)
    return SHEBANG + buf.getvalue()


def write(out_path: str, blob: bytes) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(blob)
    os.chmod(out_path, 0o755)
    return os.path.abspath(out_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="build_zipapp.py",
        description="Build a deterministic, self-contained precedent.pyz.")
    ap.add_argument("--out", default=DEFAULT_OUT, metavar="PATH",
                    help=f"where to write it (default {DEFAULT_OUT})")
    ap.add_argument("--level", type=int, default=9, choices=range(0, 10),
                    metavar="0-9", help="zlib compression level (default 9)")
    ap.add_argument("--check", action="store_true",
                    help="build twice into temporary files and fail unless the "
                         "two are byte-identical; writes nothing to --out")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    entries = sources()
    blob = build_bytes(entries, level=args.level)
    digest = hashlib.sha256(blob).hexdigest()
    contents = contents_digest(entries)

    if args.check:
        with tempfile.TemporaryDirectory() as td:
            a = write(os.path.join(td, "a.pyz"), blob)
            b = write(os.path.join(td, "b.pyz"),
                      build_bytes(sources(), level=args.level))
            with open(a, "rb") as fa, open(b, "rb") as fb:
                same = fa.read() == fb.read()
        if not same:                                  # pragma: no cover - guard
            print("build_zipapp: NOT reproducible — two builds of the same tree "
                  "differ", file=sys.stderr)
            return 1
        if not args.quiet:
            print(f"reproducible: two builds agree, sha256 {digest}")
        return 0

    path = write(args.out, blob)
    if not args.quiet:
        print(f"{path}")
        print(f"  files            {len(entries)}")
        print(f"  size             {len(blob):,} bytes")
        print(f"  contents sha256  {contents}")
        print(f"  artifact sha256  {digest}")
        rel = os.path.relpath(path, os.getcwd())
        shown = rel if len(rel) < len(path) else path
        print(f"  run              python3 {shown} --version")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
