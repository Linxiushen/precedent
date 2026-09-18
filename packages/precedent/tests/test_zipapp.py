# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""``precedent.pyz``: reproducible bytes, and a real run with nothing installed.

Two claims, and each one is checked the only way it can be:

**Reproducible.**  Build the same tree twice; the two files must be identical.
That is not a property of :mod:`zipapp` — it stamps every entry with the file's
mtime — so ``scripts/build_zipapp.py`` fixes the order, the timestamp and the
mode itself, and this is the test that would notice if it stopped.

**Self-contained.**  Run the archive under ``python -S``, which does not add
``site-packages`` to ``sys.path``.  In that interpreter ``receipts``,
``acceptor`` and ``precedent`` are not importable at all, so anything the
archive does it does out of its own bytes.  (CI does the same thing the other
way round, with a ``uv venv`` that has nothing in it.)
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BUILDER = os.path.join(REPO, "scripts", "build_zipapp.py")

if not os.path.isfile(BUILDER):                            # pragma: no cover
    pytest.skip("scripts/build_zipapp.py not found", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("precedent_zipapp_builder", BUILDER)
builder = importlib.util.module_from_spec(_spec)
sys.modules["precedent_zipapp_builder"] = builder
_spec.loader.exec_module(builder)


@pytest.fixture(scope="module")
def pyz(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("zipapp") / "precedent.pyz")
    assert builder.main(["--out", out, "--quiet"]) == 0
    return out


def _run(pyz, *argv, isolated=True):
    cmd = [sys.executable] + (["-S"] if isolated else []) + [pyz, *argv]
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(cmd, capture_output=True, text=True, env=env,
                          cwd=os.path.dirname(pyz), timeout=120)


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------

def test_two_builds_of_the_same_tree_are_byte_identical(tmp_path):
    a, b = str(tmp_path / "a.pyz"), str(tmp_path / "b.pyz")
    assert builder.main(["--out", a, "--quiet"]) == 0
    assert builder.main(["--out", b, "--quiet"]) == 0
    assert open(a, "rb").read() == open(b, "rb").read()
    assert builder.main(["--check", "--quiet"]) == 0


def test_every_entry_has_the_fixed_timestamp_and_mode(pyz):
    import zipfile
    with zipfile.ZipFile(pyz) as zf:
        infos = zf.infolist()
        assert [i.filename for i in infos] == sorted(i.filename for i in infos)
        for i in infos:
            assert i.date_time == builder.FIXED_DATE, i.filename
            assert (i.external_attr >> 16) & 0o777 == builder.FIXED_MODE
            assert i.create_system == builder.UNIX


def test_a_changed_source_file_changes_the_digest(tmp_path, monkeypatch):
    entries = builder.sources()
    before = builder.contents_digest(entries)
    mutated = [(p, b + b"\n# touched\n") if p == "precedent/cli.py" else (p, b)
               for p, b in entries]
    assert builder.contents_digest(mutated) != before
    assert builder.build_bytes(mutated) != builder.build_bytes(entries)


def test_it_carries_all_three_packages_and_nothing_else(pyz):
    import zipfile
    with zipfile.ZipFile(pyz) as zf:
        names = zf.namelist()
    assert "__main__.py" in names
    tops = {n.split("/")[0] for n in names if "/" in n}
    assert tops == {"receipts", "acceptor", "precedent"}
    assert all(n.endswith(".py") for n in names)
    assert not any("__pycache__" in n or "/tests/" in n for n in names)
    for must in ("precedent/cli.py", "precedent/_hooklib.py", "precedent/i18n.py",
                 "precedent/share.py", "receipts/scan.py", "acceptor/ledger.py"):
        assert must in names, must


def test_the_shebang_is_there_so_it_can_be_chmod_a_plus_x(pyz):
    with open(pyz, "rb") as fh:
        assert fh.read(23) == builder.SHEBANG
    assert os.stat(pyz).st_mode & 0o111


# --------------------------------------------------------------------------
# it runs on a Python that has none of the packages
# --------------------------------------------------------------------------

def test_python_dash_S_really_cannot_import_the_packages():
    """Without this, the run tests below would prove nothing."""
    p = subprocess.run(
        [sys.executable, "-S", "-c",
         "import importlib.util as u; print([m for m in "
         "('receipts','acceptor','precedent') if u.find_spec(m)])"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": ""}, timeout=60)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "[]", p.stdout


def test_version_runs_from_the_archive_alone(pyz):
    p = _run(pyz, "--version")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "precedent 0.1.0"


def test_init_runs_from_the_archive_alone(pyz, demo_home, tmp_path):
    state = str(tmp_path / "pyz-state")
    p = _run(pyz, "init", "--state-dir", state, "--claude-home", demo_home.home)
    assert p.returncode == 0, p.stderr
    assert "first screen" in p.stdout
    assert "learned artifacts  : 19" in p.stdout
    assert os.path.isfile(os.path.join(state, "ledger.jsonl"))


def test_audit_share_runs_from_the_archive_alone(pyz, demo_home, tmp_path):
    state = str(tmp_path / "pyz-state2")
    p = _run(pyz, "audit", "--share", "--state-dir", state,
             "--claude-home", demo_home.home)
    assert p.returncode == 0, p.stderr
    assert "share card" in p.stdout
    assert "0 leaks" in p.stdout
    assert demo_home.token not in p.stdout
    assert demo_home.project_a not in p.stdout


def test_the_hook_runtime_in_the_archive_is_the_file_in_the_repo(pyz):
    """`hooks install` copies _hooklib.py out; from a zip there is no file.

    :func:`precedent.hooks.plib_source` falls back to importlib.resources, and
    what it returns has to be the same bytes the tests import, or the code
    Claude Code runs stops being the code that was tested.
    """
    on_disk = os.path.join(REPO, "packages", "precedent", "src", "precedent",
                           "_hooklib.py")
    want = hashlib.sha256(open(on_disk, "rb").read()).hexdigest()
    p = _run(pyz, "hooks", "install", "claude-code", "--help")
    assert p.returncode == 0, p.stderr
    probe = subprocess.run(
        [sys.executable, "-S", "-c",
         "import sys, hashlib; sys.path.insert(0, sys.argv[1]);"
         "from precedent.hooks import plib_source;"
         "print(hashlib.sha256(plib_source().encode()).hexdigest())", pyz],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": ""}, timeout=60)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == want


def test_the_archive_writes_nothing_into_the_claude_home(pyz, demo_home, tmp_path):
    from conftest import tree_fingerprint
    before = tree_fingerprint(demo_home.home)
    _run(pyz, "init", "--state-dir", str(tmp_path / "s3"),
         "--claude-home", demo_home.home)
    _run(pyz, "audit", "--share", "--state-dir", str(tmp_path / "s3"),
         "--claude-home", demo_home.home)
    assert tree_fingerprint(demo_home.home) == before


# --------------------------------------------------------------------------
# …and a hostile PYTHONPATH cannot get a word in
# --------------------------------------------------------------------------
#
# "Self-contained" is usually tested by taking things away: run it where the
# packages are absent and see that it still works.  That is the easy half.  The
# half that matters on a stranger's laptop is the opposite: the packages are
# *present* and wrong — an old `pip install receipts` in site-packages, a
# `PYTHONPATH` left over from another project, a directory the user happens to
# be standing in.  If any of those wins the import, the archive runs somebody
# else's scanner and says nothing about it.
#
# So the decoy below is not a stub: importing it kills the process and leaves a
# file behind.  If it is ever reached, the test cannot pass quietly.

DECOY_PACKAGE = '''\
import os
with open(os.environ["DECOY_MARKER"], "a") as fh:
    fh.write("{name} package imported\\n")
raise SystemExit("DECOY {name} was imported instead of the vendored copy")
'''

DECOY_MODULE = '''\
import os
with open(os.environ["DECOY_MARKER"], "a") as fh:
    fh.write("receipts module imported\\n")
raise SystemExit("DECOY receipts.py was imported instead of the vendored copy")
'''


@pytest.fixture
def decoy(tmp_path):
    """A directory of hostile stand-ins for all three packages."""
    d = tmp_path / "decoy"
    d.mkdir()
    # a top-level module, the shape the task names…
    (d / "receipts.py").write_text(DECOY_MODULE, encoding="utf-8")
    # …and a package of the same name, which wins over a module in the same
    # sys.path entry, so testing only the module would test the weaker case
    for name in ("receipts", "acceptor", "precedent"):
        pkg = d / name
        pkg.mkdir()
        (pkg / "__init__.py").write_text(DECOY_PACKAGE.format(name=name),
                                         encoding="utf-8")

    class D:
        pass
    obj = D()
    obj.path = str(d)
    obj.marker = str(tmp_path / "decoy-marker")
    return obj


def _run_with_decoy(pyz, decoy, *argv, cwd=None):
    """Run the archive with the decoy first on ``PYTHONPATH``, site-packages on.

    Deliberately **not** ``-S``: the point is that the archive wins against a
    populated interpreter, not that it survives an empty one.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = decoy.path + os.pathsep + env.get("PYTHONPATH", "")
    env["DECOY_MARKER"] = decoy.marker
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, pyz, *argv], capture_output=True,
                          text=True, env=env, timeout=120,
                          cwd=cwd or os.path.dirname(pyz))


def test_the_decoy_really_would_be_imported_without_the_archive(decoy):
    """If the decoy is inert, everything below is a test of nothing."""
    p = subprocess.run(
        [sys.executable, "-c", "import receipts"], capture_output=True,
        text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": decoy.path,
             "DECOY_MARKER": decoy.marker, "PYTHONDONTWRITEBYTECODE": "1"})
    assert p.returncode != 0
    assert "DECOY receipts" in (p.stderr + p.stdout)
    assert os.path.isfile(decoy.marker)


def test_a_hostile_pythonpath_does_not_shadow_the_vendored_packages(pyz, decoy):
    p = _run_with_decoy(pyz, decoy, "--version")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "precedent 0.1.0"
    assert not os.path.exists(decoy.marker), "the decoy was imported"


def test_every_import_resolved_inside_the_archive(pyz, decoy):
    """Not just "it worked" — where each module's ``__file__`` actually is."""
    probe = ("import json, receipts, acceptor, precedent, precedent.share;"
             "print(json.dumps({m.__name__: m.__file__ for m in "
             "(receipts, acceptor, precedent, precedent.share)}))")
    env = dict(os.environ)
    env["PYTHONPATH"] = decoy.path
    env["DECOY_MARKER"] = decoy.marker
    p = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, sys.argv[1]); "
                        "exec(sys.argv[2])", pyz, probe],
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr
    where = json.loads(p.stdout)
    for name, path in where.items():
        assert path.startswith(pyz + os.sep), f"{name} came from {path}"
    assert not os.path.exists(decoy.marker)


def test_a_full_run_under_the_decoy_produces_the_same_card(pyz, decoy,
                                                           hostile_home, tmp_path):
    """The end-to-end case: a real scan, with a hostile scanner on the path."""
    state = str(tmp_path / "decoy-state")
    p = _run_with_decoy(pyz, decoy, "audit", "--share", "--state-dir", state,
                        "--claude-home", hostile_home.home)
    assert p.returncode == 0, p.stderr
    assert "share card" in p.stdout and "0 leaks" in p.stdout
    assert not os.path.exists(decoy.marker)
    for what, literal in hostile_home.plants:
        assert literal not in p.stdout, f"{what} leaked out of the zipapp"


def test_the_decoy_cannot_win_from_the_working_directory_either(pyz, decoy,
                                                                tmp_path):
    """``python foo.pyz`` puts the *archive* on sys.path[0], not the cwd.

    Worth pinning: it is the one line of :mod:`__main__`'s docstring that is a
    claim about CPython rather than about this repository.
    """
    env = dict(os.environ)
    env["DECOY_MARKER"] = decoy.marker
    env.pop("PYTHONPATH", None)
    p = subprocess.run([sys.executable, pyz, "--version"], capture_output=True,
                       text=True, env=env, timeout=120, cwd=decoy.path)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "precedent 0.1.0"
    assert not os.path.exists(decoy.marker)


# --------------------------------------------------------------------------
# the interpreter floor — the first thing a stranger hits
#
# macOS ships `python3` as 3.9 and the README's one-liner is
# `python3 precedent.pyz`.  Without a re-exec that command fails on a stock Mac
# for a reason that has nothing to do with the user, at the only moment they
# were ever going to try it.
# --------------------------------------------------------------------------

def _an_old_python():
    """An interpreter on this machine older than the floor, or None."""
    import shutil
    for cand in ("python3.9", "python3.10", "/usr/bin/python3",
                 "/Library/Developer/CommandLineTools/usr/bin/python3"):
        exe = shutil.which(cand) if not os.path.isabs(cand) else (
            cand if os.path.exists(cand) else None)
        if not exe:
            continue
        v = subprocess.run([exe, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                           capture_output=True, text=True)
        if v.returncode != 0:
            continue
        try:
            ver = tuple(int(x) for x in v.stdout.strip().split("."))
        except ValueError:
            continue
        if ver < (3, 11):
            return exe
    return None


def test_the_shim_parses_on_the_oldest_python_it_must_talk_to(pyz):
    """3.9 must be able to COMPILE it, or the version check never runs.

    A SyntaxError is raised before any guard executes, so the greeting for an
    old interpreter has to be written in a dialect that interpreter accepts —
    which is why the shim uses %-formatting and not a multi-line f-string.  It
    did not, once, and a stock Mac got a zipimport traceback instead of advice.
    """
    import ast
    import zipfile
    src = zipfile.ZipFile(pyz).read("__main__.py").decode()
    ast.parse(src)                        # SyntaxError here means 3.9 cannot start it
    old = _an_old_python()
    if old:
        c = subprocess.run([old, "-c",
                            "import ast,sys;ast.parse(sys.stdin.read())"],
                           input=src, capture_output=True, text=True)
        assert c.returncode == 0, c.stderr


def test_an_old_interpreter_reexecs_into_a_newer_one(pyz):
    """Given an old python and a newer one on PATH, it must not just give up."""
    old = _an_old_python()
    if not old:
        pytest.skip("no interpreter older than the floor on this machine")
    env = dict(os.environ)
    env.pop("PRECEDENT_NO_REEXEC", None)
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    p = subprocess.run([old, pyz, "--version"], capture_output=True, text=True,
                       env=env, timeout=180)
    assert p.returncode == 0, p.stderr
    assert "precedent" in p.stdout
    assert "re-running under" in p.stderr      # it says so rather than doing it silently


def test_the_reexec_cannot_loop(pyz):
    """With the escape hatch set, an old interpreter explains instead of respawning."""
    old = _an_old_python()
    if not old:
        pytest.skip("no interpreter older than the floor on this machine")
    env = dict(os.environ, PRECEDENT_NO_REEXEC="1")
    p = subprocess.run([old, pyz, "--version"], capture_output=True, text=True,
                       env=env, timeout=120)
    assert p.returncode == 2
    assert "needs Python 3.11 or newer" in p.stderr
    assert "uv python install" in p.stderr     # a command they can copy


def test_a_broken_shim_does_not_hide_a_working_interpreter(tmp_path):
    """`shutil.which` returns the first match per name — and that is the bug.

    A dangling symlink into a removed pyenv prefix, or a shim that exits
    non-zero, is a perfectly ordinary thing to have first on PATH.  The old
    loop asked `shutil.which("python3.12")`, got the broken one, and on failure
    moved to the next NAME — skipping every other python3.12 further down PATH.
    A user with one stale shim got "no newer interpreter was found" while a
    working 3.12 sat two directories away.
    """
    import os
    import subprocess
    import sys

    old = _an_old_python()
    if old is None:                                   # pragma: no cover
        pytest.skip("no interpreter older than 3.11 on this machine")

    broken, good = tmp_path / "broken", tmp_path / "good"
    broken.mkdir(), good.mkdir()
    shim = broken / "python3.12"
    shim.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    shim.chmod(0o755)
    (good / "python3.12").symlink_to(sys.executable)

    pyz = tmp_path / "p.pyz"
    subprocess.run([sys.executable, BUILDER,
                    "--out", str(pyz), "--quiet"], check=True)

    env = dict(os.environ, PATH=f"{broken}{os.pathsep}{good}{os.pathsep}"
                                + os.environ.get("PATH", ""))
    env.pop("PRECEDENT_NO_REEXEC", None)
    r = subprocess.run([old, str(pyz), "--version"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-400:]
    assert "precedent" in r.stdout, r.stdout
    assert str(good) in r.stderr, (
        "it did not re-exec into the working interpreter behind the broken "
        "shim: " + r.stderr[-400:])
