# Contributing to Precedent

Thank you for looking. This is a young project with an unusually strict set of
house rules, because the thing it ships is a **safety gate that runs inside
somebody else's agent**. A bug here does not produce a wrong answer; it
produces a wedged tool call, a silently disabled gate, or an overwritten
`settings.json`. The rules below all exist because of that.

By contributing you agree that your contribution is licensed under the
Apache License 2.0 (see [LICENSE](LICENSE)), as stated in section 5 of that
licence.

---

## The house rules

### 1. Standard library only

The runtime of all three packages is the Python standard library. No
dependency is added without an issue first, and the bar is very high.

The only exceptions are **optional extras declared in `pyproject.toml`**, and
an optional extra must **degrade gracefully and say so**. `precedent-cli[zh]`
is the pattern: with `jieba` installed the miner groups Chinese topics by
words, without it by character bigrams — and every report prints which one ran.
A feature that silently gets worse when an extra is missing is a bug.

`pytest` (in the `[test]` extra) is the only development dependency.

### 2. Never touch a real `~/.claude` — not in a test, not in a script

Every test builds a **synthetic Claude home under `tmp_path`** and points
`--state-dir` at `tmp_path` too. `packages/precedent/tests/conftest.py`
provides `fake_home`, `mining_home`, `demo_home`, `hostile_home`,
`home_builder`, `exam_builder` and `fake_claude` (a PATH shim standing in for
the `claude` binary) for this.

Two of those come from `scripts/make_fixture_home.py`, so the tree the docs
describe is the tree under test:

* `demo_home` — the README's tree. Its shape is pinned by every number in the
  docs, so changing it means changing them; plants four secrets.
* `hostile_home` — `make_fixture_home.py --hostile`. No documentation duty, so
  it is free to be as hostile as a real machine: sixteen plants (credential,
  AWS key, two phone formats, email, WeChat id, home path, Unix account, a
  project *and* a skill named after a paying customer, two session uuids, a git
  remote in both syntaxes), each in a different kind of field. Widening the
  adversarial review of `audit --share` means adding a line to
  `HOSTILE_PLANTS`, not editing a test.

Any test that exercises a write path must take the `real_home_canary` fixture
and call it at the end:

```python
def test_install_apply_backs_up_then_merges(synthetic, real_home_canary):
    ...
    real_home_canary()      # fails if ~/.claude or ~/.precedent moved
```

`hooks install --apply` is fully tested — **only against synthetic homes**.
When you run the CLI by hand during development, use `--dry-run` (the default)
or point `--claude-home` and `--state-dir` at a scratch directory. Writing into
your own real `~/.claude` is a decision you make deliberately, with
`--apply --i-know`, outside the development loop.

### 3. Every safety invariant needs a test that would fail without it

The five invariants in [README.md](README.md#safety-guarantees) and
[SECURITY.md](SECURITY.md) are not documentation, they are assertions. If you
touch code near one, the PR must show the test that proves it still holds. If
you add an invariant, add the test that fails when you delete the guard.

In particular:

* **the hook budget** — the suite asserts every hook script answers in
  **< 300 ms end-to-end**. A change that adds work to `_hooklib.py` runs on the
  hot path of every single tool call. Measure it.
* **fail-open** — a hook must exit 0 with **no stdout** on any internal
  exception, and log the error. Test the failure, not just the success.
* **`_hooklib.py` is copied byte-for-byte** to `<state>/hooks/_plib.py`. The
  code Claude Code runs is the code the tests import; there is a test asserting
  exactly that. Do not introduce a second copy.
* **backup before write** — no code path may write a `settings.json` without a
  timestamped backup under the state dir having been written first.
* **no snapshot, no overwrite** — nothing under the learned-state tree is
  deleted or overwritten without a content-addressed snapshot, and `undo` must
  round-trip.

### 4. Mechanical rejection overrides LLM approval, never the reverse

This is the PROCTOR rule and it is the spine of the project. Anything a model
produced is **untrusted input**: schema-validate it (unknown keys dropped, so a
draft cannot smuggle `"status": "active"`, its own id, or a pre-cooked verdict),
then run it through the deterministic gates. A model's opinion may *veto*, it
may never *approve*. No code path may let an LLM's answer skip a mechanical
check.

### 5. Every `claude -p` call is capped, sandboxed and recorded

Any new subprocess call to `claude` must carry `--max-budget-usd` (validated
before the subprocess starts), `--no-session-persistence`, an explicit
`--model`, and must **never** carry `--dangerously-skip-permissions`. Drafting
calls also carry `--safe-mode --tools "" --disable-slash-commands`. The cost
goes to `spend.jsonl`, and the budget check reserves each call at its cap
rather than trusting the binary's self-reported cost.

### 6. Nothing is ever applied

`improve` and `examine` produce proposals. `docket confirm` records a decision.
Neither writes a user file. An acceptance layer that also writes is a proposer
with a rubber stamp — if a change would make precedent edit a skill, a
CLAUDE.md or a settings file on its own, it is out of scope.

### 7. User-facing text goes through `i18n.STRINGS`, evidence never does

Every label, heading and summary sentence is a string id in
`packages/precedent/src/precedent/i18n.py` with an `en` and a `zh` entry.
`tests/test_i18n.py` walks the whole table and fails if an id is missing a
language or if the two languages disagree about their `{placeholders}`, so a
half-translated string cannot merge.

The other half of the rule matters more: **evidence is never translated.** A
quote from a transcript, a path, a rule id, a matcher, a tool name and a
`counts` string are reproduced byte for byte in every language. A report whose
evidence changes with `$LANG` is a report nobody can check by hand, and
checkable-by-hand is the whole product.

The same rule makes `precedent audit --share` possible: the card is assembled
from integers and closed vocabularies, never from text, which is why there is
nothing in it to redact. If you add a field to that card, add a *count*.

### 8. Honesty in the output is a feature, not a footnote

Counts before percentages. `INSUFFICIENT`, `INCONCLUSIVE`, `NSF` and "no effect
measured" are first-class results, printed plainly. A limitation belongs in the
README's *Honest limitations* section in the same PR that introduces it, not in
an issue for later. If a heuristic ran instead of the good path, the output
says which one ran.

---

## Development setup

```bash
git clone <repo> && cd precedent
./scripts/dev.sh                # create the three uv venvs, then run all suites
```

`scripts/dev.sh` needs [`uv`](https://docs.astral.sh/uv/) and Python 3.12. The
system Python on macOS is 3.9 and will not work — never use it.

```bash
./scripts/dev.sh --venvs        # (re)create the venvs only
./scripts/dev.sh --tests        # run the suites only
./scripts/dev.sh --clean        # remove the venvs and caches
./scripts/dev.sh --zipapp       # build dist/precedent.pyz, check that two
                                #   builds are byte-identical, run it in a uv
                                #   venv that has none of the packages, then
                                #   run it again with a hostile decoy first on
                                #   PYTHONPATH and print where each import
                                #   actually resolved
./scripts/dev.sh --demo         # build the fixture Claude home and print the
                                #   `audit --share` card that is in the README
```

The zipapp is how a stranger installs this (`curl` one file, run it), so a
change that breaks it breaks the install. `scripts/build_zipapp.py` only ships
`.py` files under the three `src` trees: a package that grows a data file has
to be added there deliberately, and `plib_source()` in `hooks.py` is the
worked example of code that must read its own package from *either* a
filesystem or a zip.

"Self-contained" is tested in both directions, and the second one is the one
that matters on somebody else's laptop. Running it where the packages are
*absent* only shows it does not need them; running it where they are **present
and wrong** — a stale `pip install`, a leftover `PYTHONPATH`, the directory the
user is standing in — shows the archive wins the import. The decoy in
`tests/test_zipapp.py` and in `dev.sh --zipapp` exits non-zero and writes a
marker file if it is ever imported, so a silent shadow is not a way for either
to pass.

Per package:

```bash
cd packages/receipts  && .venv/bin/python -m pytest -q
cd packages/acceptor  && .venv/bin/python -m pytest -q
cd packages/precedent && .venv/bin/python -m pytest -q
```

`precedent`'s venv has `receipts` and `acceptor` installed **editable**, so an
edit in one of them is visible to the precedent suite immediately. CI runs all
three suites on Python 3.11 and 3.12.

## Repository layout

| path | what lives there |
|---|---|
| `packages/receipts` | read-only load receipts, change feed, activation funnel |
| `packages/acceptor` | the anytime-valid acceptance core and the ledger |
| `packages/precedent` | the CLI, the loop, the hooks, the gates |
| `research/` | the evidence base — see `research/README.md` |
| `scripts/dev.sh` | venvs + suites |
| `.github/workflows/ci.yml` | the same three suites, 3.11 + 3.12 |
| `方案.md` | the full design document (Chinese) |

## Style

* Apache-2.0 header on every new source file — copy an existing one, including
  the `SPDX-License-Identifier: Apache-2.0` line.
* Module docstrings explain *why the design is this way*, and name the failure
  it is avoiding. The existing modules are the reference; match their density.
* Type hints on public functions. `from __future__ import annotations`.
* No `print` outside the CLI/report layer.
* Keep user-facing strings where they already are: the first screen and the
  mine/compile reports are Chinese, the rest is English. Do not mix within one
  report.

## Pull requests

1. One change per PR, with the tests in the same PR.
2. All three suites pass locally before you push.
3. If it touches a safety invariant, say which one and point at the test.
4. If it adds a limitation, add it to the README's limitations section.
5. Numbers in the README are measured, not estimated. If you change behaviour
   that a documented number describes, re-measure it or mark it stale.

## Reporting a bug

Include the command, the flags, the Claude Code version, and the relevant lines
of `~/.precedent/hooklog.jsonl`. **Redact before you paste**: hook logs and
transcripts contain your file paths, your prompts and sometimes your secrets.
For anything that looks like a vulnerability, follow [SECURITY.md](SECURITY.md)
instead of opening a public issue.
