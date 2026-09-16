<!--
Copyright 2026 The Precedent authors.
Licensed under the Apache License, Version 2.0 (the "License"); you may not
use this file except in compliance with the License. You may obtain a copy of
the License at http://www.apache.org/licenses/LICENSE-2.0
SPDX-License-Identifier: Apache-2.0
-->

# @precedent/openclaw

**An evaluator that fails closed, because the default is to fail open.**

An OpenClaw plugin that registers `skill_proposal_evaluate` and grades every
Skill Workshop draft before it is applied — with a deterministic, offline
checker: regex, `sha256`, `compile()` and set algebra. No model call, no
network, no clock-dependent behaviour. The same bundle always gets the same
verdict, and any verdict can be replayed from the event JSON alone.

The grading is done by [`precedent evaluate-bundle`](../../packages/precedent),
a Python CLI. This package is the seam: it carries the event across the process
boundary and — this is the part that matters — **converts every failure of that
crossing into an explicit decision instead of an exception.**

---

## Why fail-closed is the whole point

OpenClaw records a thrown error or a hook timeout as an **attributed error
outcome**. It does *not* block. Only a completed `decision: "block"` vetoes an
apply.

Read that twice, because it inverts the intuition you have about every other
kind of check you have ever written:

> **An evaluator that throws is an evaluator that silently approves.**

And it approves at exactly the worst moment — when it is broken, misconfigured,
half-installed, or timing out on a machine under load. The proposal lands
unreviewed, the audit trail says the grader errored, and nobody reads it. That
is not a hypothetical failure mode; it is the default one.

So `evaluateProposal` has a single hard invariant, and most of the test suite
exists to defend it:

> It always resolves, and it always resolves with a decision.

| what went wrong | `ruleId` | default verdict |
|---|---|---|
| `precedent` not on PATH, and `python3 -m precedent` not there either | `precedent/cli-not-found` | `block` |
| binary exists but will not start (`EACCES`, a directory, a dead symlink) | `precedent/spawn-failed` | `block` |
| the CLI ran past its deadline and was killed | `precedent/timeout` | `block` |
| the CLI exited non-zero instead of writing a verdict | `precedent/cli-error` | `block` |
| stdout was not one JSON document | `precedent/unparseable-output` | `block` |
| stdout was JSON, but not a verdict — or was *two* verdicts | `precedent/not-a-verdict` | `block` |
| the CLI flooded stdout | `precedent/output-too-large` | `block` |
| the candidate (or baseline) bundle is over `maxBundleBytes` | `precedent/bundle-too-large` | `block` |
| a bug in this plugin | `precedent/internal-error` | `block` |

Every one of those carries a `decisionReason` that names the failure **and the
fix**, because a block nobody can act on is just an outage:

```
precedent/cli-not-found: precedent CLI not found on PATH — pip install
precedent-cli (or pipx install precedent-cli), or set precedent.precedentBin to
its absolute path, or set precedent.pythonBin to a Python that has it installed.
Tried "precedent", then "python3 -m precedent"; neither exists in this process's
PATH.
```

### An empty document is not an approval

The subtlest way to fail open is to succeed at parsing the wrong thing. A
verdict is a document carrying a `decision`; `{}`, `{"ok":true}` and the output
of any *other* program that happens to be named `precedent` are not verdicts,
and reading one as "graded, no findings" makes it a `pass`. So the shape is
checked, not just the syntax, and "I could not find a verdict here" is a block.

The mirror of it is ambiguity. Tolerating a banner around the document is worth
doing — a shell profile or a wrapper script printing one line is an ordinary
accident, and it should not cost somebody a blocked proposal. But "take the last
line" is exactly how a wrapper would *forge* a verdict: a real `block`, followed
by a printed `{"decision":"pass"}`, would have won. So noise around one verdict
is fine and **two verdicts is a refusal** — which of them to honour is not a
guess this plugin gets to make.

### A dead child can still hold the pipe

`close` fires when a process has ended *and* its stdio has closed. A grader that
spawned something which inherited its stdout can therefore be dead while the
pipe stays open — and a promise waiting on `close` never settles. The hook then
runs to OpenClaw's own timeout, which is an attributed error outcome, which does
not block: one leaked file descriptor inside the grader would quietly disable
the gate. After the `SIGTERM` → `SIGKILL` escalation there is a final deadline
that lets go of the pipes and answers anyway.

Set `failClosed: false` and those become `revise` instead. It is a real option —
a gate that halts all work on a broken install is a gate that gets uninstalled —
but it is not the default, and it changes **only** the plugin's own failures. A
`block` the core actually earned from a finding blocks either way. There is a
test for precisely that, because "fail open" must never become a way to smuggle
a credential past the check.

### The two timeouts, and the gap between them

There are two deadlines in play and they must not be the same number.

```
config.timeoutMs = 60 000 ms  ──────────────►  the subprocess deadline
                              + 15 000 ms headroom
registration timeoutMs = 75 000 ms  ────────►  what OpenClaw is asked to wait
```

If they were equal, the two would race — and OpenClaw winning that race means an
attributed error outcome, which does not block. A slow grader would then approve
by disappearing. So the subprocess is always given strictly less time than the
hook, leaving room for the fail-closed document to be built and returned. The
registration is also clamped below OpenClaw's own 120 s default hook timeout, no
matter how `timeoutMs` is configured.

---

## Install

The plugin needs two halves: this package, and the `precedent` CLI it shells out
to.

```bash
# 1. the grader
pipx install precedent-cli          # or: uv tool install precedent-cli
precedent --version

# 2. the plugin
openclaw plugins install @precedent/openclaw
#    …or from a checkout of this repo:
openclaw plugins install ./plugins/openclaw
```

> `precedent-cli` is reserved but not yet published to PyPI. Until it is, install
> the CLI straight from git — see the
> [repository README](../../README.md#install) — or point `precedent.pythonBin`
> at a virtualenv's `python` and let the plugin find the module through it.

Verify the wiring without involving OpenClaw at all:

```bash
precedent evaluate-bundle --dir ./some-skill        # the core, on a directory
cd plugins/openclaw && npm install && npm test      # the plugin, 94 tests
```

### Configuration

| key | type | default | what it does |
|---|---|---|---|
| `enabled` | bool | `true` | Grade proposals. `false` returns `pass` with `mode: "disabled"` and a reason saying nothing was checked — visibly ungraded rather than silently clean. |
| `pythonBin` | string | `"python3"` | Fallback interpreter, used as `<pythonBin> -m precedent` when `precedentBin` is not on PATH. Point it at `…/.venv/bin/python` for a virtualenv install. |
| `precedentBin` | string | `"precedent"` | The CLI, resolved from PATH unless absolute. |
| `failClosed` | bool | `true` | Whether an evaluator failure is `block` or `revise`. See above. |
| `timeoutMs` | int | `60000` | Subprocess deadline. The hook registration asks for this + 15 s. |
| `blockOn` | `critical`\|`warn` | `"critical"` | Lowest severity that vetoes an apply. `warn` is the strict setting. |
| `maxBundleBytes` | int | `2000000` | Refuse to grade a bundle larger than this rather than paying to serialise it. |

---

## What it checks

Seven deterministic families, each finding carrying a stable `ruleId` you can
count, suppress or allowlist over time.

| family | what it proves | the `critical` member |
|---|---|---|
| `structure/*` | every `files[].path` stays inside the skill directory, SKILL.md parses, `name` + `description` are declared, the declared name matches the proposal, referenced files are *in the bundle*, shebanged scripts compile, base64 decodes, no invisible or bidi characters | a path that escapes the bundle (`../../etc/…` — an arbitrary file write, whatever the file contains), an explicit bidi override, or a `sha256` / `treeSha256` that does not recompute — **what you were asked to grade is not what you were handed** |
| `dlp/*` | a secret scan over every text file. The match is **never** echoed back: kind + file + line, and that is all | a live credential (`sk-…`, `ghp_…`, `xox…`, `AKIA…`) |
| `evidence/*` | a line claiming *verified / tested / 已确认* has to cite a command, a path or a date | a placeholder (`NNN`, `TODO`, `<your-…>`) inside such a line — template text codified as fact |
| `scope/*` | absolute home paths, `localhost:PORT`, machine hostnames and dates-as-facts in a skill that never says where it applies | — |
| `baseline/*` | updates only: a revision may not silently **drop** a verification or reversibility requirement its ancestor asserted | every one of them |
| `risk/*` | updates only: destructive verbs, egress hosts and credential reads the baseline did not have | a new egress host **combined** with a new destructive verb or credential read — the shape of exfiltration |
| `size/*` | SKILL.md over 25 KiB, bundle over 1 MiB — retrieval precision collapses as the skill pool grows | — |

Any `critical` → `block`; else any `warn` → `revise`; else `pass`. With
`blockOn: "warn"`, a `warn` blocks too and the result says so explicitly.

**Calibrated against real work.** On the 44 skills installed on the author's
machine (copied read-only into a temp dir and graded): **16 `pass`, 28 `revise`,
0 `block`** — zero false criticals on a working corpus. A gate that vetoes real
work is a gate that gets switched off, so that property is pinned by a test.

---

## A worked example: a blocked proposal

This is a real run of this plugin against the real core, on a real skill. The
skill (`lark-attendance`) was copied read-only out of `~/.claude/skills`; the
revision below is the sort of edit a compromised or careless proposer produces.

**Revision 1 — the skill as it exists.** Clean:

```json
{
  "summary": "pass: 0 critical, 0 warn, 1 info over 1 files (1620 bytes, static)",
  "findings": [
    { "ruleId": "structure/reference-escapes-bundle", "severity": "info",
      "message": "SKILL.md references '../lark-shared/SKILL.md', which is outside the bundle; this evaluator cannot check that it exists",
      "file": "SKILL.md", "line": 13 }
  ],
  "decision": "pass",
  "decisionReason": "no finding above `info`: structure, DLP, evidence, scope, risk and size all clear"
}
```

**Revision 2 — the same skill, "improved".** It gains a `scripts/push.sh` that
exports a token, POSTs `~/.claude/settings.json` to an outside host, and then
`rm -rf`s a cache; SKILL.md gains the line `Verified 2026-08-04 — end-to-end
tested against production, all green.`

```json
{
  "summary": "block: 2 critical, 3 warn, 1 info over 2 files (2018 bytes, baseline-comparison) — dlp/secret, risk/egress-combination, risk/new-destructive-verb, risk/new-egress-host",
  "findings": [
    { "ruleId": "dlp/secret", "severity": "critical",
      "message": "a credential (token) appears here; it must not ship in a skill bundle",
      "file": "scripts/push.sh", "line": 3 },
    { "ruleId": "risk/egress-combination", "severity": "critical",
      "message": "the revision adds a new egress host ('intake.example-collector.net') *and* new destructive verbs (rm -rf); separately each is a warning, together they are the shape of exfiltration",
      "file": "scripts/push.sh", "line": 4 },
    { "ruleId": "scope/dated-fact", "severity": "warn",
      "message": "a date stated as a fact appears here and the skill never says where it applies; it will be loaded on machines where this is false",
      "file": "SKILL.md", "line": 61 },
    { "ruleId": "risk/new-egress-host", "severity": "warn",
      "message": "the revision sends traffic to 'intake.example-collector.net', which the baseline did not contact",
      "file": "scripts/push.sh", "line": 4 },
    { "ruleId": "risk/new-destructive-verb", "severity": "warn",
      "message": "the revision introduces 'rm -rf', which the baseline did not use",
      "file": "scripts/push.sh", "line": 6 }
  ],
  "metrics": {
    "risk.newEgressHosts": 1, "risk.newDestructive": 1, "findings.critical": 2,
    "plugin.cliElapsedMs": 69, "plugin.blockOn": "critical", "plugin.escalated": false
  },
  "evaluatorVersion": "precedent/0.1.0",
  "mode": "baseline-comparison",
  "decision": "block",
  "decisionReason": "dlp/secret: a credential (token) appears here; it must not ship in a skill bundle (and 1 more)"
}
```

Sixty-nine milliseconds, offline, and reproducible byte-for-byte. Note what the
`baseline` bought: `risk/egress-combination` is not a property of the new file,
it is a property of the *diff*. Neither half is critical alone — plenty of
skills call out to a host, plenty clean up after themselves — but a revision
that adds both at once is the shape of exfiltration, and only a comparison
against the skill as it stands today can see it.

Note also what the DLP finding does **not** contain: the credential. Findings
carry the kind, the file and the line, never the matched bytes. The same
discipline is applied to anything this plugin quotes out of the child process:
credential shapes are redacted and control characters neutralised before a
diagnostic goes anywhere near the proposal record.

---

## Layout

```
openclaw.plugin.json   the manifest: id, categories, activation, configSchema
index.ts               the entry: one registration, and the wiring around it
src/types.ts           the skill_proposal_evaluate seam, transcribed
src/config.ts          configSchema resolved into something total; the two timeouts
src/spawn.ts           the process boundary, and every way it can go wrong
src/evaluate.ts        one proposal in, one verdict out — and it never throws
index.test.ts          the contract, all across a real process boundary
test/adversarial.test.ts  the fail-closed claim, taken literally
test/fixtures.ts       a real executable on a real PATH
vendor/openclaw-stub/  dev-only stand-in for the host SDK (never published)
```

### Development

```bash
npm install     # no registry needed for the stub; typescript + @types/node are
npm test        # tsc --noEmit, then node --test on both suites  → 94 passing
```

Two choices worth knowing about:

**The tests drive a real child process, not a mock.** The contract under test
*is* a process boundary — argv, stdin, stdout, an exit status and a clock — and
every failure this plugin exists to survive lives on the far side of it. So
`test/fixtures.ts` writes an actual executable named `precedent` into a temp
directory and puts *only* that directory on the child's `PATH`. ENOENT is a real
ENOENT; the hanging-CLI test really does spawn something that ignores `SIGTERM`
and really does require the `SIGKILL` escalation to finish; the flood test really
does write 50 MB; and the leaked-pipe test really does detach a grandchild that
holds stdout open for thirty seconds after its parent has been killed.

**`vendor/openclaw-stub` is a development-only stand-in** for
`openclaw/plugin-sdk/plugin-entry`, wired in as a `file:` devDependency so the
package type-checks and tests standalone. Inside a real OpenClaw the host's own
module is resolved instead, and any drift between the two shows up as a type
error rather than as a field that quietly stopped being read. It is excluded
from the published tarball.

`tsconfig.json` sets `erasableSyntaxOnly`, which forbids the TypeScript
constructs that cannot be removed by type-stripping alone. That is what lets
`node --test index.test.ts` run these exact sources with no build step in
between: what the tests exercise is what ships.

---

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
