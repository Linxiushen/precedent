# precedent

**Every correction is a test.**

`precedent` is a self-evolving Claude Code harness whose core is a *verified*
self-modification loop. The two steps everybody else skips — **accept** and
**audit** — are the product.

| step | who does it elsewhere | what `precedent` v0 does |
|---|---|---|
| ① propose | everyone | **correction miner**: your own corrections become candidate rules — including the ones you made by reverting the agent's edit instead of typing |
| ② **accept** | nobody (an LLM says yes) | two gates. A **rule** meets the **temporal birth gate**: replay it against the record *in time order* — it must fire on the action you objected to, and stay quiet after that moment. Anything else meets the **examiner**: your own sessions become `claude plugin eval` cases, run with and without the candidate, and the paired outcomes go to `acceptor`'s e-process gate, harm martingale, CTHS spend schedule and task floor |
| ③ enforce | "written but never loaded" | a **six-hook suite** in `settings.json` (installed with a backup): `PreToolUse` `deny`/`ask`/`log` **plus path-level ownership of the governed trees**, `PostToolUse` change-feed capture, `UserPromptSubmit` trusted origin, `SessionStart` + `InstructionsLoaded` **live load receipts**, `Stop` funnel counters |
| ④ **audit** | nobody (loops fail silently for weeks) | a **docket** that never piles up silently, the funnel *proposed → accepted → activated → attributed*, four **STARVATION alarms**, receipts, a sha256 hash-chained ledger, content-addressed snapshots, `undo --session`, a hook log and a spend meter |
| ⑤ re-evolve | — | a **nightly improver** clusters failure signatures, asks for ONE bounded edit per cluster, and enforces *diff == declared paths* + lint + the competent gate before anything reaches the docket; rejected drafts go to `rejected.jsonl` and come back as negative examples |

**Everything is free and offline except three optional commands.** `precedent
compile --llm`, `precedent improve` and `precedent examine` are the only things
that cost money; each is capped with an explicit budget, each records every
cent in `spend.jsonl`, and what they produce has to survive the same mechanical
gates as everything else. `precedent loop --dry-run` walks the whole nightly
cycle with **zero** model calls.

**And precedent never applies an edit.** An `ACCEPT` is a hash-chained
certificate and a docket entry. The bytes are yours to write, by hand, after
you have read them.

It is built on two libraries that are used, not forked:

* [`receipts`](../receipts) — load receipts, change feed, activation funnel
* [`acceptor`](../acceptor) — the hash-chained certificate ledger (and, in v1,
  the paired e-process gates)

## Install

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ../receipts -e ../acceptor -e . pytest
.venv/bin/python -m pytest -q          # 627 tests

uv pip install --python .venv/bin/python -e '.[zh]'   # optional: jieba
```

Python 3.11+, standard library only besides those two packages. The one extra,
`[zh]`, installs [jieba](https://github.com/fxsjy/jieba) so Chinese topics are
grouped by **words** instead of character bigrams. It is genuinely optional:
without it the tokeniser falls back to bigrams and every report says which one
ran (`中文分词 bigram` / `jieba`).

## Safety invariants

1. **One command may write a `settings.json`, and only with a backup.**
   `precedent hooks install --apply` and `hooks uninstall --apply` write
   `<claude home>/settings.json` after copying the old file to
   `<state>/backups/settings-<ts>.json`. The merge is non-destructive (your
   other hooks survive), idempotent (running it twice changes nothing) and
   atomic (`os.replace`, keeping the file's own mode). Your real `~/.claude`
   additionally needs `--i-know`, and `--scripts-only` writes the hook scripts
   without touching settings. **A `settings.json` precedent cannot parse is
   never written** — not valid JSON, not a JSON object, or a `hooks` value that
   is not an object: merging into any of those would mean *replacing* your file
   rather than adding to it, so `--apply` refuses, names the reason and exits
   2. **Every other command is read-only on the Claude home**, enforced by
   `state.guard_not_under_claude_home()`, which raises rather than writes.
2. **No hook script can break Claude Code.** All six are three lines of logic
   over one shared library (`<state>/hooks/_plib.py`, which is
   `precedent/_hooklib.py` copied byte for byte — the code Claude Code runs is
   the code the tests import). Typical decision cost is under a millisecond on
   top of one Python start; the tests assert < 300 ms end-to-end **for every
   one of the six**. They read stdin defensively; on **any** internal exception
   they exit 0 with no stdout and append the error to `<state>/hooklog.jsonl`;
   a missing `_plib.py` exits 0 too. They deny or ask **only** when a rule
   whose `status` is exactly `active` — i.e. one you confirmed — matches, and
   the ownership guard is such a rule (`p-own-guard`, written by `precedent own
   … --user`). Everything else is allow + log. Each entry carries a `timeout`.
3. **Nothing is deleted or overwritten without a content-addressed snapshot**,
   and `undo` round-trips (`test_snapshot.py`).
4. **Every `claude -p` call is capped and recorded**: `--max-budget-usd`
   (0.30 for drafts; `--llm-budget` is itself validated — outside
   `(0, 1.00]` USD per call the run is refused before the subprocess starts,
   because a budget of `0` is not a cap), `--no-session-persistence`,
   `--model sonnet`,
   `--output-format json`, never `--dangerously-skip-permissions`; the cost
   goes to `<state>/spend.jsonl`. A **drafting** call additionally carries
   `--safe-mode --tools "" --disable-slash-commands`: a draftsman needs no
   tools, and it must not be able to read the CLAUDE.md, skills, plugins,
   hooks and MCP servers it is drafting *about* — the L4 proposer/evaluator
   separation. (It is also what makes the call affordable: measured on this
   machine, the same prompt cost **$0.33 and 107 s** with the default loadout
   and **$0.009 and 3.8 s** with those three flags, and the default loadout was
   burning the whole per-call budget before the model ever answered.) Every
   spending command also takes a **total** budget (`improve --budget-usd`,
   `examine --max-cost-usd`, itself capped at $20 for one exam) that is
   checked *before* each call. The check does **not** trust the binary's own
   cost report: each call is first *reserved* at its per-call cap and the
   budget is compared against `max(reported, reserved)`, so a `claude` that
   ignores `--max-budget-usd` and reports `$0.00` still cannot buy more than
   `floor(budget / per-call cap)` calls. A cost that is not a finite number
   ≥ 0 (`NaN`, `Infinity`, a string, a refund) is booked as `$0` and counted
   as untrusted — otherwise one `NaN` would turn off every budget comparison
   at once, and `spend.jsonl` would stop being valid JSON. The examiner that
   `improve` runs is billed against the **same** `--budget-usd` and is handed
   only what is left of it. LLM output is untrusted: schema-validate,
   then gate. **Mechanical rejection overrides LLM approval, never the
   reverse.**
5. **A draft may not relabel a protected file as a rule.** `surface` is a
   label the model picked; `skill` and `claude_md` are pinned to a basename,
   so a path guard (`improve.protected_path_problems`) is what stops a
   `precedent`-surface draft from putting `settings.json`, anything under
   `hooks/`, `.git/`, `.ssh/` or precedent's own state on the docket for you
   to paste in. Settings and hooks are L4/L5 events and go through a human
   merge, not the nightly loop.
6. **Nothing is ever applied.** `improve` and `examine` write proposals, not
   files. `docket confirm` on a proposal records your decision and prints the
   path; it does not touch the file. An acceptance layer that also writes is a
   proposer with a rubber stamp.
7. **All state lives in `~/.precedent/`** (`--state-dir` anywhere else; the
   test suite always points it at `tmp_path`, builds a synthetic `~/.claude`
   there, and a `real_home_canary` fixture asserts the real `~/.claude` was not
   touched).

## Commands

```bash
precedent init                        # detect, create state + ledger, scan, first screen
precedent scan --last 5               # receipts, archived into the state dir
precedent mine --last 8 --md out.md   # the correction miner (+ auto-compile)
precedent compile t-1a2b3c4d          # topic -> DSL v1 rule -> TEMPORAL GATE
precedent compile t-1a2b,t-3c4d --llm # several topics, drafted by claude -p
precedent confirm p-9f8e7d6c          # rule -> precedents.json
precedent docket                      # the queue: candidates + evidence + diff
precedent docket --batch              # the same thing on one screen, for a push
precedent docket confirm p-9f8e7d6c   # …or reject <id> --reason … / snooze <id>
precedent own ~/.claude/skills/x/SKILL.md --user   # and --agent, and --guard on|off
precedent hooks install claude-code   # dry run; --apply merges settings.json
precedent hooks uninstall claude-code # dry run; --apply removes only our entries
precedent hooks status claude-code    # installed state + drift (exit 1 on drift)
precedent snapshot                    # content-addressed learned-state snapshot
precedent undo --session <id>         # dry-run plan; --apply restores
precedent report --md digest.md       # the daily digest: alarms, funnel, spend
precedent examine --candidate <p|id>  # cassettes -> two-armed exam -> certificate
precedent improve --budget-usd 2      # NIGHTLY: failure clusters -> bounded edits
precedent loop --dry-run              # one whole cycle, zero model calls
precedent loop --cron                 # the launchd/crontab snippet (prints only)
precedent evaluate-bundle --stdin --json   # OpenClaw skill bundle in, verdict out
precedent evaluate-bundle --dir ./skill --baseline-dir ~/.claude/skills/x
```

All of them take `--claude-home` and `--state-dir`; the scan-backed ones also
take `--project` and `--last`.

### `precedent init`

Ten lines of Chinese, every one of them a count you can re-derive with `grep`:

```
precedent 0.1.0 — 首屏（只读，零模型调用）
 1. Claude home     : /Users/you/.claude
 2. 状态目录        : /Users/you/.precedent
 3. 会话            : 扫描 8 / 发现 8 个
 4. 学习工件        : 60 个（skill 55 / memory 3 / CLAUDE.md 2）
 5. 从未被引用      : 41 个（可引用工件的 71%）
 6. 曾被截断        : 1 个工件在 ≥1 个会话里只加载了一部分
 7. 失效索引/缺文件 : 2 条
 8. 近重复工件      : 8 个
 9. 无人值守写入    : 最近 7 天 34 次（子代理 3 次，Bash 绕过记忆工具 31 次）
10. 已强制执行的先例: 0 条  → 下一步：precedent mine
```

### `precedent mine` — the correction miner

A human turn is `origin.kind == "human"`, or a user record that carries text
and no `sourceToolAssistantUUID`. Skill and slash-command expansions arrive as
user records too and are **skipped**: anything starting with `<`, with
`Base directory for this skill`, with `# Workflow authoring reference`, or
containing `<command-message>`. The skipped count is reported, never hidden.

The turn is then **split into sentences** and each one is judged on its own, so
"继续执行。还有，你能不能省着点 fable5 用量？" is mined as its second sentence, and
that sentence — not the whole turn — is the quote that ends up in the rule's
message.

Five layered signals produce one confidence score:

| layer | signal | weight |
|---|---|---|
| (i) | a strong negation/imperative fires — `不要 / 别 / 不是 / 不对 / 错了 / 我说过 / 改用 / 换成 / 不用 / 停 / 省着点 / 撤销 / 回滚 / 还原 / don't / stop / instead / i said / wrong / never / undo / revert` | **0.55** |
| (i′) | a *soft* pattern fires — `又 / 重新 / 应该 / again` | 0.30 |
| (i″) | a **revert of an agent edit** — `git revert / restore / checkout -- / reset --hard / stash pop` naming a file the agent wrote earlier in the same session. This is a correction with **no words at all** | 0.50 |
| (i‴) | the turn **restates an earlier correction from another session** (Jaccard ≥ 0.34, ≥ 3 shared tokens) even without any pattern word — you should not have to say 我说过 twice | 0.30 |
| (ii) | the turn follows an assistant `tool_use` — that call is recorded as the **violating action** | +0.25 |
| (iii) | the topic recurs in ≥2 sessions (decided after grouping) | +0.20 |
| (iv) | a revert accompanies the turn | +0.15 |

**The question filter.** A sentence that ends in `？`/`?` and contains no
imperative is a question, not a correction: "这个不是已经做完了吗？" is asking,
"不要用 pip，好吗？" is telling. The imperative list is deliberately separate
from the pattern list, which is why "你能不能省着点 fable5 用量？" survives (省着点
is an imperative) while "我们不是做自进化的吗？" does not. Filtered sentences are
counted in the report.

**Lookaround guards.** `别` / `不是` / `不用` / `不要` / `停` carry guards,
because those characters sit inside 特别, 别的, 是不是, 不用担心, 不要紧, 不停.
Each one has a test.

**Chinese tokenisation.** With the `[zh]` extra, jieba words; without it,
character bigrams. Stopwords *and the pattern words themselves* are stripped —
otherwise every correction would look like every other correction, since they
all contain 不要/don't. Topics are deterministic single-link clusters over
Jaccard overlap.

**Auto-compile.** Every topic is then run through the compiler and the gate,
and the header says `N topics, K compiled, J PASS`. A topic that cannot be
compiled says why.


### `precedent compile <topic-id>[,<topic-id>…]` — the DSL and the gate

#### Rule DSL v1

```json
{"schemaVersion": 1, "id": "p-e22604f6", "hook": "PreToolUse",
 "tool": "Agent|Workflow", "match": "any",
 "matchers": [{"type": "input_field_missing", "field": "model"},
              {"type": "input_field_equals", "field": "model",
               "value": "opus", "negate": true}],
 "action": "deny", "scope": "project", "cwd_glob": "/Users/me/proj/**",
 "message": "Agent/Workflow 必须带 model=opus — 你在 2026-09-15 说：改用 Opus 5"}
```

* `tool` — one or more of `Bash Edit Write MultiEdit NotebookEdit Agent
  Workflow WebFetch Skill *`, joined with `|`.
* matchers, all on a **named input field** (`command`, `file_path`, `prompt`,
  `model`, `url`, `skill`, `script`, `notebook_path`, …):
  `input_regex`, `input_field_missing` (an `Agent` call with no `model`),
  `input_field_equals` (with `negate`). A *missing* field never satisfies an
  `equals`, negated or not — that is what `input_field_missing` is for.
* `match` — `all` (default) or `any`.
* `action` — `deny` | `ask` | `log`.
* `scope` — `project` (optionally narrowed by `cwd_glob`) or `global`. A
  project rule is not *eligible* where it cannot fire, so scoping it does not
  flatter its gate numbers.
* `message` — carries the correction quote and its date, and is what the agent
  is shown.

Six templates:

| template | phrasing | compiles to |
|---|---|---|
| `dont_use` | "don't use X" / 不要用 X | `Bash` regex on **X** in `command` |
| `use_x_not_y` | "use X not Y" / 用 X 不要用 Y | `Bash` regex on **Y** |
| `dont_touch_path` | "don't touch P" / 不要改 P | `Edit\|Write\|MultiEdit\|NotebookEdit` regex on `file_path` |
| `require_field` | "Agent/Workflow calls must set model=opus" | `input_field_missing` **or** `input_field_equals … negate` |
| `forbid_flag` | "never pass `--no-verify` to `git commit`" | flag regex, optionally `all`-matched with the command |
| `ask_before` | "ask me first" | `action: ask` + a preset: `git_push_force`, `rm_rf`, `curl_pipe_sh` |

The template is auto-detected from the topic's quotes; `--template` with
`--x/--y/--path/--field/--value/--flag/--preset` overrides it when the phrasing
is not one the extractor knows (which, on real Chinese transcripts, is often).
Several topics can be compiled into **one** rule by comma-separating their ids
— the Opus case is two topics ("省着点 fable5" and "改用 Opus 5") and one policy.

#### Regex safety

A rule can come from an LLM, so every pattern is treated as hostile:

* **length caps** — 400 chars of pattern, 4096 chars of subject;
* **a static linter** rejects, *before the rule is ever stored*: nested
  quantifiers (`(a+)+`), quantified overlapping alternation (`(a|aa)+`),
  **adjacent quantifiers over overlapping character sets** (`a*a*a*b`,
  `.*.*.*=`, `\s*\s*=`, `[a-z]*[a-z]*y`), bounded repeats above 200, more than
  four repeats in one pattern, and backreference-plus-repeat;
* **a bounded matcher** times every match and **quarantines** a pattern that
  blows its 50 ms budget — from then on it is skipped (fail-open) and the event
  is logged;
* **`precedent confirm` re-lints** — `precedents.json` is the only file the
  hook trusts, so a candidate compiled by an older build (or edited by hand)
  is re-validated against the current schema and the current linter before it
  can become `active`. `--force` overrides the birth-gate *verdict*, never the
  schema.

Honest limitation: CPython's `re` holds the GIL and ignores signals mid-match,
so a *hard* deadline is not available to a stdlib-only tool (a worker thread
does not help — it keeps the GIL and starves the timer). The quarantine only
fires *after* a slow match returns, so for the shapes that never return the
linter is the only defence — which is why it rejects the adjacency family
(`a*a*a*b` took >10 s on the 4096-char subject cap before it was added) rather
than trusting the match-time budget to catch them.

#### The TEMPORAL BIRTH GATE

The v0 gate asked "does this fire on corrected sessions and stay quiet on
uncorrected ones?" That question is **anachronistic**: it judges a rule against
behaviour from before the user ever stated the policy. On this machine it threw
out a perfectly good rule because the user had legitimately used a headless
browser *in the weeks before they asked for it to stop*.

A correction expresses a policy **from the moment it was made**. So for rule R
from topic T with earliest correction time `t0`:

* **(a) HIT** — R must fire on the violating action recorded immediately before
  ≥1 correction in T (the `tool_use` preceding the correcting human turn).
* **(b) QUIET-AFTER** — among the actions after `t0` where R was eligible (its
  tool was used, in scope), a fire followed within the same session by another
  correction from T in the next 3 human turns is a **true positive**. Every
  other fire is a **false fire**, tolerated only while
  `false_fires / eligible_after ≤ ε` (default 2 %).
* **(c) pre-`t0` behaviour is reported, never counted.**

Verdict `PASS` / `FAIL` / `INSUFFICIENT` (fewer than 3 eligible actions after
`t0`). The whole evidence block is stored on the candidate and in the ledger:

```
  t0（最早一次纠正）: 2026-09-07T08:26:39.020Z
  (a) 命中违规动作   : 1/3   → HIT
  (b) t0 之后可判定   : 7474 次动作
      其中触发         : 17 次 = 真阳 0 + 误触 17
      误触率           : 17/7474 = 0.2%  (阈值 2%)
      误触分布（仅报告）: 出现在 1/6 个会话里
  (c) t0 之前         : 33/945（只报告，不计入）
  判定               : **PASS**
```

Read the absolute numbers, not the percentage: ε is measured **per action**, so
a large corpus is a permissive denominator. 17 tolerated fires is 17 times the
agent would have been interrupted, and the report says so in both units.

`precedent confirm <id>` moves a candidate into `precedents.json`. Confirming
one that did not PASS needs `--force`, and the forced verdict is recorded on
the rule and in the certificate (PROCTOR: mechanical rejection overrides
everything).

### `precedent compile <topic> --llm` — a draftsman, never a judge

```
claude -p <prompt> --model sonnet --max-budget-usd 0.30 \
        --output-format json --no-session-persistence
```

Never `--dangerously-skip-permissions`. The prompt carries the correction
quotes with their dates, the violating actions, the DSL spec, and the drafts
that were **rejected last time** as negative examples. The answer is then:

1. **schema-validated** — unknown keys are dropped, so a draft cannot smuggle
   `"status": "active"`, its own `id` or a pre-cooked `"birth": {"verdict":
   "PASS"}` past anything; every regex is linted;
2. **gated** — a draft that does not PASS the temporal gate never becomes a
   candidate, whatever the model claimed about it.

Rejected drafts go to `<state>/rejected.jsonl` with the reason; the cost of the
call goes to `<state>/spend.jsonl` whether it helped or not.


### `precedent hooks install claude-code` — the six-hook suite

Dry-run (the default) prints the two things you ask before an `--apply` — **the
concrete path your current `settings.json` would be copied to** (not a `<ts>`
placeholder: the same naming function `--apply` uses, collision suffix included)
and **the exact unified diff**, byte level, from what is on disk now to what
would be written, with the sha256 of both sides in the header. Then the merged
file in full, the hooks block on its own, an inventory of every script with its
sha256, and the decision contract; `--show-scripts` adds every script in full.

`--apply` writes the scripts, then **backs up** `settings.json` into
`<state>/backups/` and merges the block. `--scripts-only` stops before touching
settings. Both `install` and `uninstall` are idempotent: a second run reports
`idempotent : yes — nothing to change`, prints an empty diff, and writes no
second backup.

`uninstall --apply` round-trips: the install receipt records whether a `hooks`
key existed *before* the merge, so uninstalling removes a `hooks` object
precedent created and leaves one you already had — including an empty one. A
`settings.json` that had no hooks at all comes back byte for byte
(`test_install_then_uninstall_round_trips_settings_json_byte_for_byte`).

| event | script | timeout | what it does |
|---|---|---|---|
| `PreToolUse` | `pre_tool_use.py` | 5s | confirmed precedents (`deny`/`ask`/`log`) **and** ownership of governed writes |
| `PostToolUse` | `post_tool_use.py` | 5s | change-feed capture: what a `Write`/`Edit`/`Bash` call actually touched |
| `UserPromptSubmit` | `user_prompt_submit.py` | 5s | record the human turn — the one origin precedent treats as trusted |
| `SessionStart` | `session_start.py` | 10s | live receipt: this session started, with N active rules |
| `InstructionsLoaded` | `instructions_loaded.py` | 10s | live receipt: which instruction files reached the model, whole or truncated |
| `Stop` | `stop.py` | 10s | funnel counters for the session (turns, candidates, asks, loads) |

The `PreToolUse` matcher is **one** entry — every tool an active rule names,
union `Bash|Edit|MultiEdit|NotebookEdit|Write` for ownership. Two entries whose
matchers both match `Bash` would run the hook twice per call, log it twice and
answer twice.

Every entry we write is **marker-tagged twice**: the command points into *this*
state dir's `hooks/`, and it carries `--precedent-hook <Event>`. An entry is
ours only when it carries **both** marks, so `hooks uninstall --apply` removes
exactly ours (`grep -c precedent-hook ~/.claude/settings.json` counts them) and
nothing else — not a script you parked next to ours in the same directory (that
one is listed by `hooks status` as drift instead), and not another precedent
install pointed at a different `--state-dir`. `hooks status` diffs what is
installed against `<state>/hooks/installed.json`, the receipt written by the
last `--apply`.

The hook answers:

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "permissionDecision": "deny",
  "permissionDecisionReason": "[precedent p-1a2b3c4d] 用 uv，不要用 pip -- from your correction on 2026-09-12: 不要用 pip，用 uv"}}
```

No match → `{}` and exit 0, plus an `allow` line in `<state>/hooklog.jsonl`.
An `ask`/`deny` is logged with the rule id and the decision time. The other
five events print **nothing at all**: `PreToolUse` is the only one whose answer
is a decision, and for `UserPromptSubmit` and `SessionStart` Claude Code puts a
hook's stdout in front of the model — an empty object is still a change to the
prompt, and we have no reason to make one. **Fail-open by construction**: a missing or malformed `precedents.json`, an uncompilable
regex, a torn stdin, a missing `_plib.py` — all exit 0 **with no stdout** and an
`error` line in the hook log. A gate that bricks the agent when its own state
file is broken is worse than no gate; starvation is reported by `precedent
report`, not by wedging a tool call.

### `precedent hooks status claude-code` — drift

```
installed          : yes  (at 2026-09-15T04:31:12+00:00, precedent 0.1.0)
settings.json      : /Users/you/.claude/settings.json  (present)
entries            : 6/6 ours
PreToolUse matcher : Bash|Edit|MultiEdit|NotebookEdit|Write
active rules       : 2

scripts:
  current     _plib.py               8f70e42e7ca2  /Users/you/.precedent/hooks/_plib.py
  …
drift              : none — what is installed is what this build would write
```

It exits 1 when there is drift, and names each kind: a script missing, from an
older build, or **edited by hand** (it knows the difference, because
`installed.json` records the sha256 of what was written); one of our entries
deleted; a stale entry whose matcher predates a rule you confirmed later; an
active rule the installed matcher does not cover. That last one is the
"accepted but not activated" gap, and it is also a `DRIFT` alarm in `report`.

### Ownership — write permission by **path**, not by tool

The worst incident in the whole research corpus is not a bad rule; it is a
2:17am background fork rewriting an artifact the user had verified — with a
Bash heredoc, straight past the memory tool's approval gate (hermes#99729: "the
approval gate is a convention"). So the `PreToolUse` hook watches the governed
trees by path, whatever tool is used:

```
<claude home>/projects/<slug>/memory/**      <claude home>/skills/**
CLAUDE.md / CLAUDE.local.md (anywhere)       <cwd>/.claude/rules/**
<cwd>/.claude/skills/**                      ~/.claude/skills/**
```

For **every** agent write into one of them — including a `>` redirect, a `tee`,
a `sed -i` or a `VAR=<path>` assignment inside a Bash command (heredoc bodies
are blanked first, so a command that *writes a file containing* the path is not
a write to it) — the hook:

1. **snapshots the pre-image** into `<state>/blobs/<sha256>` (PreToolUse runs
   *before* the tool: that is the only moment it still exists);
2. appends a candidate to `<state>/candidates.jsonl` with the agent kind
   (`foreground` | `subagent`, from `agent_id`), the path, the governed tree,
   the owner and why, and a `+N −M lines` diff summary computed by applying the
   edit in memory;
3. answers **`ask`** if, and only if, the write is from a **subagent**, to a
   **user-owned** artifact, and you have confirmed the ownership guard.
   Otherwise: allow, and the record stands.

"User-owned" is decided in this order: an explicit declaration
(`precedent own <path> --user`, globs allowed), then the change feed — a file
no agent has been recorded *creating* (`agent_created.json`, rebuilt from the
transcripts and from this tool's own snapshots on every `hooks install`), which
exists, is yours. A file that does not exist yet is being created by the call
in front of us, so it is the agent's.

```bash
precedent own ~/.claude/skills/chrome-devtools/SKILL.md --user   # arms the guard
precedent own '~/.claude/skills/**' --agent                      # a whole tree
precedent own --guard off                                        # observe only
precedent own                                                    # list
```

The guard is a rule in `precedents.json` (`p-own-guard`, `kind:
ownership-guard`, `status: active`) so that safety invariant 2 holds without an
exception: the hook still asks **only** when a confirmed precedent matches.
Turn it off and the ownership pass keeps recording candidates and never
interrupts.

### Live receipts — the hook beats the inference

`receipts` infers a load status after the fact and honestly says `unknown` when
a transcript carries no context record. `InstructionsLoaded` knows: it hands us
the files, with their content, at the moment they load. Those rows go to
`<state>/receipts/<session>.jsonl` with a status in the same vocabulary
(`loaded_complete` / `loaded_truncated` / `not_loaded` / `missing_on_disk`, plus
`loaded_declared` when the payload names a file but proves no bytes), and
`precedent scan` merges them **live-wins**. Every override is counted and
printed — silently preferring one evidence source over another is the failure
this tool exists to make visible.

### `precedent docket` — the queue that never piles up silently

Two kinds of entry: rule candidates (with their birth-gate counts and the rule
JSON) and governed-write candidates (with the pre-image sha256 and the diff
summary). Three verbs:

```bash
precedent docket                       # full evidence
precedent docket --batch               # one screen, grouped, for a daily push
precedent docket confirm <id>          # a rule becomes active; a write is accepted
precedent docket reject <id> --reason "太宽了，会拦到 grep pip"
precedent docket snooze <id>           # 14 days
```

A snooze **comes back older, not gone**. A rejected rule keeps its reason in
`rejected.jsonl` and returns as a negative example in the next `compile --llm`
prompt. A rejected write prints the blob holding its pre-image. Every decision
is a hash-chained certificate in the ledger (`docket/v1`, `ACCEPT`/`REJECT`/
`HOLD`), and anything pending more than 7 days is a `PENDING` alarm — because
the failure being designed away from is `write_approval` accumulating 241
staged writes over eight weeks with nobody told (hermes#105770).

### `precedent report` — the daily digest

Twelve sections; the first is the alarms, because **passing is silent, blocking
is loud, starvation is never silent**:

| alarm | fires when | the production failure it copies |
|---|---|---|
| `PENDING` | a docket entry has waited ≥ 7 days | 241 staged writes, 8 weeks, unread |
| `HOOK` | hook errors, quarantined regexes, an unreadable `precedents.json` | enforcement is fail-open, so an outage is *quiet* |
| `NOT_INSTALLED` / `NO_FIRES` | rules confirmed but not installed; or installed ≥ 14 days and never once fired | Hermes: 41 forks, 0 updates, weeks |
| `DRIFT` | what is installed is not what this build would install | a gate that stopped matching |

Then the funnel — **proposed → accepted → activated → attributed** — where
*activated* means the matcher that is in `settings.json` right now covers the
rule, and *attributed* means the hook log recorded it firing. The gap between
② and ③ is the industry's "written but never loaded" (41 of 60 artifacts on
this machine), and it is printed as a number.

Then the docket, ownership, live receipts, the mine summary, the confirmed
precedents with their fire counts, the **spend meter**, a per-day table, the
ledger tail, `hooks status`, and the limitations.

The spend meter has two halves and says which is which: our own `claude -p`
calls are **exact** (from `spend.jsonl`, each one capped by
`--max-budget-usd`), and the transcript token totals are priced at Anthropic's
published list rates (cache reads 0.1×, cache writes 1.25× input) as an
**estimate** — subscription usage is not billed per token at all, and models
the table does not know are counted in tokens and left unpriced.

### `precedent examine --candidate <path-or-id>` — history becomes an exam

The examiner is the second half of ②: the temporal gate judges *rules*, and
everything else — a skill, a CLAUDE.md edit, a nightly draft — has to be
measured instead.

**Which sessions become cassettes.** A session is eligible when it carries a
*detectable terminal verification command* — the last `Bash` call matching
`pytest / tox / npm test / jest / vitest / cargo test / go test / make
test|check|lint / gradle / mvn / rspec / ruff / mypy / tsc / eslint` — **or**
when at least one confirmed precedent can be expressed as a grader. A session
with neither has no oracle, is skipped, and the reason is printed. The oracle
is the session's own, written by you at the time; the examiner never invents
one.

**What a case looks like.** A temporary *plugin* directory is built around the
candidate (`.claude-plugin/plugin.json` + `skills/<name>/SKILL.md`) and each
cassette becomes `evals/<case>/case.yaml` (schema 1.1):

```json
{"schema_version": "1.1", "name": "case-f04b24b3",
 "context": {"scaffold_script": "git -C <repo> archive --format=tar <sha> | tar -x -C \"$PWD\"",
             "history_file": "history.jsonl"},
 "execution": {"prompt": "<the last human turn before the fork>",
               "allowed_tools": ["Read", "Glob", "Grep"], "max_turns": 12},
 "runs": 2,
 "graders": [{"type": "tool_used", "name": "verification-ran", "tool": "Bash",
              "input_match": "\\.venv/bin/python -m pytest -q", "min": 1, "weight": 2},
             {"type": "regex", "name": "verification-in-trace", "target": "trace",
              "pattern": "\\.venv/bin/python -m pytest -q", "weight": 1},
             {"type": "tool_used", "name": "precedent-p-1a2b-Bash", "tool": "Bash",
              "input_match": "^pip install", "max": 0, "weight": 1}]}
```

* `scaffold_script` restores the tree as of the commit the repo was on when the
  session ran (`git rev-list -1 --before=<session start>`, then `git archive |
  tar -x`). It is **omitted** when no git snapshot is recoverable, and the case
  says so rather than pretending.
* `history_file` is the transcript truncated to the records *before* the fork
  point — the arm resumes where the session was, and the verification command
  is on the far side of the cut, so running it is a decision the arm has to
  make.
* graders are the verification command (`tool_used` + `regex`) plus one
  `tool_used … max: 0` per confirmed precedent that binds to a tool and an
  input regex. A precedent that cannot be expressed in the grader language
  (`input_field_missing`, `input_field_equals`) is **left out and counted**,
  not approximated.
* `case.yaml` is emitted as **JSON**, which is valid YAML 1.2. The writer stays
  stdlib-only and the escaping of regex graders cannot go wrong.

**Running it.** The design's argv is

```
claude plugin eval <dir> --json out.json --trust-plugin --no-publish \
       --max-cost-usd <cap> --runs 2 --ablation with-without --scaffold
```

and what actually runs is that list **filtered by what the installed binary
advertises** in `claude plugin eval --help`; a flag this build does not know is
dropped and named in the output, because an unknown option makes the
sub-command exit before anything runs. On this machine `--trust-plugin` is
dropped and reported.

**The fallback.** When the sub-command answers ``\`plugin eval\` is currently in
early access`` — or is otherwise unavailable — precedent runs the two arms
itself: `claude -p` per arm, the candidate reaching only the with-arm
(`--plugin-dir` for the built plugin, plus `--append-system-prompt`), and the
**same graders** implemented locally (`tool_used`, `regex`, `file_exists`; a
grader type the fallback cannot implement is reported as skipped, never counted
as a pass). Before every call the runner *reserves* one `--arm-budget`, and
stops when `max(reported spend, reserved)` plus one more arm would pass
`--max-cost-usd` — so the cap holds at `floor(--max-cost-usd / --arm-budget)`
calls even against a binary that ignores its own per-call flag and reports
`$0.00`.

**The evaluator does not get to write its own score sheet.** The results JSON
comes out of a subprocess whose plugin directory is built *around the
candidate*. An outcome is paired only when its case is one this run actually
compiled and its run index is one this run actually asked for; a case name we
never emitted, a run past `--runs`, or the same `case#run` twice is discarded
and named in the output. (方案 §2: Camp B's seven evaluator leaks are one bug —
the evaluator sitting inside the proposer's view.) Whatever `claude plugin
eval` reports as its own cost is written to `spend.jsonl` marked
`reportedByEvaluator`, because the whole exam is one subprocess and there is no
per-call reservation to bound it with.

**Ties are free.** A cassette the candidate could never have loaded in — a
project-scoped artifact and a session in another project, a project-scoped rule
whose `cwd_glob` excludes the cwd — is a tie *by construction*. Those cases are
never executed, cost nothing, and are still fed to the gate as ties so `n_pairs`
is the truth.

**And an exam that cannot answer is not paid for.** If no grader in a case could
fire for *either* arm with the tools they are allowed, the case is a tie by
construction too and nothing is run.

### `precedent examine` — the gate it feeds

`cases[].arms.with[i]` / `cases[].arms.without[i]` become paired binary
outcomes, one per run index, and go to `acceptor`:

* **`PairedBinaryGate(betting="mixture")`** for the superiority test, with the
  **harm martingale** at `α_harm = 0.10` — harm is deliberately cheaper to
  prove than improvement;
* **`SpendSchedule`** (SEA's CTHS, `Z = 3.3877`) hands out one α per examine
  run, `reserve()` → `commit()`; a run with **zero discordant pairs releases
  its round** — no evidence was examined at that level, so the budget is
  untouched. Re-examining the same content hash **reuses its round** instead of
  buying a fresh α;
* **`ProtectedCorpus` (TaskFloor)** on cases the incumbent has passed k/k in
  *earlier* runs, with k-of-n confirmation. The floor and the gate consume
  **disjoint** streams — `acceptor`'s own documentation is explicit that
  feeding the protected corpus to a gate makes the harm martingale fire ~100 %
  of the time on an equal-quality candidate — so a protected case is judged by
  the floor and excluded from the gate. On a fresh machine nothing is protected
  yet, the floor is `INCOMPLETE` (**not** a pass), and the certificate says so;
  `--require-floor` turns an `ACCEPT` with an unproven floor into a `HOLD`;
* the **order is owned by the acceptor**: an `InstanceSampler` seeded with the
  candidate's content hash commits the evaluation order before the first pair,
  so the same outcomes handed over in a different sequence produce the same
  `evidence_hash`.

Decision precedence is mechanical first: a `BLOCKED` floor beats everything,
then the harm martingale, then `ACCEPT` / `NSF` / `REJECT`, then `HOLD`. The
result is one `Certificate` in the hash-chained ledger — carrying
`content_hash`, `instance_set_hash`, `evidence_hash`, `prev_evidence_hash`,
`n_pairs`, `schedule_round` — and one **proposal** on the docket. Nothing is
applied.

`precedent` keeps lifecycle events, docket decisions and gate certificates in
*one* chain, so the ledger runs non-strict; `accept.verify_acceptance()` replays
just the gate certificates through `acceptor`'s own `CandidateMonitor`, which is
what catches a rewound wealth process or two candidates sharing a schedule
round.

### `precedent improve [--budget-usd 2]` — the nightly improver

Four failure signatures, all found offline:

| signature | what it is |
|---|---|
| `tool_error` | a tool result the model was handed as an error, keyed on the first *informative* line (a bare `Exit code 1` is skipped — on this machine it collapsed 39 unrelated failures into one bucket) |
| `retry` | the **identical** call repeated inside a 6-call window. Identity is the whole input, not a prefix: a 120-character prefix made every `cd "…" && python - <<'EOF'` heredoc look like the same call and produced a 149-strong "retry" cluster that was not a retry at all. Polling tools (`TaskOutput`, `BashOutput`, `Monitor`, …) are exempt — a poll repeated 27 times is a poll |
| `correction_no_precedent` | a mined topic that no confirmed rule covers |
| `written_but_violated` | a topic whose words are already in CLAUDE.md / memory, and which happened anyway |

The top clusters get **one bounded edit each**, drafted by `claude -p --model
sonnet` under both a per-call and a total budget. The draft must be a single
JSON object with `surface` (`claude_md` | `skill` | `precedent` — hooks,
settings and precedent's own code are not surfaces the improver may touch),
`hypothesis`, `expected_effect`, `declared_paths` and `edits`. Then, in order:

1. **schema** — one file, bounded content, and an LLM cannot smuggle `"status":
   "active"`, its own `id` or a pre-cooked `"birth"` block into a rule;
2. **diff == declared paths** — exactly the declared files, no more and no
   fewer, and no `..`;
3. **lint** — frontmatter parses and carries `name`/`description`; every
   referenced file exists next to the declared path; no secret (Anthropic /
   OpenAI-style / GitHub / AWS / Slack / Google keys, private-key blocks,
   bearer tokens, assigned secrets); and **no session-specific fact in a global
   artifact** — an absolute home path, a `host:port`, an IP, a dated statement,
   a session id or a pid. The same fact in a *project*-scoped file is fine, and
   the scope is decided by where the declared path lives;
4. **the competent gate** — a `precedent` draft goes to the TEMPORAL BIRTH
   GATE; anything else goes to the EXAMINER when eligible cassettes exist, and
   is `HOLD`-ed with reason **`no evidence`** when they do not. "We could not
   measure this" is a first-class answer here, not a silent pass.

Everything that survives lands on the docket as a proposal; everything rejected
lands in `rejected.jsonl` with its reason and comes back as a negative example
in the next run's prompt. That is ⑤.

### `precedent loop` — the whole cycle, and its schedule

```
precedent loop --dry-run     # the default: zero model calls, zero writes
precedent loop --apply --budget-usd 2 --candidate <path-or-id>
precedent loop --cron        # a launchd plist and a crontab line, printed
```

`--dry-run` walks ① miner → ① improver (clusters only) → ② cassettes → ③ hooks
status → ④ funnel + alarms → ⑤ negative examples and prints what each step
found. A step that fails is reported and the cycle continues, because a nightly
job that dies on step three tells you nothing about steps four and five.

`--cron` **prints** the plist and the crontab line and installs neither — the
same rule as `hooks install`: scheduling something that spends money is a
decision you make with your own hands.

### `precedent snapshot` / `precedent undo --session <id>`

Tar-free and content-addressed: every learned-state file body goes to
`blobs/<sha256>` (deduplicated across snapshots) and a JSON manifest in
`snapshots/` records the tree. Roots:

```
<claude home>/CLAUDE.md, <claude home>/skills/**, <claude home>/projects/<slug>/memory/**
<cwd>/CLAUDE.md, <cwd>/.claude/skills/**, <cwd>/.claude/rules/**     for every cwd seen
```

`undo --session <id>` asks the receipts change feed which of those files the
session mutated, picks the newest snapshot **older than the session start**,
and plans `restore` / `recreate` / `delete` / `unchanged` / `blocked` per file,
flagging any file a *later* session also wrote (`CONFLICT:` — restoring would
discard that work too). Dry-run by default.

### `precedent evaluate-bundle` — the OpenClaw bundle gate

The Python core an OpenClaw plugin shells out to from its
`skill_proposal_evaluate` hook. **Event JSON in on stdin, result JSON out on
stdout, nothing else on stdout ever** — the TypeScript shim parses it blind, so
every diagnostic goes to stderr. That is enforced rather than intended: the
whole evaluation runs with `sys.stdout` redirected to `sys.stderr`, so a stray
`print` inside a check — or a check that tries to write `{"decision": "pass"}`
itself — lands where diagnostics belong instead of splicing itself into the
document the shim parses. The only write to the real stdout is the finished
document.

```bash
precedent evaluate-bundle --stdin --json < event.json     # what the plugin runs
precedent evaluate-bundle --dir ./candidate-skill         # …or a directory
precedent evaluate-bundle --dir ./new --baseline-dir ./current   # an update
```

Seven deterministic check families — regex, sha256, `compile()` and set
algebra, **no model call and no network** — each finding carrying a stable
`ruleId` a plugin can count, suppress or allowlist:

| family | what it proves | the CRITICAL member |
|---|---|---|
| `structure/*` | every `files[].path` stays inside the bundle, SKILL.md parses, `name`+`description` are declared, the name matches the proposal, referenced files are *in the bundle*, shebanged scripts compile (`compile()` / `bash -n`), base64 decodes, no invisible or bidi characters, nothing was silently truncated | a path that escapes the bundle (`../../etc/cron.d/evil` — applying it is an arbitrary file write, and what the file *contains* is beside the point), an explicit bidi override (the line reviewed is not the line that runs), or a `sha256` / `treeSha256` that does not recompute — **what you were asked to grade is not what you were handed** |
| `dlp/*` | `precedent.scrub` over every text file, and over the *logical* lines too, so a credential split across a shell line continuation cannot hide in the seam. The match is **never** echoed: kind + file + line, and that is all | a credential (`sk-…`, `ghp_…`, `xox…`, `AKIA…`) |
| `evidence/*` | a line claiming *verified / tested / 已确认* has to cite a command, a path or a date | a placeholder (`NNN`, `HH:MM`, `TODO`, `<your-…>`, `xxx`) inside such a line — hermes#89963, where template text was codified as "Verified 2026-08-04" |
| `scope/*` | absolute home paths, `localhost:PORT`, machine hostnames and dates-as-facts in a skill that never says where it applies | — |
| `baseline/*` | **the SafeEvolve rule**, updates only: a revision may not silently DROP a verification or reversibility requirement its ancestor asserted — nor *reformat* it away, which is why a replacement has to share two content tokens with what it replaces and not merely one. Each dropped requirement is listed individually | every one of them |
| `risk/*` | updates only: destructive verbs, egress hosts and credential reads the baseline did not have | a new egress host **combined** with a new credential read or destructive verb — the shape of exfiltration |
| `size/*` | SKILL.md over 25 KiB, bundle over 1 MiB — retrieval precision collapses as the pool grows | — |

Any `critical` → `block`; else any `warn` → `revise`; else `pass`.

**Fail-closed, and that is the whole point.** OpenClaw records a thrown error or
a timeout as an *attributed error outcome* — it does **not** block. Only a
completed `decision: "block"` vetoes an apply, so an evaluator that crashes has
silently stopped being a gate. `evaluate_event` therefore converts every
internal failure — a malformed event, a check that raises, a snapshot that does
not decode — into a valid result document with `decision: "block"` and a
`decisionReason` naming the exception, and the CLI exits **0** whenever it
managed to write a document: `pass`, `revise` and `block` alike. The decision
lives in the document, never in the exit status. `--no-fail-closed` lets the
exception out, for debugging only.

Calibration, measured on the 44 real skills in `~/.claude/skills` on this
machine (copied read-only into a tmp dir): **16 `pass`, 28 `revise`, 0
`block`** — zero false criticals on a working corpus, and the rules added by
the adversarial pass (`unsafe-path`, `bidi-override`, `invisible-character`,
`duplicate-path`, `file-truncated`) fire **zero** times across its 825 files. A
gate that vetoes real work gets switched off, so `tests/test_bundle.py` asserts
that property and `tests/test_bundle_adversarial.py` asserts each new rule is
silent on an ordinary skill as well as loud on the attack.

## Layout

```
src/precedent/
  state.py     state dir, claude-home detection, the write guard, the ledger
  mine.py      human turns, sentences, patterns + guards, the question filter,
               reverts, cross-session repeats, tokens, topics
  rules.py     RULE DSL v1: tools, matchers, templates, the regex linter and
               the bounded matcher
  compile.py   extraction, topic -> rule, the TEMPORAL BIRTH GATE, auto-compile
  llm.py       `claude -p`: the argv (budget, safe-mode, no tools), the
               prompt, the spend ledger, the negative examples
  examine.py   THE EXAMINER: cassette selection, the git scaffold, the
               transcript prefix, case.yaml, the `claude plugin eval` runner
               with its flag probe, the paired `claude -p` fallback and the
               grader language implemented locally
  accept.py    ② the wiring to `acceptor`: PairedBinaryGate + harm martingale,
               SpendSchedule, ProtectedCorpus, the certificate, the docket
  improve.py   ⑤ failure clustering, the bounded-edit prompt, the schema, the
               declared-paths check, the lint, the routing
  proposals.py the third kind of docket entry — a bounded edit that is never
               applied
  loop.py      one nightly cycle, and the launchd/crontab snippet
  _hooklib.py  THE HOOK RUNTIME — copied verbatim to <state>/hooks/_plib.py:
               governed trees, bash write targets, snapshots, diff summaries,
               ownership, the DSL evaluator, the six handlers, guard_main
  hooks.py     rendering the six scripts, the settings block with timeouts and
               markers, backup/merge/uninstall, the install receipt, drift
  ownership.py owners.json, the guard rule, the agent-created index, candidates
  live.py      live receipts, and the live-wins merge into a receipts scan
  docket.py    entries + evidence, confirm / reject / snooze, the batch digest
  audit.py     the funnel, the four STARVATION alarms, the spend meter, daily
  snapshot.py  content-addressed snapshots, undo planning and application
  report.py    the 10-line first screen, the mine report, the daily digest
  bundle.py    THE OPENCLAW BUNDLE GATE: BundleSnapshot decoding and digest
               recomputation, the seven check families, requirement extraction
               and the replacement test, the risk delta, fail-closed grading
  cli.py       argparse wiring
```

```
~/.precedent/
  precedents.json    CONFIRMED rules — the only file the hook trusts
  candidates.json    compiled rule candidates + birth-gate counts
  candidates.jsonl   governed-write candidates, appended by the hook
  owners.json        who owns what (`precedent own`)
  agent_created.json paths an agent was recorded creating
  docket.json        confirm / reject / snooze decisions, with reasons
  receipts/<id>.jsonl LIVE receipts + the per-session event log
  changes.jsonl      the live change feed (PostToolUse)
  origins.jsonl      human turns (UserPromptSubmit) = trusted origin
  funnel.jsonl       per-session funnel counters (Stop)
  hooklog.jsonl      every hook decision and every hook error
  hooks/             _plib.py, the six scripts, installed.json
  blobs/ snapshots/ scans/ backups/ ledger.jsonl rejected.jsonl spend.jsonl
```


## Honest limitations

* Corrections are found by **surface pattern**, never by a model. A correction
  phrased without any listed word is missed; a turn that merely quotes one can
  be a false positive. The confidence score, not a boolean, is the output.
* The question filter uses "ends with ？/? and has no imperative". A polite
  order with no imperative word in it ("能顺手换成 uv 吗") is missed.
* Chinese topic grouping is jieba words *or* character bigrams. Which one ran
  is printed; the similarity threshold (`--similarity`, default 0.15) is a
  tunable, not a truth, and on small corpora single-link clustering can chain.
* The revert detector reads the **main** transcript only, blanks heredoc bodies
  (a command that writes a file *containing* `git checkout --` is not a
  revert — that cost four false positives on this machine before it was fixed)
  and matches only at command positions. A revert performed outside Claude Code
  is invisible.
* **The bundle evaluator's `treeSha256` has no wire-format specification.**
  The seam documents the field, not its encoding. Per-file `sha256` is
  unambiguous and is enforced exactly; the tree digest is recomputed in the
  three canonical shapes a sane producer would use and only reported when
  *none* agrees. A content change moves all three, so tamper detection holds —
  but a producer using a fourth encoding would get a false `critical`, which is
  the direction this gate is allowed to be wrong in.
* **A secret can still be assembled out of pieces the scrub cannot see.** A
  credential split across a shell line continuation is caught, because the
  logical line is scanned too; one built by string concatenation
  (`"sk-" + tail`) or read out of a variable is not, and a deterministic
  checker cannot chase arbitrary string algebra. The DLP pass is a net for
  shapes, not a proof of absence.
* **`evaluate-bundle` is lexical, like everything else here.** It extracts
  requirements phrased as sentences; an obligation that lives only in a diagram
  or a table header is invisible to it. `dlp/*` inherits `scrub`'s
  conservatism — it will call a 32-character digest an opaque blob — and its
  Chinese claim detection deliberately matches only the *completed* forms
  (`已确认`, `确认过`), because bare `确认` is an instruction far more often
  than a claim and treating it as one made every command table in a real skill
  a critical finding.
* **The gate is retrospective, not predictive.** It proves the rule fires where
  it should and is quiet where it should *on the record you already have*,
  after the moment you stated the policy. It does not prove generalisation, and
  it cannot distinguish "quiet because the rule is right" from "quiet because
  the rule is so narrow it only matches the one action that provoked it" — the
  `--llm` drafts on this machine did exactly that, and passed. Read the rule,
  not just the verdict.
* ε is a per-**action** rate. A big corpus makes it permissive; the report
  prints the absolute false-fire count and the number of sessions it spans for
  exactly that reason.
* "Written but violated" is keyword matching. It shows the topic's words are in
  a memory file; it does not prove that file was loaded in the session where
  the correction happened — run `precedent scan` for that.
* `dont_use` matches the whole `command` string, so a rule against `pip` fires
  on `grep pip` too. Tighten the regex by hand before confirming.
* Only deterministic checks exist. Executable checks (sandboxed tests) and
  judge-based checks are not implemented.
* **The `InstructionsLoaded` payload shape is read defensively, not from a
  spec.** The handler accepts `files` / `instructions` / `loaded_files` /
  `memory_files`, as a list of strings or of objects, and it only claims
  `loaded_complete` when it was handed content it could compare against the
  file on disk. If a build hands it a shape it does not recognise it writes one
  row saying so and stops — it never guesses a status.
* **Ownership defaults to "yours", which is a choice.** A governed file created
  out of band (by you, by a script, by another tool) has no agent-creation
  record, so a subagent write to it asks. The conservative direction is
  deliberate — it is an `ask`, never a `deny` — but it does mean the index is
  only as good as the last `hooks install` / scan that rebuilt it.
* **"Subagent" is read from the payload, not from a spec.** `agent_id` /
  `agentId` / `subagent_id` / `isSidechain` / a non-`main` `agent_type` all
  count, and anything agent-shaped is treated as a subagent because the failure
  being guarded is the background fork. If a Claude Code build sends none of
  them, every write reads as `foreground` and **the guard never fires**: the
  pass still records every governed write, with its pre-image and its diff, but
  it never interrupts. `precedent docket` showing writes whose `agent` is
  always `foreground` is what that looks like, and it is worth checking before
  trusting the guard.
* **The ownership pass is still reached through a tool matcher.** Enforcement
  is by *path* once the hook runs, but the hook runs for
  `Bash|Edit|MultiEdit|NotebookEdit|Write` (plus whatever active rules name). A
  third-party MCP tool that writes files does not match, and is invisible to
  it.
* **Bash write detection is the same three-confidence heuristic as the receipts
  change feed**, restricted to tokens that are already governed. A governed
  write hidden inside a script the agent invokes (`./deploy.sh`) is invisible
  to it; the `PostToolUse` capture and the transcript change feed are the
  backstop, not a guarantee. It is also **bounded on purpose**, because it runs
  before every Bash call: 8 KB of command, 32 distinct governed-looking tokens,
  8 occurrences of each, and 512 characters of left context to find the write
  construct in. A write construct further away than that is missed. (The
  earlier version searched the whole command with two lazy unbounded gaps —
  `\bsed\b[^|;&]*?-i[^|;&]*?<path>` — which cost **32 s** on an 8 KB command
  with thirty governed paths in it, i.e. past the hook's own 5 s timeout, on
  the hot path of every Bash call. It is now a `str.find` plus a bounded
  window; the same case is 0.4 ms and the budget has a test.)
* **At most 16 governed paths per tool call** are snapshotted and recorded. A
  command that writes more says so in `hooklog.jsonl` (`targets_truncated`,
  with the paths it dropped) — the cap is real, but it is not silent.
* **Symlinks are resolved on both sides before a path is called ungoverned**:
  a `~/.claude` that is itself a symlink (every dotfile manager does this) and
  a symlink pointing *into* the governed tree both used to make the tree
  invisible to the hook. The textual test runs first and answers almost every
  call; `realpath` is a cached fallback taken only when it says "not governed".
  A hard link is still invisible — it has no path to resolve.
* **`Stop` counters are per session and come from that session's own receipt
  file.** A session whose hooks were installed midway has partial counts, and
  the `Stop` hook fires per agent turn, so the funnel line is a running tally,
  not a single end-of-session record.
* **The transcript half of the spend meter is an estimate at list price.**
  Subscription usage is not billed per token; models not in the table are
  counted in tokens and left unpriced; a session that names two models
  attributes its tokens to the first and says how many sessions did that.
* **The funnel's ④ attributed counts hook fires, not behaviour change.** It
  proves the rule matched something real; it does not prove the agent would
  have done the thing, nor that blocking it helped.
* The regex bound is reject-then-quarantine, not a hard mid-match deadline
  (see above). The linter is a structural filter over the known catastrophic
  families, not a decision procedure: a pattern outside those families that is
  merely slow still has to be caught by the quarantine.
* A correction is mined from prose only: fenced code blocks (```` ``` ````,
  `~~~`) are stripped first, because a pasted log that reads `ERROR: don't use
  pip` is something the user is *showing* the agent. A correction written
  inside a fenced block is therefore missed.
* **The miner reproduces your own sentences verbatim, and there is no DLP pass
  on its output.** `mine --md`, `report --md` and `docket` print the correction
  quote, and a correction can carry anything you typed — on this machine one
  mined quote contained a phone number and a WeChat id (see
  [DEMO.md](../../DEMO.md) §1, where it is the file's single redaction). The
  secret scan runs on `improve` *drafts*, which is a different path. Read a
  mined report before you paste it anywhere.

### The examiner

* **`--allow-bash` is off by default, and that costs the exam most of its
  power.** The strong grader asks "did the arm *run* the session's own test
  command?", and answering it means handing an unattended agent a
  `Bash(<prefix>*)` grant over your real working tree — a *prefix* grant,
  because the recorded command is an absolute `cd … && …`. With the default,
  the `tool_used` grader is **dropped rather than failed** (an arm that was
  never allowed to call `Bash` is not evidence about the candidate) and what is
  left is the weak oracle: did the arm *reach for* the verification command.
  The case's description and tags say `WEAK` / `read-only-arms` so this is on
  the record and not in a footnote.
* **The fallback does not replay the transcript.** `claude -p` has no way to
  load a history file, so `context.history_file` is honoured by `claude plugin
  eval` and **ignored** by the paired fallback, which replays
  `execution.prompt` into a fresh session on the scaffolded tree. Both arms are
  handicapped identically so the pairing is still sound, but the arms start
  colder than the official evaluator's would. Every fallback result carries the
  note.
* **`claude plugin eval` is gated behind early access per organisation.** On
  this machine it prints `` `plugin eval` is currently in early access `` and
  exits 0 with no JSON. That is detected as *unavailable*, not as a failed
  candidate — and the difference matters, because the second reading would
  reject every candidate on a machine that simply cannot run the evaluator.
  `--trust-plugin` does not exist in 2.1.258 either; it is dropped and named.
* **The dollar cap is a bound on *calls*, not on money.** The reservation
  makes `floor(--max-cost-usd / --arm-budget)` the maximum number of arms,
  and each arm carries `--max-budget-usd`. If the binary honours that flag the
  run stays inside the cap; if it ignores the flag *and* under-reports, the
  count is still bounded but the dollars are not. There is no way to bound
  actual spend from outside a subprocess that lies about both — the honest
  claim is "bounded calls at a declared per-call cap", not "bounded dollars".
* **`claude plugin eval` gets no reservation.** The whole exam is one
  subprocess with one `--max-cost-usd`, so its cost is whatever it reports.
  That number is recorded as `reportedByEvaluator` precisely so it reads as a
  claim rather than as a measurement.
* **The case-name filter is not evaluator isolation.** Discarding results for
  cases this run never compiled stops the cheap version of the attack (a
  fabricated sweep); it does not make the evaluator unforgeable. A `with`-arm
  that can write inside its own sandbox can still influence a grader that
  reads that sandbox. Real isolation is the L4 item (mount namespace /
  container), and on macOS 方案 §L4 already labels it *a weaker boundary*.
* **The git scaffold is `rev-list -1 --before=<session start>`**, which is the
  commit the repo was *on* only if nothing was committed and reverted in
  between, and it falls back to `HEAD` (reported as the weaker stand-in) when
  no commit predates the session. A project that is not a git repository gets
  no scaffold at all, and both arms then start in an empty directory.
* **`runs: 2` is two samples.** Two runs per arm on one cassette is four paired
  observations at most; the gate's honest answer at that sample size is almost
  always `NSF` ("the budget ran out before 1/α was reachable"), which is
  exactly what the real run below reports. Certifying a +30pp improvement needs
  ~40 pairs and +10pp needs 160+.
* **The local grader is not the official one.** `tool_used`, `regex`
  (`last_message` / `trace` / a file) and `file_exists` are implemented;
  `tool_order`, `llm` and `baseline` are not, and are reported as *skipped*
  rather than counted as passes. The examiner only emits the first three, so a
  skipped grader means somebody hand-edited a case.

### The improver

* **LLM-written skills have no measured benefit in the literature**
  (SkillsBench: 0 vs +16.2 for human-written; CTA: +0.3pp). Everything the
  improver produces is treated accordingly: it is a draft, it is gated, it is
  never applied, and `HOLD — no evidence` is the expected outcome on a machine
  without cassettes.
* **The clusters are signatures, not diagnoses.** "The same file was read 25
  times" and "a traceback appeared in four sessions" are real, countable
  patterns; whether either is a *problem* is the model's guess and your
  judgement, not a measurement.
* **The retry detector is still a heuristic.** Identity is the whole normalised
  input, polling tools are exempt by an explicit (and therefore arguable)
  list, and a legitimate repeated call — re-reading a file that genuinely
  changed — still reads as a retry.
* **The session-fact lint is a regex bank.** It catches home paths, `host:port`,
  IPv4, ISO dates, uuids and pids. A machine-specific fact phrased in prose
  ("the build server", "our staging box") goes straight through.
* **The secret scan is prefix-based.** It knows the common key shapes; a secret
  that does not look like one is not caught.
* **`diff == declared paths` compares paths, not content.** It proves the edit
  touches exactly what it said it would; it does not prove the *content* is
  what the hypothesis describes.
* **The protected-path guard is a denylist, not a sandbox.** It names
  `settings.json`, the `hooks/`, `.git/`, `.ssh/`, `.aws/` and `.precedent/`
  directories and precedent's own state files. A path it has not heard of is
  allowed onto the docket — which is safe only because nothing is ever
  applied: what the guard actually buys is that the docket never *recommends*
  that you hand-edit one of those files with model-written bytes.


## Measured on this machine (2026-09-15, read-only, $1.85 total)

Every number here came out of a real run against the author's own
`~/.claude` (8 sessions), with the state directory pointed at a scratchpad so
nothing under `~/.precedent` moved either. `settings.json` was byte-identical
before and after; no skill was created.

```
precedent loop --dry-run                        3.4 s, $0.00
  ① 8 sessions · 160 human turns (119 expansions skipped)
    18 corrections (11.2 %) → 16 topics, 1 repeated; 2 compiled, 0 PASS
  ① 25 failure clusters
  ② 1 eligible cassette of 8 sessions
    (7 have no terminal verification command and no confirmed precedent)
  ③ 0 confirmed precedents, hooks not installed
  ④ DRIFT alarm (nothing installed yet); funnel 0 → 0 → 0 → 0
  ⑤ 0 rejected drafts

precedent improve --top 4 --budget-usd 0.60      $0.1863, 4 drafts
  4 drafts, all survived schema + declared-paths + lint
  4 × HOLD "no evidence" (run with --no-examine)
  e.g. d-028863b6  skill  ~/.claude/skills/avoid-redundant-reads/SKILL.md
       hypothesis: the agent keeps re-reading the same image file many times
                   within one session instead of reusing what it already saw
       expected_effect: the "Read: …/vN/kN_N.png" repeat signature drops

precedent examine --candidate d-028863b6 --runs 2 --max-cost-usd 0.80
                                                 $0.6892, 4 arms
  claude plugin eval  : unavailable — `plugin eval` is currently in early access
  dropped flags       : --trust-plugin (not in 2.1.258)
  fallback            : 4 paired `claude -p` calls, read-only arms
  pairs               : 2 (ties 2, discordant 0)
  verdict             : NSF — threshold_unreachable_within_budget
  schedule            : round released (no evidence examined, α untouched)
  certificate         : 5102b89e… in the ledger; docket d-a336df5d, not applied
```

Two things that run found and that are now fixed in the code:

* **A drafting call cost $0.33 and 107 s and never answered** — it hit
  `error_max_budget_usd` three times out of three, because `claude -p` was
  loading the full CLAUDE.md/skills/plugins/hooks loadout. With `--safe-mode
  --tools "" --disable-slash-commands` the same prompt is **$0.009 and 3.8 s**.
  That is a 37× cost difference *and* the right security posture: a proposer
  should not be able to read the state it is proposing to change.
* **The first retry clustering was junk** — normalising paths to `<path>`
  merged 149 unrelated `Read`s into one cluster, and comparing only the first
  120 characters merged every `cd "…" && python - <<'EOF'` heredoc into one
  more. Whole-input identity plus a polling-tool exemption took 57 clusters
  down to 25 real ones.

And the honest reading of the `NSF`: two runs on one cassette is four paired
observations, both arms scored 0 on the weak read-only oracle, and the gate
correctly refused to call that an improvement. **"No effect measured" is the
result, it cost $0.69, and the α budget was not spent** — which is the design
working, not the design failing.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
