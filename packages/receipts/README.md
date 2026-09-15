# receipts

Read-only **load receipts, change feed and activation funnel** for Claude Code
learned state — memory, `CLAUDE.md`, `.claude/rules/*.md` and skills.

It answers the three questions users actually file, using nothing but the
transcripts and files Claude Code already writes:

| Question | Filed as | Section of the report |
|---|---|---|
| "Did my memory / CLAUDE.md / skill actually load — whole, truncated, or not at all?" | claude-code#82056 (48 comments), #92998 (overflow silently drops the newest entries), #90604 | **Receipts** |
| "What changed in my agent's learned state since yesterday, from which session, why?" | hermes-agent#12238 (most-reacted loop issue), prime-agent#1208 ("/refine is invisible"), codex#34668 ("visible write receipt") | **Change feed** |
| "Which learned artifacts are never used?" | hermes#96704 (182 skills, many `use_count=0`, three clusters of duplicates) | **Activation funnel** |

Design constraints: Python 3.11+, standard library only, streaming readers, and
**it never writes anything under the Claude home it reads** (the CLI refuses an
output path inside it).

## Install

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e . pytest
```

## Use

```bash
python -m receipts scan                                   # all projects, Markdown to stdout
python -m receipts scan --claude-home ~/.claude \
                        --project Agent-Harness \
                        --last 5 \
                        --json out/receipts.json \
                        --md  out/receipts.md
```

| flag | meaning |
|---|---|
| `--claude-home PATH` | Claude home to read (default `~/.claude`) |
| `--project SUBSTRING` | only projects whose slug contains this substring |
| `--last N` | only the N most recent sessions |
| `--json PATH` | full result, `schemaVersion: 1` |
| `--md PATH` | Markdown report (default: stdout) |
| `--no-subagents` | ignore `<session-id>/subagents/**.jsonl` |
| `--quiet` | suppress stdout |

Every claim in both outputs carries a session id, a timestamp and a
`<transcript file>:<line>` locator, so any line can be checked by hand:

```bash
sed -n '20p' ~/.claude/projects/<slug>/<session-id>.jsonl | python3 -m json.tool | less
```

## What it reads

| Source | Used for |
|---|---|
| `~/.claude/projects/<slug>/<session-id>.jsonl` | sessions, tool calls, assistant text, usage |
| `…/<session-id>/subagents/**/*.jsonl` | subagent writes, folded into the parent session |
| `attachment.type == "prompt_snapshot"` → `systemPrompt[]` | the rendered system prompt — the ground truth for "did it load" |
| `attachment.type == "instructions"` → `files[{path,type,content}]` | authoritative per-file load record (`AutoMem`, project instructions) |
| `attachment.type == "skill_listing"` → `names`, `content` | which skills the session was shown, with their descriptions |
| `attachment.type == "invoked_skills"`, `tool_use{name:"Skill"}`, `attributionSkill` | skill invocation (a citation signal) |
| `<cc-memory filenames="a.md,b.md">…</cc-memory>` in assistant text | memory citation — the direct adherence signal |
| `file-history-delta` → `trackingPath` | Claude Code's own first-write backup marker, used to corroborate the feed |
| `memory/MEMORY.md` + `memory/*.md`, `CLAUDE.md`, `.claude/rules/*.md`, `skills/*/SKILL.md` | the artifacts themselves |

## How a status is decided

Content is compared after collapsing whitespace, because the renderer reflows it.

1. **exact** — the whole normalized body is a substring of a recorded context blob.
2. **sections** — the body is split into paragraphs/headings and each is looked up
   separately. A partial hit is the signature of truncation. `MEMORY.md` is compared
   **line by line**, so the report can name the index lines that did not make it.
3. **lcs** — a ≥200-character longest-common-substring test (rolling-hash k-gram
   intersection) as a last resort, so heavily reflowed content still reads as
   "something got through" rather than "nothing loaded".

| status | meaning |
|---|---|
| `loaded_complete` | every comparable unit was found |
| `loaded_truncated` | some found, some not |
| `not_loaded` | the session *has* a usable context record and the artifact is not in it |
| `missing_on_disk` | referenced (e.g. by a `MEMORY.md` line) but the file does not exist |
| `unknown` | the transcript records nothing about this class of context — **no claim is made** |

`unknown` is load-bearing. Most sessions carry only a couple of `prompt_snapshot`
attachments; a tool that reported those as `not_loaded` would be lying.

## Change feed

Mutations come from `Write`, `Edit`, `NotebookEdit`, and `Bash`. Bash writes are
detected by pattern and the path must sit *inside* the write construct
(`> path`, `tee path`, `sed -i … path`, `rm|mv|cp path`) or be assigned to a
variable in a command that also contains a writer —

```bash
M=~/.claude/projects/<slug>/memory/project.md && python3 - "$M" <<'EOF'
import sys; p = sys.argv[1]; open(p, 'w').write(open(p).read().replace(...))
EOF
```

— which is how agents actually bypass the memory tool path (cf. hermes#99729:
"the approval gate is a convention — write_file/patch/terminal reach the skills
directory directly").

| confidence | meaning |
|---|---|
| `high` | tool-recorded (`Write`/`Edit`), or the path sits inside the write construct (`sed -i … path`, `> path`, `tee path`) |
| `medium` | the path is assigned to a variable in a command that also contains a writer |
| `low` | the path appears **only** inside an inline-code argument (`python -c "…"`, `node -e '…'`) — a quoted, demonstrated or tested command is indistinguishable from an executed one |

`+fh` in the table means a `file-history-delta` corroborates the row. Paths
outside the Claude home and outside every working directory the scan has seen
are out of scope, so a command that names some other repo's `CLAUDE.md` is not
mistaken for a learned-state write.

Each mutation carries the assistant's **stated reason**: the nearest preceding
assistant text block in the same transcript, ≤300 characters, with its own line
number.

### Out-of-band detection

For each artifact the feed predicts what should be on disk; the tool compares:

| reason | severity | meaning |
|---|---|---|
| `content_mismatch` | high | on-disk content ≠ the last attributed `Write` |
| `edit_not_present` | high | the last `Edit`'s new text is gone |
| `missing_after_write` | high | written by a session, absent from disk |
| `mtime_after_last_write` | high | mtime is newer than the last attributed write |
| `frontmatter_stamped_by_harness` | info | body matches; only YAML frontmatter differs — Claude Code stamps `node_type` / `originSessionId` / `modified` after a memory write |
| `harness_recorded_write_not_attributed` | info | a `file-history-delta` exists but no tool call explains it |
| `no_attributed_write` | info | no scanned session wrote it (vendor skills, hand edits, transcripts outside the scan) |

## Activation funnel

Per artifact: `created_by_session` / `created_at` (change feed, falling back to
`metadata.originSessionId`), `n_sessions_eligible` (sessions that started after
the artifact existed), `n_sessions_loaded`, `n_sessions_cited`, `last_cited`,
`last_modified`, `age_days`, `size_bytes`, `never_cited`, and near-duplicates by
normalized-description Jaccard ≥ 0.6. Kinds with no citation signal
(`CLAUDE.md`, rules) report `n/a` rather than a misleading zero.

Warnings are bucketed: truncated index / over cap, stale index entries, orphans,
near-duplicates, never cited, out-of-band.

## Limits

- Load detection is textual. It proves the artifact's text was present in a
  recorded context blob; it cannot prove the model attended to it. Absence of a
  `<cc-memory>` tag is weak evidence of non-use, not proof.
- Sessions without a `prompt_snapshot` yield `unknown`, not `not_loaded`.
- Bash mutations have no recoverable before/after in the transcript; the
  out-of-band check falls back to mtime for those.
- `file-history-delta` is emitted on a session's *first* write to a tracked file,
  so it corroborates rather than enumerates.

## Layout

```
src/receipts/
  textmatch.py   normalization, section/line coverage, 200-char LCS, Jaccard
  artifacts.py   artifact discovery, minimal YAML frontmatter, MEMORY.md index + caps
  paths.py       path -> artifact-id classification
  transcripts.py streaming JSONL reader, evidence extraction
  receipts.py    (session x artifact) load status
  changefeed.py  mutations, attribution, corroboration, out-of-band
  funnel.py      activation funnel, near-duplicates
  scan.py        orchestration, schemaVersion 1 result
  report.py      Markdown rendering
  cli.py         `python -m receipts scan`
```

## Tests

```bash
.venv/bin/python -m pytest -q
```

The suite builds a synthetic `~/.claude` under `tmp_path` covering a complete
load, a truncated 210-line index, a stale entry, an orphan, a `cc-memory`
citation, Write / Edit / Bash-heredoc mutations with attribution, an out-of-band
change and a near-duplicate pair. It never touches the real Claude home.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). `receipts` is one of
the three packages in the [Precedent](../../README.md) monorepo.
