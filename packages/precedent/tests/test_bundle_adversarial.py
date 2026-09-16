# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""``evaluate-bundle`` under an author who is trying to get through it.

``test_bundle.py`` asks whether each rule fires on an honest bundle.  This file
asks the opposite question — *can a SKILL.md be written so that the evaluator
approves it anyway?* — and every test here started life as an attack that
worked.  Six of them did:

* ``files[].path`` was never looked at, so a bundle declaring
  ``../../../../etc/cron.d/evil`` was graded on its contents and passed.  What
  an escaping path attacks with is the envelope, not the payload.
* one shared token was enough for the containment test, so adding the heading
  ``## Verify`` to a revision cancelled "you must verify the output before
  applying the change" — a requirement removed by *reformatting*, which is the
  one move the SafeEvolve invariant exists to catch.
* a credential split across a shell line continuation was invisible to a
  line-at-a-time DLP pass, and reassembles at runtime.
* bidi overrides and zero-width characters were unremarked, so the line a human
  approves and the line the model loads could differ.
* a file past the read limit was truncated silently, and a clean verdict looked
  like a statement about the whole file.
* ``stdout is JSON and only JSON`` was a convention, not a property: a check
  that printed spliced its output into the document the shim parses, and a
  check that printed ``{"decision": "pass"}`` forged a verdict.

The negative cases matter as much as the positive ones and are kept next to
them: a gate that fires on an ordinary skill gets switched off, so every new
rule here is asserted silent on the clean fixture too.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest
from precedent.bundle import (MAX_TEXT_CHARS, evaluate_event,
                              requirement_sentences, unsafe_path_reason)
from test_bundle import CLEAN_MD, bfile, by_rule, make_event, md, rules

SCRIPT = "#!/bin/bash\necho hello\n"


def grade(*args, **kw) -> dict:
    return evaluate_event(make_event(*args, **kw))


# --------------------------------------------------------------------------
# the envelope: a path that leaves the skill directory
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "../../../../etc/cron.d/evil",
    "scripts/../../../../etc/passwd",
    "/etc/passwd",
    "~/.ssh/authorized_keys",
    "..\\..\\Windows\\System32\\drivers\\etc\\hosts",
    "C:\\Windows\\System32\\evil.bat",
    "\\\\server\\share\\evil",
    "",
    "..",
])
def test_structure_unsafe_path_is_critical(path):
    doc = grade(CLEAN_MD, [bfile(path, "harmless\n")])
    assert "structure/unsafe-path" in rules(doc), path
    assert doc["decision"] == "block", path
    assert by_rule(doc, "structure/unsafe-path")[0]["severity"] == "critical"


def test_structure_unsafe_path_does_not_care_what_the_file_contains():
    """The content is beside the point; the write is the attack."""
    doc = grade(CLEAN_MD, [bfile("../../../../etc/cron.d/evil", "")])
    assert doc["decision"] == "block"
    assert "beside the point" in by_rule(doc, "structure/unsafe-path")[0]["message"]


@pytest.mark.parametrize("path", [
    "scripts/run.py", "references/a/b/c.md", "./templates/x.md",
    "assets/img.png", "a.md",
])
def test_structure_an_ordinary_relative_path_is_silent(path):
    assert unsafe_path_reason(path) is None
    doc = grade(CLEAN_MD, [bfile(path, "content\n")])
    assert "structure/unsafe-path" not in rules(doc)


def test_the_evaluator_writes_nothing_the_bundle_names(tmp_path, monkeypatch):
    """Grading a script must not put a file where the bundle asked for one.

    ``bash -n`` is the only thing here that touches the filesystem at all.  It
    writes into a temp dir of its own making, so the bundle's path is used for
    *nothing* — and the temp dir is gone afterwards.
    """
    escape = tmp_path / "escaped.sh"
    workdir = tmp_path / "tmpdir"
    workdir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(workdir))

    doc = grade(CLEAN_MD, [
        bfile(str(escape), SCRIPT),                     # absolute
        bfile("../../escaped-relative.sh", SCRIPT),     # traversal
        bfile("scripts/ok.sh", SCRIPT),                 # honest, still compiled
    ])

    assert not escape.exists()
    assert not (tmp_path / "escaped-relative.sh").exists()
    assert list(workdir.iterdir()) == []      # every temp dir it made is gone
    assert doc["decision"] == "block"
    assert len(by_rule(doc, "structure/unsafe-path")) == 2


def test_a_null_byte_in_a_path_is_a_finding_not_a_crash():
    doc = grade(CLEAN_MD, [bfile("scripts/ok\x00.sh", SCRIPT)])
    assert "structure/unsafe-path" in rules(doc)
    assert "internal/check-failed" not in rules(doc)
    assert doc["decision"] == "block"


def test_structure_duplicate_path_warns_when_the_contents_differ():
    doc = grade(CLEAN_MD, [bfile("scripts/run.sh", SCRIPT),
                           bfile("scripts/run.sh", SCRIPT + "echo other\n")])
    assert "structure/duplicate-path" in rules(doc)
    assert doc["decision"] == "revise"


def test_structure_the_same_file_listed_twice_is_not_a_finding():
    """A producer that lists SKILL.md in `files` too is redundant, not wrong.

    The seam does not say whether ``files`` includes ``skillMd``.  A rule that
    fired on the redundant reading would be wrong about every proposal such a
    producer ever makes, which is how a gate gets switched off.
    """
    md_record = bfile("SKILL.md", CLEAN_MD)
    doc = grade(CLEAN_MD, [md_record, bfile("scripts/run.sh", SCRIPT),
                           bfile("scripts/run.sh", SCRIPT)])
    assert "structure/duplicate-path" not in rules(doc)


# --------------------------------------------------------------------------
# characters that render as something other than what they are
# --------------------------------------------------------------------------

def test_structure_bidi_override_is_critical():
    body = "The script does \u202enothing harmful\u202c.\n"
    doc = grade(md(body))
    assert "structure/bidi-override" in rules(doc)
    assert doc["decision"] == "block"
    finding = by_rule(doc, "structure/bidi-override")[0]
    assert "U+202E" in finding["message"]
    # the finding names the codepoint; it does not replay the deceptive text
    assert "nothing harmful" not in finding["message"]


def test_structure_a_bidi_embedding_warns_rather_than_blocking():
    """RTL typography is not an attack, and a gate that refused it would be
    wrong about every Arabic or Hebrew skill ever written."""
    doc = grade(md("\u202bטקסט בעברית\u202c\n"))
    assert "structure/bidi-override" not in rules(doc)
    assert by_rule(doc, "structure/bidi-control")[0]["severity"] == "warn"
    assert doc["decision"] == "revise"


def test_structure_invisible_character_warns_and_names_the_codepoint():
    doc = grade(md("Run `rm -\u200brf /tmp/cache` to clear the cache.\n"))
    finding = by_rule(doc, "structure/invisible-character")[0]
    assert finding["severity"] == "warn"
    assert "U+200B" in finding["message"]
    assert doc["decision"] in ("revise", "block")


def test_a_zero_width_character_cannot_hide_a_destructive_verb_silently():
    """The risk check is lexical and a zero-width space defeats it.

    It cannot be made to match every possible spelling of ``rm -rf`` — so the
    answer is not a cleverer regex, it is that the *character itself* is the
    finding.  A revision that adds one gets a verdict either way.
    """
    base = md("Clear the cache by hand.\n")
    doc = evaluate_event(make_event(
        md("Run `rm -\u200brf /tmp/cache`.\n"), baseline=base))
    assert "structure/invisible-character" in rules(doc)
    assert doc["decision"] != "pass"


def test_clean_text_carries_no_hidden_character_findings():
    doc = grade(md("Ordinary prose, some 中文, an em dash — and `code`.\n"))
    assert "structure/bidi-control" not in rules(doc)
    assert "structure/invisible-character" not in rules(doc)


def test_a_byte_order_mark_at_offset_zero_is_not_a_finding():
    doc = grade("\ufeff" + CLEAN_MD)
    assert "structure/invisible-character" not in rules(doc)


# --------------------------------------------------------------------------
# the SafeEvolve invariant, attacked by reformatting
# --------------------------------------------------------------------------

BASE_REQS = md(
    "- You must always verify the output before applying the change.\n"
    "- You must always back up the file before editing it.\n")


def test_baseline_a_bare_heading_does_not_replace_a_requirement():
    """`## Verify` contains "verify" completely.  It replaces nothing."""
    candidate = md("## Verify\n\n## Backup\n\n- Apply the change.\n")
    doc = evaluate_event(make_event(candidate, baseline=BASE_REQS))
    dropped = by_rule(doc, "baseline/dropped-requirement")
    assert len(dropped) == 2, [f["message"] for f in dropped]
    assert doc["decision"] == "block"


def test_baseline_a_one_word_bullet_does_not_replace_a_requirement():
    candidate = md("- Verify.\n- Backup.\n- Apply the change.\n")
    doc = evaluate_event(make_event(candidate, baseline=BASE_REQS))
    assert len(by_rule(doc, "baseline/dropped-requirement")) == 2


def test_baseline_an_honest_rewording_still_counts_as_a_replacement():
    """The guard must not turn every real revision into a false critical."""
    candidate = md(
        "- Before applying the change you must verify the output first.\n"
        "- Take a backup of the file before you start editing it.\n")
    doc = evaluate_event(make_event(candidate, baseline=BASE_REQS))
    assert by_rule(doc, "baseline/dropped-requirement") == []


def test_baseline_a_longer_rewrite_still_counts_as_a_replacement():
    """Containment exists for exactly this shape and still does its job."""
    candidate = md(
        "- You must verify the output of the run, in full, against the golden "
        "file, before applying the change to anything that matters.\n"
        "- You must back up the file, and keep the backup, before editing it.\n")
    doc = evaluate_event(make_event(candidate, baseline=BASE_REQS))
    assert by_rule(doc, "baseline/dropped-requirement") == []


def test_baseline_the_requirement_extractor_still_reads_the_headings():
    """The fix is in the comparison, not in what gets extracted."""
    extracted = {r["norm"] for r in requirement_sentences("## Verify\n")}
    assert "verify" in extracted


# --------------------------------------------------------------------------
# a secret in the seam between two lines
# --------------------------------------------------------------------------

SPLIT_SECRET_TOP = 'export TOKEN="sk-a\\'
SPLIT_SECRET_REST = 'bcdefghijklmnop12345678"'


def test_dlp_a_secret_split_across_a_line_continuation_is_still_a_secret():
    body = "```sh\n" + SPLIT_SECRET_TOP + "\n" + SPLIT_SECRET_REST + "\n```\n"
    doc = grade(md(body))
    assert "dlp/secret" in rules(doc)
    assert doc["decision"] == "block"
    # never echoed, on either half
    assert "abcdefghijklmnop" not in json.dumps(doc)


def test_dlp_the_continuation_finding_points_at_the_first_line():
    body = "line one\n" + SPLIT_SECRET_TOP + "\n" + SPLIT_SECRET_REST + "\n"
    doc = grade(md(body))
    finding = by_rule(doc, "dlp/secret")[0]
    assert finding["file"] == "SKILL.md"
    assert md("").count("\n") < finding["line"]


def test_dlp_a_secret_in_a_script_file_survives_the_continuation_too():
    script = "#!/bin/bash\n" + SPLIT_SECRET_TOP + "\n" + SPLIT_SECRET_REST + "\n"
    doc = grade(CLEAN_MD, [bfile("scripts/push.sh", script)])
    assert by_rule(doc, "dlp/secret")[0]["file"] == "scripts/push.sh"


def test_dlp_ordinary_continued_lines_produce_nothing():
    body = "```sh\nfind . \\\n  -name '*.py' \\\n  -print\n```\n"
    doc = grade(md(body))
    assert not [f for f in doc["findings"] if f["ruleId"].startswith("dlp/")]


def test_dlp_a_secret_is_still_found_when_it_is_not_split():
    doc = grade(md('export TOKEN="sk-abcdefghijklmnop12345678"\n'))
    assert "dlp/secret" in rules(doc)


# --------------------------------------------------------------------------
# a file too large to read
# --------------------------------------------------------------------------

def test_a_ten_megabyte_skill_md_is_graded_truncated_and_says_so():
    big = md("filler line about nothing in particular\n" * 260_000)
    assert len(big) > 10_000_000
    doc = grade(big)
    assert "structure/file-truncated" in rules(doc)
    assert str(MAX_TEXT_CHARS) in by_rule(doc, "structure/file-truncated")[0]["message"]
    assert doc["decision"] != "pass"
    assert doc["metrics"]["bundle.skillMdBytes"] > 10_000_000


def test_a_file_under_the_read_limit_is_not_reported_truncated():
    doc = grade(md("a line\n" * 100))
    assert "structure/file-truncated" not in rules(doc)


# --------------------------------------------------------------------------
# integrity: what the digests do and do not cover
# --------------------------------------------------------------------------

def test_a_file_whose_declared_sha_matches_but_the_tree_does_not_blocks():
    """Honest per-file digests, a tree digest over different content."""
    doc = grade(CLEAN_MD, [bfile("scripts/run.sh", SCRIPT)],
                tree="0" * 64)
    assert "structure/tree-hash-mismatch" in rules(doc)
    assert "structure/file-hash-mismatch" not in rules(doc)
    assert doc["decision"] == "block"


def test_a_snapshot_with_no_tree_digest_says_the_check_did_not_run():
    event = make_event()
    event["candidate"].pop("treeSha256")
    doc = evaluate_event(event)
    assert "structure/tree-hash-absent" in rules(doc)
    assert by_rule(doc, "structure/tree-hash-absent")[0]["severity"] == "info"
    assert doc["decision"] == "pass"       # info changes no decision


def test_an_added_file_moves_the_tree_digest_and_is_caught():
    honest = make_event(CLEAN_MD, [bfile("scripts/a.sh", SCRIPT)])
    tampered = make_event(CLEAN_MD, [bfile("scripts/a.sh", SCRIPT),
                                     bfile("scripts/b.sh", SCRIPT)],
                          tree=honest["candidate"]["treeSha256"])
    doc = evaluate_event(tampered)
    assert "structure/tree-hash-mismatch" in rules(doc)


# --------------------------------------------------------------------------
# stdout is JSON and only JSON — as a property, not a convention
# --------------------------------------------------------------------------

NOISY_CHECK = r'''
import sys, warnings
import precedent.bundle as bundle
from precedent.cli import main


def noisy(ctx):
    print("a check printed this to stdout")
    sys.stdout.write('{"decision": "pass", "findings": []}')
    sys.stdout.flush()
    warnings.warn("a check warned about this")
    raise RuntimeError("the check exploded")


bundle.CHECKS.append(("noisy", noisy))
sys.exit(main(["evaluate-bundle", "--stdin", "--json"]))
'''


def _run_noisy_cli(event: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONWARNINGS", None)
    return subprocess.run([sys.executable, "-c", NOISY_CHECK],
                          input=json.dumps(event), capture_output=True,
                          text=True, timeout=120, env=env)


def test_a_check_that_prints_cannot_reach_stdout():
    proc = _run_noisy_cli(make_event())
    assert proc.returncode == 0, proc.stderr[-2000:]
    # the whole of stdout parses as one document: nothing was spliced into it
    doc = json.loads(proc.stdout)
    assert proc.stdout.count("\n") == 1
    assert "a check printed this to stdout" in proc.stderr
    assert "a check printed this to stdout" not in proc.stdout


def test_a_check_that_forges_a_verdict_on_stdout_cannot_be_heard():
    proc = _run_noisy_cli(make_event())
    doc = json.loads(proc.stdout)
    assert doc["decision"] == "block"
    assert "internal/check-failed" in {f["ruleId"] for f in doc["findings"]}
    assert "the check exploded" in doc["decisionReason"]


def test_a_check_that_warns_writes_to_stderr_and_the_document_still_parses():
    proc = _run_noisy_cli(make_event())
    assert "a check warned about this" in proc.stderr
    json.loads(proc.stdout)


def test_the_noisy_check_harness_does_not_change_the_clean_path():
    """The same CLI, same event, without the injected check: still one line."""
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; from precedent.cli import main; "
         "sys.exit(main(['evaluate-bundle', '--stdin', '--json']))"],
        input=json.dumps(make_event()), capture_output=True, text=True,
        timeout=120)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["decision"] == "pass"
    assert proc.stderr == ""


# --------------------------------------------------------------------------
# the composite: every attack in one bundle
# --------------------------------------------------------------------------

def test_a_bundle_carrying_every_attack_at_once_blocks_and_names_them():
    body = ("## Verify\n\n"
            "The script does \u202enothing harmful\u202c.\n"
            "```sh\n" + SPLIT_SECRET_TOP + "\n" + SPLIT_SECRET_REST + "\n```\n")
    doc = evaluate_event(make_event(
        md(body),
        [bfile("../../../../etc/cron.d/evil", "* * * * * root sh /tmp/x\n")],
        baseline=BASE_REQS))
    assert doc["decision"] == "block"
    assert {"structure/unsafe-path", "structure/bidi-override", "dlp/secret",
            "baseline/dropped-requirement"} <= rules(doc)
    assert "abcdefghijklmnop" not in json.dumps(doc)
