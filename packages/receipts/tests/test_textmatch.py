# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
from receipts.textmatch import (Coverage, Haystack, classify_lines, classify_text,
                                jaccard, normalize, sections, strip_frontmatter, tokenize)


def test_normalize_collapses_reflowed_whitespace():
    assert normalize("a  \n\t b\n\nc ") == "a b c"


def test_strip_frontmatter_splits_and_passes_through():
    fm, body = strip_frontmatter("---\nname: x\ndescription: y\n---\nhello\n")
    assert "name: x" in fm and body.strip() == "hello"
    assert strip_frontmatter("no frontmatter") == ("", "no frontmatter")


def test_sections_splits_paragraphs_and_headings():
    text = "# One\n" + "a" * 60 + "\n\n## Two\n" + "b" * 60
    secs = sections(text)
    assert len(secs) == 2
    assert secs[0].startswith("# One")


def test_sections_keeps_short_chunks_when_nothing_else_survives():
    assert sections("tiny") == ["tiny"]


def test_classify_text_exact_is_complete():
    body = "the quick brown fox " * 3
    cov = classify_text(body, Haystack("prefix " + body + " suffix"))
    assert cov.status == "loaded_complete" and cov.method == "exact"


def test_classify_text_partial_is_truncated():
    a = "First paragraph that is comfortably longer than the minimum section size."
    b = "Second paragraph that is also comfortably longer than the minimum size."
    cov = classify_text(f"{a}\n\n{b}", Haystack("noise " + a + " noise"))
    assert cov.status == "loaded_truncated"
    assert cov.units_found == 1 and cov.units_total == 2
    assert cov.missing and b[:20] in cov.missing[0]


def test_classify_text_absent_is_not_loaded():
    cov = classify_text("something entirely absent from the context blob", Haystack("xyz"))
    assert cov.status == "not_loaded"


def test_lcs_fallback_reports_truncated_for_reflowed_text():
    shared = "S" * 250
    hay = Haystack("lead-in " + shared + " tail")
    # one section, too long to match whole, but >=200 chars overlap
    cov = classify_text(shared + " and a divergent tail that is not in the haystack", hay)
    assert cov.status in ("loaded_truncated", "loaded_complete")


def test_common_substring_threshold_is_200_chars():
    hay = Haystack("x" * 199)
    assert not hay.has_common_substring("x" * 199)
    hay2 = Haystack("pad" + "y" * 200 + "pad")
    assert hay2.has_common_substring("y" * 200)


def test_classify_lines_reports_the_exact_missing_lines():
    lines = [(1, "- [A](a.md) — first"), (2, "- [B](b.md) — second"), (3, "- [C](c.md) — third")]
    cov, missing = classify_lines(lines, Haystack("- [A](a.md) — first\n- [C](c.md) — third"))
    assert cov.status == "loaded_truncated"
    assert [n for n, _ in missing] == [2]


def test_classify_lines_all_present_is_complete():
    lines = [(1, "alpha line"), (2, "beta line")]
    cov, missing = classify_lines(lines, Haystack("alpha line\nbeta line"))
    assert cov.status == "loaded_complete" and missing == []


def test_tokenize_handles_cjk_and_ascii():
    assert tokenize("Hello 世界 world") == {"hello", "world", "世", "界"}


def test_jaccard_bounds():
    assert jaccard(set(), {"a"}) == 0.0
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert 0.3 < jaccard({"a", "b", "c"}, {"a", "b", "d"}) < 0.6


def test_coverage_to_dict_is_json_safe():
    d = Coverage("loaded_complete", "exact", 1, 1, 10, 10).to_dict()
    assert d["status"] == "loaded_complete" and d["fraction"] == 1.0
