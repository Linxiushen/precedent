# Copyright 2026 The precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""The correction miner v1: sentences, the question filter, the guards,
reverts, cross-session repeats, and the Chinese tokeniser fallback."""

from __future__ import annotations

import pytest
from conftest import sid
from precedent.mine import (analyse_text, content_tokens, detect_patterns,
                            detect_reverts, has_imperative, is_question,
                            is_skill_expansion, jaccard, mine, segmenter_name,
                            split_sentences, strip_heredocs, zh_tokens)


def _quotes(result):
    return [c.turn.quote() for c in result.corrections]


def _topic_with(result, needle):
    for t in result.topics:
        if any(needle in c.turn.text for c in t.members):
            return t
    return None


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

def test_patterns_zh_and_en():
    assert [p for p, _ in detect_patterns("不要用 pip")] == ["不要"]
    assert "我说过" in [p for p, _ in detect_patterns("我说过了，别用 pip")]
    assert "别" in [p for p, _ in detect_patterns("我说过了，别用 pip")]
    assert "stop" in [p for p, _ in detect_patterns("stop using curl")]
    assert "instead" in [p for p, _ in detect_patterns("use httpie instead")]
    assert "i said" in [p for p, _ in detect_patterns("i said httpie")]


def test_lookaround_guards_reject_the_common_false_positives():
    decoy = "这个方案特别好，别的事情先不用担心，级别也够了。"
    assert detect_patterns(decoy) == []
    assert detect_patterns("不停地跑") == []
    assert detect_patterns("please don't worry") != []      # don't is still a pattern


def test_weak_patterns_score_lower_than_strong():
    weak = dict(detect_patterns("重新来一遍"))
    strong = dict(detect_patterns("不要这样"))
    assert max(weak.values()) < max(strong.values())


# --------------------------------------------------------------------------
# skip list
# --------------------------------------------------------------------------

def test_is_skill_expansion_text_prefixes():
    assert is_skill_expansion("<command-name>/model</command-name>")
    assert is_skill_expansion("x\n<command-message>foo</command-message>")
    assert is_skill_expansion("Base directory for this skill: /skills/deps\n不要用 pip")
    assert is_skill_expansion("# Workflow authoring reference\n\nnever use pip")
    assert not is_skill_expansion("不要用 pip，用 uv。")


def test_is_skill_expansion_structural_markers():
    rec = {"sourceToolUseID": "toolu_1"}
    assert is_skill_expansion("Approach this as the design lead…", rec)
    assert not is_skill_expansion("Approach this as the design lead…", {})
    # a record that declares itself human is never skipped structurally
    human = {"origin": {"kind": "human"}, "isMeta": True}
    assert not is_skill_expansion("不要用 pip", human)


def test_skip_list_excludes_skill_expansions(mining_home):
    result = mine(mining_home.home)
    assert result.n_skipped_expansions == 4, "S1 plants exactly four expansions"
    text = "\n".join(c.turn.text for c in result.corrections)
    assert "Base directory for this skill" not in text
    assert "Workflow authoring reference" not in text
    assert "<command-message>" not in text
    assert "local-command-stdout" not in text


# --------------------------------------------------------------------------
# mining the planted corrections
# --------------------------------------------------------------------------

def test_mining_detects_the_planted_corrections(mining_home):
    result = mine(mining_home.home)
    assert result.n_sessions == 7
    # 18 human turns are planted; the four expansions are not among them
    assert result.n_human_turns == 18
    quotes = " | ".join(_quotes(result))
    assert "不要用 pip，用 uv。" in quotes
    assert "我说过了，别用 pip，改用 uv 装。" in quotes
    assert "stop using curl, use httpie instead" in quotes
    assert "don't use curl again, i said httpie instead" in quotes
    assert "不要修改 protected.md 这个文件，它是我手写的。" in quotes
    assert result.n_corrections == 6
    assert result.pct_corrections == round(100 * 6 / 18, 1)


def test_decoy_turn_is_not_a_correction(mining_home):
    result = mine(mining_home.home)
    assert not any("特别好" in c.turn.text for c in result.corrections)


def test_correction_records_the_violating_action(mining_home):
    result = mine(mining_home.home)
    pip = [c for c in result.corrections if "不要用 pip" in c.turn.text][0]
    assert pip.follows_tool
    tools = pip.turn.preceding_tools
    assert tools[0]["tool"] == "Bash"
    assert tools[0]["summary"] == "pip install requests"
    assert tools[0]["lineNo"] == 3


def test_confidence_layers(mining_home):
    result = mine(mining_home.home)
    pip = [c for c in result.corrections if "不要用 pip" in c.turn.text][0]
    # strong pattern 0.55 + follows tool 0.25 + repeated topic 0.20
    assert pip.base == 0.55
    assert pip.follows_tool and pip.repeated
    assert pip.confidence == 1.0
    thanks = [c for c in result.corrections if "又改了" in c.turn.text][0]
    assert thanks.confidence >= 0.55


def test_repeats_are_grouped_into_one_topic(mining_home):
    result = mine(mining_home.home)
    pip = _topic_with(result, "不要用 pip")
    assert pip is not None
    assert pip.count == 2
    assert len(pip.sessions) == 2
    assert pip.repeated

    curl = _topic_with(result, "stop using curl")
    assert curl is not None and curl.count == 2 and curl.repeated

    path = _topic_with(result, "protected.md")
    assert path is not None and path.count == 2 and path.repeated

    assert len(result.repeated_topics) == 3
    # the three topics stay apart: no accidental mega-cluster
    assert {pip.id, curl.id, path.id} == {t.id for t in result.repeated_topics}


def test_topics_are_addressable_by_id_and_index(mining_home):
    result = mine(mining_home.home)
    first = result.ordered_topics()[0]
    assert result.topic(first.id) is first
    assert result.topic("T1") is first
    assert result.topic("nope") is None


def test_written_but_violated(mining_home):
    """CLAUDE.md already says "do not reach for pip install" — and it happened."""
    result = mine(mining_home.home)
    pip = _topic_with(result, "不要用 pip")
    assert pip.written_in, "the pip topic is written in CLAUDE.md and still occurred"
    assert any("CLAUDE.md" in w["artifact"] for w in pip.written_in)
    assert result.written_but_violated


def test_determinism(mining_home):
    a = mine(mining_home.home).to_dict()
    b = mine(mining_home.home).to_dict()
    a["mine"]["generatedAt"] = b["mine"]["generatedAt"] = ""
    assert a == b


def test_last_n_narrows_the_window(mining_home):
    full = mine(mining_home.home)
    narrow = mine(mining_home.home, last_n=2)
    assert narrow.n_sessions == 2
    assert narrow.n_corrections < full.n_corrections


# --------------------------------------------------------------------------
# tokenisation
# --------------------------------------------------------------------------

def test_content_tokens_strip_stopwords_and_pattern_words():
    toks = content_tokens("不要用 pip，用 uv。")
    assert "pip" in toks and "uv" in toks
    assert "不要" not in toks          # the pattern word itself must not be a token
    assert "don" not in content_tokens("don't use pip")


def test_content_tokens_keep_alphanumeric_tokens():
    assert "12h" in content_tokens("重新来一个，12h的密码")
    assert "2026" not in content_tokens("在 2026 年")     # bare numbers are dropped


def test_jaccard_is_symmetric_and_bounded():
    a, b = {"x", "y"}, {"y", "z"}
    assert jaccard(a, b) == jaccard(b, a) == 1 / 3
    assert jaccard(set(), b) == 0.0
    assert jaccard(a, a) == 1.0


# --------------------------------------------------------------------------
# v1: sentence-level detection
# --------------------------------------------------------------------------

def test_split_sentences_keeps_the_ender():
    assert split_sentences("继续执行。还有，你能不能省着点用量？") == [
        "继续执行。", "还有，你能不能省着点用量？"]
    assert split_sentences("do this; not that. ok?") == ["do this;", "not that.", "ok?"]
    assert split_sentences("") == []


def test_the_quote_is_the_sentence_not_the_whole_turn(home_builder):
    home = home_builder({sid(1): [
        ("human", "2026-09-01T09:00:00.000Z",
         "先把测试跑一遍，看看有没有回归。然后不要用 pip，用 uv 装依赖。"),
        ("assistant", "2026-09-01T09:01:00.000Z", "ok"),
    ]})
    result = mine(home)
    c = result.corrections[0]
    assert c.quote() == "然后不要用 pip，用 uv 装依赖。"
    assert "回归" not in c.quote()
    assert "回归" in c.turn.quote()          # the full turn is still available


# --------------------------------------------------------------------------
# v1: the question filter
# --------------------------------------------------------------------------

def test_is_question_and_has_imperative():
    assert is_question("这个不是已经做完了吗？")
    assert is_question("are we done?")
    assert not is_question("不要用 pip。")
    assert has_imperative("不要用 pip，好吗？")
    assert has_imperative("你能不能省着点用量？")       # 省着点 is an imperative
    assert not has_imperative("这个不是已经做完了吗？")


def test_a_question_without_an_imperative_is_not_a_correction():
    for q in ["这个不是已经做完了吗？",
              "我们不是做自进化迭代的东西吗，怎么变成别的了？",
              "是不是应该先跑测试？",
              "isn't that wrong?"]:
        got = analyse_text(q)
        assert got.is_correction is False, q
        assert got.rejected and got.rejected[0]["reason"] == "question"


def test_a_question_that_carries_an_imperative_is_still_a_correction():
    for q in ["不要用 pip，好吗？", "你能不能省着点 fable5 用量？",
              "can you stop using curl?"]:
        assert analyse_text(q).is_correction is True, q


def test_question_filter_is_counted_in_the_report(home_builder):
    home = home_builder({sid(1): [
        ("human", "2026-09-01T09:00:00.000Z", "这个不是已经做完了吗？"),
        ("human", "2026-09-01T09:02:00.000Z", "不要用 pip。"),
    ]})
    result = mine(home)
    assert result.n_corrections == 1
    assert result.n_questions_filtered == 1
    assert result.to_dict()["mine"]["nQuestionsFiltered"] == 1


# --------------------------------------------------------------------------
# v1: the lookaround guards, one by one
# --------------------------------------------------------------------------

def test_every_named_lookaround_guard():
    for decoy in ["这个方案特别好", "别的事情先放着", "不用担心这个",
                  "是不是这样", "级别不够", "区别在哪", "不要紧的",
                  "他不停地跑", "不用客气"]:
        assert detect_patterns(decoy) == [], decoy
    # the guards must not swallow the real thing
    for real in ["别用 pip", "不要用 pip", "这不是我要的", "不用 pip 了",
                 "停一下"]:
        assert detect_patterns(real) != [], real


# --------------------------------------------------------------------------
# v1: reverts
# --------------------------------------------------------------------------

REVERT_SESSION = [
    ("human", "2026-09-01T09:00:00.000Z", "写一份报告到 report.md"),
    ("assistant", "2026-09-01T09:01:00.000Z", "Writing it."),
    ("tool", "2026-09-01T09:02:00.000Z", "Write",
     {"file_path": "/tmp/ws/report.md", "content": "draft\n"}),
    ("tool", "2026-09-01T09:03:00.000Z", "Bash",
     {"command": "git checkout -- /tmp/ws/report.md"}),
    ("human", "2026-09-01T09:04:00.000Z", "接着看下一个文件。"),
]


def test_detect_reverts_finds_git_undo_of_an_agent_edit(home_builder):
    from precedent.mine import load_sessions
    home = home_builder({sid(1): REVERT_SESSION})
    sess = load_sessions(home)[0]
    reverts = detect_reverts(sess)
    assert len(reverts) == 1
    assert reverts[0].undoes_agent_edit is True
    assert "/tmp/ws/report.md" in reverts[0].reverted_paths
    assert reverts[0].to_dict()["command"].startswith("git checkout --")


def test_a_revert_is_a_correction_with_no_words_at_all(home_builder):
    home = home_builder({sid(1): REVERT_SESSION})
    result = mine(home)
    assert len(result.reverts) == 1
    hit = [c for c in result.corrections if "revert-action" in c.signals]
    assert len(hit) == 1, "the git checkout is itself the correction"
    assert hit[0].base == 0.50
    assert hit[0].confidence >= 0.50
    assert detect_patterns(hit[0].turn.text) == []     # the turn has no pattern


def test_a_revert_that_does_not_touch_an_agent_edit_is_not_a_correction(home_builder):
    home = home_builder({sid(1): [
        ("human", "2026-09-01T09:00:00.000Z", "看一下仓库状态"),
        ("tool", "2026-09-01T09:02:00.000Z", "Bash",
         {"command": "git checkout -- /tmp/ws/somebody-elses.md"}),
        ("human", "2026-09-01T09:04:00.000Z", "好的。"),
    ]})
    result = mine(home)
    assert result.reverts and result.reverts[0].undoes_agent_edit is False
    assert not [c for c in result.corrections if "revert-action" in c.signals]


def test_zh_undo_words_are_strong_patterns():
    for w in ["撤销刚才的改动", "回滚到上一个版本", "把它改回去", "还原那个文件"]:
        assert detect_patterns(w), w
    assert "revert" in [p for p, _ in detect_patterns("please revert that commit")]
    assert "undo" in [p for p, _ in detect_patterns("undo the last edit")]


@pytest.mark.parametrize("cmd", [
    "git revert HEAD", "git restore src/x.py", "git checkout -- .",
    "git reset --hard origin/main", "git stash pop",
])
def test_every_revert_command_shape_is_recognised(home_builder, cmd):
    from precedent.mine import load_sessions
    home = home_builder({sid(1): [
        ("human", "2026-09-01T09:00:00.000Z", "改一下"),
        ("tool", "2026-09-01T09:02:00.000Z", "Bash", {"command": cmd}),
    ]})
    sess = load_sessions(home)[0]
    assert len(detect_reverts(sess)) == 1, cmd


# --------------------------------------------------------------------------
# v1: repeated instructions across sessions
# --------------------------------------------------------------------------

def test_a_restatement_in_another_session_is_mined_without_any_pattern(home_builder):
    home = home_builder({
        sid(1): [("human", "2026-09-01T09:00:00.000Z", "不要用 pip 装依赖，用 uv"),
                 ("assistant", "2026-09-01T09:01:00.000Z", "ok")],
        sid(2): [("human", "2026-09-02T09:00:00.000Z", "依赖用 uv 装，pip 就算了"),
                 ("assistant", "2026-09-02T09:01:00.000Z", "ok")],
    })
    result = mine(home)
    repeats = [c for c in result.corrections
               if "repeat-across-sessions" in c.signals]
    assert len(repeats) == 1
    assert repeats[0].turn.session_id == sid(2)
    assert detect_patterns(repeats[0].turn.text) == []
    assert repeats[0].base == 0.30


def test_a_restatement_in_the_same_session_is_not_double_counted(home_builder):
    home = home_builder({sid(1): [
        ("human", "2026-09-01T09:00:00.000Z", "不要用 pip 装依赖，用 uv"),
        ("human", "2026-09-01T09:05:00.000Z", "依赖用 uv 装，pip 就算了"),
    ]})
    result = mine(home)
    assert not [c for c in result.corrections
                if "repeat-across-sessions" in c.signals]


# --------------------------------------------------------------------------
# v1: the Chinese tokeniser and its graceful fallback
# --------------------------------------------------------------------------

def test_segmenter_reports_which_path_ran():
    assert segmenter_name() in ("jieba", "bigram")
    assert mine.__module__ == "precedent.mine"


def test_bigram_fallback_still_groups_the_same_topic(monkeypatch):
    """With the [zh] extra absent the tokeniser must degrade, not fail."""
    import precedent.mine as M
    monkeypatch.setattr(M, "_JIEBA", None, raising=False)
    monkeypatch.setattr(M, "_JIEBA_TRIED", True, raising=False)
    assert M.segmenter_name() == "bigram"
    toks = M.zh_tokens("装依赖")
    assert toks and all(len(t) == 2 for t in toks)
    a = M.content_tokens("不要用 pip 装依赖")
    b = M.content_tokens("装依赖不要用 pip")
    assert M.jaccard(a, b) > 0.5


def test_jieba_path_is_used_when_the_extra_is_installed(monkeypatch):
    """Simulate the [zh] extra with a stub, so the branch is covered either way."""
    import precedent.mine as M

    class FakeJieba:
        @staticmethod
        def cut(run, cut_all=False):
            return ["装", "依赖", "管理"] if "依赖" in run else list(run)

        @staticmethod
        def setLogLevel(level):
            pass

    monkeypatch.setattr(M, "_JIEBA", FakeJieba, raising=False)
    monkeypatch.setattr(M, "_JIEBA_TRIED", True, raising=False)
    assert M.segmenter_name() == "jieba"
    assert M.zh_tokens("装依赖管理") == ["依赖", "管理"]     # words, not bigrams


def test_a_heredoc_that_merely_contains_a_revert_is_not_a_revert(home_builder):
    """Observed on this machine: writing a test file whose body contains
    ``git checkout -- x`` was mined as four reverts.  It is not one."""
    from precedent.mine import load_sessions
    body = ('cat > /tmp/ws/t.sh <<"EOF"\n'
            'git checkout -- /tmp/ws/report.md\n'
            'EOF\n'
            'echo written')
    assert "git checkout" not in strip_heredocs(body)
    home = home_builder({sid(1): [
        ("human", "2026-09-01T09:00:00.000Z", "写个脚本"),
        ("tool", "2026-09-01T09:01:00.000Z", "Write",
         {"file_path": "/tmp/ws/report.md", "content": "draft"}),
        ("tool", "2026-09-01T09:02:00.000Z", "Bash", {"command": body}),
        ("human", "2026-09-01T09:03:00.000Z", "好的。"),
    ]})
    sess = load_sessions(home)[0]
    assert detect_reverts(sess) == []
    assert mine(home).reverts == []


def test_a_revert_inside_a_quoted_string_is_not_a_revert(home_builder):
    from precedent.mine import load_sessions
    home = home_builder({sid(1): [
        ("human", "2026-09-01T09:00:00.000Z", "查一下"),
        ("tool", "2026-09-01T09:01:00.000Z", "Write",
         {"file_path": "/tmp/ws/report.md", "content": "draft"}),
        ("tool", "2026-09-01T09:02:00.000Z", "Bash",
         {"command": 'grep -n "git checkout -- /tmp/ws/report.md" notes.txt'}),
        ("human", "2026-09-01T09:03:00.000Z", "好的。"),
    ]})
    assert detect_reverts(load_sessions(home)[0]) == []


def test_a_revert_after_a_command_separator_is_still_a_revert(home_builder):
    from precedent.mine import load_sessions
    for cmd in ['cd /tmp/ws && git checkout -- report.md',
                'echo hi; git restore report.md',
                'git reset --hard HEAD']:
        home = home_builder({sid(1): [
            ("human", "2026-09-01T09:00:00.000Z", "改一下"),
            ("tool", "2026-09-01T09:01:00.000Z", "Write",
             {"file_path": "/tmp/ws/report.md", "content": "draft"}),
            ("tool", "2026-09-01T09:02:00.000Z", "Bash", {"command": cmd}),
        ]})
        assert len(detect_reverts(load_sessions(home)[0])) == 1, cmd
