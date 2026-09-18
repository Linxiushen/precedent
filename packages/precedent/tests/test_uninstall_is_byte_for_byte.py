# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""`uninstall --apply` puts settings.json back byte for byte — now actually.

DEMO.md and packages/precedent/README.md both promise it in those words.  It
restored every key, every value and their order exactly, and then re-serialised
at a hard-coded indent of 2.  A settings.json the user had written with four
spaces, or with tabs, came back semantically identical and textually different
— so the sha256 moved, and "byte for byte" was false for anyone who had not
happened to indent the way we do.

It is a small thing that matters more than its size: this is the one file
precedent is allowed to write inside a Claude home, and the promise that it
can be put back exactly is most of the reason a user runs `--apply` at all.
"""

import hashlib
import subprocess
import sys

import pytest

LAYOUTS = {
    "two-space": '{\n  "model": "opus",\n  "env": {\n    "FOO": "bar"\n  }\n}\n',
    "four-space": '{\n    "model": "opus",\n    "env": {\n        "FOO": "bar"\n    }\n}\n',
    "tabs": '{\n\t"model": "opus",\n\t"env": {\n\t\t"FOO": "bar"\n\t}\n}\n',
}


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(*args):
    return subprocess.run([sys.executable, "-m", "precedent", *args],
                          capture_output=True, text=True)


@pytest.mark.parametrize("name", sorted(LAYOUTS))
def test_install_then_uninstall_restores_the_exact_bytes(tmp_path, demo_home,
                                                         name, real_home_canary):
    import pathlib
    home = pathlib.Path(demo_home.home)
    state = str(tmp_path / "state")
    settings = home / "settings.json"
    settings.write_text(LAYOUTS[name], encoding="utf-8")
    before = _sha(settings)

    common = ["--state-dir", state, "--claude-home", str(home)]
    assert _run("init", *common).returncode == 0
    assert _run("hooks", "install", "claude-code", "--apply", "--i-know",
                *common).returncode == 0
    assert _sha(settings) != before, "install did not change the file at all"
    assert _run("hooks", "uninstall", "claude-code", "--apply", "--i-know",
                *common).returncode == 0

    assert _sha(settings) == before, (
        f"{name}: uninstall restored the content but not the bytes — the file "
        f"was re-serialised.\n--- before ---\n{LAYOUTS[name]}"
        f"--- after ---\n{settings.read_text(encoding='utf-8')}")
    real_home_canary()


@pytest.mark.parametrize("text,want", [
    ('{\n  "a": 1\n}\n', 2),
    ('{\n    "a": 1\n}\n', 4),
    ('{\n\t"a": 1\n}\n', 1),
    ('{"a": 1}\n', 2),          # single line: nothing to read, keep the default
    ("", 2),
])
def test_detect_indent(text, want):
    from precedent.hooks import detect_indent
    assert detect_indent(text) == want


def test_the_dry_run_diff_previews_the_bytes_the_apply_writes(tmp_path, demo_home):
    """Otherwise the preview is honest about content and lies about formatting."""
    import pathlib

    from precedent.hooks import settings_diff, settings_text_like

    home = pathlib.Path(demo_home.home)
    settings = home / "settings.json"
    settings.write_text(LAYOUTS["four-space"], encoding="utf-8")
    merged = {"model": "opus", "env": {"FOO": "bar"}, "hooks": {}}

    diff = "\n".join(settings_diff(str(settings), merged))
    written = settings_text_like(merged, settings.read_text(encoding="utf-8"))
    for line in written.splitlines():
        if line.strip().startswith('"FOO"'):
            assert line in diff or ("+" + line) in diff, (
                "the diff previewed a different indent than the apply writes:\n"
                + diff[:600])
