<!--
Copyright 2026 The precedent authors.
Licensed under the Apache License, Version 2.0 (the "License"); you may not
use this file except in compliance with the License. You may obtain a copy of
the License at http://www.apache.org/licenses/LICENSE-2.0
SPDX-License-Identifier: Apache-2.0
-->

# DEMO — precedent, on this machine, 2026-09-15

A live-demo dossier for the five-step loop (① propose → ② accept → ③ enforce →
④ audit → ⑤ re-evolve) of [`packages/precedent`](packages/precedent).

**Every command below was really run on this machine and every block of output
is pasted verbatim.** Where a number is uncomfortable it is still here: the
rule the demo was *supposed* to end on did **not** pass the gate, and §2 says
exactly why.

*One exception, marked where it appears:* the correction miner quotes your own
sentences back at you, and one of the mined quotes (topic **T16** in §1)
contains the author's real phone number and WeChat id. Those two digit strings
are masked as `178█████136`. Nothing else in this file is altered — and the
fact that a correction miner will happily print your phone number into a report
is itself worth knowing before you run `precedent mine --md` and paste the
result anywhere.

## The hard rule this dossier was produced under

> Nothing under the real `~/.claude` may be modified — not even `settings.json` —
> and no hook may be run against the live Claude Code config. `hooks install
> --apply` is fully implemented and tested, but on this machine it is only ever
> run with `--dry-run` (the default) or against a **throwaway home** under a
> scratchpad directory. **You** run the `--apply` (§6).

§7 is the audit that proves it: `~/.claude` fingerprinted (path → size, mtime,
sha256) before and after, every changed file listed and attributed.

## Reproducing this

```bash
cd "<repo>"
./scripts/dev.sh                    # three uv venvs on Python 3.12 + all three suites
export PATH="$PWD/packages/precedent/.venv/bin:$PATH"
precedent --version                 # precedent 0.1.0
```

Every `precedent …` line below is that entry point. Throwaway paths are shown
as shell variables, set once:

```bash
SP=/private/tmp/claude-501/…/scratchpad/eprep     # this session's scratchpad
FAKE="$SP/fakehome/.claude"                       # a Claude home that is NOT yours
FSTATE="$SP/fakestate"                            # a precedent state dir that is NOT yours
```

The transcripts under `~/.claude/projects` are **live** — five other Claude Code
sessions were running on this machine throughout (see §7) — so counts drift by
one or two between commands minutes apart. Where a run had to be reproducible
(§4) it reads a frozen copy of the transcripts instead, and says so.

---

## 1 · `precedent init` and `precedent mine`

### `precedent init`

```bash
$ precedent init
```

```
precedent 0.1.0 — 首屏（只读，零模型调用）
──────────────────────────────────────────────────────────────
 1. Claude home     : /Users/leonskennedy/.claude
 2. 状态目录        : /Users/leonskennedy/.precedent
 3. 会话            : 扫描 8 / 发现 8 个
 4. 学习工件        : 61 个（skill 44 / memory 10 / MEMORY.md 7）
 5. 从未被引用      : 42 个（可引用工件的 78%）
 6. 曾被截断        : 2 个工件在 ≥1 个会话里只加载了一部分
 7. 失效索引/缺文件 : 2 条
 8. 近重复工件      : 4 个
 9. 无人值守写入    : 最近 7 天 29 次（子代理 2 次，Bash 绕过记忆工具 16 次）
10. 已强制执行的先例: 0 条  → 下一步：precedent mine
──────────────────────────────────────────────────────────────
账本 /Users/leonskennedy/.precedent/ledger.jsonl（哈希链，2 条记录，校验 ok）

（receipts 扫描已存档: /Users/leonskennedy/.precedent/scans/receipts-20260915T075543Z.json）
```

`time`: `4.30s user 0.42s system 90% cpu 5.240 total`. Zero model calls, zero
bytes written under `~/.claude`.

### `precedent mine`

```bash
$ precedent mine
```

`time`: `1.86s user 0.47s system 86% cpu 2.680 total`.

```markdown
# precedent mine — 纠正挖掘 v1

Claude home: `/Users/leonskennedy/.claude`  ·  生成于 2026-09-15T07:55:57.522815+00:00  ·  中文分词 `bigram`

| 指标 | 值 |
|---|---|
| 会话 | 8 |
| 人类轮次（已剔除 skill/命令展开 119 条） | 164 |
| 检出纠正 | 20（12.2%） |
| 被问句过滤掉的句子 | 1 |
| 撤销/回滚动作 | 0 |
| 主题 | 18 |
| **重复主题**（≥2 个会话） | 1 |
| 已写进 CLAUDE.md/记忆、却仍在发生（written but violated） | 4 |

**自动编译：18 个主题，2 个编译成功，0 个通过出生门（PASS）**，2 个 FAIL。

你纠正了 agent **20** 次，其中 **1** 个主题重复出现；**4** 个主题已经写在 CLAUDE.md / 记忆里、但仍然发生。

## 主题

### T1 · `t-307dca53` — 主浏 头浏 打开 无头 浏览

- 次数 **2** · 会话 **2** · 重复 是 · 最高置信度 1.0 · t0 2026-09-07T08:26:39.020Z
- ⚠️ **written but violated**：已出现在 anker-hackathon-2026.md (memory)（命中 token: 打开, 无头）
- 原话：
  - “为什么这么卡，你不用去打开无头浏览器去操作主浏览器。” — `5f788631-bcd6-4110-8c37-9138da4b90f3.jsonl:6033` (5f788631, 2026-09-07, 置信度 1.0, 信号 pattern/repeated-topic, 模式 不用)
  - “不要打开一个新的无头浏览器，去我的主浏览器里帮我重新上传。” — `18baa501-3494-4bb8-bc4d-4f0b6652d835.jsonl:1210` (18baa501, 2026-09-15, 置信度 0.75, 信号 pattern/repeated-topic, 模式 不要/重新)
- 被纠正前的动作（violating action）：
  - `Bash` — cd "/Users/leonskennedy/AI coding open/Microduck 机器鸭二开/microcat" && until grep -qE "LOGIN OK|LOGIN TIMEOUT" marketing/so… [5f788631:6019]
  - `Bash` — cd "/Users/leonskennedy/AI coding open/Microduck 机器鸭二开/microcat" && cat > marketing/social/xhs_post.py <<'EOF' """Create… [5f788631:6014]
  - `SendUserFile` — (caption,display,files,status) [5f788631:6012]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-307dca53`

### T2 · `t-e5f8ffce` — 12h 密码 的密 的验 证码

- 次数 **2** · 会话 **1** · 重复 否 · 最高置信度 0.55 · t0 2026-09-05T03:29:11.139Z
- ⚠️ **written but violated**：已出现在 kimi-job-application-2026.md (memory)（命中 token: 证码, 验证）
- 原话：
  - “重新来一个，12h的密码” — `5f788631-bcd6-4110-8c37-9138da4b90f3.jsonl:4102` (5f788631, 2026-09-05, 置信度 0.55, 信号 pattern, 模式 重新)
  - “重新来一个12h的验证码” — `5f788631-bcd6-4110-8c37-9138da4b90f3.jsonl:4134` (5f788631, 2026-09-07, 置信度 0.3, 信号 pattern, 模式 重新)
- 被纠正前的动作（violating action）：
  - `SendUserFile` — (caption,display,files,status) [5f788631:4090]
  - `Bash` — SP=/private/tmp/claude-501/-Users-leonskennedy-AI-coding-open-Microduck------/5f788631-bcd6-4110-8c37-9138da4b90f3/scrat… [5f788631:4086]
  - `Bash` — SP=/private/tmp/claude-501/-Users-leonskennedy-AI-coding-open-Microduck------/5f788631-bcd6-4110-8c37-9138da4b90f3/scrat… [5f788631:4074]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-e5f8ffce`

### T3 · `t-1b81b48c` — 充完 完整 就补 补充

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.8 · t0 2026-09-07T06:16:22.298Z
- 原话：
  - “如果不是，就补充完整” — `5f788631-bcd6-4110-8c37-9138da4b90f3.jsonl:5363` (5f788631, 2026-09-07, 置信度 0.8, 信号 pattern, 模式 不是)
- 被纠正前的动作（violating action）：
  - `Bash` — python3 - <<'EOF' p="/Users/leonskennedy/.claude/projects/-Users-leonskennedy-AI-coding-open-Microduck------/memory/micr… [5f788631:5351]
  - `Artifact` — /Users/leonskennedy/AI coding open/Microduck 机器鸭二开/microcat/marketing/web/microcat_private.html [5f788631:5350]
  - `Artifact` — /Users/leonskennedy/AI coding open/Microduck 机器鸭二开/microcat/marketing/web/microcat_investor.html [5f788631:5331]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-1b81b48c`

### T4 · `t-23cebdab` — 之前 们关 你算 关心 前不

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.8 · t0 2026-09-04T10:09:54.647Z
- 原话：
  - “对了，我之前不是说，让你算出来大概可能的销量吗，还有就是成本啊什么之类的，老板他们关心的东西” — `5f788631-bcd6-4110-8c37-9138da4b90f3.jsonl:3990` (5f788631, 2026-09-04, 置信度 0.8, 信号 pattern, 模式 不是)
- 被纠正前的动作（violating action）：
  - `Bash` — export LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1; lark-cli im messages delete --as user --pa… [5f788631:3974]
  - `SendUserFile` — (caption,files,status) [5f788631:3973]
  - `Bash` — export LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1; cd /private/tmp/claude-501/-Users-leonsken… [5f788631:3967]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-23cebdab`

### T5 · `t-8903f168` — 么东 西怎

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.8 · t0 2026-09-02T09:06:02.572Z
- 原话：
  - “我说的是，怎么东西怎么用” — `b5d0b707-4824-4451-92e7-97e6ad2525c6.jsonl:515` (b5d0b707, 2026-09-02, 置信度 0.8, 信号 pattern, 模式 我说过)
- 被纠正前的动作（violating action）：
  - `Bash` — tail -15 /private/tmp/claude-501/-Users-leonskennedy/b5d0b707-4824-4451-92e7-97e6ad2525c6/tasks/bxkzstw8r.output; echo "… [b5d0b707:504]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-8903f168`

### T6 · `t-8beee290` — fable5 用量

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.8 · t0 2026-09-15T00:38:19.781Z
- 原话：
  - “还有，你能不能省着点fable5用量？” — `f04b24b3-2830-4463-af6b-d8a31560b47f.jsonl:840` (f04b24b3, 2026-09-15, 置信度 0.8, 信号 pattern, 模式 省着点)
- 被纠正前的动作（violating action）：
  - `Edit` — /Users/leonskennedy/.claude/projects/-Users-leonskennedy-AI-coding-open----Agent-Harness/memory/project-self-evolving-ha… [f04b24b3:810]
  - `Workflow` — /Users/leonskennedy/.claude/projects/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a3156… [f04b24b3:805]
  - `Read` — /Users/leonskennedy/.claude/projects/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a3156… [f04b24b3:789]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-8beee290`

### T7 · `t-aa7f904f` — 个小 主密 也设 享出 出去

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.8 · t0 2026-09-03T10:38:28.826Z
- 原话：
  - “主密码也设计成8个小时的，但是密码不要改，因为我已经分享出去了” — `5f788631-bcd6-4110-8c37-9138da4b90f3.jsonl:2757` (5f788631, 2026-09-03, 置信度 0.8, 信号 pattern, 模式 不要)
- 被纠正前的动作（violating action）：
  - `Bash` — cd "/Users/leonskennedy/AI coding open/Microduck 机器鸭二开/microcat" && echo "master: $(cat marketing/web/.vault_password)" … [5f788631:2744]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-aa7f904f`

### T8 · `t-aadd9e11` — 东部 个队 友来 另一 我另

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.8 · t0 2026-09-09T03:19:40.008Z
- 原话：
  - “不是我来报名，是我另一个队友来报名，因为要报东部赛区。” — `3de1ccb0-50dc-4b49-a937-4d6170ec18f3.jsonl:771` (3de1ccb0, 2026-09-09, 置信度 0.8, 信号 pattern, 模式 不是)
- 被纠正前的动作（violating action）：
  - `Bash` — cd "/Users/leonskennedy/AI coding open/清华绿色AI项目" && rm -rf draft_72854625_folder draft_7b9f8601_folder && rm -rf "/priva… [3de1ccb0:765]
  - `Bash` — cd "/Users/leonskennedy/AI coding open/清华绿色AI项目" && lark-cli docs +create --doc-format xml --content "@./draft_7b9f8601_… [3de1ccb0:761]
  - `Bash` — cd "/Users/leonskennedy/AI coding open/清华绿色AI项目" && cat > draft_7b9f8601_folder/draft.xml <<'XMLEOF' <title>今晚定稿 · 改动留痕<… [3de1ccb0:754]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-aadd9e11`

### T9 · `t-e1e2ac9b` — voe3 是视 更好 模型 的更

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.8 · t0 2026-09-03T11:13:21.664Z
- 原话：
  - “然后就是视频，能不能不要用voe3，用别的更好的模型？” — `5f788631-bcd6-4110-8c37-9138da4b90f3.jsonl:2967` (5f788631, 2026-09-03, 置信度 0.8, 信号 pattern, 模式 不要)
- 被纠正前的动作（violating action）：
  - `Artifact` — /Users/leonskennedy/AI coding open/Microduck 机器鸭二开/microcat/marketing/web/microcat_private.html [5f788631:2954]
  - `Artifact` — /Users/leonskennedy/AI coding open/Microduck 机器鸭二开/microcat/marketing/web/microcat_overview.html [5f788631:2953]
  - `Bash` — cd "/Users/leonskennedy/AI coding open/Microduck 机器鸭二开/microcat" && python3 - <<'EOF' p="marketing/web/build_page.py"; s… [5f788631:2943]
  建议检查: use_x_not_y x=别的更好的模型 y=voe3 → Bash deny
  - 自动编译: `p-a3e8172d` Bash deny [all] 模板 use_x_not_y → 出生门 **FAIL** ❌
    匹配器: [{"type": "input_regex", "field": "command", "regex": "(?i)(?<![A-Za-z0-9_])voe3(?![A-Za-z0-9_])"}]
    证据  : hit 0/3 actions (0/1 corrections) · after-t0 false 0/8763 (true 0, in 0/6 sessions) · pre-t0 0/332 (not counted)
    ✗ (a) HIT: the rule does not fire on any of the 3 violating action(s) recorded immediately before the 1 correction(s) in this topic — it is not a compilation of this correction
  `precedent compile t-e1e2ac9b`

### T10 · `t-e4beadb1` — 京啊 人在 北京 发给 吐了

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.8 · t0 2026-09-09T05:09:27.276Z
- 原话：
  - “申请人在北京啊，我吐了，你看一下要不要重新设计，然后就是把全部的物料地址发给我” — `3de1ccb0-50dc-4b49-a937-4d6170ec18f3.jsonl:946` (3de1ccb0, 2026-09-09, 置信度 0.8, 信号 pattern, 模式 不要/重新)
- 被纠正前的动作（violating action）：
  - `Bash` — cd "/Users/leonskennedy/AI coding open/清华绿色AI项目" && rm -rf draft_2bc656df_folder && rm -rf "/private/tmp/claude-501/-Use… [3de1ccb0:934]
  - `Bash` — cd "/Users/leonskennedy/AI coding open/清华绿色AI项目" && lark-cli docs +create --doc-format xml --content "@./draft_2bc656df_… [3de1ccb0:931]
  - `Bash` — cd "/Users/leonskennedy/AI coding open/清华绿色AI项目" && cat > draft_2bc656df_folder/draft.xml <<'XMLEOF' <title>洪眼 · 报名物料总包<… [3de1ccb0:924]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-e4beadb1`

### T11 · `t-ef526063` — 反正 很舒 正眼 看起 眼睛

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.8 · t0 2026-09-15T06:45:53.228Z
- 原话：
  - “反正眼睛看起来不是很舒服。” — `18baa501-3494-4bb8-bc4d-4f0b6652d835.jsonl:1450` (18baa501, 2026-09-15, 置信度 0.8, 信号 pattern, 模式 不是)
- 被纠正前的动作（violating action）：
  - `Bash` — cd "/Users/leonskennedy/AI coding open/英伟达AI项目/Kimi简历-终版/03-源文件" && python3 build.py A-岗位版内容.json kimi-dark /tmp/_t.html… [18baa501:1443]
  - `Bash` — set -e; S="/private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----AI--/18baa501-3494-4bb8-bc4d-4f0b6652d835/scrat… [18baa501:1435]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-ef526063`

### T12 · `t-2488f8f0` — 么东 到底 发个 底应 开发

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.55 · t0 2026-09-07T09:00:56.155Z
- 原话：
  - “所以说到底应该开发个什么东西” — `18baa501-3494-4bb8-bc4d-4f0b6652d835.jsonl:396` (18baa501, 2026-09-07, 置信度 0.55, 信号 pattern, 模式 应该)
- 被纠正前的动作（violating action）：
  - `Bash` — cd "/Users/leonskennedy/AI coding open/英伟达AI项目" && grep -n "^## " 报名信息草稿.md && sed -n '/^## 项目/,/^- 技术方案/p' 报名信息草稿.md | … [18baa501:389]
  - `Bash` — python3 - <<'EOF' p = "/Users/leonskennedy/.claude/skills/my-profile/projects.md" s = open(p, encoding='utf-8').read() a… [18baa501:379]
  - `Bash` — cat > "/Users/leonskennedy/AI coding open/英伟达AI项目/开发计划-CatForge.md" <<'EOF' # CatForge 技能锻造炉 · 10 天开发计划 > 选题依据：`选题报告-第三届… [18baa501:377]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-2488f8f0`

### T13 · `t-4d2201ee` — 个托 个烈 个评 书里 人黑

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.55 · t0 2026-09-04T08:13:20.097Z
- 原话：
  - “你去看飞书里面，我跟那个托托的聊天记录，她说的评委+投资人，应该是那个烈变·千人黑客松正式选手2群里面的信息，然后看哪个评委可能对我的项目感兴趣，去搞清楚” — `5f788631-bcd6-4110-8c37-9138da4b90f3.jsonl:3689` (5f788631, 2026-09-04, 置信度 0.55, 信号 pattern, 模式 应该)
- 被纠正前的动作（violating action）：
  - `Bash` — set -a; . ~/.lovart/credentials.env; set +a; cd ~/.claude/skills/lovart-api/scripts && python3 - <<'EOF' import os, sys,… [5f788631:3680]
  - `Bash` — set -a; . ~/.lovart/credentials.env; set +a; cd ~/.claude/skills/lovart-api/scripts && python3 - <<'EOF' import os, sys,… [5f788631:3669]
  - `Bash` — S=~/.claude/skills/lovart-api/scripts/agent_skill.py; sed -n 34,60p $S | grep -nE "def __init__|base_url|path_prefix|acc… [5f788631:3664]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-4d2201ee`

### T14 · `t-667e69fb` — 件夹 内容 到一 夹里 容放

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.55 · t0 2026-09-15T06:05:04.242Z
- ⚠️ **written but violated**：已出现在 microcat-project.md (memory), deliverables-to-feishu-not-disk.md (memory)（命中 token: 内容, 到一, 容放, 放到）
- 原话：
  - “你把重做的内容放到一个新的文件夹里，全部整理好。” — `18baa501-3494-4bb8-bc4d-4f0b6652d835.jsonl:1431` (18baa501, 2026-09-15, 置信度 0.55, 信号 pattern, 模式 重新)
- 被纠正前的动作（violating action）：
  - `Bash` — cd "/private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----AI--/18baa501-3494-4bb8-bc4d-4f0b6652d835/scratchpad/r… [18baa501:1425]
  - `Read` — /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----AI--/18baa501-3494-4bb8-bc4d-4f0b6652d835/scratchpad/A_dar… [18baa501:1415]
  - `Bash` — cd "/private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----AI--/18baa501-3494-4bb8-bc4d-4f0b6652d835/scratchpad/p… [18baa501:1412]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-667e69fb`

### T15 · `t-66d62c6e` — opus 代码 修改 具改 及收

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.55 · t0 2026-09-15T00:39:17.433Z
- 原话：
  - “后续代码修改，以及收据工具改用 Opus 5” — `f04b24b3-2830-4463-af6b-d8a31560b47f.jsonl:853` (f04b24b3, 2026-09-15, 置信度 0.55, 信号 pattern, 模式 改用)
- 被纠正前的动作：（无——这些纠正不紧跟任何工具调用）
  建议检查: require_field field=model value=opus → Agent|Workflow deny
  - 自动编译: `p-5e8c51c1` Agent|Workflow deny [any] 模板 require_field → 出生门 **FAIL** ❌
    匹配器: [{"type": "input_field_missing", "field": "model"}, {"type": "input_field_equals", "field": "model", "value": "opus", "negate": true, "case_sensitive": false}]
    证据  : hit 0/0 actions (0/1 corrections) · after-t0 false 4/8 (true 0, in 2/2 sessions) · pre-t0 49/49 (not counted)
    ✗ (a) HIT: the rule does not fire on any of the 0 violating action(s) recorded immediately before the 1 correction(s) in this topic — it is not a compilation of this correction
    ✗ (b) QUIET-AFTER: 4/8 = 50.0% of post-t0 eligible actions are tolerated (unpunished) fires > epsilon 2%
  `precedent compile t-66d62c6e`

### T16 · `t-ee8e1024` — 交一 信号 号码 微信 提交

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.55 · t0 2026-09-15T01:34:33.593Z
- ⚠️ **written but violated**：已出现在 kimi-job-application-2026.md (memory), nvidia-dgx-spark-hackathon-2026.md (memory)（命中 token: 信号, 微信, 提交）
- 原话：
  - “重新提交一遍，电话号码写178█████136 微信号：178█████528” — `18baa501-3494-4bb8-bc4d-4f0b6652d835.jsonl:1184` (18baa501, 2026-09-15, 置信度 0.55, 信号 pattern, 模式 重新)
- 被纠正前的动作（violating action）：
  - `Bash` — python3 - <<'EOF' c = "/Users/leonskennedy/.claude/skills/my-profile/competitions.md" s = open(c, encoding='utf-8').read… [18baa501:1178]
  - `Bash` — cd "/private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----AI--/18baa501-3494-4bb8-bc4d-4f0b6652d835/scratchpad/p… [18baa501:1175]
  - `Bash` — cd "/private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----AI--/18baa501-3494-4bb8-bc4d-4f0b6652d835/scratchpad/p… [18baa501:1165]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-ee8e1024`

### T17 · `t-ff98fa40` — 下载 载就

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.55 · t0 2026-09-02T09:04:37.509Z
- 原话：
  - “这个下载就可以用了吗，怎么又” — `b5d0b707-4824-4451-92e7-97e6ad2525c6.jsonl:499` (b5d0b707, 2026-09-02, 置信度 0.55, 信号 pattern, 模式 又)
- 被纠正前的动作（violating action）：
  - `Bash` — i=0; until ! pgrep -f "run.py --execution-provider" >/dev/null || [ $i -ge 10 ]; do sleep 3; i=$((i+1)); done; tail -5 /… [b5d0b707:492]
  - `Bash` — eval "$(/opt/homebrew/bin/brew shellenv)" && cd /Users/leonskennedy/Deep-Live-Cam && TF_CPP_MIN_LOG_LEVEL=3 ./venv/bin/p… [b5d0b707:489]
  - `Bash` — export HOMEBREW_API_DOMAIN="https://mirrors.ustc.edu.cn/homebrew-bottles/api" HOMEBREW_BOTTLE_DOMAIN="https://mirrors.us… [b5d0b707:486]
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-ff98fa40`

### T18 · `t-6e827ea8` — x-lab 一步 于明 众号 公众

- 次数 **1** · 会话 **1** · 重复 否 · 最高置信度 0.3 · t0 2026-09-08T03:09:34.347Z
- 原话：
  - “已完成报名的团队，如有需要，也可在新的截止时间前进一步完善项目材料，并重新提交（相关推文将于明日通过「清华 x-lab 公众号正式推送）。” — `3de1ccb0-50dc-4b49-a937-4d6170ec18f3.jsonl:4` (3de1ccb0, 2026-09-08, 置信度 0.3, 信号 pattern, 模式 重新)
- 被纠正前的动作：（无——这些纠正不紧跟任何工具调用）
  建议检查: （无法自动编译）no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  - 自动编译: 未编译 — no deterministic template matched these quotes; pass --template with its arguments, or try --llm
  `precedent compile t-6e827ea8`

## Limitations

- A correction is detected by surface pattern, never by a model. A turn that corrects the agent without any of the listed words is missed (false negative), and a turn that merely quotes one can be a false positive — the confidence score, not a boolean, is the honest output.
- Chinese is tokenised with bigram (character bigrams; `pip install precedent-cli[zh]` swaps in jieba); the similarity threshold (0.15) is a tunable, not a truth.
- The question filter drops a sentence that ends in ？/? and carries no imperative (1 dropped here). A question that is really an order phrased politely without any imperative word is therefore missed.
- "Written but violated" is substring/keyword matching against CLAUDE.md / memory / rule files. It shows the topic's words are in the file; it does not prove the file was loaded in the session where the correction happened (run `precedent scan` for that).
- Only the main transcript is read for human turns; a subagent has no human in it. Tool calls from subagents are still counted as recorded actions by the temporal birth gate.
```

*(Redaction note: in **T16** the two digit strings are masked — see the top of
this file. Everything else in that block is the miner's real output.)*

The two lines that matter for §2:

* **T6 `t-8beee290`** — “还有，你能不能省着点fable5用量？”, and the action
  recorded immediately before it is a **`Workflow`** call
  (`f04b24b3:805`).
* **T15 `t-66d62c6e`** — “后续代码修改，以及收据工具改用 Opus 5”, with **no**
  preceding tool call at all (the user had interrupted the turn).

That is one policy in two topics, which is why §2 compiles them together.

---

## 2 · Compiling `require_field model=opus`, and what the temporal gate said

The candidate the demo was planned around is the one the README uses as its DSL
example: *Agent/Workflow tool calls must set `model=opus`*, from session
`f04b24b3` (this session). Here is what actually happened when it met the
**temporal birth gate**.

### 2a · `Agent|Workflow` — the policy as the user stated it → **FAIL**

```bash
$ precedent compile t-8beee290,t-66d62c6e --template require_field --field model --value opus
```

```
# precedent compile t-8beee290,t-66d62c6e

主题 `t-8beee290`: fable5 用量  ·  1 次纠正 / 1 个会话
主题 `t-66d62c6e`: opus 代码 修改 具改 及收  ·  1 次纠正 / 1 个会话

## 规则（DSL v1）

{
  "id": "p-5e8c51c1",
  "schemaVersion": 1,
  "hook": "PreToolUse",
  "tool": "Agent|Workflow",
  "match": "any",
  "matchers": [
    {
      "type": "input_field_missing",
      "field": "model"
    },
    {
      "type": "input_field_equals",
      "field": "model",
      "value": "opus",
      "negate": true,
      "case_sensitive": false
    }
  ],
  "action": "deny",
  "scope": "project",
  "message": "Agent|Workflow 调用必须带 model=opus — 你在 2026-09-15 说：还有，你能不能省着点fable5用量？"
}

模板 require_field  field=model  value=opus

## 出生门（TEMPORAL BIRTH GATE v1，离线，零模型调用）

  t0（最早一次纠正）: 2026-09-15T00:38:19.781Z
  (a) 命中违规动作   : 1/3   → HIT
      （分母是动作数：每条纠正回看最近 3 次工具调用；1/2 条纠正有命中）
  (b) t0 之后可判定   : 8 次动作（该工具被用到的次数）
      其中触发         : 4 次 = 真阳 0（后面 3 轮内又被纠正） + 误触 4
      误触率           : 4/8 = 50.0%  (阈值 2%)
      误触分布（仅报告）: 出现在 2/2 个会话里 —— ε 是按动作算的，语料越大越宽松，请读绝对次数
  (c) t0 之前         : 49/49（只报告，不计入）
  扫描的工具调用     : 57
  判定               : **FAIL**
    ✗ (b) QUIET-AFTER: 4/8 = 50.0% of post-t0 eligible actions are tolerated (unpunished) fires > epsilon 2%

  命中的违规动作:
    - Workflow f04b24b3-2830-4463-af6b-d8a31560b47f.jsonl:805: /Users/leonskennedy/.claude/projects/-Users-leonskennedy-AI-coding-open----Agent
      → 紧接着的纠正 f04b24b3-2830-4463-af6b-d8a31560b47f.jsonl:840: “还有，你能不能省着点fable5用量？”

  t0 之后的误触（被容忍的触发）:
    - Workflow 18baa501-3494-4bb8-bc4d-4f0b6652d835.jsonl:1047 2026-09-15T01:04:38.710Z: 
    - Workflow 18baa501-3494-4bb8-bc4d-4f0b6652d835.jsonl:1519 2026-09-15T07:02:52.686Z: 
    - Workflow f04b24b3-2830-4463-af6b-d8a31560b47f.jsonl:1109 2026-09-15T02:47:12.702Z: 
    - Workflow f04b24b3-2830-4463-af6b-d8a31560b47f.jsonl:1120 2026-09-15T02:49:22.288Z: 

→ 出生门未通过：规则留在 candidates.json，不会被确认。改用 --template/--field/--x/--y/--path 重编译，或放弃这个主题。
```

Exit code 1. Read the counts, not the verdict:

* **(a) HIT passes.** The rule fires on `Workflow f04b24b3:805`, the call the
  user interrupted — this *is* a compilation of that correction.
* **(b) QUIET-AFTER fails, 4/8 = 50 %.** After `t0` there were 8 `Agent`/
  `Workflow` calls; 4 of them fired, none was followed by another correction.

### 2b · Why — a fact about the tools, found by reading the record

```
every Agent/Workflow tool_use in ~/.claude/projects, by input-key set
(483 .jsonl files: 8 main sessions + every subagent / workflow sidechain)

  Workflow  29    15  ('script',)
                   7  ('args', 'script')
                   3  ('resumeFromRunId', 'scriptPath')
                   2  ('args', 'resumeFromRunId', 'scriptPath')
                   1  ('description', 'script')
                   1  ('args', 'scriptPath')
  Agent     28    21  ('description', 'prompt', 'subagent_type')
                   4  ('description', 'model', 'prompt', 'run_in_background')
                   3  ('description', 'prompt', 'run_in_background', 'subagent_type')

  a `model` key at all:   Workflow  0/29        Agent  4/28  (all four == "opus")
  (main-session transcripts alone: Workflow 29, Agent 11 — 4 with model=opus)
```

**`Workflow` has no `model` input field at all.** A `Workflow` script declares
its models *inside the script*. So a rule that requires `model=opus` on
`Workflow` fires on every `Workflow` call ever made, forever. The gate is not
being fussy; it found a real defect in the rule.

### 2c · `Agent` only — quiet, but not a compilation of this correction → **FAIL**

```bash
$ precedent compile t-8beee290,t-66d62c6e --template require_field --field model --value opus --tool Agent
```

```
# precedent compile t-8beee290,t-66d62c6e

主题 `t-8beee290`: fable5 用量  ·  1 次纠正 / 1 个会话
主题 `t-66d62c6e`: opus 代码 修改 具改 及收  ·  1 次纠正 / 1 个会话

## 规则（DSL v1）

{
  "id": "p-9d8a8ee7",
  "schemaVersion": 1,
  "hook": "PreToolUse",
  "tool": "Agent",
  "match": "any",
  "matchers": [
    {
      "type": "input_field_missing",
      "field": "model"
    },
    {
      "type": "input_field_equals",
      "field": "model",
      "value": "opus",
      "negate": true,
      "case_sensitive": false
    }
  ],
  "action": "deny",
  "scope": "project",
  "message": "Agent 调用必须带 model=opus — 你在 2026-09-15 说：还有，你能不能省着点fable5用量？"
}

模板 require_field  field=model  value=opus

## 出生门（TEMPORAL BIRTH GATE v1，离线，零模型调用）

  t0（最早一次纠正）: 2026-09-15T00:38:19.781Z
  (a) 命中违规动作   : 0/3   → NO HIT
      （分母是动作数：每条纠正回看最近 3 次工具调用；0/2 条纠正有命中）
  (b) t0 之后可判定   : 4 次动作（该工具被用到的次数）
      其中触发         : 0 次 = 真阳 0（后面 3 轮内又被纠正） + 误触 0
      误触率           : 0/4 = 0.0%  (阈值 2%)
      误触分布（仅报告）: 出现在 0/1 个会话里 —— ε 是按动作算的，语料越大越宽松，请读绝对次数
  (c) t0 之前         : 24/24（只报告，不计入）
  扫描的工具调用     : 28
  判定               : **FAIL**
    ✗ (a) HIT: the rule does not fire on any of the 3 violating action(s) recorded immediately before the 2 correction(s) in this topic — it is not a compilation of this correction

→ 出生门未通过：规则留在 candidates.json，不会被确认。改用 --template/--field/--x/--y/--path 重编译，或放弃这个主题。
```

This is the interesting one:

* **(b) QUIET-AFTER is perfect: 0/4 = 0 %.** Every `Agent` call after `t0`
  carried `model=opus`. The agent obeyed, immediately — 00:39:17 the user said
  it, 00:40:24 the next `Agent` call had `model: "opus"`.
* **(c) before `t0`: 24/24** — reported, not counted. That is the whole point of
  the v1 gate: the pre-`t0` behaviour is not evidence against a policy that did
  not exist yet.
* **(a) HIT fails.** The action the user objected to was a `Workflow` call, so
  an `Agent`-only rule does not fire on it.

**The honest finding.** This correction is *preemptive*: “后续…改用 Opus 5”
(“from now on, switch to Opus 5”). Temporal birth gate v1 requires a **recorded
violation** to bind the rule to, and a forward-looking policy has none. The gate
returns `FAIL` (not `INSUFFICIENT`, which is reserved for `eligible_after < 3`).
That is the specified behaviour, and it is also a real limitation — written up
in §8 rather than smoothed over.

Both candidates are in `candidates.json` with their evidence, and both are
`BLOCKED` certificates in the hash-chained ledger:

```
2026-09-15T07:56:31+00:00 | certificate | p-5e8c51c1 | BLOCKED | 0fa15aac3789…
2026-09-15T07:57:19+00:00 | certificate | p-9d8a8ee7 | BLOCKED | 42aad3c7a313…
```

Neither was confirmed. Standing confirmation was given for candidates that
**PASS**; these did not.

### 2d · The candidate that did PASS → `precedent confirm`

The one repeated topic on this machine is T1 `t-307dca53` — “不要打开一个新的
无头浏览器” across two sessions. Under the **v0** gate (which asked "does it fire
on corrected sessions and stay quiet on uncorrected ones?") this rule was
`BLOCKED`, because the user had legitimately used headless browsers in the weeks
*before* asking for it to stop. Under the temporal gate:

```bash
$ precedent compile t-307dca53 --template dont_use --x headless
```

```
# precedent compile t-307dca53

主题 `t-307dca53`: 主浏 头浏 打开 无头 浏览  ·  2 次纠正 / 2 个会话

## 规则（DSL v1）

{
  "id": "p-036f5125",
  "schemaVersion": 1,
  "hook": "PreToolUse",
  "tool": "Bash",
  "match": "all",
  "matchers": [
    {
      "type": "input_regex",
      "field": "command",
      "regex": "(?i)(?<![A-Za-z0-9_])headless(?![A-Za-z0-9_])"
    }
  ],
  "action": "deny",
  "scope": "project",
  "message": "不要使用 headless — 你在 2026-09-07 说：为什么这么卡，你不用去打开无头浏览器去操作主浏览器。"
}

模板 dont_use  x=headless

## 出生门（TEMPORAL BIRTH GATE v1，离线，零模型调用）

  t0（最早一次纠正）: 2026-09-07T08:26:39.020Z
  (a) 命中违规动作   : 1/3   → HIT
      （分母是动作数：每条纠正回看最近 3 次工具调用；1/2 条纠正有命中）
  (b) t0 之后可判定   : 8164 次动作（该工具被用到的次数）
      其中触发         : 32 次 = 真阳 0（后面 3 轮内又被纠正） + 误触 32
      误触率           : 32/8164 = 0.4%  (阈值 2%)
      误触分布（仅报告）: 出现在 3/6 个会话里 —— ε 是按动作算的，语料越大越宽松，请读绝对次数
  (c) t0 之前         : 33/945（只报告，不计入）
  扫描的工具调用     : 9109
  判定               : **PASS**

  命中的违规动作:
    - Bash 5f788631-bcd6-4110-8c37-9138da4b90f3.jsonl:6014: cd "/Users/leonskennedy/AI coding open/Microduck 机器鸭二开/microcat" && cat > market
      → 紧接着的纠正 5f788631-bcd6-4110-8c37-9138da4b90f3.jsonl:6033: “为什么这么卡，你不用去打开无头浏览器去操作主浏览器。”

  t0 之后的误触（被容忍的触发）:
    - Bash 18baa501-3494-4bb8-bc4d-4f0b6652d835.jsonl:680 2026-09-11T11:52:01.895Z: cd "/private/tmp/claude-501/-Users-leonskennedy-AI-coding-op
    - Bash 18baa501-3494-4bb8-bc4d-4f0b6652d835.jsonl:721 2026-09-11T11:55:36.312Z: cd "/private/tmp/claude-501/-Users-leonskennedy-AI-coding-op
    - Bash 18baa501-3494-4bb8-bc4d-4f0b6652d835.jsonl:788 2026-09-11T12:03:21.850Z: cd "/private/tmp/claude-501/-Users-leonskennedy-AI-coding-op
    - Bash 18baa501-3494-4bb8-bc4d-4f0b6652d835.jsonl:1111 2026-09-15T01:19:53.112Z: cd "/private/tmp/claude-501/-Users-leonskennedy-AI-coding-op
    - Bash 18baa501-3494-4bb8-bc4d-4f0b6652d835.jsonl:1127 2026-09-15T01:22:02.232Z: cd "/private/tmp/claude-501/-Users-leonskennedy-AI-coding-op

→ `precedent confirm p-036f5125` 把它写进 precedents.json
```

```bash
$ precedent confirm p-036f5125
```

```
precedent: confirmed p-036f5125 → /Users/leonskennedy/.precedent/precedents.json
  Bash  deny  [all] [{"type": "input_regex", "field": "command", "regex": "(?i)(?<![A-Za-z0-9_])headless(?![A-Za-z0-9_])"}]
  出生门 PASS — hit 1/3 actions (1/2 corrections) · after-t0 false 32/8164 (true 0, in 3/6 sessions) · pre-t0 33/945 (not counted)
  下一步: precedent hooks install claude-code   (默认 dry-run)
```

(The `compile` run says `31/8160` and the `confirm` a minute later says
`32/8164`: the corpus grew by four tool calls and one fire in between, because
the other sessions on this machine were still working. Nothing is cached — the
gate is re-run against the record at confirm time, which is also why `confirm`
re-lints the rule.)

**Read the absolute number before you enjoy the percentage.** `32/8164 = 0.4 %`
is inside ε, but 32 is 32 times this rule would have interrupted a real session,
spread over 3 of 6 sessions — and §4g catches three of them in random draws, all
genuine `chromium.launch({ headless: true })` / `puppeteer.launch({ headless:
'new' })` calls in a *different* project, where the user never asked for
anything. ε is a per-action rate and a large corpus is a permissive denominator;
that is why the gate prints both units. If you do not want that rule live, §6
says how to drop it or how to scope it.

Sweep over all 18 mined topics: **2** were auto-compiled by `mine` (both FAIL),
**3 more rules** were compiled by hand from 2 further topics with an explicit
`--template` (2 FAIL, 1 PASS), and **14 topics never produced a rule at all** —
no deterministic template the extractor could detect in Chinese prose. Net:
**1 PASS, 1 confirmed, 0 enforced on this machine.**

---

## 3 · `precedent hooks install claude-code` — the dry run

Dry-run is the default. It now prints (this stage added both) **the concrete
backup path it would use**, and **the exact unified diff** from the bytes on
disk to the bytes `--apply` would write.

```bash
$ precedent hooks install claude-code
```

````markdown
# precedent hooks install claude-code  (--dry-run)

claude home          : /Users/leonskennedy/.claude
state dir            : /Users/leonskennedy/.precedent
confirmed precedents : 1
  - p-036f5125  Bash  deny  [all] (?i)(?<![A-Za-z0-9_])headless(?![A-Za-z0-9_])

## the six hooks

| event | script | timeout | what it does |
|---|---|---|---|
| PreToolUse | `pre_tool_use.py` | 5s | confirmed precedents (deny/ask) + ownership of governed writes |
| PostToolUse | `post_tool_use.py` | 5s | change-feed capture: what a Write/Edit/Bash call actually touched |
| UserPromptSubmit | `user_prompt_submit.py` | 5s | record the human turn — the one origin precedent treats as trusted |
| SessionStart | `session_start.py` | 10s | live receipt: this session started, with N active rules |
| InstructionsLoaded | `instructions_loaded.py` | 10s | live receipt: which instruction files reached the model, whole or truncated |
| Stop | `stop.py` | 10s | funnel counters for the session (turns, candidates, asks, loads) |

PreToolUse matcher   : `Bash|Edit|MultiEdit|NotebookEdit|Write`  (tools named by active rules ∪ Bash|Edit|MultiEdit|NotebookEdit|Write for ownership)
PostToolUse matcher  : `Write|Edit|Bash`

## files precedent would write (all of them inside the state dir)

  /Users/leonskennedy/.precedent/hooks/_plib.py
      1138 lines, 44819 bytes, sha256 a61f756294cd6d91…
  /Users/leonskennedy/.precedent/hooks/pre_tool_use.py
      29 lines, 1127 bytes, sha256 f916ad6dd36cdc56…
  /Users/leonskennedy/.precedent/hooks/post_tool_use.py
      29 lines, 1134 bytes, sha256 81b395471b2ff5e8…
  /Users/leonskennedy/.precedent/hooks/user_prompt_submit.py
      29 lines, 1155 bytes, sha256 569c2a2aa674e674…
  /Users/leonskennedy/.precedent/hooks/session_start.py
      29 lines, 1127 bytes, sha256 06c339aa37d5c0d0…
  /Users/leonskennedy/.precedent/hooks/instructions_loaded.py
      29 lines, 1171 bytes, sha256 99da035145262571…
  /Users/leonskennedy/.precedent/hooks/stop.py
      29 lines, 1103 bytes, sha256 8ef17107620b75c4…
  /Users/leonskennedy/.precedent/hooks/installed.json
      the install receipt `precedent hooks status` diffs against

settings.json        : /Users/leonskennedy/.claude/settings.json
  backup             : /Users/leonskennedy/.precedent/backups/settings-20260915T082141Z.json
                       (the exact path this run would copy your current file to, before any change)
  marker             : every entry we write contains `--precedent-hook` and points into /Users/leonskennedy/.precedent/hooks/
  idempotent         : no — this run changes the file

## settings.json — the exact diff

```diff
--- /Users/leonskennedy/.claude/settings.json  (now, sha256 db7c3d1c1da5…)
+++ /Users/leonskennedy/.claude/settings.json  (after --apply, sha256 6f695ff25701…)
@@ -1,5 +1,75 @@
 {
   "model": "claude-fable-5-1[1m]",
   "effortLevel": "xhigh",
-  "agentPushNotifEnabled": true
+  "agentPushNotifEnabled": true,
+  "hooks": {
+    "PreToolUse": [
+      {
+        "matcher": "Bash|Edit|MultiEdit|NotebookEdit|Write",
+        "hooks": [
+          {
+            "type": "command",
+            "command": "python3 /Users/leonskennedy/.precedent/hooks/pre_tool_use.py --precedent-hook PreToolUse",
+            "timeout": 5
+          }
+        ]
+      }
+    ],
+    "PostToolUse": [
+      {
+        "matcher": "Write|Edit|Bash",
+        "hooks": [
+          {
+            "type": "command",
+            "command": "python3 /Users/leonskennedy/.precedent/hooks/post_tool_use.py --precedent-hook PostToolUse",
+            "timeout": 5
+          }
+        ]
+      }
+    ],
+    "UserPromptSubmit": [
+      {
+        "hooks": [
+          {
+            "type": "command",
+            "command": "python3 /Users/leonskennedy/.precedent/hooks/user_prompt_submit.py --precedent-hook UserPromptSubmit",
+            "timeout": 5
+          }
+        ]
+      }
+    ],
+    "SessionStart": [
+      {
+        "hooks": [
+          {
+            "type": "command",
+            "command": "python3 /Users/leonskennedy/.precedent/hooks/session_start.py --precedent-hook SessionStart",
+            "timeout": 10
+          }
+        ]
+      }
+    ],
+    "InstructionsLoaded": [
+      {
+        "hooks": [
+          {
+            "type": "command",
+            "command": "python3 /Users/leonskennedy/.precedent/hooks/instructions_loaded.py --precedent-hook InstructionsLoaded",
+            "timeout": 10
+          }
+        ]
+      }
+    ],
+    "Stop": [
+      {
+        "hooks": [
+          {
+            "type": "command",
+            "command": "python3 /Users/leonskennedy/.precedent/hooks/stop.py --precedent-hook Stop",
+            "timeout": 10
+          }
+        ]
+      }
+    ]
+  }
 }
```

## settings.json — the exact result precedent would write

```json
{
  "model": "claude-fable-5-1[1m]",
  "effortLevel": "xhigh",
  "agentPushNotifEnabled": true,
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Edit|MultiEdit|NotebookEdit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/leonskennedy/.precedent/hooks/pre_tool_use.py --precedent-hook PreToolUse",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/leonskennedy/.precedent/hooks/post_tool_use.py --precedent-hook PostToolUse",
            "timeout": 5
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/leonskennedy/.precedent/hooks/user_prompt_submit.py --precedent-hook UserPromptSubmit",
            "timeout": 5
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/leonskennedy/.precedent/hooks/session_start.py --precedent-hook SessionStart",
            "timeout": 10
          }
        ]
      }
    ],
    "InstructionsLoaded": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/leonskennedy/.precedent/hooks/instructions_loaded.py --precedent-hook InstructionsLoaded",
            "timeout": 10
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/leonskennedy/.precedent/hooks/stop.py --precedent-hook Stop",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

## just the hooks block

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Edit|MultiEdit|NotebookEdit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/leonskennedy/.precedent/hooks/pre_tool_use.py --precedent-hook PreToolUse",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/leonskennedy/.precedent/hooks/post_tool_use.py --precedent-hook PostToolUse",
            "timeout": 5
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/leonskennedy/.precedent/hooks/user_prompt_submit.py --precedent-hook UserPromptSubmit",
            "timeout": 5
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/leonskennedy/.precedent/hooks/session_start.py --precedent-hook SessionStart",
            "timeout": 10
          }
        ]
      }
    ],
    "InstructionsLoaded": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/leonskennedy/.precedent/hooks/instructions_loaded.py --precedent-hook InstructionsLoaded",
            "timeout": 10
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/leonskennedy/.precedent/hooks/stop.py --precedent-hook Stop",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

## what the PreToolUse hook answers when a precedent matches

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "[precedent p-1a2b3c4d] 用 uv，不要用 pip -- from your correction on 2026-09-12: 不要用 pip，用 uv"
  }
}
```

No match → `{}` and exit 0. Any internal exception → exit 0, no stdout, and the error in `<state>/hooklog.jsonl`.

(Re-run with `--show-scripts` to print every generated script in full.)

precedent: dry run — nothing was written. Re-run with --apply to write the hook scripts and merge settings.json.
````

The two lines the hard rule exists for:

```
  backup             : /Users/leonskennedy/.precedent/backups/settings-20260915T082141Z.json
                       (the exact path this run would copy your current file to, before any change)
```

and the diff header, which names the sha256 on both sides:

```diff
--- /Users/leonskennedy/.claude/settings.json  (now, sha256 db7c3d1c1da5…)
+++ /Users/leonskennedy/.claude/settings.json  (after --apply, sha256 6f695ff25701…)
```

`db7c3d1c1da5…` is the sha256 of `~/.claude/settings.json` **now**, and §7
shows it is still that at the end of this stage.

### The guard on the real home

`--apply` against a path inside your real `~/.claude` refuses unless you add
`--i-know`:

```
$ precedent hooks install claude-code --apply          # WITHOUT --i-know, against the real ~/.claude
precedent: /Users/leonskennedy/.claude/settings.json is inside your real ~/.claude. Re-run with --i-know if you really mean it (a timestamped backup is written to /Users/leonskennedy/.precedent/backups first either way).
exit code: 2   (settings.json untouched, sha256 db7c3d1c1da5… before and after)
```

---

## 4 · The deny path, end to end, on a throwaway home

Everything in this section happens under `$SP`. The real `~/.claude` is only
ever **read**.

### 4a · A throwaway home holding a byte-identical copy of the real settings.json

```bash
$ FAKE="$SP/fakehome/.claude"; FSTATE="$SP/fakestate"
$ mkdir -p "$FAKE" "$FSTATE"
$ cp ~/.claude/settings.json "$FAKE/settings.json"
$ cp ~/.precedent/precedents.json "$FSTATE/precedents.json"
$ cp ~/.precedent/candidates.json "$FSTATE/candidates.json"
$ shasum -a 256 ~/.claude/settings.json "$FAKE/settings.json"
db7c3d1c1da5a7334250a7451f6385b5c922143e7eb51b6084cb576891b18697  /Users/leonskennedy/.claude/settings.json
db7c3d1c1da5a7334250a7451f6385b5c922143e7eb51b6084cb576891b18697  …/eprep/fakehome/.claude/settings.json
```

### 4b · The `model=opus` rule, force-confirmed **in the throwaway state dir only**

§2 ended with the opus rule `FAIL`ed by the gate, so it is not active in
`~/.precedent`. To prove the *enforcement* path for the exact payload the demo
is about — an `Agent` call with no `model` — it is confirmed here with
`--force`, in `$FSTATE`, which is thrown away at the end of this section.

```bash
$ precedent confirm p-5e8c51c1 --force --state-dir "$FSTATE" --claude-home "$FAKE"
```

```
precedent: confirmed p-5e8c51c1 → /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/precedents.json
  Agent|Workflow  deny  [any] [{"type": "input_field_missing", "field": "model"}, {"type": "input_field_equals", "field": "model", "value": "opus", "negate": true, "case_sensitive": false}]
  出生门 FAIL — hit 1/3 actions (1/2 corrections) · after-t0 false 4/8 (true 0, in 2/2 sessions) · pre-t0 49/49 (not counted)
  下一步: precedent hooks install claude-code   (默认 dry-run)
```

The override is on the record, not in a footnote: the stored rule carries
`"forcedPastGate": "FAIL"`, and `precedent report` prints the forced verdict
next to the rule. PROCTOR's rule — *mechanical rejection overrides LLM approval,
never the reverse* — is untouched: `--force` is a **human** override with an
audit trail, and no model can reach it.

### 4c · `hooks install --apply`, against the throwaway home

```bash
$ precedent hooks install claude-code --apply --state-dir "$FSTATE" --claude-home "$FAKE"
```

````
$ precedent hooks install claude-code --apply --state-dir "$FSTATE" --claude-home "$FAKE"
settings.json        : /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakehome/.claude/settings.json
  backup             : /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/backups/settings-20260915T081126Z.json
                       (the exact path this run would copy your current file to, before any change)
  marker             : every entry we write contains `--precedent-hook` and points into /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/
  idempotent         : no — this run changes the file

## settings.json — the exact diff

```diff
--- /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakehome/.claude/settings.json  (now, sha256 db7c3d1c1da5…)
+++ /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakehome/.claude/settings.json  (after --apply, sha256 dae199597bcf…)
@@ -1,5 +1,75 @@
 {
   "model": "claude-fable-5-1[1m]",
   "effortLevel": "xhigh",
-  "agentPushNotifEnabled": true
+  "agentPushNotifEnabled": true,
+  "hooks": {
+    "PreToolUse": [
+      {
+        "matcher": "Agent|Bash|Edit|MultiEdit|NotebookEdit|Workflow|Write",
+        "hooks": [
+          {
+            "type": "command",
+            "command": "python3 /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/pre_tool_use.py --precedent-hook PreToolUse",
+            "timeout": 5
+          }
+        ]
+      }
+    ],
+    "PostToolUse": [
+      {
+        "matcher": "Write|Edit|Bash",
+        "hooks": [
+          {
+            "type": "command",
+            "command": "python3 /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/post_tool_use.py --precedent-hook PostToolUse",
+            "timeout": 5
+          }
+        ]
+      }
+    ],
+    "UserPromptSubmit": [
+      {
+        "hooks": [
+          {
+            "type": "command",
+            "command": "python3 /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/user_prompt_submit.py --precedent-hook UserPromptSubmit",
+            "timeout": 5
+          }
+        ]
+      }
+    ],
+    "SessionStart": [
+      {
+        "hooks": [
+          {
+            "type": "command",
+            "command": "python3 /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/session_start.py --precedent-hook SessionStart",
+            "timeout": 10
+          }
+        ]
+      }
+    ],
+    "InstructionsLoaded": [
+      {
+        "hooks": [
+          {
+            "type": "command",
+            "command": "python3 /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/instructions_loaded.py --precedent-hook InstructionsLoaded",
+            "timeout": 10
+          }
+        ]
+      }
+    ],
+    "Stop": [
+      {
+        "hooks": [
+          {
+            "type": "command",
+            "command": "python3 /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/stop.py --precedent-hook Stop",
+            "timeout": 10
+          }
+        ]
+      }
+    ]
+  }
 }
```

[… elided: the "## settings.json — the exact result", "## just the hooks block"
 and "## what the PreToolUse hook answers" sections, byte-for-byte the same as
 §3 with /Users/leonskennedy/.precedent swapped for $FSTATE. Full capture:
 09-fake-install-apply.out, 316 lines …]

(Re-run with `--show-scripts` to print every generated script in full.)

precedent: wrote (state dir):
  /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/_plib.py
  /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/pre_tool_use.py
  /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/post_tool_use.py
  /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/user_prompt_submit.py
  /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/session_start.py
  /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/instructions_loaded.py
  /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/stop.py
precedent: merged into /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakehome/.claude/settings.json
  backup   : /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/backups/settings-20260915T081126Z.json
precedent: agent-created index rebuilt (0 path(s) — everything else in the governed trees counts as yours) → /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/agent_created.json
precedent: install receipt → /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/installed.json
````

`--apply` prints the same `backup             :` line the dry run does, and
then writes exactly that file — `settings-20260915T081126Z.json` appears both
in the plan and in the `backup   :` confirmation at the bottom. Same naming
function, one definition, tested
(`test_dry_run_prints_the_concrete_backup_path_and_the_exact_diff`).

### 4d · An `Agent` call with **no `model`** → **deny**

The payload is the shape Claude Code sends, built from a real call in this
session's transcript (`f04b24b3:870`, `description: "Fix acceptor gate lib per
reviews"`), with the `model` field removed:

```bash
$ cat "$SP/payload-agent-violation.json"
{"session_id":"f04b24b3-2830-4463-af6b-d8a31560b47f","transcript_path":"/Users/leonskennedy/.claude/projects/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f.jsonl","cwd":"/Users/leonskennedy/AI coding open/自进化Agent Harness","permission_mode":"acceptEdits","hook_event_name":"PreToolUse","tool_name":"Agent","tool_input":{"description":"Fix acceptor gate lib per reviews","prompt":"Read packages/acceptor and fix the review findings.","subagent_type":"general-purpose"}}

$ python3 "$FSTATE/hooks/pre_tool_use.py" --precedent-hook PreToolUse < "$SP/payload-agent-violation.json"
```

```json
{
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "[precedent p-5e8c51c1] Agent|Workflow 调用必须带 model=opus — 你在 2026-09-15 说：还有，你能不能省着点fable5用量？"
    }
}
```

`exit=0`, wall **46 ms** — which is one Python interpreter start. The decision
itself, from the hook log, is **0.68 ms**; the suite's budget is 300 ms
end-to-end for every one of the six scripts and there is a test for it.

The reason string carries both things the design promises: the **precedent id**
(`p-5e8c51c1`) and **the user's own words, with their date**
(`你在 2026-09-15 说：还有，你能不能省着点fable5用量？`).

### 4e · The same call with `model=opus` → **allow**

```bash
$ python3 "$FSTATE/hooks/pre_tool_use.py" --precedent-hook PreToolUse < "$SP/payload-agent-compliant.json"
{}
exit=0  wall=31 ms (one python start)
```

`{}` is the whole answer: no `hookSpecificOutput`, no `permissionDecision`,
nothing in front of the model. Adding `model: "opus"` to the same payload is the
only difference between the two runs.

### 4f · And the rule that actually passed the gate

```bash
$ python3 "$FSTATE/hooks/pre_tool_use.py" --precedent-hook PreToolUse < "$SP/payload-headless-violation.json"
```

```json
{
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "[precedent p-036f5125] 不要使用 headless — 你在 2026-09-07 说：为什么这么卡，你不用去打开无头浏览器去操作主浏览器。"
    }
}
```

All three decisions, as the hook recorded them:

```
$ cat "$FSTATE/hooklog.jsonl"
{"event": "deny", "hook": "PreToolUse", "rule": "p-5e8c51c1", "ms": 0.68, "session": "f04b24b3-2830-4463-af6b-d8a31560b47f", "ts": "2026-09-15T08:29:56Z"}
{"event": "allow", "hook": "PreToolUse", "ms": 0.37, "session": "f04b24b3-2830-4463-af6b-d8a31560b47f", "ts": "2026-09-15T08:29:56Z"}
{"event": "deny", "hook": "PreToolUse", "rule": "p-036f5125", "ms": 3.06, "session": "5f788631-bcd6-4110-8c37-9138da4b90f3", "ts": "2026-09-15T08:29:56Z"}
```

An `allow` is logged too — passing is silent to the *agent*, never to the
audit. (The 3.06 ms is the first regex compile in that process; 0.68 and 0.37
are field lookups.)

### 4g · 20 random recorded tool calls → **0 denies**

Reproducible on purpose: the transcripts are live, so this reads a **frozen
copy** (`cp -R ~/.claude/projects "$SP/frozen/projects"`, 483 `.jsonl` files,
main sessions *and* subagent sidechains) and samples with a fixed seed. Each
call is replayed through the **installed hook script as a subprocess**, exactly
as Claude Code runs it.

```bash
$ python3 "$SP/replay_hook.py" "$FSTATE/hooks/pre_tool_use.py" "$SP/frozen/projects" --n 20 --seed 20260915
$ python3 "$SP/replay_hook.py" "$FSTATE/hooks/pre_tool_use.py" "$SP/frozen/projects" --all --skip-tools ""
```

```
### 20 random recorded tool calls, both confirmed rules active
recorded tool calls in /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/frozen/projects: 17785  (tools skipped: Agent/Workflow)
 1. exit=0  17.7ms allow Bash         {"command": "cd \"/private/tmp/claude-501/-Users-leonskennedy-
      -Users-leonskennedy-AI-coding-open-----AI--/3de1ccb0-50dc-4b49-a937-4d6170ec18f3/subagents/workflows/wf_d351ab9c-5bc/agent-a01594225d992ab0d.jsonl:30
 2. exit=0  16.6ms allow WebFetch     {"url": "https://huggingface.co/papers/2606.23075", "prompt": 
      -Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/subagents/workflows/wf_0995fe5f-b5f/agent-aa5e5119d63fba935.jsonl:22
 3. exit=0  15.6ms allow Bash         {"command": "cd \"/private/tmp/claude-501/-Users-leonskennedy-
      -Users-leonskennedy-AI-coding-open-----AI--/3de1ccb0-50dc-4b49-a937-4d6170ec18f3/subagents/workflows/wf_ca05e494-bc1/agent-aebf92d09603fa8fb.jsonl:134
 4. exit=0  15.7ms allow Bash         {"command": "cd /private/tmp/claude-501/-Users-leonskennedy-AI
      -Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/subagents/workflows/wf_d2a2663c-8f0/agent-abad7a78b1a15c4a4.jsonl:145
 5. exit=0  15.7ms allow WebFetch     {"url": "https://turingpi.com/lfm2-5-2-6b-rk3588-llama-cpp-tur
      -Users-leonskennedy-AI-coding-open------/8ae0ea7c-ad1f-493f-a764-6278d6d500e8/subagents/workflows/wf_691b9004-698/agent-a8cc22550a82c1bea.jsonl:209
 6. exit=0  15.2ms allow Read         {"file_path": "/Users/leonskennedy/AI coding open/自进化Agent Har
      -Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/subagents/workflows/wf_f22de5ee-ba9/agent-ae5f9fad32d6a788e.jsonl:47
 7. exit=0  15.5ms allow WebFetch     {"url": "https://arxiv.org/abs/2609.02246", "prompt": "Give: t
      -Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/subagents/workflows/wf_d2a2663c-8f0/agent-a565fc7273d6ac192.jsonl:109
 8. exit=0  27.9ms allow Bash         {"command": "curl -sL \"https://huggingface.co/ibm-nasa-geospa
      -Users-leonskennedy-AI-coding-open-----AI--/3de1ccb0-50dc-4b49-a937-4d6170ec18f3/subagents/workflows/wf_e5119a76-6b1/agent-ab9af22016b140a15.jsonl:19
 9. exit=0  19.1ms allow Bash         {"command": "curl -s --max-time 25 -A \"Mozilla/5.0\" \"https:
      -Users-leonskennedy-AI-coding-open---ai--/86fdfe65-284d-4229-886a-958d57174069/subagents/agent-a919cd285dc566622.jsonl:221
10. exit=0  20.3ms allow WebFetch     {"url": "https://developer.nvidia.com/blog/transform-video-int
      -Users-leonskennedy-AI-coding-open----AI--/18baa501-3494-4bb8-bc4d-4f0b6652d835/subagents/workflows/wf_b69facb5-3cb/agent-ad05ed618f9198419.jsonl:223
11. exit=0  16.1ms allow Bash         {"command": "cd /private/tmp/claude-501/-Users-leonskennedy-AI
      -Users-leonskennedy-AI-coding-open-----AI--/3de1ccb0-50dc-4b49-a937-4d6170ec18f3/subagents/agent-ab44189e0cf51d8c7.jsonl:67
12. exit=0  16.0ms allow Bash         {"command": "cd \"/private/tmp/claude-501/-Users-leonskennedy-
      -Users-leonskennedy-AI-coding-open-----AI--/3de1ccb0-50dc-4b49-a937-4d6170ec18f3/subagents/workflows/wf_bb901f5f-ac0/agent-a39ff3e906dec1ad4.jsonl:27
13. exit=0  16.5ms allow Bash         {"command": "cd \"/private/tmp/claude-501/-Users-leonskennedy-
      -Users-leonskennedy-AI-coding-open-----AI--/3de1ccb0-50dc-4b49-a937-4d6170ec18f3/subagents/workflows/wf_d351ab9c-5bc/agent-abd778db0d1a148eb.jsonl:30
14. exit=0  15.8ms allow WebFetch     {"url": "https://zhuanlan.zhihu.com/p/2027805123313681309", "p
      -Users-leonskennedy-AI-coding-open----AI--/18baa501-3494-4bb8-bc4d-4f0b6652d835/subagents/workflows/wf_b69facb5-3cb/agent-aba40b4ad069c84b9.jsonl:76
15. exit=0  15.9ms allow WebSearch    {"query": "\"Faithful Self-Evolvers\" arxiv 2026"}
      -Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/subagents/workflows/wf_0995fe5f-b5f/agent-a6e77d63bf3739f46.jsonl:61
16. exit=0  15.8ms allow WebSearch    {"query": "\"SafeEvolve\" harness-policy co-evolution safety a
      -Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/subagents/workflows/wf_6e152a0a-e2a/agent-a4724ce5094e5dfbc.jsonl:142
17. exit=0  15.9ms allow WebSearch    {"query": "Colab Pro 定价 2026 中国 大陆 可用 支付 compute units A100 L4
      -Users-leonskennedy-AI-coding-open------/8ae0ea7c-ad1f-493f-a764-6278d6d500e8/subagents/workflows/wf_691b9004-698/agent-a58046d0487f6ece2.jsonl:320
18. exit=0  19.8ms allow Bash         {"command": "cd \"/private/tmp/claude-501/-Users-leonskennedy-
      -Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/subagents/workflows/wf_d2a2663c-8f0/agent-a25c13bd4138e142b.jsonl:148
19. exit=0  19.6ms allow WebSearch    {"query": "Anker Innovations overseas revenue share North Amer
      -Users-leonskennedy-AI-coding-open---ai--/86fdfe65-284d-4229-886a-958d57174069/subagents/workflows/wf_3857b5fd-797/agent-acb726466673adfc6.jsonl:74
20. exit=0  16.4ms allow WebFetch     {"url": "https://open.feishu.cn/document/server-docs/docs/minu
      -Users-leonskennedy-AI-coding-open---ai--/86fdfe65-284d-4229-886a-958d57174069/subagents/agent-abac9c9a716bd8ab7.jsonl:387

20 random recorded tool calls (seed 20260915) → deny 0 · ask 0 · allow 20   (slowest 28 ms, every exit 0)
rc=0

### the whole frozen corpus, every tool, both rules
recorded tool calls in /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/frozen/projects: 17842  (tools skipped: none)
active rules: ['p-036f5125', 'p-5e8c51c1']
swept 17842 recorded calls against 2 active rule(s) in 0.1s
  p-036f5125: deny/ask on 68/17842 = 0.38% of recorded calls
  p-5e8c51c1: deny/ask on 53/17842 = 0.30% of recorded calls
  total interruptions this rule set would have caused: 121
```

`replay_hook.py` is in the scratchpad, not the repo; the same chain is a
committed test — `test_demo_deny_path_on_a_throwaway_home` in
`packages/precedent/tests/test_integration_enforce.py` runs install → deny →
allow → 20 unrelated calls → uninstall on a synthetic home under `tmp_path`.

**The honest second half of that number.** Zero denies in a random 20 is a fact
about the *sample*; the sweep over all 17,842 recorded calls is the fact about
the *population*: **121 interruptions**, 68 from `p-036f5125` (headless) and 53
from `p-5e8c51c1` (the force-confirmed opus rule, which is why it is not
confirmed for real). Two earlier draws from main-session transcripts **only** (no
sidechains, a 2,229- then 2,231-call population — it grew between the two runs,
because the transcripts are live) hit 2 denies in 20 and then 1 in 20. All three
were real headless browser launches, e.g.

```
    -> [precedent p-036f5125] 不要使用 headless — 你在 2026-09-07 说：为什么这么卡，你不用去打开无头浏览器去操作主浏览器。
    -> matched at char 315: … const browser = await chromium.launch({ channel: 'chrome', headless: true });  const page = await…
```

That is the rule doing what it says, in a project where the user meant it and a
project where they did not. Scope it with `--cwd-glob` before you install, or
drop it (§6).

### 4h · `uninstall --apply` puts the file back, byte for byte

This stage fixed a real defect here: uninstall used to leave a `"hooks": {}`
object behind in a `settings.json` that never had one. The install receipt now
records whether the `hooks` key existed before the merge, and uninstall removes
only a key it created.

```bash
$ precedent hooks uninstall claude-code --apply --state-dir "$FSTATE" --claude-home "$FAKE"
```

````
$ precedent hooks uninstall claude-code --apply --state-dir "$FSTATE" --claude-home "$FAKE"
settings.json        : /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakehome/.claude/settings.json
  entries to remove  : 6
  backup             : /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/backups/settings-20260915T081144Z.json
                       (the exact path this run would copy your current file to, before any change)
  marker             : every entry we write contains `--precedent-hook` and points into /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/
  idempotent         : no — this run changes the file

## settings.json — the exact diff

```diff
--- /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakehome/.claude/settings.json  (now, sha256 dae199597bcf…)
+++ /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakehome/.claude/settings.json  (after --apply, sha256 db7c3d1c1da5…)
@@ -1,75 +1,5 @@
 {
   "model": "claude-fable-5-1[1m]",
   "effortLevel": "xhigh",
-  "agentPushNotifEnabled": true,
-  "hooks": {
-    "PreToolUse": [
-      {
-        "matcher": "Agent|Bash|Edit|MultiEdit|NotebookEdit|Workflow|Write",
-        "hooks": [
-          {
-            "type": "command",
-            "command": "python3 /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/pre_tool_use.py --precedent-hook PreToolUse",
-            "timeout": 5
-          }
-        ]
-      }
-    ],
-    "PostToolUse": [
-      {
-        "matcher": "Write|Edit|Bash",
-        "hooks": [
-          {
-            "type": "command",
-            "command": "python3 /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/post_tool_use.py --precedent-hook PostToolUse",
-            "timeout": 5
-          }
-        ]
-      }
-    ],
-    "UserPromptSubmit": [
-      {
-        "hooks": [
-          {
-            "type": "command",
-            "command": "python3 /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/user_prompt_submit.py --precedent-hook UserPromptSubmit",
-            "timeout": 5
-          }
-        ]
-      }
-    ],
-    "SessionStart": [
-      {
-        "hooks": [
-          {
-            "type": "command",
-            "command": "python3 /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/session_start.py --precedent-hook SessionStart",
-            "timeout": 10
-          }
-        ]
-      }
-    ],
-    "InstructionsLoaded": [
-      {
-        "hooks": [
-          {
-            "type": "command",
-            "command": "python3 /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/instructions_loaded.py --precedent-hook InstructionsLoaded",
-            "timeout": 10
-          }
-        ]
-      }
-    ],
-    "Stop": [
-      {
-        "hooks": [
-          {
-            "type": "command",
-            "command": "python3 /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/hooks/stop.py --precedent-hook Stop",
-            "timeout": 10
-          }
-        ]
-      }
-    ]
-  }
+  "agentPushNotifEnabled": true
 }
```

## settings.json — the exact result precedent would write

```json
{
  "model": "claude-fable-5-1[1m]",
  "effortLevel": "xhigh",
  "agentPushNotifEnabled": true
}
```

precedent: removed 6 hook entries from /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakehome/.claude/settings.json
  backup   : /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakestate/backups/settings-20260915T081144Z.json

$ shasum -a 256 ~/.claude/settings.json "$FAKE/settings.json"
db7c3d1c1da5a7334250a7451f6385b5c922143e7eb51b6084cb576891b18697  /Users/leonskennedy/.claude/settings.json
db7c3d1c1da5a7334250a7451f6385b5c922143e7eb51b6084cb576891b18697  /private/tmp/claude-501/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/scratchpad/eprep/fakehome/.claude/settings.json
$ diff ~/.claude/settings.json "$FAKE/settings.json" && echo "byte-identical"
byte-identical
````

Also checked in the same run: a second `--apply` reports `idempotent : yes —
nothing to change`, prints `(empty — the file on disk is already byte-identical
to what this run would write)` and writes **no second backup**; `hooks status`
reports `6/6 ours`, `drift : none`, exit 0.

### 4i · The one link this dossier does **not** close

Every piece of the chain is proven except *"Claude Code itself invokes the hook
named in `settings.json`"* — which needs the hook wired into a live config, and
the hard rule forbids that here. The attempt that stays inside the rule:

```
# CONTROL 3: the live-binary test I deliberately did NOT run against ~/.claude

$ CLAUDE_CONFIG_DIR="$FAKE" claude -p 'Use the Bash tool to run exactly this command …: echo "precedent headless smoke test"' \
      --model haiku --max-budget-usd 0.10 --no-session-persistence \
      --allowed-tools 'Bash(echo:*)' --output-format json

{
  "is_error": true,
  "result": "Not logged in · Please run /login",
  "terminal_reason": "api_error",
  "total_cost_usd": 0,
  "num_turns": 1
}

$ fpdiff  (real ~/.claude, across that attempt)
root            : /Users/leonskennedy/.claude
files before    : 1625
files after     : 1625
added           : 0
removed         : 0
content changed : 1
mtime-only      : 0  (same bytes)
  CHANGED     projects/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/subagents/workflows/wf_46243239-338/agent-a1da7412ccee66023.jsonl   1142427 -> 1155384 bytes
```

Credentials live in the config dir, so a throwaway `CLAUDE_CONFIG_DIR` cannot
log in, and copying `.credentials.json` into a scratchpad is not something this
dossier will do. §6 step 2 closes that link in about twenty seconds, on your
machine, with your hands.

---

## 5 · `report`, `improve --budget-usd 1`, `loop --dry-run`

### `precedent report`

```bash
$ precedent report
```

`time`: `6.16s user 0.90s system 92% cpu 7.600 total`.

````markdown
# precedent report

生成于 2026-09-15T08:11:59.269371+00:00 · state `/Users/leonskennedy/.precedent` · claude home `/Users/leonskennedy/.claude`

## 0. 告警（STARVATION）

**2 条告警。饿死绝不静默。**

| 级别 | 代码 | 说明 | 下一步 |
|---|---|---|---|
| STARVATION | `NOT_INSTALLED` | 1 条已确认的先例，但 settings.json 里没有我们的钩子——强制执行 = 0 | `precedent hooks install claude-code   # 然后 --apply` |
| STARVATION | `DRIFT` | 安装漂移 9 项：script missing: _plib.py — run `hooks install --apply`；script missing: pre_tool_use.py — run `hooks install --apply`；script missing: post_tool_use.py — run `hooks install --apply` | `precedent hooks status` |

## 1. 首屏

```
precedent 0.1.0 — 首屏（只读，零模型调用）
──────────────────────────────────────────────────────────────
 1. Claude home     : /Users/leonskennedy/.claude
 2. 状态目录        : /Users/leonskennedy/.precedent
 3. 会话            : 扫描 8 / 发现 8 个
 4. 学习工件        : 61 个（skill 44 / memory 10 / MEMORY.md 7）
 5. 从未被引用      : 42 个（可引用工件的 78%）
 6. 曾被截断        : 2 个工件在 ≥1 个会话里只加载了一部分
 7. 失效索引/缺文件 : 2 条
 8. 近重复工件      : 4 个
 9. 无人值守写入    : 最近 7 天 29 次（子代理 2 次，Bash 绕过记忆工具 16 次）
10. 已强制执行的先例: 1 条
──────────────────────────────────────────────────────────────
账本 /Users/leonskennedy/.precedent/ledger.jsonl（哈希链，8 条记录，校验 ok）
```

## 2. 漏斗：proposed → accepted → activated → attributed

| 阶段 | 数量 | 含义 |
|---|---|---|
| ① proposed | 4 | 规则候选 4 + 治理树写入候选 0 + 提案（examiner / 夜间改进器）0 |
| ② accepted | 1 | 过了门并被你确认的先例 1 + 你接受的写入 0 + 你确认的提案 0（确认提案只记录决定，precedent 不会替你写文件） |
| ③ activated | 0 | 现在真的在 settings.json 的 PreToolUse matcher 覆盖范围里 |
| ④ attributed | 0 | 钩子日志里真的触发过的规则 |

钩子调用 0 次，其中触发 0 次（n/a）。所有权守卫：关。

⚠️ 1 条已接受但未激活——这就是全行业的 “written but never loaded”，本机 60 个工件里 41 个从未被引用的同一格。

## 3. 待办（docket）

- 待办 3 条（规则 3 / 写入 0），已推迟 0 条

| id | 类型 | 年龄 | 证据 |
|---|---|---|---|
| `p-9d8a8ee7` | rule | 0 天 | 出生门 FAIL ❌ — hit 0/3 actions (0/2 corrections) · after-t0 false 0/4 (t |
| `p-5e8c51c1` | rule | 0 天 | 出生门 FAIL ❌ — hit 1/3 actions (1/2 corrections) · after-t0 false 4/8 (t |
| `p-6161ab13` | rule | 0 天 | 出生门 FAIL ❌ — 2/2 vs 2/5 |

`precedent docket --batch` 看全部；`precedent docket confirm|reject|snooze <id>`。

## 4. 治理树所有权

- 已声明所有权的路径：0 条（`/Users/leonskennedy/.precedent/owners.json`）
- 钩子记录的治理树写入：0 次（前台 0 / 子代理 0），其中被拦下询问 0 次
  （钩子还没装，或者装了之后还没有人写过治理树。`precedent hooks install claude-code` 先看 dry-run。）

## 5. 加载收据（live 优先）

- 没有实时收据（SessionStart / InstructionsLoaded 钩子未安装，或这些会话早于安装时间）。此时状态全部来自转录推断，`unknown` 是诚实答案。

## 6. 纠正挖掘

- 人类轮次 165，检出纠正 20（12.1%）
- 主题 18，其中重复主题 1
- written but violated: 4

| 主题 | 次数 | 会话 | 重复 | 已写入 | 标签 |
|---|---|---|---|---|---|
| `t-307dca53` | 2 | 2 | 是 | 是 | 主浏 头浏 打开 无头 浏览 |
| `t-e5f8ffce` | 2 | 1 | 否 | 是 | 12h 密码 的密 的验 证码 |
| `t-1b81b48c` | 1 | 1 | 否 | 否 | 充完 完整 就补 补充 |
| `t-23cebdab` | 1 | 1 | 否 | 否 | 之前 们关 你算 关心 前不 |
| `t-8903f168` | 1 | 1 | 否 | 否 | 么东 西怎 |
| `t-8beee290` | 1 | 1 | 否 | 否 | fable5 用量 |
| `t-aa7f904f` | 1 | 1 | 否 | 否 | 个小 主密 也设 享出 出去 |
| `t-aadd9e11` | 1 | 1 | 否 | 否 | 东部 个队 友来 另一 我另 |
| `t-e1e2ac9b` | 1 | 1 | 否 | 否 | voe3 是视 更好 模型 的更 |
| `t-e4beadb1` | 1 | 1 | 否 | 否 | 京啊 人在 北京 发给 吐了 |
| `t-ef526063` | 1 | 1 | 否 | 否 | 反正 很舒 正眼 看起 眼睛 |
| `t-2488f8f0` | 1 | 1 | 否 | 否 | 么东 到底 发个 底应 开发 |

## 7. 已确认的先例（enforced）

| id | tool | action | matchers | 出生门 | 来源纠正 | 触发 |
|---|---|---|---|---|---|---|
| `p-036f5125` | Bash | deny | `[{"type": "input_regex", "field": "command", "regex": "(?i)(` | PASS hit 1/3 actions (1/2 corrections) · after-t0 false 32/8164 (true 0, in 3/6 sessions) · pre-t0 33/945 (not counted) | 为什么这么卡，你不用去打开无头浏览器去操作主浏览器。 | 0 |

## 8. 支出表

- 我们自己的 `claude -p`：**$0.0000**，0 次调用（精确值，来自 spend.jsonl；每次都带 --max-budget-usd）
- 会话转录里的 token（不是我们花的，是这些会话本身的用量）：输入 960,001 / 输出 17,557,405 / 缓存读 4,037,637,830 / 缓存写 309,955,235
  - 按 2026-06-24 的官方 list price 折算 **约 $1032.69**（估算：订阅用量并不按 token 计费；缓存读按 0.1×、缓存写按 1.25× 输入价）

| 模型 | 会话 | 输入 | 输出 | 估算 $ |
|---|---|---|---|---|
| `claude-fable-5-1` | 2 | 31,362 | 1,999,572 | 1032.69 |
| `<synthetic>` | 6 | 928,639 | 15,557,833 | 0.00 |
  - 未定价的模型（只记 token，不折算）：<synthetic>

## 9. 最近 7 天

| 日期 | 新候选 | 确认 | 拒绝 | 推迟 | 钩子调用 | 触发 | 异常 | 会话 | $ |
|---|---|---|---|---|---|---|---|---|---|
| 2026-09-15 | 4 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 |

## 10. 账本尾部（哈希链）

| ts | type | candidate | event/decision | hash |
|---|---|---|---|---|
| 2026-09-15T02:03:46.303799+00:00 | certificate | `precedent:init` | HOLD | `980587aaa447…` |
| 2026-09-15T02:04:41+00:00 | certificate | `p-6161ab13` | BLOCKED | `cbee125454be…` |
| 2026-09-15T07:56:31+00:00 | certificate | `p-5e8c51c1` | BLOCKED | `0fa15aac3789…` |
| 2026-09-15T07:57:19+00:00 | certificate | `p-9d8a8ee7` | BLOCKED | `42aad3c7a313…` |
| 2026-09-15T07:59:11+00:00 | certificate | `p-036f5125` | HOLD | `46e7932c5f0f…` |
| 2026-09-15T08:01:29+00:00 | certificate | `p-9d8a8ee7` | BLOCKED | `bdcf65b40145…` |
| 2026-09-15T08:01:31+00:00 | certificate | `p-036f5125` | HOLD | `1f25288a5936…` |
| 2026-09-15T08:01:31+00:00 | certificate | `p-036f5125` | ACCEPT | `417268793675…` |

## 11. 安装状态

```
# precedent hooks status

installed          : no
settings.json      : /Users/leonskennedy/.claude/settings.json  (present)
entries            : 0/6 ours
PreToolUse matcher : Bash|Edit|MultiEdit|NotebookEdit|Write
active rules       : 1

scripts:
  missing     _plib.py               -  /Users/leonskennedy/.precedent/hooks/_plib.py
  missing     pre_tool_use.py        -  /Users/leonskennedy/.precedent/hooks/pre_tool_use.py
  missing     post_tool_use.py       -  /Users/leonskennedy/.precedent/hooks/post_tool_use.py
  missing     user_prompt_submit.py  -  /Users/leonskennedy/.precedent/hooks/user_prompt_submit.py
  missing     session_start.py       -  /Users/leonskennedy/.precedent/hooks/session_start.py
  missing     instructions_loaded.py -  /Users/leonskennedy/.precedent/hooks/instructions_loaded.py
  missing     stop.py                -  /Users/leonskennedy/.precedent/hooks/stop.py

DRIFT (9):
  ! script missing: _plib.py — run `hooks install --apply`
  ! script missing: pre_tool_use.py — run `hooks install --apply`
  ! script missing: post_tool_use.py — run `hooks install --apply`
  ! script missing: user_prompt_submit.py — run `hooks install --apply`
  ! script missing: session_start.py — run `hooks install --apply`
  ! script missing: instructions_loaded.py — run `hooks install --apply`
  ! script missing: stop.py — run `hooks install --apply`
  ! settings.json is missing 6 of our entries: InstructionsLoaded, PostToolUse, PreToolUse, SessionStart, Stop, UserPromptSubmit
  ! not installed (no installed.json, no entries in settings.json)
```

## 12. 诚实的限制

- v1 只做确定性检查（DSL 正则 + 字段断言）；可执行检查与判据型检查不在范围内。
- 纠正靠表层模式识别，会漏也会误判；置信度是输出的一部分，不是装饰。
- 出生门用的是历史会话，不是留出集：它证明规则在纠正发生那一刻之后「该响时响、不该响时静」，不证明它在未来会话上泛化。
- 漏斗的 ④ attributed 只数“钩子真的触发过”，不证明 agent 因此改了行为；要的是可核对，不是归因。
- 支出表里只有 `claude -p` 那一行是精确的；转录 token 折算是按 list price 的估算，订阅用量并不按 token 计费。
- 所有权默认规则是“没有 agent 创建记录、且文件存在 = 你的”。带外创建的文件因此算你的（保守方向：ask，不是 deny）。
- 只有 `hooks install/uninstall --apply` 会写 settings.json，且写之前先把原文件按时间戳备份到 `<state>/backups/`；其余命令对 Claude home 只读。

- （receipts）1/8 scanned session(s) carry no `prompt_snapshot` attachment; for those, non-skill load status is `unknown`, not `not_loaded` — the transcript simply does not record what the system prompt contained: b5d0b707
- （receipts）46 artifact(s) have no attributed write in the scanned sessions. That is expected for hand-written or vendor-shipped files and for sessions whose transcripts were deleted or are outside --project/--last.
- （receipts）None of the 10 memory entry *bodies* was ever observed in a recorded context: Claude Code loads MEMORY.md (the index) every session and pulls an entry's body only when it recalls it. `not_loaded` on an entry therefore means 'this session never recalled it', not 'memory is broken'.
- （receipts）Load detection is textual: it proves the artifact's text is present in a recorded context blob. It cannot prove the model attended to it. Absence of a `<cc-memory>` tag or Skill invocation is weak evidence of non-use, not proof.
````

### `precedent improve --budget-usd 1`

The only command here that spends money. Every call carries
`--max-budget-usd 0.30`, `--no-session-persistence`, `--model sonnet`,
`--output-format json`, `--safe-mode --tools "" --disable-slash-commands`, and
never `--dangerously-skip-permissions`. Each is *reserved* at its per-call cap
before it runs, so a binary that ignores the flag and reports `$0.00` still
cannot buy more than `floor(budget / per-call cap)` calls.

```bash
$ precedent improve --budget-usd 1
```

`time`: `5.97s user 1.54s system 5% cpu 2:26.72 total`.

```markdown
# precedent improve — 夜间改进器

失败签名聚类 26 个（试了前 3 个） · 花费 $0.1627 / 预算 $1

| # | kind | 次数 | 会话 | 签名 |
|---|---|---|---|---|
| 1 | retry | 25 | 1 | `Read: …/leonskennedy/AI coding open…/vN/kN_N.png` |
| 2 | retry | 23 | 1 | `Read: …/scratchpad/td_MC-N.png` |
| 3 | retry | 15 | 1 | `Read: …/leonskennedy/AI coding open…/概念图与场景图/kN_N.png` |
| 4 | retry | 10 | 1 | `Read: …/tool-results/bovNqmxfm.txt` |
| 5 | tool_error | 6 | 4 | `Bash: Traceback (most recent call last):` |
| 6 | retry | 6 | 1 | `Read: …/scratchpad/wild_N.jpg` |
| 7 | retry | 5 | 1 | `Bash: cd "…/leonskennedy/AI coding open/Microduck 机器鸭二开/microcat" && t` |
| 8 | tool_error | 4 | 3 | `Bash: Permission for this action was denied by the Claude Code auto mo` |
| 9 | retry | 3 | 1 | `Bash: cd "…/workflows/wf_N-N" && echo "results: $(grep -c '"type":"res` |
| 10 | retry | 3 | 1 | `Read: …/app/wb-flowN.png` |

## HOLD（没有证据）

- `d-193a8982` skill — no evidence: the examiner found no eligible cassette for this candidate

## 被机械检查拒绝（回灌 rejected.jsonl，下次作为反例）

- fc-7ce0681b: schema: no JSON object in the model's answer
- fc-818c8aa1: schema: no JSON object in the model's answer

机械拒绝永远覆盖模型的批准，反过来不行（PROCTOR）。`precedent docket` 看证据，应用与否由你自己动手。
```

**$0.1627 of a $1 budget, 3 calls, 1 survivor, and the survivor is a `HOLD`.**
Two drafts were thrown away by the schema check before anything looked at their
content; the third passed schema, declared-paths and lint and then had no
eligible cassette to be measured on, so it is `HOLD — no evidence`. Nothing was
applied. `rejected.jsonl` keeps both rejections as negative examples for the
next prompt — that is ⑤.

```
$ cat ~/.precedent/spend.jsonl
{"ts": "2026-09-15T08:12:35.008092+00:00", "command": "improve", "model": "sonnet", "budgetUsd": 0.3, "costUsd": 0.045719800000000005, "durationMs": 38286, "cluster": "fc-7ce0681b", "clusterKind": "retry", "ok": true, "error": null}
{"ts": "2026-09-15T08:12:35.008092+00:00", "command": "improve", "model": "sonnet", "budgetUsd": 0.3, "costUsd": 0.0474078, "durationMs": 39845, "cluster": "fc-d48ff2d4", "clusterKind": "retry", "ok": true, "error": null}
{"ts": "2026-09-15T08:12:35.008092+00:00", "command": "improve", "model": "sonnet", "budgetUsd": 0.3, "costUsd": 0.06957580000000001, "durationMs": 64972, "cluster": "fc-818c8aa1", "clusterKind": "retry", "ok": true, "error": null}
```

Three calls, each capped at `budgetUsd: 0.3`, total `$0.1627`. `fc-7ce0681b`
and `fc-818c8aa1` are the two the schema check threw away — they were paid for
and rejected anyway, which is the correct order of operations.

### `precedent loop --dry-run`

```bash
$ precedent loop --dry-run
```

`time`: `2.95s user 0.60s system 86% cpu 4.087 total`.

```markdown
# precedent loop — 一个夜间周期（--dry-run）

claude home `/Users/leonskennedy/.claude` · state `/Users/leonskennedy/.precedent`
预算 $0 · 实花 $0.0000

## ① propose · correction miner

`precedent mine --similarity 0.15`

- 8 个会话 · 165 条人类输入 (跳过 119 条展开)
- 20 条纠正 (12.1%) → 18 个主题，重复主题 1 个
- 自动编译 2 个，出生门 PASS 0 个 (分词 bigram)

## ① propose · nightly improver（未执行）

`precedent improve --budget-usd 0 --dry-run`

- 26 个失败签名聚类（--dry-run：不调用模型）
-   · [retry] x25 / 1 会话 — Read: …/leonskennedy/AI coding open…/vN/kN_N.png
-   · [retry] x23 / 1 会话 — Read: …/scratchpad/td_MC-N.png
-   · [retry] x15 / 1 会话 — Read: …/leonskennedy/AI coding open…/概念图与场景图/kN_N.png
-   · [retry] x10 / 1 会话 — Read: …/tool-results/bovNqmxfm.txt
-   · [tool_error] x6 / 4 会话 — Bash: Traceback (most recent call last):

## ② accept · examiner + paired gate（未执行）

`precedent examine --candidate <path-or-id> --dry-run`

- 7 个合格磁带 / 8 个会话（其余 1 个没有终端验证命令，也没有可当评分器的先例）
-   · case-f04b24b3 — `cd "/Users/leonskennedy/AI coding open/自进化Agent Harness/packages/prece`
-   · case-8ae0ea7c — `(precedents only)`
-   · case-3de1ccb0 — `(precedents only)`
-   · case-86fdfe65 — `(precedents only)`
-   · case-18baa501 — `(precedents only)`
- 没有 --candidate：只列出磁带

## ③ enforce · hooks

`precedent hooks status claude-code`

- 已确认先例 1 条 · 已安装 否 · 我们的条目 0/6
- 漂移: script missing: _plib.py — run `hooks install --apply`; script missing: pre_tool_use.py — run `hooks install --apply`; script missing: post_tool_use.py — run `hooks install --apply`

## ④ audit · docket + alarms

`precedent report`

- docket 待办 4 条（饿死 ≥7 天 0 条）
- 漏斗 提出 5 → 接受 1 → 激活 0 → 归因 0
-   ALARM NOT_INSTALLED: 1 条已确认的先例，但 settings.json 里没有我们的钩子——强制执行 = 0
-   ALARM DRIFT: 安装漂移 9 项：script missing: _plib.py — run `hooks install --apply`；script missing: pre_tool_use.py — run `hooks install --a

## ⑤ re-evolve · negative examples

`cat /Users/leonskennedy/.precedent/rejected.jsonl`

- 2 条被拒草稿会作为反例进入下一轮提示词
-   · schema: no JSON object in the model's answer
-   · schema: no JSON object in the model's answer

> --dry-run：本次没有任何模型调用，也没有向 Claude home 写入任何字节
```

All five steps, **$0.00**, no model call, no byte written under `~/.claude`.

---

## 6 · USER RUNBOOK — closing the loop live

Three commands. You run them; nothing above touched your `settings.json`.

```bash
export PATH="<repo>/packages/precedent/.venv/bin:$PATH"

# 1 — enforce.  Merges six hooks into ~/.claude/settings.json AFTER copying the
#     current file to ~/.precedent/backups/settings-<ts>.json.  --i-know is the
#     deliberate second hand on the key; run it without --apply first to read
#     the diff.
precedent hooks install claude-code --apply --i-know

# 2 — violate, headless, in a throwaway directory.  p-036f5125 denies any Bash
#     command containing the word "headless".  --allowed-tools is there so the
#     run does not stall on a permission prompt instead; the deny you want to
#     see comes from the hook, and it names a precedent id.
cd /tmp && claude -p 'Use the Bash tool to run exactly this command and show me its output: echo "precedent headless smoke test"' \
    --model haiku --max-budget-usd 0.10 --no-session-persistence \
    --allowed-tools 'Bash(echo:*)' --output-format json

# 3 — audit.  The funnel's ③ activated and ④ attributed both go to 1, the
#     NOT_INSTALLED alarm clears, and the hook log carries the deny with its
#     rule id and your own words.
precedent report
```

What to look for in step 2: the model is handed
`[precedent p-036f5125] 不要使用 headless — 你在 2026-09-07 说：…` instead of a
shell. What to look for in step 3:

```
| 阶段 | 数量 |
| ③ activated | 1 |
| ④ attributed | 1 |
```

and in `~/.precedent/hooklog.jsonl`, a line
`{"event": "deny", "rule": "p-036f5125", …}`.

### Undo — one line

```bash
precedent hooks uninstall claude-code --apply --i-know
```

Removes exactly the six entries carrying both markers (`--precedent-hook` **and**
a command pointing into your `~/.precedent/hooks/`), after another timestamped
backup, and — proven in §4h — leaves `settings.json` byte-identical to what it
was before the install. Anything else in your `hooks` block survives.

If you only want the headless rule gone and the receipts/ownership hooks kept:

```bash
precedent docket reject p-036f5125 --reason "太宽了，别的项目我确实要用无头浏览器"
precedent hooks install claude-code --apply --i-know      # re-merge without it
```

Or tighten it instead of dropping it — recompile scoped to the project where you
meant it, and re-run the gate:

```bash
precedent compile t-307dca53 --template dont_use --x headless \
    --cwd-glob '/Users/leonskennedy/AI coding open/Microduck 机器鸭二开/**'
```

Backups, newest last:

```bash
ls -lt ~/.precedent/backups/
```

---

## 7 · Final audit — did anything under `~/.claude` change?

Fingerprint = every file under `~/.claude` as `path → (size, mtime_ns,
sha256)`, 1,625 files, taken before the first command of this stage and after
the last.

### 7a · The whole stage

```
root            : /Users/leonskennedy/.claude
files before    : 1625
files after     : 1625
added           : 3
removed         : 3
content changed : 5
mtime-only      : 0  (same bytes)
  ADDED       backups/.claude.json.backup.1789459955477
  ADDED       backups/.claude.json.backup.1789460034051
  ADDED       backups/.claude.json.backup.1789460379317
  REMOVED     backups/.claude.json.backup.1789453029046
  REMOVED     backups/.claude.json.backup.1789453099083
  REMOVED     backups/.claude.json.backup.1789453161467
  CHANGED     projects/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f.jsonl   4599522 -> 4654727 bytes
  CHANGED     projects/-Users-leonskennedy-AI-coding-open----Agent-Harness/f04b24b3-2830-4463-af6b-d8a31560b47f/subagents/workflows/wf_46243239-338/agent-a1da7412ccee66023.jsonl   315281 -> 1351894 bytes
  CHANGED     projects/-Users-leonskennedy-AI-coding-open-Microduck------/5f788631-bcd6-4110-8c37-9138da4b90f3.jsonl   64865514 -> 64866331 bytes
  CHANGED     skills/my-profile/competitions.md   6025 -> 6451 bytes
  CHANGED     skills/my-profile/profile.md   2839 -> 2917 bytes
```

Eleven entries, in three groups. (Re-running the fingerprint a few minutes
later, after the last edits to this file, gives the **same eleven paths** — only
the sidechain transcript keeps climbing, `315,281 → 1,351,894 → 1,486,544`
bytes, because it is the record of writing this very section.) Each one
attributed:

| file(s) | what it is | who wrote it |
|---|---|---|
| `projects/…/f04b24b3-….jsonl` | **this session's own transcript** | Claude Code, appending as I worked |
| `projects/…/f04b24b3-…/subagents/workflows/wf_46243239-338/agent-….jsonl` | the sidechain transcript of the agent that wrote this file | Claude Code, same |
| `projects/…/5f788631-….jsonl` | **another session's** transcript, +817 bytes | Claude Code — that session is still running (`ps`: `claude … --resume=5f788631-…`) |
| `backups/.claude.json.backup.*` — 3 added, 3 removed | Claude Code's rotating backups of `~/.claude.json` (a file that lives *outside* `~/.claude`) | the `claude` binary, not precedent — proven in **7c** |
| `skills/my-profile/competitions.md`, `skills/my-profile/profile.md` | a hackathon-registration skill's own notes | **not precedent** — proven in **7d** |

Three transcripts Claude Code appends (allowed by the hard rule), one backup
rotation, and two files that need an actual investigation rather than an
assertion. `settings.json` is **not** in the list, and neither is anything else.

The count is 1,625 files before and 1,625 after — the backups directory keeps a
fixed number and rotates.

### 7b · Control 1 — eight precedent commands, zero bytes

```
# CONTROL: is it precedent, or is it the other live sessions?

$ date -u +%FT%TZ ; fingerprint ~/.claude
2026-09-15T08:18:27Z

$ precedent init && precedent mine && precedent compile … && precedent report && precedent loop --dry-run && precedent hooks install claude-code   # all read-only, no claude -p
precedent: re-run `precedent hooks install claude-code` (dry-run) to see the merge that fixes this.
rc=0
2026-09-15T08:18:53Z

$ fpdiff ctl-before.json ctl-after.json
root            : /Users/leonskennedy/.claude
files before    : 1625
files after     : 1625
added           : 0
removed         : 0
content changed : 0
mtime-only      : 0  (same bytes)
```

Eight commands including `init`, `mine`, `compile`, `report`, `loop --dry-run`,
`hooks install` (dry-run), `hooks status` and `snapshot`, in a 26-second window:
**0 added, 0 removed, 0 changed, 0 mtimes moved.** Not even this session's own
transcript, because Claude Code flushes it per turn.

This is the invariant the code enforces with
`state.guard_not_under_claude_home()`, which raises rather than writes, and
which every command has a test for.

### 7c · Control 2 — what *does* touch `~/.claude` is the binary precedent shells out to

`precedent improve` runs `claude -p`. **That** is Claude Code, and Claude Code
rotates its own `~/.claude.json` backups on startup. One call, with precedent's
exact flags, reproduces exactly the backup churn seen in 7a:

```
# CONTROL 2: the one thing that DOES touch ~/.claude is the `claude` binary precedent shells out to

$ ls ~/.claude/backups/ ; date -u
2026-09-15T08:19:37Z

$ claude -p 'reply with the single word ok' --model haiku --max-budget-usd 0.05 --output-format json --no-session-persistence --safe-mode --tools '' --disable-slash-commands
{"duration_api_ms":1449,"stop_reason":"end_turn","session_id":"766c0ee3-3433-4ebe-a299-d5c2bead2a96","total_cost_usd":0.004007,"usage":{"input_tokens":3802,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":41,"output_tokens_details":{"thinking_tokens":35},"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},"service_tier":"standard","cache_creation":{"ephemeral_1h_input_tokens":0,"ephemeral_5m_input_tokens":0},"inference_geo":"not_available","iterations":[{"input_tokens":3802,"output_tokens":41,"cache_read_input_tokens":0,"cache_creation_input_tokens":0,

2026-09-15T08:19:41Z
$ fpdiff
root            : /Users/leonskennedy/.claude
files before    : 1625
files after     : 1625
added           : 1
removed         : 1
content changed : 0
mtime-only      : 0  (same bytes)
  ADDED       backups/.claude.json.backup.1789460379317
  REMOVED     backups/.claude.json.backup.1789453161467
```

One backup added, one rotated out, nothing else — and
`backups/.claude.json.backup.1789460379317` is the third of the three added
files in 7a, written by *this control*. The other two are epoch-ms
`1789459955477` = `08:12:35` and `1789460034051` = `08:13:54`, both inside the
window of `improve`'s three `claude -p` calls (`spend.jsonl`: 38 s + 40 s + 65 s,
wall 2:26.7). So all three added backups, and the three rotated out to make room
for them, are accounted for.

**precedent wrote none of them; the `claude` it invoked did.** Worth stating
plainly, because "this tool is read-only on your Claude home" is only true of
the tool, not of the subprocess it starts. `precedent init / scan / mine /
compile / confirm / docket / own / report / snapshot / undo --dry-run / loop
--dry-run / hooks install --dry-run` never start one; `compile --llm`, `improve`
and `examine` do, and they are the three commands the README marks as the ones
that cost money.

### 7d · The two `skills/my-profile/*.md` files — not us

They were written at `2026-09-15T07:55:42Z`. Searching the machine for
everything modified within ±3 s of that instant:

```
2026-09-15T07:55:42.327948+00:00 /Users/leonskennedy/.agents/skills/my-profile/profile.md
2026-09-15T07:55:42.328675+00:00 /Users/leonskennedy/.claude/skills/my-profile/profile.md
2026-09-15T07:55:42.329520+00:00 /Users/leonskennedy/.agents/skills/my-profile/competitions.md
2026-09-15T07:55:42.330497+00:00 /Users/leonskennedy/.claude/skills/my-profile/competitions.md
2026-09-15T07:55:42.331625+00:00 "/Users/leonskennedy/AI coding open/AI黑客松 2026.9.15/海外AI黑客松-统一报名执行包.md"
```

Five files in **4 milliseconds**, mirroring the same skill into a *second*
skills root (`~/.agents/skills/`) and updating a hackathon planning document.
precedent has never heard of `~/.agents`, does not know that `.md` file exists,
and has exactly one code path that writes inside a Claude home — `hooks
install/uninstall --apply` — which §3 shows refusing to run against `~/.claude`
without `--i-know`, and which was never run with it (`settings.json` is still
`db7c3d1c1da5…`).

The content is hackathon-registration bookkeeping (emails, team names, deadlines)
and the same two files were edited by session `18baa501` earlier the same day
with the identical heredoc pattern:

```
2026-09-15T01:29:45.440Z  18baa501…jsonl:1178  Bash
    python3 - <<'EOF'
    c = "/Users/leonskennedy/.claude/skills/my-profile/competitions.md"
2026-09-15T01:35:36.595Z  18baa501…jsonl:1191  Bash
    python3 - <<'EOF'
    p = "/Users/leonskennedy/.claude/skills/my-profile/profile.md"
```

The 07:55:42 write has **no** matching record in any `~/.claude` transcript,
which is precisely the change-feed category `receipts` calls *actor unknown* —
an out-of-band write into the governed tree. That is the failure mode this
project exists to surface, observed live, on the author's own machine, during
the demo prep. The `PostToolUse` change feed and the `PreToolUse` ownership pass
are what would have caught it; neither is installed yet, which is why it took a
filesystem-wide `mtime` search to attribute.

### 7e · `settings.json`, the file the hard rule is about

```
before  db7c3d1c1da5a7334250a7451f6385b5c922143e7eb51b6084cb576891b18697
after   db7c3d1c1da5a7334250a7451f6385b5c922143e7eb51b6084cb576891b18697
```

Unchanged. `~/.precedent/hooks/` is still empty — not one hook script is on
disk in the real state dir. Enforcement on this machine is **0 rules
installed**, exactly as the hard rule requires.

### 7f · The three suites

```bash
$ for p in receipts acceptor precedent; do (cd packages/$p && .venv/bin/python -m pytest -o addopts="" -q); done
```

```
receipts    71 passed in 0.96s
acceptor   112 passed in 6.84s
precedent  627 passed in 29.45s
--------------------------------
total      810 passed, 0 failed
```

`precedent` was 618 before this stage; the 9 new tests are:

| test | what it pins |
|---|---|
| `test_dry_run_prints_the_concrete_backup_path_and_the_exact_diff` | the dry run names a real path and prints a real diff, and writes nothing |
| `test_the_dry_run_diff_is_the_bytes_apply_actually_writes` | the printed diff == the diff between the backup taken and the file written |
| `test_dry_run_says_none_when_there_is_nothing_to_back_up` | no file / nothing to change → `backup : none`, empty diff, `idempotent : yes` |
| `test_backup_path_for_never_collides_and_never_writes` | two backups in the same second get `-2.json`; the preview writes nothing |
| `test_install_then_uninstall_round_trips_settings_json_byte_for_byte` | no `"hooks": {}` scar |
| `test_uninstall_keeps_a_hooks_key_the_user_already_had` | including an empty one they had themselves |
| `test_a_second_apply_does_not_forget_who_created_the_hooks_key` | the receipt answer is sticky — a re-run must not turn "we made it" into "it was already there" |
| `test_uninstall_dry_run_shows_the_same_result_as_the_apply` | the uninstall dry run is not a different answer |
| `test_demo_deny_path_on_a_throwaway_home` | §4 as a test: install → deny (id + quote) → allow → 20 unrelated calls → uninstall |

Every one of them runs against a synthetic home under `tmp_path`, and the
`real_home_canary` fixture fails the test if `~/.claude` or `~/.precedent` is
created, removed or has its directory mtime moved.

Two of the nine exist because writing this dossier found two real defects:

1. **`uninstall` did not round-trip.** The first run of §4h left `"hooks": {}`
   in a `settings.json` that never had a `hooks` key. Visible only because the
   demo diffed the file against the original instead of asserting "our entries
   are gone".
2. **…and the first fix had its own bug.** A *second* `--apply` re-wrote the
   install receipt with `hooksKeyExisted: true` — true at that moment, and true
   only because the first `--apply` had created it — which would have brought
   the scar straight back. The answer is now sticky, and
   `test_a_second_apply_does_not_forget_who_created_the_hooks_key` fails on the
   intermediate version (checked, by reverting it).

---

## 8 · What this demo does not prove

* **The `model=opus` precedent is not enforced on this machine.** It failed the
  gate twice, for two different and both-correct reasons (§2). It is active only
  inside the throwaway state dir of §4, force-confirmed, flagged
  `forcedPastGate: "FAIL"`.
* **Temporal birth gate v1 cannot certify a preemptive policy.** "From now on,
  use Opus 5" has no recorded violation to bind (a) to, so it fails rather than
  returning something like `NO_VIOLATION_RECORDED`. The v1 spec is implemented
  exactly as specified; this is a v2 question, not a bug to quietly patch.
* **ε is a per-action rate.** `32/8164 = 0.4 %` passes, and 32 interruptions is
  still 32 interruptions. Read both numbers. The one confirmed rule here would
  have fired **68 times** across the full 17,842-call corpus — and every one of
  those is a real headless-browser launch in a project where the user never
  complained about headless browsers.
* **The gate is retrospective, not predictive.** It proves a rule fires where it
  should and is quiet where it should *on the record you already have, after the
  moment you stated the policy*. It says nothing about generalisation, and it
  cannot tell "quiet because the rule is right" from "quiet because the rule is
  so narrow it only matches the one action that provoked it".
* **The last link is yours.** Claude Code invoking the hook from a live
  `settings.json` is the one step not demonstrated here, by design (§4i). The
  hook script, the payload parsing, the decision, the JSON shape, the timing and
  the settings merge are all proven; the wiring is §6 step 1.
* **`improve`'s honest result was `HOLD — no evidence`.** No cassette on this
  machine could measure a skill draft. That is the designed answer, not a
  failure, and it is what the literature predicts for LLM-written skills
  (SkillsBench: 0 vs +16.2 for human-written).
* **Mining is surface-pattern matching on Chinese prose without a word
  segmenter** (`bigram`; `pip install precedent-cli[zh]` swaps in jieba). 20
  corrections out of 165 human turns, 18 topics, 1 repeated — a small corpus,
  and the topic labels (`么东 西怎`) show what bigrams do to Chinese.
* **The corpus is alive.** Five other Claude Code sessions were writing to
  `~/.claude/projects` throughout, so `mine` says 164 human turns and `report`
  four minutes later says 165, and the same gate says `31/8160` at compile and
  `32/8164` at confirm. Nothing here is cached; every number is re-derived from
  the record at the moment the command ran. §4g is the one place that had to be
  reproducible, and it reads a frozen copy for exactly this reason.
* **The dollar cap bounds calls, not dollars.** `--max-budget-usd` is handed to
  a subprocess. precedent additionally *reserves* each call at its per-call cap
  and compares the budget against `max(reported, reserved)`, so a binary that
  ignores the flag and reports `$0.00` still cannot buy more than
  `floor(budget / per-call cap)` calls. If it ignores the flag **and**
  under-reports, the call count is bounded and the money is not. That is the
  honest claim.
* **`mine` prints your own sentences, and some of them are private.** The one
  redaction in this file is a mined quote carrying a phone number and a WeChat
  id, because that is what the user typed into a correction. `precedent mine
  --md out.md`, `precedent report --md digest.md` and `precedent docket` all
  reproduce correction text verbatim; there is **no DLP pass on the miner's
  output** (the secret scan runs on `improve` drafts, which is a different
  path). Read a mined report before you share it.
* **This dossier's own cost: $0.1667.** `improve` $0.1627 (3 sonnet drafting
  calls) plus $0.0040 for the one haiku call in control 2. Everything else —
  `init`, `mine`, three `compile`s, `confirm`, `report`, `loop --dry-run`, the
  dry runs, the whole of §4 — was free and offline.
