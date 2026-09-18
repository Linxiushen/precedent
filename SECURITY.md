# Security policy

Precedent installs code that runs inside your agent, on the hot path of every
tool call, as you. This document says exactly what that means, what the tool
guarantees, what it explicitly does **not** guarantee, and how to get it back
out again.

## Reporting a vulnerability

Please **do not** open a public issue for a security problem. Use GitHub's
[private vulnerability reporting](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
on this repository ("Security" → "Report a vulnerability").

Include the version (`precedent --version`), the command, and a minimal
reproduction against a **synthetic** Claude home — never paste your real
transcripts, hook logs or `settings.json`; they contain your prompts, your file
paths and sometimes your credentials. Expect an acknowledgement within a few
days. This is a small project with no SLA; that is the honest answer rather
than a promised one.

Please do report: a way to make a hook script emit output that changes Claude
Code's decision without a confirmed precedent; a way to get `--apply` to write
a `settings.json` without a backup or to drop a user's own entries; a path that
overwrites a user file without a snapshot; a way for model-written bytes to
reach a file on disk; a regex or input that hangs a hook past its timeout; any
way `--state-dir` or `--claude-home` escapes to somewhere unexpected.

---

## The one thing to understand first

### Hooks run as you, with your permissions

`precedent hooks install claude-code --apply` writes six Python scripts into
`~/.precedent/hooks/` and adds entries to `~/.claude/settings.json` that tell
Claude Code to execute them. From then on **Claude Code runs those scripts as
your user, with your environment, your filesystem access and your credentials,
before and after tool calls.** That is the same trust level as anything else in
your `settings.json`, and it is not a sandbox.

Consequences you should be comfortable with before you run `--apply`:

* anyone who can write `~/.precedent/hooks/` can run code as you, every time
  you use Claude Code. Precedent's own `PreToolUse` hook treats that directory
  as governed, but **the filesystem permissions are the real boundary**;
* the hooks read `~/.precedent/precedents.json` on every call. That file is the
  only thing they trust, which is why `precedent confirm` re-validates a rule
  against the current schema and the current regex linter before it can become
  `active` — a candidate compiled by an older build, or hand-edited, does not
  get a free pass;
* the hooks write logs (`hooklog.jsonl`), change records (`changes.jsonl`),
  human turns (`origins.jsonl`) and **pre-image file snapshots** (`blobs/`)
  under your state directory. Those contain your prompts and your file
  contents. `~/.precedent/` is as sensitive as `~/.claude/` — back it up the
  same way and do not commit it.

This is why installation is a deliberate, hand-run step with its own flag
(`--i-know` for your real home) rather than something `init` does for you.

---

## What is guaranteed

Each of these is an invariant with tests behind it. The test suite runs against
synthetic Claude homes under `tmp_path`, with a `real_home_canary` fixture that
fails the test if the real `~/.claude` or `~/.precedent` was touched.

### 1. Exactly one command writes a `settings.json`, and only after a backup

`hooks install --apply` and `hooks uninstall --apply` are the only writers.
Before either touches the file it copies it to
`~/.precedent/backups/settings-<timestamp>.json`.

The merge is **non-destructive** (your other hooks and settings survive),
**idempotent** (a second run reports "nothing changed" and writes no second
backup) and **atomic** (`os.replace`, preserving the file's own mode). A
`settings.json` precedent cannot parse — not valid JSON, not a JSON object, or
a `hooks` value that is not an object — is **never written**: merging into one
of those would mean *replacing* your file rather than adding to it, so
`--apply` refuses, names the reason, and exits 2.

Every entry precedent writes is **marker-tagged twice**: the command points
into that state directory's `hooks/`, and it carries `--precedent-hook <Event>`.
An entry is ours only when it carries **both** marks, so uninstall removes
exactly ours and nothing else.

**Every other command is read-only on the Claude home**, enforced by
`state.guard_not_under_claude_home()`, which raises rather than writes.

### 2. A hook cannot break Claude Code

* **Bounded**: the tests assert every one of the six scripts answers
  **end-to-end in under 300 ms**. Regex matching is length-capped (400 chars of
  pattern, 4096 chars of subject), statically linted against the catastrophic
  backtracking families before a rule is ever stored, and a pattern that blows
  a 50 ms match budget is **quarantined** and skipped from then on.
* **Defensive**: stdin JSON is parsed defensively; a torn or unexpected payload
  is handled, not assumed away.
* **Fail-open**: on **any** internal exception a hook exits 0 with **no
  stdout** and appends the error to `~/.precedent/hooklog.jsonl`. A missing
  `_plib.py` exits 0 as well. A missing or corrupt `precedents.json` denies
  nothing.
* **Narrow**: a hook returns `deny` or `ask` **only** when a precedent whose
  `status` is exactly `active` — one you confirmed by hand — matches.
  Everything else is allow + log. Five of the six hooks print nothing at all;
  `PreToolUse` is the only one whose answer is a decision.

### 3. Nothing is deleted or overwritten without a snapshot

Every learned-state file body goes to `~/.precedent/blobs/<sha256>` before it
can be replaced, and `undo --session <id>` round-trips. `undo` is a dry run by
default, and it flags any file a *later* session also wrote rather than
silently discarding that work.

### 4. Model output is untrusted, and money is capped

Every `claude -p` call carries `--max-budget-usd` (validated before the
subprocess starts), `--no-session-persistence` and an explicit `--model`, and
**never** `--dangerously-skip-permissions`. Drafting calls also carry
`--safe-mode --tools "" --disable-slash-commands`, so a draftsman cannot read
the CLAUDE.md, skills, plugins and hooks it is drafting *about*. Spend is
recorded in `spend.jsonl`, and the budget check reserves each call at its cap
instead of trusting the binary's self-reported cost.

What comes back is schema-validated (unknown keys dropped, so a draft cannot
smuggle `"status": "active"`, its own id or a pre-cooked verdict) and then run
through the deterministic gates. **Mechanical rejection overrides LLM approval,
never the reverse.**

### 5. Nothing is applied automatically

`improve` and `examine` write proposals, not files. `docket confirm` records a
decision and prints the path. Precedent never edits a skill, a CLAUDE.md or any
other user file on its own.

---

## Fail-open semantics, and why

**When enforcement breaks, tool calls are allowed, not blocked.** A corrupt
`precedents.json`, a hook exception, an uncompilable regex, a `_plib.py` that
was deleted — every one of them results in exit 0, no output, and a line in
`hooklog.jsonl`.

This is a deliberate trade, and it cuts both ways:

* **The good half.** A gate that bricks your agent when its own state file is
  broken is worse than no gate at all. Precedent is not the last line of
  defence in your system, and it should never be the reason you cannot work.
* **The bad half, stated plainly.** An adversary who can corrupt
  `~/.precedent/precedents.json`, delete `~/.precedent/hooks/_plib.py`, or make
  a hook throw, **silently disables enforcement**. Failing open means failing
  quietly, and quiet failure is exactly the production disease this project was
  built to diagnose (a learning loop broken for weeks with nobody told).

Precedent's answer to the bad half is **audit, not blocking**:

* `precedent report` raises a `HOOK` alarm on hook errors, quarantined regexes
  and an unreadable `precedents.json`;
* it raises `NOT_INSTALLED` (rules confirmed but not installed) and `NO_FIRES`
  (installed ≥ 14 days, never fired once);
* `precedent hooks status claude-code` diffs what is installed against the
  receipt written by the last `--apply` and **exits 1 on any drift** — a script
  missing, from an older build, or **edited by hand** (it knows the difference,
  because the receipt records the sha256 of what was written).

**If you rely on enforcement, run `precedent hooks status` on a schedule and
read the alarms in `precedent report`.** That is the intended operational
posture. Treat precedent as a policy *ratchet with an audit trail*, not as a
security boundary.

---

## What is explicitly NOT guaranteed

* **Precedent is not a security boundary.** It is a policy gate over Claude
  Code's own hook API. An agent that can run arbitrary Bash can, in principle,
  modify the state that gates it. The ownership pass snapshots and records
  every governed write — including `>` redirects, `tee`, `sed -i` and heredocs
  — but it is a *heuristic over command text*, bounded on purpose (8 KB of
  command, 16 governed paths per call, a 512-character context window). A write
  hidden inside a script the agent invokes (`./deploy.sh`) is invisible to it.
* **Enforcement can be redirected, but not silently.** The generated hooks
  read a state directory baked in at install time. `PRECEDENT_STATE_DIR` (and
  `PRECEDENT_CLAUDE_HOME`) can point them somewhere else, which is how the
  test suite runs the whole thing against `tmp_path` — but only when
  `PRECEDENT_ALLOW_STATE_REDIRECT` is set alongside. Without it the variable
  is ignored and the hook writes one line to stderr naming the path it
  declined and the path it is still enforcing against. Until 2026-09-18 the
  redirect needed no opt-in and said nothing, so `export
  PRECEDENT_STATE_DIR=$(mktemp -d)` turned every deny off with no trace in
  the output, the hook log or the ledger. Anyone who can set environment
  variables for your Claude Code process can still turn enforcement off, with
  the opt-in or by uninstalling the hooks; what they can no longer do is turn
  it off without the transcript showing it.
* **Enforcement is reached through a tool matcher.** The hook runs for
  `Bash|Edit|MultiEdit|NotebookEdit|Write` plus whatever tools your active
  rules name. A third-party MCP tool that writes files does not match and is
  not seen.
* **"Subagent" is read from the payload, not from a spec.** If a Claude Code
  build sends none of the fields precedent recognises, every write reads as
  `foreground` and the ownership guard never fires. The pass still records
  every governed write; it just never interrupts. `precedent docket` showing
  writes whose agent is *always* `foreground` is what that looks like.
* **The regex defence is reject-then-quarantine, not a hard deadline.**
  CPython's `re` holds the GIL and ignores signals mid-match, so a stdlib-only
  tool cannot enforce a real mid-match timeout. The static linter is the only
  defence against patterns that never return.
* **L4 isolation is structural, not unforgeable.** The proposer/evaluator
  separation (`--safe-mode --tools ""`) and the case-name binding stop the
  cheap attacks. They do not make an evaluator tamper-proof. On macOS the
  design document already labels this a weaker boundary.
* **The dollar cap bounds calls, not dollars.** If the `claude` binary ignores
  `--max-budget-usd` *and* under-reports its cost, the number of calls is still
  bounded but the money is not. There is no way to bound actual spend from
  outside a subprocess that lies about both.
* **Hard links are invisible.** Symlinks are resolved on both sides before a
  path is called ungoverned; a hard link has no path to resolve.
* **Secret scanning is prefix-based.** The lint knows the common key shapes
  (Anthropic, OpenAI-style, GitHub, AWS, Slack, Google, private-key blocks,
  bearer tokens). A secret that does not look like one is not caught.

---

## Data precedent stores, and where

Everything is under `~/.precedent/` (or `--state-dir`). Nothing is sent
anywhere. The only network activity in the entire tool is the `claude`
subprocess invoked by the three optional paid commands (`compile --llm`,
`improve`, `examine`), and those are the only commands that cost money.

| file | contains |
|---|---|
| `precedents.json` | your confirmed rules — the only file a hook trusts |
| `candidates.json` / `candidates.jsonl` | compiled rules with gate evidence; governed-write candidates |
| `blobs/` + `snapshots/` | **pre-image copies of your files**, content-addressed |
| `hooklog.jsonl` | every hook decision and error, with command fragments |
| `changes.jsonl`, `origins.jsonl`, `receipts/`, `funnel.jsonl` | change feed, **your prompts**, load receipts, counters |
| `ledger.jsonl` | the sha256 hash-chained certificate chain |
| `spend.jsonl` | every `claude -p` call and what it cost |
| `backups/settings-<ts>.json` | copies of your `settings.json` before each write |

Treat this directory as being as sensitive as `~/.claude`.

---

## Uninstall

### Remove the hooks (stop enforcement)

```bash
precedent hooks uninstall claude-code                    # dry run: shows the exact removal
precedent hooks uninstall claude-code --apply --i-know   # write it
```

This backs up `settings.json` first, then removes **only** entries carrying
both of precedent's markers. Your other hooks are untouched. It is idempotent.
Verify:

```bash
grep -c precedent-hook ~/.claude/settings.json   # expect 0
precedent hooks status claude-code               # expect: installed : no
```

### Remove everything

```bash
precedent hooks uninstall claude-code --apply --i-know
rm -rf ~/.precedent          # deletes rules, logs, snapshots, ledger and backups
pipx uninstall precedent-cli # or: uv tool uninstall precedent-cli
```

`rm -rf ~/.precedent` is irreversible and takes the snapshot blobs with it. If
you might want to `undo` a past session, or keep the audit trail, move the
directory aside instead of deleting it.

### If something went wrong and you want the old settings back

Every `--apply` wrote a timestamped backup first:

```bash
ls -lt ~/.precedent/backups/                 # newest first
cp ~/.precedent/backups/settings-<ts>.json ~/.claude/settings.json
```

### Manual removal, if the CLI is gone

Hook entries in `~/.claude/settings.json` are the ones whose `command`
contains `--precedent-hook` and points into a `.precedent/hooks/` directory.
Delete those entries (and any now-empty event arrays), then `rm -rf
~/.precedent`. Make your own copy of `settings.json` first.
