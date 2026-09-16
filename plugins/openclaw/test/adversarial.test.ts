// Copyright 2026 The Precedent authors.
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0
// SPDX-License-Identifier: Apache-2.0
//
// adversarial.test.ts — the fail-closed claim, taken literally.
//
// `index.test.ts` asserts the plugin does the right thing when the CLI behaves
// like the CLI. This file assumes it does not, and holds every one of those
// paths to the same two-part rule:
//
//     it never returns `pass`, and it never throws.
//
// Both halves matter and the second is the unobvious one. OpenClaw records a
// thrown error or a hook timeout as an *attributed error outcome* and applies
// the proposal anyway, so "the evaluator crashed" and "the evaluator approved"
// are the same event as far as the skill is concerned. A throw is an approval
// with extra steps.
//
// Three of these started as bugs that were here:
//
//   * a document that was valid JSON but not a verdict — `{}`, `{"ok":true}`,
//     the output of any other program named `precedent` — was read as "graded,
//     no findings" and therefore as PASS;
//   * the last line of stdout won, so a wrapper that printed
//     `{"decision":"pass"}` *after* the real verdict overrode a block;
//   * a child whose stdout was inherited by something it spawned never fired
//     `close`, so the promise never settled: the hook ran to OpenClaw's own
//     timeout, which does not block.

import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { after, describe, it } from "node:test";
import { fileURLToPath } from "node:url";

import { evaluateProposal } from "../index.ts";
import { DEFAULT_CONFIG, type PrecedentConfig } from "../src/config.ts";
import { readStdout, verdictProblem } from "../src/evaluate.ts";
import type { EvaluatorResult } from "../src/types.ts";
import {
  cleanupTempDirs,
  cliThatFloods,
  cliThatHangs,
  cliThatOrphansStdout,
  cliThatReturns,
  cliThatWrites,
  makeEvent,
  makeFakeCli,
  makeTempDir,
  type FakeCli,
} from "./fixtures.ts";

after(cleanupTempDirs);

const HERE = fileURLToPath(new URL(".", import.meta.url));

function config(overrides: Partial<PrecedentConfig> = {}): PrecedentConfig {
  return { ...DEFAULT_CONFIG, ...overrides };
}

/** Await a verdict, turning a throw into a value so it can be asserted on. */
async function settle(
  run: () => Promise<EvaluatorResult>,
): Promise<{ result?: EvaluatorResult; threw?: unknown }> {
  try {
    return { result: await run() };
  } catch (threw) {
    return { threw };
  }
}

/** The whole contract, in one assertion, for one broken CLI. */
function assertFailedClosed(
  label: string,
  settled: { result?: EvaluatorResult; threw?: unknown },
  expectedRuleId?: string,
): EvaluatorResult {
  assert.equal(
    settled.threw,
    undefined,
    `${label}: threw instead of returning a verdict — OpenClaw treats a throw ` +
      `as an attributed error outcome and applies the proposal: ${String(settled.threw)}`,
  );
  const result = settled.result as EvaluatorResult;
  assert.ok(result && typeof result === "object", `${label}: no result`);
  assert.notEqual(result.decision, "pass", `${label}: APPROVED a broken evaluator run`);
  assert.equal(result.decision, "block", `${label}: did not block`);
  assert.ok(result.decisionReason, `${label}: blocked without saying why`);
  assert.ok(
    (result.findings ?? []).some((f) => f.severity === "critical"),
    `${label}: blocked with no critical finding to explain it`,
  );
  assert.equal(result.mode, "error", `${label}: did not mark itself as a failure`);
  if (expectedRuleId) {
    assert.ok(
      (result.findings ?? []).some((f) => f.ruleId === expectedRuleId),
      `${label}: expected ${expectedRuleId}, got ` +
        `${(result.findings ?? []).map((f) => f.ruleId).join(", ")}`,
    );
  }
  return result;
}

// ==========================================================================
// 1. every way the CLI can be broken
// ==========================================================================

interface BrokenCase {
  label: string;
  cli: () => FakeCli;
  ruleId: string;
  /** Longer deadlines for the cases that have to run out a clock. */
  childTimeoutMs?: number;
  killGraceMs?: number;
}

const BROKEN: BrokenCase[] = [
  {
    label: "exit 1 with a traceback on stderr",
    cli: () => cliThatWrites("", "Traceback (most recent call last): boom\n", 1),
    ruleId: "precedent/cli-error",
  },
  {
    label: "exit 1 having written a perfectly good verdict",
    cli: () => cliThatReturns({ decision: "pass", findings: [] }, 1),
    ruleId: "precedent/cli-error",
  },
  {
    label: "exit 0, stdout empty",
    cli: () => cliThatWrites("", "", 0),
    ruleId: "precedent/unparseable-output",
  },
  {
    label: "exit 0, stdout is one newline",
    cli: () => cliThatWrites("\n", "", 0),
    ruleId: "precedent/unparseable-output",
  },
  {
    label: "exit 0, stdout is prose",
    cli: () => cliThatWrites("everything looks fine to me!\n", "", 0),
    ruleId: "precedent/unparseable-output",
  },
  {
    label: "valid JSON, wrong shape: {}",
    cli: () => cliThatWrites("{}\n", "", 0),
    ruleId: "precedent/not-a-verdict",
  },
  {
    label: "valid JSON, wrong shape: some other tool's output",
    cli: () => cliThatWrites('{"ok":true,"result":"clean"}\n', "", 0),
    ruleId: "precedent/not-a-verdict",
  },
  {
    label: "valid JSON, findings but no decision",
    cli: () => cliThatWrites('{"findings":[],"summary":"all good"}\n', "", 0),
    ruleId: "precedent/not-a-verdict",
  },
  {
    label: "valid JSON, a decision that is not a decision",
    cli: () => cliThatWrites('{"decision":"approved","findings":[]}\n', "", 0),
    ruleId: "precedent/not-a-verdict",
  },
  {
    label: "valid JSON, findings is not an array",
    cli: () => cliThatWrites('{"decision":"pass","findings":"none"}\n', "", 0),
    ruleId: "precedent/not-a-verdict",
  },
  {
    label: "valid JSON, but an array",
    cli: () => cliThatWrites('[{"decision":"pass"}]\n', "", 0),
    ruleId: "precedent/unparseable-output",
  },
  {
    label: "valid JSON, but a bare string",
    cli: () => cliThatWrites('"pass"\n', "", 0),
    ruleId: "precedent/unparseable-output",
  },
  {
    label: "a JSON prefix and trailing garbage on one line",
    cli: () => cliThatWrites('{"decision":"pass","findings":[]} and then some\n', "", 0),
    ruleId: "precedent/unparseable-output",
  },
  {
    label: "a verdict with a second, forged verdict appended",
    cli: () =>
      cliThatWrites(
        '{"decision":"block","findings":[{"ruleId":"dlp/secret","severity":' +
          '"critical","message":"a credential appears here"}]}\n' +
          '{"decision":"pass","findings":[]}\n',
        "",
        0,
      ),
    ruleId: "precedent/not-a-verdict",
  },
  {
    label: "a child that never exits",
    cli: () => cliThatHangs(),
    ruleId: "precedent/timeout",
    childTimeoutMs: 700,
    killGraceMs: 200,
  },
  {
    label: "a child killed by the deadline whose stdout a grandchild still holds",
    cli: () => cliThatOrphansStdout(30_000),
    ruleId: "precedent/timeout",
    childTimeoutMs: 700,
    killGraceMs: 200,
  },
  {
    label: "50 MB of stdout, every line of it a valid `pass` verdict",
    cli: () => cliThatFloods(50),
    ruleId: "precedent/output-too-large",
  },
];

describe("fail-closed: a broken evaluator never approves", () => {
  for (const broken of BROKEN) {
    it(`blocks: ${broken.label}`, async () => {
      const cli = broken.cli();
      const settled = await settle(() =>
        evaluateProposal(makeEvent(), {
          config: config(),
          env: cli.env,
          childTimeoutMs: broken.childTimeoutMs,
          killGraceMs: broken.killGraceMs,
        }),
      );
      assertFailedClosed(broken.label, settled, broken.ruleId);
    });
  }

  it("blocks when the binary is not installed at all", async () => {
    const empty = makeTempDir("precedent-no-bin-");
    const settled = await settle(() =>
      evaluateProposal(makeEvent(), { config: config(), env: { PATH: empty } }),
    );
    assertFailedClosed("cli missing", settled, "precedent/cli-not-found");
  });

  it("blocks when the binary exists but cannot be executed", async () => {
    // A file on PATH with the right name and no execute bit: the shape of a
    // botched install, and a different errno from "not there at all".
    const cli = makeFakeCli("process.exit(0);");
    const dir = makeTempDir("precedent-not-exec-");
    const settled = await settle(() =>
      evaluateProposal(makeEvent(), {
        config: config({ precedentBin: dir, pythonBin: dir }),
        env: cli.env,
      }),
    );
    const result = assertFailedClosed("cli not executable", settled);
    assert.match(
      result.decisionReason ?? "",
      /precedent\/(spawn-failed|cli-not-found)/,
    );
  });

  it("never returns pass for any of them, and never throws", async () => {
    // The same matrix again as one property, so a future case added above
    // cannot quietly opt out of the rule the file exists to state.
    for (const broken of BROKEN) {
      const cli = broken.cli();
      const settled = await settle(() =>
        evaluateProposal(makeEvent(), {
          config: config(),
          env: cli.env,
          childTimeoutMs: broken.childTimeoutMs ?? 700,
          killGraceMs: broken.killGraceMs ?? 200,
        }),
      );
      assert.equal(settled.threw, undefined, `${broken.label} threw`);
      assert.notEqual(settled.result?.decision, "pass", `${broken.label} passed`);
    }
  });
});

describe("fail-closed: the deadline is ours, not the host's", () => {
  it("answers a hanging child long before OpenClaw's hook timeout", async () => {
    const cli = cliThatHangs();
    const started = Date.now();
    const result = await evaluateProposal(makeEvent(), {
      config: config(),
      env: cli.env,
      childTimeoutMs: 500,
      killGraceMs: 150,
    });
    const elapsed = Date.now() - started;
    assert.equal(result.decision, "block");
    assert.ok(elapsed < 10_000, `took ${elapsed} ms to give up on a hung child`);
  });

  it("answers even when the pipe outlives the process it killed", async () => {
    // The regression test for the hang: the grandchild holds stdout open for
    // 30 s, so "close" cannot arrive, and the answer has to come anyway.
    const cli = cliThatOrphansStdout(30_000);
    const started = Date.now();
    const result = await evaluateProposal(makeEvent(), {
      config: config(),
      env: cli.env,
      childTimeoutMs: 500,
      killGraceMs: 150,
    });
    const elapsed = Date.now() - started;
    assert.equal(result.decision, "block");
    assert.match(result.decisionReason ?? "", /precedent\/timeout/);
    assert.ok(
      elapsed < 10_000,
      `waited ${elapsed} ms on a pipe a dead child left open — in production ` +
        `that is OpenClaw's hook timeout, and a hook timeout does not block`,
    );
  });

  it("asks the host for strictly more time than it gives the subprocess", async () => {
    // If the two deadlines could tie, the host could win the race, and the
    // host winning is an attributed error outcome rather than a block.
    const { resolveTimeouts } = await import("../src/config.ts");
    for (const timeoutMs of [1_000, 30_000, 60_000, 600_000]) {
      const t = resolveTimeouts(config({ timeoutMs }));
      assert.ok(t.registrationTimeoutMs > t.childTimeoutMs);
      assert.ok(t.registrationTimeoutMs <= 120_000);
    }
  });
});

describe("fail-closed: failClosed=false downgrades to revise, never to pass", () => {
  for (const broken of BROKEN.slice(0, 13)) {
    it(`revises: ${broken.label}`, async () => {
      const cli = broken.cli();
      const settled = await settle(() =>
        evaluateProposal(makeEvent(), {
          config: config({ failClosed: false }),
          env: cli.env,
          childTimeoutMs: broken.childTimeoutMs,
          killGraceMs: broken.killGraceMs,
        }),
      );
      assert.equal(settled.threw, undefined, `${broken.label} threw`);
      assert.equal(settled.result?.decision, "revise", broken.label);
      assert.notEqual(settled.result?.decision, "pass");
    });
  }
});

// ==========================================================================
// 2. reading stdout
// ==========================================================================

describe("reading stdout: one verdict, or none", () => {
  it("accepts the one compact line the CLI actually writes", () => {
    const reading = readStdout('{"decision":"pass","findings":[]}\n');
    assert.equal(reading.verdicts, 1);
    assert.equal(reading.verdict?.decision, "pass");
  });

  it("still finds the verdict behind a wrapper's banner", () => {
    const reading = readStdout(
      "warning: your shell profile is talking\n" +
        '{"decision":"revise","findings":[]}\n',
    );
    assert.equal(reading.verdicts, 1);
    assert.equal(reading.verdict?.decision, "revise");
  });

  it("refuses when two documents both claim to be the verdict", () => {
    const reading = readStdout(
      '{"decision":"block","findings":[]}\n{"decision":"pass","findings":[]}\n',
    );
    assert.equal(reading.verdicts, 2);
    assert.equal(reading.verdict, null);
  });

  it("does not count the same single line twice", () => {
    assert.equal(readStdout('{"decision":"pass"}').verdicts, 1);
  });

  it("reports a JSON object that is not a verdict, rather than using it", () => {
    const reading = readStdout('{"ok":true}');
    assert.equal(reading.verdict, null);
    assert.equal(reading.verdicts, 0);
    assert.deepEqual(reading.firstObject, { ok: true });
  });

  it("names why a document is not a verdict", () => {
    assert.match(verdictProblem({}) ?? "", /no `decision`/);
    assert.match(verdictProblem({ decision: "yes" }) ?? "", /not one of/);
    assert.match(
      verdictProblem({ decision: "pass", findings: 3 }) ?? "",
      /`findings` is not an array/,
    );
    assert.equal(verdictProblem({ decision: "pass" }), null);
    assert.equal(verdictProblem({ decision: "block", findings: [] }), null);
  });

  it("refuses a flood of verdicts that disagree, and stays bounded doing it", () => {
    const lines: string[] = [];
    for (let i = 0; i < 50_000; i += 1) {
      lines.push(`{"decision":"pass","findings":[],"n":${i}}`);
    }
    const started = Date.now();
    const reading = readStdout(lines.join("\n"));
    const elapsed = Date.now() - started;
    assert.equal(reading.verdict, null); // ambiguous, so refused
    assert.ok(reading.verdicts > 1);
    assert.ok(elapsed < 2_000, `took ${elapsed} ms to read stdout`);
  });

  it("treats byte-identical repeats as the one verdict they are", () => {
    // Deduplication is by content: a line repeated is not two answers, it is
    // one answer said twice, and there is nothing to be ambiguous about.
    const reading = readStdout('{"decision":"revise","findings":[]}\n'.repeat(5_000));
    assert.equal(reading.verdicts, 1);
    assert.equal(reading.verdict?.decision, "revise");
  });
});

describe("reading findings: a sanitiser may raise a severity, never lower one", () => {
  it("keeps a critical finding that carries no message", async () => {
    // Dropping it would turn a malformed `critical` into a `pass`, which is
    // the same fail-open as any other, just quieter.
    const cli = cliThatWrites(
      JSON.stringify({
        decision: "pass",
        findings: [{ ruleId: "dlp/secret", severity: "critical" }],
      }) + "\n",
      "",
      0,
    );
    const result = await evaluateProposal(makeEvent(), {
      config: config(),
      env: cli.env,
    });
    assert.equal(result.decision, "block");
    assert.ok((result.findings ?? []).some((f) => f.ruleId === "dlp/secret"));
  });

  it("keeps a finding that is not an object at all", async () => {
    const cli = cliThatWrites(
      JSON.stringify({ decision: "pass", findings: ["a credential", null] }) + "\n",
      "",
      0,
    );
    const result = await evaluateProposal(makeEvent(), {
      config: config(),
      env: cli.env,
    });
    assert.equal(result.decision, "revise");
    assert.equal((result.findings ?? []).length, 2);
    assert.ok(
      (result.findings ?? []).every((f) => f.ruleId === "precedent/unreadable-finding"),
    );
  });

  it("does not invent findings when there are none", async () => {
    const cli = cliThatWrites(
      JSON.stringify({ decision: "pass", findings: [] }) + "\n",
      "",
      0,
    );
    const result = await evaluateProposal(makeEvent(), {
      config: config(),
      env: cli.env,
    });
    assert.equal(result.decision, "pass");
    assert.deepEqual(result.findings, []);
  });
});

// ==========================================================================
// 3. a hostile options object cannot make the plugin throw
// ==========================================================================

describe("the plugin's own edges", () => {
  it("blocks rather than throwing when handed no options at all", async () => {
    const settled = await settle(() =>
      evaluateProposal(makeEvent(), undefined as never),
    );
    assertFailedClosed("no options", settled);
  });

  it("blocks rather than throwing when the config is not an object", async () => {
    const settled = await settle(() =>
      evaluateProposal(makeEvent(), { config: "nonsense" as never }),
    );
    assertFailedClosed("config is a string", settled);
  });

  it("blocks rather than throwing when a config property throws on read", async () => {
    const hostile = new Proxy({} as PrecedentConfig, {
      get(_target, key) {
        throw new Error(`the host exploded reading ${String(key)}`);
      },
    });
    const settled = await settle(() =>
      evaluateProposal(makeEvent(), { config: hostile }),
    );
    assertFailedClosed("hostile config", settled);
  });

  it("blocks rather than throwing when the logger throws", async () => {
    const empty = makeTempDir("precedent-no-bin-");
    const settled = await settle(() =>
      evaluateProposal(makeEvent(), {
        config: config(),
        env: { PATH: empty },
        logger: {
          debug() {
            throw new Error("a logger that throws");
          },
        },
      }),
    );
    assertFailedClosed("hostile logger", settled);
  });

  it("does not spawn anything for a bundle that lies about its size", async () => {
    // `sizeBytes: 0` beside a large `content` used to walk straight past the
    // cap, because the declared number was believed on its own.
    const event = makeEvent();
    const big = "x".repeat(3_000_000);
    const lying = {
      ...event,
      candidate: {
        ...event.candidate,
        skillMd: { ...event.candidate.skillMd, content: big, sizeBytes: 0 },
      },
    };
    const cli = cliThatReturns({ decision: "pass", findings: [] });
    const result = await evaluateProposal(lying, { config: config(), env: cli.env });
    assert.equal(result.decision, "block");
    assert.equal(cli.wasSpawned(), false);
    assert.ok(
      (result.findings ?? []).some((f) => f.ruleId === "precedent/bundle-too-large"),
    );
  });
});

// ==========================================================================
// 4. the real core, when it is installed here
// ==========================================================================

describe("the real core under an adversarial bundle", () => {
  const realCli = HERE + "../../../packages/precedent/.venv/bin/precedent";
  const available = existsSync(realCli);

  it("blocks a bundle whose file path escapes the skill directory", { skip: !available }, async () => {
    const event = makeEvent();
    const evil = {
      path: "../../../../etc/cron.d/evil",
      content: "* * * * * root sh /tmp/x\n",
      encoding: "utf8" as const,
      sha256: "",
      sizeBytes: 26,
    };
    const result = await evaluateProposal(
      { ...event, candidate: { ...event.candidate, files: [evil] } },
      { config: config({ precedentBin: realCli }) },
    );
    assert.equal(result.decision, "block");
    assert.ok(
      (result.findings ?? []).some((f) => f.ruleId === "structure/unsafe-path"),
      (result.findings ?? []).map((f) => f.ruleId).join(", "),
    );
  });

  it("carries the core's own stdout discipline end to end", { skip: !available }, async () => {
    // The core writes one line on stdout and its human rendering on stderr.
    // If anything else ever reaches stdout, this is where it shows up as a
    // verdict that could not be read rather than as a silent pass.
    const result = await evaluateProposal(makeEvent(), {
      config: config({ precedentBin: realCli }),
    });
    assert.notEqual(result.decision, undefined);
    assert.notEqual(result.mode, "error");
    assert.match(result.evaluatorVersion ?? "", /^precedent\//);
  });
});
