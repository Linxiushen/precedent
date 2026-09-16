// Copyright 2026 The Precedent authors.
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0
// SPDX-License-Identifier: Apache-2.0
//
// fixtures.ts — a real executable on a real PATH.
//
// The contract under test is a process boundary: argv, stdin, stdout, stderr,
// an exit status and a clock. Mocking `child_process.spawn` would test our
// belief about that boundary instead of the boundary, and every failure this
// plugin exists to survive — ENOENT, a child that ignores SIGTERM, a child
// that floods stdout — lives on the far side of it. So the fake CLI here is an
// actual file, marked executable, found by PATH lookup exactly as the real one
// would be.

import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Buffer } from "node:buffer";

import type {
  BundleFile,
  BundleSnapshot,
  SkillProposalEvaluateEvent,
} from "../src/types.ts";

const tempRoots: string[] = [];

export function makeTempDir(prefix = "precedent-openclaw-"): string {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  tempRoots.push(dir);
  return dir;
}

/** Remove every temp dir this module created. Call from an `after` hook. */
export function cleanupTempDirs(): void {
  while (tempRoots.length > 0) {
    const dir = tempRoots.pop();
    if (dir) rmSync(dir, { recursive: true, force: true });
  }
}

function readIfExists(path: string): string | null {
  try {
    return readFileSync(path, "utf8");
  } catch {
    return null;
  }
}

export interface FakeCli {
  /** The directory to put on PATH. */
  binDir: string;
  /** An env whose PATH contains only `binDir`, so nothing real can be found. */
  env: NodeJS.ProcessEnv;
  /** Path the script writes to when it runs, for "was it spawned?" assertions. */
  markerPath: string;
  /** True once the fake CLI has actually been executed. */
  wasSpawned(): boolean;
  /** Whatever the fake CLI read on stdin, if it was told to record it. */
  capturedStdin(): string | null;
}

/**
 * Write an executable named `precedent` whose body is the given JavaScript.
 *
 * The script is run by this same Node binary (absolute path in the shebang),
 * so the fixture needs nothing on PATH but itself. It has no file extension,
 * so Node loads it as CommonJS and `require` is available inside the body.
 * Two constants are injected ahead of the body: `MARKER` (touched on every
 * run) and `STDIN_PATH` (where a body may record what it read).
 */
export function makeFakeCli(body: string, options: { name?: string } = {}): FakeCli {
  const dir = makeTempDir("precedent-fakecli-");
  const binDir = join(dir, "bin");
  mkdirSync(binDir, { recursive: true });
  const markerPath = join(dir, "spawned.marker");
  const stdinPath = join(dir, "stdin.captured");
  const name = options.name ?? "precedent";
  const script = [
    "#!" + process.execPath,
    "const MARKER = " + JSON.stringify(markerPath) + ";",
    "const STDIN_PATH = " + JSON.stringify(stdinPath) + ";",
    'require("node:fs").writeFileSync(MARKER, String(Date.now()));',
    body,
    "",
  ].join("\n");
  writeFileSync(join(binDir, name), script, { mode: 0o755 });

  return {
    binDir,
    // PATH is exactly this one directory: a test that accidentally finds a real
    // `precedent` or a real `python3` is not testing what it claims to.
    env: { PATH: binDir },
    markerPath,
    wasSpawned(): boolean {
      return readIfExists(markerPath) !== null;
    },
    capturedStdin(): string | null {
      return readIfExists(stdinPath);
    },
  };
}

/** A fake CLI that drains stdin, records it, then writes `doc` as one line. */
export function cliThatReturns(doc: unknown, exitCode = 0): FakeCli {
  const line = JSON.stringify(JSON.stringify(doc));
  return makeFakeCli(
    [
      "const chunks = [];",
      'process.stdin.on("data", (c) => chunks.push(c));',
      'process.stdin.on("end", () => {',
      '  require("node:fs").writeFileSync(STDIN_PATH, Buffer.concat(chunks).toString("utf8"));',
      "  process.stdout.write(" + line + ');',
      "  process.exit(" + String(exitCode) + ");",
      "});",
    ].join("\n"),
  );
}

/** A fake CLI that writes arbitrary bytes on stdout and exits with `exitCode`. */
export function cliThatWrites(
  stdout: string,
  stderr = "",
  exitCode = 0,
): FakeCli {
  return makeFakeCli(
    [
      "const chunks = [];",
      'process.stdin.on("data", (c) => chunks.push(c));',
      'process.stdin.on("end", () => {',
      "  process.stdout.write(" + JSON.stringify(stdout) + ");",
      "  process.stderr.write(" + JSON.stringify(stderr) + ");",
      "  process.exit(" + String(exitCode) + ");",
      "});",
    ].join("\n"),
  );
}

/** A fake CLI that drains stdin and then never exits. */
export function cliThatHangs(): FakeCli {
  return makeFakeCli(
    [
      'process.stdin.on("data", () => {});',
      // Nothing is ever written and nothing ever exits: the only way out is
      // the caller's own deadline.
      "setInterval(() => {}, 1000);",
    ].join("\n"),
  );
}

/**
 * A fake CLI that leaks its stdout to a detached grandchild and then hangs.
 *
 * This is the shape that used to hang the plugin forever. Killing the child
 * does not close the pipe, because the grandchild still holds the write end,
 * so `close` never fires — and a hook that never returns runs to OpenClaw's
 * own timeout, which is an attributed error outcome, which does NOT block.
 * `lifetimeMs` is how long the grandchild outlives the kill.
 */
export function cliThatOrphansStdout(lifetimeMs = 30_000): FakeCli {
  return makeFakeCli(
    [
      'const cp = require("node:child_process");',
      'process.stdin.on("data", () => {});',
      "cp.spawn(process.execPath,",
      '  ["-e", "setTimeout(() => {}, ' + String(lifetimeMs) + ')"],',
      '  { stdio: ["ignore", "inherit", "ignore"], detached: true }).unref();',
      "setInterval(() => {}, 1000);",
    ].join("\n"),
  );
}

/** A fake CLI that writes `megabytes` MB of plausible JSON to stdout. */
export function cliThatFloods(megabytes: number): FakeCli {
  return makeFakeCli(
    [
      'const chunk = "{\\"decision\\":\\"pass\\",\\"pad\\":\\"" + "x".repeat(65535) + "\\"}\\n";',
      'process.stdin.on("data", () => {});',
      "let written = 0;",
      "const pump = () => {",
      "  while (written < " + String(megabytes) + " * 1024 * 1024) {",
      "    written += chunk.length;",
      "    if (!process.stdout.write(chunk)) {",
      '      process.stdout.once("drain", pump);',
      "      return;",
      "    }",
      "  }",
      "  process.exit(0);",
      "};",
      "pump();",
    ].join("\n"),
  );
}

// --------------------------------------------------------------------------
// events
// --------------------------------------------------------------------------

function sha256(text: string): string {
  return createHash("sha256").update(Buffer.from(text, "utf8")).digest("hex");
}

export function makeFile(path: string, content: string): BundleFile {
  return {
    path,
    content,
    encoding: "utf8",
    sha256: sha256(content),
    sizeBytes: Buffer.byteLength(content, "utf8"),
  };
}

export function makeSnapshot(skillMdText: string, files: BundleFile[] = []): BundleSnapshot {
  const skillMd = makeFile("SKILL.md", skillMdText);
  const all = [skillMd, ...files];
  const tree = all
    .slice()
    .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0))
    .map((f) => f.path + " " + f.sha256 + "\n")
    .join("");
  return { skillMd, files, treeSha256: sha256(tree) };
}

export const SAMPLE_SKILL_MD = [
  "---",
  "name: sample-skill",
  "description: A small skill used by the @precedent/openclaw test suite.",
  "---",
  "",
  "# sample-skill",
  "",
  "Run `sample --check` to see the current state.",
  "",
].join("\n");

/** A well-formed `create` proposal. Override any field for a specific test. */
export function makeEvent(
  overrides: Partial<SkillProposalEvaluateEvent> = {},
): SkillProposalEvaluateEvent {
  const candidate = overrides.candidate ?? makeSnapshot(SAMPLE_SKILL_MD);
  return {
    correlationId: "test-correlation-0001",
    proposal: {
      id: "proposal-0001",
      kind: "create",
      revision: 1,
      revisionSha256: candidate.treeSha256,
    },
    skill: {
      name: "sample-skill",
      skillKey: "sample-skill",
      description: "A small skill used by the @precedent/openclaw test suite.",
    },
    candidate,
    reason: "created",
    ...overrides,
  };
}

/**
 * A candidate whose declared `sizeBytes` add up to `bytes`, without allocating
 * them. The size gate reads `sizeBytes` off the snapshot, which is the field
 * OpenClaw fills in, so this exercises the gate honestly and cheaply.
 */
export function makeOversizedEvent(bytes: number): SkillProposalEvaluateEvent {
  const event = makeEvent();
  const skillMd = { ...event.candidate.skillMd, sizeBytes: bytes };
  return { ...event, candidate: { ...event.candidate, skillMd } };
}

// --------------------------------------------------------------------------
// result documents the fake CLI can return
// --------------------------------------------------------------------------

export const PASS_DOCUMENT = {
  summary: "pass: 0 critical, 0 warn, 0 info over 1 files (1620 bytes, static)",
  findings: [],
  metrics: { "bundle.files": 1, "bundle.bytes": 1620, "checks.run": 7 },
  evaluatorVersion: "precedent/0.1.0",
  mode: "static",
  decision: "pass",
  decisionReason:
    "no finding above `info`: structure, DLP, evidence, scope, risk and size all clear",
};

export const REVISE_DOCUMENT = {
  summary: "revise: 0 critical, 1 warn, 0 info over 1 files (2967 bytes, static)",
  findings: [
    {
      ruleId: "structure/missing-reference",
      severity: "warn",
      message:
        "SKILL.md references 'templates/registration-draft.md', which is not in the bundle",
      file: "SKILL.md",
      line: 7,
    },
  ],
  metrics: { "bundle.files": 1, "findings.warn": 1 },
  evaluatorVersion: "precedent/0.1.0",
  mode: "static",
  decision: "revise",
  decisionReason:
    "structure/missing-reference: SKILL.md references 'templates/registration-draft.md', " +
    "which is not in the bundle",
};

export const BLOCK_DOCUMENT = {
  summary: "block: 1 critical, 1 warn, 0 info over 2 files (4211 bytes, static)",
  findings: [
    {
      ruleId: "dlp/credential",
      severity: "critical",
      message: "a credential was found in scripts/deploy.sh",
      file: "scripts/deploy.sh",
      line: 12,
    },
    {
      ruleId: "scope/home-path",
      severity: "warn",
      message: "an absolute home path appears in SKILL.md",
      file: "SKILL.md",
      line: 30,
    },
  ],
  metrics: { "findings.critical": 1, "findings.warn": 1 },
  evaluatorVersion: "precedent/0.1.0",
  mode: "static",
  decision: "block",
  decisionReason: "dlp/credential: a credential was found in scripts/deploy.sh",
};
