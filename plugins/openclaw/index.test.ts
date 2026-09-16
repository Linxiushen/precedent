// Copyright 2026 The Precedent authors.
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0
// SPDX-License-Identifier: Apache-2.0
//
// index.test.ts — the process boundary, exercised across it.
//
// Every test here drives a real child process found by a real PATH lookup. The
// single most important property is negative and appears in most of these
// cases: `evaluateProposal` resolves. It does not throw, it does not reject,
// and it does not return without a decision — because OpenClaw treats a thrown
// error and a hook timeout alike as an attributed error outcome that does NOT
// block, so any of those would turn this gate into a rubber stamp at exactly
// the moment it was needed.

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { after, describe, it } from "node:test";
import { fileURLToPath } from "node:url";

import { activate, readPluginConfig } from "./index.ts";
import {
  DEFAULT_CONFIG,
  HOST_HOOK_TIMEOUT_MS,
  REGISTRATION_HEADROOM_MS,
  resolveConfig,
  type PrecedentConfig,
} from "./src/config.ts";
import {
  PLUGIN_VERSION,
  REGISTRATION_ID,
  evaluateProposal,
  parseResultDocument,
  redact,
} from "./src/evaluate.ts";
import type { EvaluatorResult, Finding } from "./src/types.ts";
import {
  BLOCK_DOCUMENT,
  PASS_DOCUMENT,
  REVISE_DOCUMENT,
  cleanupTempDirs,
  cliThatReturns,
  cliThatWrites,
  makeEvent,
  makeFakeCli,
  makeOversizedEvent,
  makeTempDir,
} from "./test/fixtures.ts";

after(cleanupTempDirs);

const HERE = fileURLToPath(new URL(".", import.meta.url));

function config(overrides: Partial<PrecedentConfig> = {}): PrecedentConfig {
  return { ...resolveConfig(undefined), ...overrides };
}

function findingById(result: EvaluatorResult, ruleId: string): Finding | undefined {
  return (result.findings ?? []).find((f) => f.ruleId === ruleId);
}

/** Every result must be answerable by OpenClaw: a decision, and a reason. */
function assertWellFormed(result: EvaluatorResult): void {
  assert.ok(
    result.decision === "pass" || result.decision === "revise" || result.decision === "block",
    `decision must be pass|revise|block, got ${String(result.decision)}`,
  );
  assert.equal(typeof result.decisionReason, "string");
  assert.ok((result.decisionReason ?? "").length > 0, "decisionReason must not be empty");
  for (const finding of result.findings ?? []) {
    assert.ok(finding.ruleId.length > 0);
    assert.ok(["info", "warn", "critical"].includes(finding.severity));
    assert.ok(finding.message.length > 0);
  }
}

// ==========================================================================
// registration
// ==========================================================================

interface Registration {
  event: string;
  handler: (event: unknown) => Promise<unknown>;
  options: { registrationId?: string; timeoutMs?: number } | undefined;
}

function fakeApi(pluginConfig?: unknown) {
  const registrations: Registration[] = [];
  const logs: string[] = [];
  const api = {
    config: pluginConfig,
    logger: {
      debug: (m: string) => logs.push("debug " + m),
      info: (m: string) => logs.push("info " + m),
      warn: (m: string) => logs.push("warn " + m),
      error: (m: string) => logs.push("error " + m),
    },
    on(event: string, handler: (e: unknown) => Promise<unknown>, options?: unknown) {
      registrations.push({ event, handler, options: options as Registration["options"] });
      return () => {};
    },
  };
  // The fake stands in for the host; the plugin only ever sees this surface.
  return { api: api as unknown as Parameters<typeof activate>[0], registrations, logs };
}

describe("registration", () => {
  it("registers skill_proposal_evaluate under a stable registrationId", () => {
    const { api, registrations } = fakeApi();
    activate(api);
    assert.equal(registrations.length, 1);
    assert.equal(registrations[0].event, "skill_proposal_evaluate");
    assert.equal(registrations[0].options?.registrationId, "precedent-evidence-gate");
    assert.equal(registrations[0].options?.registrationId, REGISTRATION_ID);
  });

  it("asks OpenClaw for strictly more time than it gives the subprocess", () => {
    // If these were equal the two deadlines would race, and OpenClaw winning
    // means an attributed error outcome, which does not block. The gap is the
    // reason a slow grader still returns a verdict instead of vanishing.
    const { api, registrations } = fakeApi({ timeoutMs: 60_000 });
    activate(api);
    const asked = registrations[0].options?.timeoutMs ?? 0;
    assert.equal(asked, 60_000 + REGISTRATION_HEADROOM_MS);
    assert.ok(asked > 60_000);
    assert.ok(asked < HOST_HOOK_TIMEOUT_MS);
  });

  it("keeps the registration under OpenClaw's own hook timeout however it is configured", () => {
    const { api, registrations } = fakeApi({ timeoutMs: 9_999_999 });
    activate(api);
    const asked = registrations[0].options?.timeoutMs ?? 0;
    assert.ok(asked < HOST_HOOK_TIMEOUT_MS, `asked for ${asked} ms`);
  });

  it("warns in the log, not on the console, when configured to fail open", () => {
    const { api, logs } = fakeApi({ failClosed: false });
    activate(api);
    assert.ok(logs.some((l) => l.startsWith("warn ") && l.includes("failClosed=false")));
  });

  it("reads config from api.config, a nested wrapper, or getConfig", () => {
    assert.equal(readPluginConfig(fakeApi({ blockOn: "warn" }).api).blockOn, "warn");
    assert.equal(
      readPluginConfig(fakeApi({ precedent: { blockOn: "warn" } }).api).blockOn,
      "warn",
    );
    const throwing = {
      get config(): unknown {
        throw new Error("host config unavailable");
      },
    } as unknown as Parameters<typeof activate>[0];
    // A host that throws on a config read still gets a working evaluator.
    assert.deepEqual(readPluginConfig(throwing), DEFAULT_CONFIG);
  });
});

// ==========================================================================
// happy path
// ==========================================================================

describe("happy path", () => {
  it("passes a clean bundle through, verbatim", async () => {
    const cli = cliThatReturns(PASS_DOCUMENT);
    const result = await evaluateProposal(makeEvent(), {
      config: config(),
      env: cli.env,
    });
    assertWellFormed(result);
    assert.equal(result.decision, "pass");
    assert.equal(result.findings?.length, 0);
    assert.equal(result.evaluatorVersion, "precedent/0.1.0");
    assert.equal(result.mode, "static");
    assert.equal(result.summary, PASS_DOCUMENT.summary);
    // The core's own metrics survive alongside the plugin's.
    assert.equal(result.metrics?.["bundle.files"], 1);
    assert.equal(result.metrics?.["plugin.version"], PLUGIN_VERSION);
    assert.equal(result.metrics?.["plugin.findingsTotal"], 0);
  });

  it("turns a warn finding into revise", async () => {
    const cli = cliThatReturns(REVISE_DOCUMENT);
    const result = await evaluateProposal(makeEvent(), { config: config(), env: cli.env });
    assertWellFormed(result);
    assert.equal(result.decision, "revise");
    assert.equal(result.findings?.length, 1);
    assert.equal(findingById(result, "structure/missing-reference")?.line, 7);
    assert.match(result.decisionReason ?? "", /structure\/missing-reference/);
  });

  it("turns a critical finding into block", async () => {
    const cli = cliThatReturns(BLOCK_DOCUMENT);
    const result = await evaluateProposal(makeEvent(), { config: config(), env: cli.env });
    assertWellFormed(result);
    assert.equal(result.decision, "block");
    assert.equal(result.findings?.length, 2);
    assert.equal(findingById(result, "dlp/credential")?.severity, "critical");
    assert.match(result.decisionReason ?? "", /^dlp\/credential: /);
  });

  it("hands the complete event to the CLI on stdin", async () => {
    const cli = cliThatReturns(PASS_DOCUMENT);
    const event = makeEvent();
    await evaluateProposal(event, { config: config(), env: cli.env });
    const seen = cli.capturedStdin();
    assert.ok(seen, "the CLI received nothing on stdin");
    const parsed = JSON.parse(seen) as typeof event;
    assert.deepEqual(parsed, event);
    assert.equal(parsed.candidate.skillMd.encoding, "utf8");
    assert.equal(parsed.correlationId, "test-correlation-0001");
  });

  it("tolerates a banner printed ahead of the document", async () => {
    // The likeliest real-world breakage: a wrapper script or a shell profile
    // writing to stdout. Recoverable, so it should not cost a blocked apply.
    const cli = cliThatWrites(
      "warning: virtualenv activated\n" + JSON.stringify(PASS_DOCUMENT) + "\n",
    );
    const result = await evaluateProposal(makeEvent(), { config: config(), env: cli.env });
    assert.equal(result.decision, "pass");
  });
});

// ==========================================================================
// the failures that must not become silent approvals
// ==========================================================================

describe("failure: the CLI is not installed", () => {
  it("blocks, naming both attempts and the fix", async () => {
    const empty = makeTempDir("precedent-empty-path-");
    const result = await evaluateProposal(makeEvent(), {
      config: config(),
      env: { PATH: empty },
    });
    assertWellFormed(result);
    assert.equal(result.decision, "block");
    assert.equal(result.mode, "error");
    const finding = findingById(result, "precedent/cli-not-found");
    assert.ok(finding, "expected a precedent/cli-not-found finding");
    assert.equal(finding.severity, "critical");
    assert.match(finding.message, /pip install precedent-cli/);
    assert.match(finding.message, /precedent\.precedentBin/);
    assert.match(finding.message, /precedent\.pythonBin/);
    assert.equal(result.metrics?.["plugin.failure"], "cli-not-found");
  });

  it("falls back to `<pythonBin> -m precedent` before giving up", async () => {
    // precedent is commonly installed in a virtualenv that is not on the
    // host's PATH; the interpreter still knows where it is.
    const cli = cliThatReturns(PASS_DOCUMENT);
    const result = await evaluateProposal(makeEvent(), {
      config: config({ precedentBin: "no-such-precedent-binary", pythonBin: "precedent" }),
      env: cli.env,
    });
    assert.equal(result.decision, "pass");
    assert.equal(result.metrics?.["plugin.usedPythonFallback"], true);
  });
});

describe("failure: the CLI exits non-zero with garbage", () => {
  it("blocks, quoting stderr and saying why a non-zero exit is itself a bug", async () => {
    const cli = cliThatWrites(
      "not json at all",
      "Traceback (most recent call last):\n  ImportError: no module named precedent\n",
      1,
    );
    const result = await evaluateProposal(makeEvent(), { config: config(), env: cli.env });
    assertWellFormed(result);
    assert.equal(result.decision, "block");
    const finding = findingById(result, "precedent/cli-error");
    assert.ok(finding);
    assert.match(finding.message, /exited 1/);
    assert.match(finding.message, /ImportError/);
    assert.match(finding.message, /exits 0 for pass, revise and block alike/);
  });

  it("never echoes a credential out of the child's stderr", async () => {
    const secret = "sk-" + "A1b2C3d4E5f6G7h8I9j0";
    const cli = cliThatWrites("", "failed while reading " + secret + "\n", 2);
    const result = await evaluateProposal(makeEvent(), { config: config(), env: cli.env });
    assert.equal(result.decision, "block");
    const serialised = JSON.stringify(result);
    assert.ok(!serialised.includes(secret), "a credential leaked into the result");
    assert.match(serialised, /\[redacted\]/);
  });
});

describe("failure: the CLI hangs", () => {
  // The deadline here has to clear a cold Node start (which on a loaded
  // machine is a few hundred ms) or the child gets killed before it has run a
  // line, and the test passes for the wrong reason: it would be measuring
  // process startup, not the timeout path, and the SIGTERM case below would
  // never reach the handler it is supposed to be defeating.
  const DEADLINE_MS = 1_500;

  it("kills it and blocks, well inside the time it was going to take", async () => {
    const cli = makeFakeCli(
      [
        "process.stdin.resume();",
        "setTimeout(() => process.exit(0), 30000);",
      ].join("\n"),
    );
    const started = Date.now();
    const result = await evaluateProposal(makeEvent(), {
      config: config(),
      env: cli.env,
      childTimeoutMs: DEADLINE_MS,
      killGraceMs: 200,
    });
    const elapsed = Date.now() - started;
    assertWellFormed(result);
    assert.equal(result.decision, "block");
    const finding = findingById(result, "precedent/timeout");
    assert.ok(finding);
    assert.match(finding.message, /did not finish within 1500 ms/);
    assert.match(finding.message, /precedent\.timeoutMs/);
    assert.ok(elapsed < 10_000, `took ${elapsed} ms; the child was going to take 30 s`);
    assert.ok(cli.wasSpawned(), "the child never got far enough to be a real timeout");
  });

  it("escalates to SIGKILL for a child that ignores SIGTERM", async () => {
    const cli = makeFakeCli(
      [
        'process.on("SIGTERM", () => {});',
        "process.stdin.resume();",
        "setTimeout(() => process.exit(0), 30000);",
      ].join("\n"),
    );
    const started = Date.now();
    const result = await evaluateProposal(makeEvent(), {
      config: config(),
      env: cli.env,
      childTimeoutMs: DEADLINE_MS,
      killGraceMs: 200,
    });
    const elapsed = Date.now() - started;
    assert.equal(result.decision, "block");
    assert.ok(findingById(result, "precedent/timeout"));
    // The marker proves the body ran, so the SIGTERM handler really was
    // installed and really was ignored: only SIGKILL could have ended this.
    assert.ok(cli.wasSpawned(), "the child never installed its SIGTERM handler");
    assert.ok(elapsed < 10_000, `took ${elapsed} ms`);
  });
});

describe("failure: the CLI floods stdout", () => {
  it("stops reading, kills it, and blocks", async () => {
    const cli = makeFakeCli(
      [
        "process.stdin.resume();",
        'const chunk = "x".repeat(64 * 1024);',
        "const tick = () => { for (let i = 0; i < 8; i++) process.stdout.write(chunk); " +
          "setTimeout(tick, 1); };",
        "tick();",
      ].join("\n"),
    );
    const result = await evaluateProposal(makeEvent(), {
      config: config(),
      env: cli.env,
      maxStdoutBytes: 2_000,
      childTimeoutMs: 10_000,
      killGraceMs: 200,
    });
    assertWellFormed(result);
    assert.equal(result.decision, "block");
    const finding = findingById(result, "precedent/output-too-large");
    assert.ok(finding);
    assert.match(finding.message, /more than 2000 bytes/);
  });
});

describe("failure: the bundle is too large", () => {
  it("blocks without spawning anything at all", async () => {
    const cli = cliThatReturns(PASS_DOCUMENT);
    const result = await evaluateProposal(makeOversizedEvent(5_000_000), {
      config: config({ maxBundleBytes: 2_000_000 }),
      env: cli.env,
    });
    assertWellFormed(result);
    assert.equal(result.decision, "block");
    const finding = findingById(result, "precedent/bundle-too-large");
    assert.ok(finding);
    assert.match(finding.message, /5000000 bytes/);
    assert.match(finding.message, /precedent\.maxBundleBytes/);
    assert.equal(cli.wasSpawned(), false, "the CLI should never have been started");
    assert.equal(result.metrics?.["plugin.candidateBytes"], 5_000_000);
  });

  it("applies the same cap to the baseline of an update proposal", async () => {
    const cli = cliThatReturns(PASS_DOCUMENT);
    const base = makeEvent();
    const event = makeEvent({
      proposal: { ...base.proposal, kind: "update", revision: 2 },
      baseline: {
        ...base.candidate,
        skillMd: { ...base.candidate.skillMd, sizeBytes: 9_000_000 },
      },
      reason: "revised",
    });
    const result = await evaluateProposal(event, { config: config(), env: cli.env });
    assert.equal(result.decision, "block");
    assert.match(
      findingById(result, "precedent/bundle-too-large")?.message ?? "",
      /baseline bundle/,
    );
    assert.equal(cli.wasSpawned(), false);
  });
});

describe("failure: stdout is not a verdict", () => {
  it("blocks when the CLI exits 0 having printed prose", async () => {
    const cli = cliThatWrites("everything looks fine to me!\n", "", 0);
    const result = await evaluateProposal(makeEvent(), { config: config(), env: cli.env });
    assertWellFormed(result);
    assert.equal(result.decision, "block");
    const finding = findingById(result, "precedent/unparseable-output");
    assert.ok(finding);
    assert.match(finding.message, /stdout must be JSON and only JSON/);
  });

  it("blocks when the CLI exits 0 having printed nothing", async () => {
    const cli = cliThatWrites("", "", 0);
    const result = await evaluateProposal(makeEvent(), { config: config(), env: cli.env });
    assert.equal(result.decision, "block");
    assert.ok(findingById(result, "precedent/unparseable-output"));
  });

  it("blocks when stdout is valid JSON but not an object", async () => {
    const cli = cliThatWrites("[1, 2, 3]\n", "", 0);
    const result = await evaluateProposal(makeEvent(), { config: config(), env: cli.env });
    assert.equal(result.decision, "block");
    assert.ok(findingById(result, "precedent/unparseable-output"));
  });
});

describe("failure: the plugin itself breaks", () => {
  it("returns a verdict rather than throwing on an unserialisable event", async () => {
    const event = makeEvent() as unknown as Record<string, unknown>;
    event.self = event; // JSON.stringify throws on a cycle
    const cli = cliThatReturns(PASS_DOCUMENT);
    const result = await evaluateProposal(
      event as never,
      { config: config(), env: cli.env },
    );
    assertWellFormed(result);
    assert.equal(result.decision, "block");
    assert.ok(findingById(result, "precedent/internal-error"));
    assert.equal(cli.wasSpawned(), false);
  });

  it("returns a verdict rather than throwing on a null event", async () => {
    const empty = makeTempDir("precedent-empty-path-");
    const result = await evaluateProposal(null as never, {
      config: config(),
      env: { PATH: empty },
    });
    assertWellFormed(result);
    assert.equal(result.decision, "block");
  });
});

// ==========================================================================
// failClosed
// ==========================================================================

describe("failClosed", () => {
  it("downgrades an evaluator failure from block to revise when false", async () => {
    const empty = makeTempDir("precedent-empty-path-");
    const result = await evaluateProposal(makeEvent(), {
      config: config({ failClosed: false }),
      env: { PATH: empty },
    });
    assertWellFormed(result);
    assert.equal(result.decision, "revise");
    assert.equal(findingById(result, "precedent/cli-not-found")?.severity, "warn");
    assert.equal(result.metrics?.["plugin.failClosed"], false);
  });

  it("still blocks a proposal the core actually refused", async () => {
    // failClosed governs *our* failures. A verdict the core earned is not a
    // failure, and turning fail-open on must not smuggle a credential through.
    const cli = cliThatReturns(BLOCK_DOCUMENT);
    const result = await evaluateProposal(makeEvent(), {
      config: config({ failClosed: false }),
      env: cli.env,
    });
    assert.equal(result.decision, "block");
    assert.match(result.decisionReason ?? "", /dlp\/credential/);
  });

  it("blocks by default, on every one of the failure kinds", async () => {
    const empty = makeTempDir("precedent-empty-path-");
    const cases: Array<[string, Awaited<ReturnType<typeof evaluateProposal>>]> = [
      ["cli-not-found", await evaluateProposal(makeEvent(), {
        config: config(), env: { PATH: empty },
      })],
      ["cli-error", await evaluateProposal(makeEvent(), {
        config: config(), env: cliThatWrites("", "boom", 3).env,
      })],
      ["unparseable-output", await evaluateProposal(makeEvent(), {
        config: config(), env: cliThatWrites("hello", "", 0).env,
      })],
      ["bundle-too-large", await evaluateProposal(makeOversizedEvent(9_000_000), {
        config: config(), env: cliThatReturns(PASS_DOCUMENT).env,
      })],
    ];
    for (const [name, result] of cases) {
      assertWellFormed(result);
      assert.equal(result.decision, "block", `${name} did not block`);
      assert.equal(result.metrics?.["plugin.failure"], name);
      // Every failure must say what broke *and* what to do about it.
      assert.ok((result.decisionReason ?? "").length > 80, `${name} reason too terse`);
    }
  });
});

// ==========================================================================
// policy
// ==========================================================================

describe("policy", () => {
  it("escalates revise to block when blockOn is warn", async () => {
    const cli = cliThatReturns(REVISE_DOCUMENT);
    const result = await evaluateProposal(makeEvent(), {
      config: config({ blockOn: "warn" }),
      env: cli.env,
    });
    assertWellFormed(result);
    assert.equal(result.decision, "block");
    assert.equal(result.metrics?.["plugin.escalated"], true);
    assert.match(result.decisionReason ?? "", /precedent\.blockOn/);
    assert.match(result.summary ?? "", /^block \(precedent\.blockOn = "warn"\)/);
  });

  it("leaves a clean bundle alone even when blockOn is warn", async () => {
    const cli = cliThatReturns(PASS_DOCUMENT);
    const result = await evaluateProposal(makeEvent(), {
      config: config({ blockOn: "warn" }),
      env: cli.env,
    });
    assert.equal(result.decision, "pass");
    assert.equal(result.metrics?.["plugin.escalated"], false);
  });

  it("treats a severity it cannot read as warn, not as info", async () => {
    const cli = cliThatReturns({
      ...PASS_DOCUMENT,
      decision: "pass",
      findings: [{ ruleId: "future/rule", severity: "catastrophic", message: "?" }],
    });
    const result = await evaluateProposal(makeEvent(), { config: config(), env: cli.env });
    assert.equal(findingById(result, "future/rule")?.severity, "warn");
    assert.equal(result.decision, "revise");
  });

  it("honours a core block even when no finding explains it", async () => {
    const cli = cliThatReturns({ decision: "block", decisionReason: "", findings: [] });
    const result = await evaluateProposal(makeEvent(), { config: config(), env: cli.env });
    assertWellFormed(result);
    assert.equal(result.decision, "block");
  });

  it("does not spawn anything when disabled, and says so", async () => {
    const cli = cliThatReturns(BLOCK_DOCUMENT);
    const result = await evaluateProposal(makeEvent(), {
      config: config({ enabled: false }),
      env: cli.env,
    });
    assertWellFormed(result);
    assert.equal(result.decision, "pass");
    assert.equal(result.mode, "disabled");
    assert.match(result.decisionReason ?? "", /not a clean bill of health/);
    assert.equal(cli.wasSpawned(), false);
  });

  it("caps the findings it carries back without ever hiding a critical", async () => {
    const many = Array.from({ length: 500 }, (_, i) => ({
      ruleId: "dlp/pii",
      severity: "warn",
      message: "a personal identifier appears at line " + i,
      file: "SKILL.md",
      line: i + 1,
    }));
    many.push({
      ruleId: "dlp/credential",
      severity: "critical",
      message: "a credential appears in SKILL.md",
      file: "SKILL.md",
      line: 999,
    });
    const cli = cliThatReturns({ ...PASS_DOCUMENT, decision: "block", findings: many });
    const result = await evaluateProposal(makeEvent(), { config: config(), env: cli.env });
    assert.equal(result.decision, "block");
    assert.ok((result.findings ?? []).length <= 200);
    assert.ok(findingById(result, "dlp/credential"), "the critical was dropped by the cap");
    assert.equal(result.metrics?.["plugin.findingsTotal"], 501);
    assert.ok(findingById(result, "precedent/findings-truncated"));
  });
});

describe("parsing and redaction helpers", () => {
  it("parses the compact single line the CLI actually writes", () => {
    assert.deepEqual(parseResultDocument('{"decision":"pass"}\n'), { decision: "pass" });
    assert.equal(parseResultDocument(""), null);
    assert.equal(parseResultDocument("nope"), null);
    assert.equal(parseResultDocument('["a"]'), null);
  });

  it("redacts the four credential shapes and neutralises control characters", () => {
    assert.equal(redact("token sk-" + "abcdefgh1234"), "token [redacted]");
    assert.equal(redact("token ghp_" + "abcdefgh1234"), "token [redacted]");
    assert.equal(redact("token AKIA" + "ABCDEFGHIJKL"), "token [redacted]");
    assert.equal(redact("token xoxb-" + "abcdefgh1234"), "token [redacted]");
    const esc = String.fromCharCode(0x1b);
    // An escape sequence in a diagnostic must not survive into a terminal.
    assert.equal(redact("a" + esc + "[2Jb"), "a [2Jb");
    assert.ok(!redact("a" + esc + "b").includes(esc));
  });
});

// ==========================================================================
// the package agrees with itself
// ==========================================================================

describe("package consistency", () => {
  const pkg = JSON.parse(readFileSync(HERE + "package.json", "utf8")) as {
    version: string;
    name: string;
  };
  const manifest = JSON.parse(readFileSync(HERE + "openclaw.plugin.json", "utf8")) as {
    id: string;
    categories: string[];
    activation: { onStartup: boolean };
    configSchema: { properties: Record<string, { default?: unknown; enum?: string[] }> };
  };

  it("keeps PLUGIN_VERSION in step with package.json", () => {
    assert.equal(pkg.version, PLUGIN_VERSION);
    assert.equal(pkg.name, "@precedent/openclaw");
  });

  it("declares the manifest the deliverable asks for", () => {
    assert.equal(manifest.id, "precedent");
    assert.deepEqual(manifest.categories, ["security", "quality"]);
    assert.equal(manifest.activation.onStartup, true);
  });

  it("matches every configSchema default to the code's default", () => {
    // A manifest default that disagrees with the code is a setting whose
    // documented value is not the value you get.
    const props = manifest.configSchema.properties;
    for (const [key, expected] of Object.entries(DEFAULT_CONFIG)) {
      assert.ok(props[key], `configSchema is missing ${key}`);
      assert.equal(props[key].default, expected, `default for ${key} disagrees`);
    }
    assert.deepEqual(props.blockOn.enum, ["critical", "warn"]);
    assert.equal(Object.keys(props).length, Object.keys(DEFAULT_CONFIG).length);
  });
});

// ==========================================================================
// the real core, when it is installed here
// ==========================================================================

describe("integration with the real precedent CLI", () => {
  // Skipped on a machine (or a CI runner) without the repo's venv, so the
  // suite stays runnable standalone. When it is there, this is the only test
  // that proves the two halves of the seam agree about the wire format.
  const realCli = HERE + "../../packages/precedent/.venv/bin/precedent";
  const available = existsSync(realCli);

  it("grades a clean bundle with the actual core", { skip: !available }, async () => {
    const result = await evaluateProposal(makeEvent(), {
      config: config({ precedentBin: realCli }),
    });
    assertWellFormed(result);
    assert.match(result.evaluatorVersion ?? "", /^precedent\//);
    assert.equal(result.mode, "static");
    assert.notEqual(result.decision, "block");
  });

  it("blocks a bundle the actual core refuses", { skip: !available }, async () => {
    // A tampered digest: what you were asked to grade is not what you were
    // handed. The core calls that critical, so it must come back as a block.
    const event = makeEvent();
    const tampered = {
      ...event,
      candidate: {
        ...event.candidate,
        skillMd: { ...event.candidate.skillMd, sha256: "0".repeat(64) },
      },
    };
    const result = await evaluateProposal(tampered, {
      config: config({ precedentBin: realCli }),
    });
    assertWellFormed(result);
    assert.equal(result.decision, "block");
    assert.ok((result.findings ?? []).some((f) => f.severity === "critical"));
  });
});
