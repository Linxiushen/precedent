// Copyright 2026 The Precedent authors.
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0
// SPDX-License-Identifier: Apache-2.0
//
// evaluate.ts — one skill proposal in, one evaluator result out.
//
// THE RULE THIS FILE EXISTS TO ENFORCE
// ------------------------------------
// On a thrown error or a hook timeout, OpenClaw records an *attributed error
// outcome*. It does not block. Only a completed `decision: "block"` vetoes an
// apply. So an evaluator that throws is an evaluator that silently approves —
// and it approves exactly when it is broken, which is when you can least afford
// it. `evaluateProposal` therefore has one hard invariant:
//
//     it always resolves, and it always resolves with a decision.
//
// Every internal failure — missing binary, crash, timeout, garbage on stdout,
// an oversized bundle, a bug in this file — becomes a *verdict* saying which
// failure happened and how to fix it. `failClosed` (default true) chooses
// whether that verdict is `block` or `revise`. It governs only our own
// failures: a `block` the core actually earned from a finding blocks either
// way, because that is not an infrastructure failure, that is the gate working.

import { Buffer } from "node:buffer";

import {
  DEFAULT_MAX_STDOUT_BYTES,
  MAX_FINDINGS_RETURNED,
  resolveConfig,
  resolveTimeouts,
  type PrecedentConfig,
} from "./config.ts";
import { runCli, type CliRun } from "./spawn.ts";
import {
  SEVERITY_RANK,
  type Decision,
  type EvaluatorResult,
  type Finding,
  type PluginLogger,
  type Severity,
  type SkillProposalEvaluateEvent,
} from "./types.ts";

/** Kept in step with package.json; `index.test.ts` asserts they agree. */
export const PLUGIN_VERSION = "0.1.0";
export const PLUGIN_EVALUATOR_ID = "@precedent/openclaw";

/** The registration id OpenClaw dedupes and attributes outcomes by. Stable
 *  forever: changing it orphans the history of every verdict already recorded. */
export const REGISTRATION_ID = "precedent-evidence-gate";

const DECISION_RANK: Record<Decision, number> = { pass: 0, revise: 1, block: 2 };

export interface EvaluateOptions {
  config: PrecedentConfig;
  logger?: PluginLogger;
  /** Environment the CLI is PATH-resolved and run in. Defaults to `process.env`. */
  env?: NodeJS.ProcessEnv;
  /** Test seam: shrink the stdout cap without touching the published schema. */
  maxStdoutBytes?: number;
  /** Test seam: override the subprocess deadline derived from the config. */
  childTimeoutMs?: number;
  killGraceMs?: number;
}

// --------------------------------------------------------------------------
// redaction
// --------------------------------------------------------------------------

// The core never echoes a matched secret back — a DLP finding carries the kind,
// the file and the line and nothing else. A diagnostic built here out of a
// misconfigured binary's stdout has no such discipline behind it, so it gets
// the same treatment before it goes anywhere near the proposal record.
const CREDENTIAL_SHAPES = [
  /sk-[A-Za-z0-9_-]{8,}/g,
  /gh[pousr]_[A-Za-z0-9]{8,}/g,
  /xox[abprs]-[A-Za-z0-9-]{8,}/g,
  /AKIA[0-9A-Z]{12,}/g,
];

/** C0 controls and DEL, written as escapes so this file stays plain ASCII. */
const CONTROL_CHARS = new RegExp("[\\u0000-\\u001f\\u007f]", "g");

export function redact(text: string): string {
  let out = text;
  for (const shape of CREDENTIAL_SHAPES) out = out.replace(shape, "[redacted]");
  // Neutralise control characters so a diagnostic cannot rewrite a terminal.
  return out.replace(CONTROL_CHARS, " ");
}

function excerpt(text: string, limit: number): string {
  const flat = redact(text).replace(/\s+/g, " ").trim();
  if (flat.length <= limit) return flat;
  return `${flat.slice(0, limit)}… (${flat.length} chars)`;
}

// --------------------------------------------------------------------------
// failure verdicts
// --------------------------------------------------------------------------

export type FailureKind =
  | "cli-not-found"
  | "spawn-failed"
  | "timeout"
  | "cli-error"
  | "unparseable-output"
  | "not-a-verdict"
  | "output-too-large"
  | "bundle-too-large"
  | "internal-error";

/**
 * Build the verdict for a failure of the evaluator itself.
 *
 * `failClosed` picks the severity and the decision together, so the finding and
 * the decision can never disagree — a `warn` finding beside a `block` would be
 * unreadable in the proposal record.
 */
function failure(
  kind: FailureKind,
  message: string,
  config: PrecedentConfig,
  metrics: Record<string, string | number | boolean>,
): EvaluatorResult {
  const severity: Severity = config.failClosed ? "critical" : "warn";
  const decision: Decision = config.failClosed ? "block" : "revise";
  const ruleId = `precedent/${kind}`;
  return {
    summary: `${decision}: the Precedent evaluator could not grade this proposal (${kind})`,
    findings: [{ ruleId, severity, message }],
    metrics: {
      ...metrics,
      "plugin.version": PLUGIN_VERSION,
      "plugin.failure": kind,
      "plugin.failClosed": config.failClosed,
    },
    evaluatorVersion: `${PLUGIN_EVALUATOR_ID}/${PLUGIN_VERSION}`,
    mode: "error",
    decision,
    decisionReason: `${ruleId}: ${message}`,
  };
}

/**
 * The fail-closed verdict for a failure *outside* `evaluateProposal` — the
 * entry point's own last-resort catch.
 *
 * Exported because `index.ts` runs inside OpenClaw's process, where a throw is
 * an attributed error outcome and therefore an approval. It needs to be able
 * to answer even when it has no config to answer with, so the default (which
 * is fail-closed) stands in.
 */
export function internalErrorResult(
  error: unknown,
  config?: PrecedentConfig,
): EvaluatorResult {
  const err = error as Error | undefined;
  return failure(
    "internal-error",
    `the Precedent plugin's entry point threw ${err?.name ?? "Error"}: ` +
      `${redact(err?.message ?? String(error))} — this is a bug in ` +
      `@precedent/openclaw, or a host that this version does not understand. ` +
      `Please report it with this decisionReason; until then, set ` +
      `precedent.enabled to false to stop it gating applies.`,
    config ?? resolveConfig(undefined),
    {},
  );
}

// --------------------------------------------------------------------------
// reading the core's document
// --------------------------------------------------------------------------

function isSeverity(value: unknown): value is Severity {
  return value === "info" || value === "warn" || value === "critical";
}

function isDecision(value: unknown): value is Decision {
  return value === "pass" || value === "revise" || value === "block";
}

/** Coerce the core's findings into the seam's shape.
 *
 *  One rule governs every branch here: **this function may raise a severity
 *  and must never lower one, and it never drops a finding.** An unrecognised
 *  severity becomes `warn`, not `info`, because a severity we cannot read is
 *  one we must not assume is harmless; a finding with no message keeps its
 *  severity and gets a stand-in, because dropping it would let a malformed
 *  `critical` quietly become a `pass`. The decision is computed from what comes
 *  out of here, so anything lost in here is lost from the verdict. */
export function sanitiseFindings(raw: unknown): Finding[] {
  if (!Array.isArray(raw)) return [];
  const out: Finding[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") {
      // Not even an object. It is still something the core wanted to say.
      out.push({
        ruleId: "precedent/unreadable-finding",
        severity: "warn",
        message:
          `the evaluator returned a finding that is not an object ` +
          `(${typeof item}); it is reported rather than dropped, because a ` +
          `finding this plugin cannot read is not a finding it may discard`,
      });
      continue;
    }
    const f = item as Record<string, unknown>;
    const message =
      typeof f.message === "string" && f.message.trim()
        ? f.message
        : "(the evaluator reported this rule with no message)";
    const finding: Finding = {
      ruleId: typeof f.ruleId === "string" && f.ruleId ? f.ruleId : "precedent/unnamed",
      severity: isSeverity(f.severity) ? f.severity : "warn",
      message,
    };
    if (typeof f.file === "string" && f.file) finding.file = f.file;
    if (typeof f.line === "number" && Number.isFinite(f.line)) finding.line = Math.trunc(f.line);
    out.push(finding);
  }
  return out;
}

function sanitiseMetrics(raw: unknown): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  if (!raw || typeof raw !== "object") return out;
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof value === "string" || typeof value === "boolean") out[key] = value;
    else if (typeof value === "number" && Number.isFinite(value)) out[key] = value;
  }
  return out;
}

/**
 * Why `document` is not an evaluator verdict, or `null` when it is one.
 *
 * This is the check that closes the widest fail-open in the plugin. A verdict
 * is a document with a `decision`; *any* other JSON object — `{}`, `{"ok":1}`,
 * the output of some other program that happens to be named `precedent`, a
 * wrapper's progress line — used to be read as a graded result with no
 * findings, and a result with no findings is a `pass`. A binary that is not
 * the evaluator must never be able to approve a proposal by exiting 0, so
 * "I could not find a verdict here" is a block, not an empty one.
 */
export function verdictProblem(document: Record<string, unknown>): string | null {
  if (!("decision" in document)) return "it carries no `decision` field";
  if (!isDecision(document.decision)) {
    return `its \`decision\` is ${JSON.stringify(document.decision)}, which is ` +
      `not one of "pass", "revise" or "block"`;
  }
  if (
    "findings" in document &&
    document.findings !== null &&
    document.findings !== undefined &&
    !Array.isArray(document.findings)
  ) {
    return "its `findings` is not an array";
  }
  return null;
}

export interface StdoutReading {
  /** The verdict on stdout — set only when there is exactly one. */
  verdict: Record<string, unknown> | null;
  /** How many distinct candidates parsed as a verdict. */
  verdicts: number;
  /** The first candidate that parsed as a JSON object, verdict or not. */
  firstObject: Record<string, unknown> | null;
}

/** How many lines of a flooded stdout we are willing to try to parse. */
const MAX_SCANNED_LINES = 64;

/**
 * Read the CLI's stdout and find the verdict in it, if there is exactly one.
 *
 * `--json` writes one compact line, so the trimmed whole is normally the
 * document. Lines are tried as well, because the most likely way to break this
 * in the field is a wrapper script or a shell profile printing a banner around
 * it — a recoverable accident that should not cost somebody a blocked
 * proposal.
 *
 * The one thing that is *not* tolerated is ambiguity. "Take the last line"
 * sounds harmless until you notice that appending a line is exactly how a
 * wrapper would forge a verdict: a real `block` followed by a printed
 * `{"decision":"pass"}` would have been read as a pass. So noise around one
 * verdict is fine, and two verdicts is a refusal — which of them is the answer
 * is not something this plugin gets to guess.
 */
export function readStdout(stdout: string): StdoutReading {
  const trimmed = stdout.trim();
  const reading: StdoutReading = { verdict: null, verdicts: 0, firstObject: null };
  if (!trimmed) return reading;

  const lines = trimmed.split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  // The head and the tail: a banner is at one end, a forgery at the other, and
  // a CLI that wrote thousands of lines is broken in a way already reported.
  const scanned = lines.length <= MAX_SCANNED_LINES * 2
    ? lines
    : [...lines.slice(0, MAX_SCANNED_LINES), ...lines.slice(-MAX_SCANNED_LINES)];
  for (const candidate of new Set([trimmed, ...scanned])) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(candidate) as unknown;
    } catch {
      continue; // not JSON; try the next shape
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) continue;
    const object = parsed as Record<string, unknown>;
    if (reading.firstObject === null) reading.firstObject = object;
    if (verdictProblem(object) !== null) continue;
    reading.verdicts += 1;
    reading.verdict = reading.verdicts === 1 ? object : null;
  }
  return reading;
}

/** The verdict on stdout, or the first JSON object when none is unambiguous.
 *
 *  Kept as the narrow, testable front door to {@link readStdout}: a caller that
 *  only wants the document does not have to care why there wasn't one. */
export function parseResultDocument(stdout: string): Record<string, unknown> | null {
  const reading = readStdout(stdout);
  return reading.verdict ?? reading.firstObject;
}

// --------------------------------------------------------------------------
// the decision
// --------------------------------------------------------------------------

export interface DecisionOutcome {
  decision: Decision;
  reason: string;
  escalated: boolean;
}

/**
 * Turn findings into a decision, honouring `blockOn` and never softening the
 * core's own verdict.
 *
 * Monotone in both inputs: the answer is the worse of what the findings say and
 * what the core said. This plugin can escalate (that is what `blockOn: "warn"`
 * is for) but it can never let through something the core refused.
 */
export function decide(
  findings: Finding[],
  coreDecision: Decision | undefined,
  coreReason: string | undefined,
  blockOn: "critical" | "warn",
): DecisionOutcome {
  const worst = findings.reduce((acc, f) => Math.max(acc, SEVERITY_RANK[f.severity]), -1);

  let decision: Decision = "pass";
  let reason = "no finding above `info`: structure, DLP, evidence, scope, risk and size all clear";
  let escalated = false;

  if (worst === SEVERITY_RANK.critical) {
    const criticals = findings.filter((f) => f.severity === "critical");
    const head = criticals[0];
    const extra = criticals.length > 1 ? ` (and ${criticals.length - 1} more)` : "";
    decision = "block";
    reason = `${head.ruleId}: ${head.message}${extra}`;
  } else if (worst === SEVERITY_RANK.warn) {
    const warns = findings.filter((f) => f.severity === "warn");
    const head = warns[0];
    const extra = warns.length > 1 ? ` (and ${warns.length - 1} more)` : "";
    if (blockOn === "warn") {
      decision = "block";
      escalated = true;
      reason =
        `${head.ruleId}: ${head.message}${extra} — blocked because precedent.blockOn ` +
        `is "warn"; at the default "critical" this would be a revise`;
    } else {
      decision = "revise";
      reason = `${head.ruleId}: ${head.message}${extra}`;
    }
  }

  if (coreDecision && DECISION_RANK[coreDecision] > DECISION_RANK[decision]) {
    decision = coreDecision;
    reason = coreReason?.trim()
      ? coreReason
      : `the precedent core returned decision "${coreDecision}" without naming a finding`;
  }

  return { decision, reason, escalated };
}

/** Carry back at most `MAX_FINDINGS_RETURNED`, worst first, keeping every
 *  critical. The decision was computed from the full set before this runs, so
 *  the cap is a transport limit and never a safety one. */
function capFindings(findings: Finding[]): { findings: Finding[]; omitted: number } {
  if (findings.length <= MAX_FINDINGS_RETURNED) return { findings, omitted: 0 };
  const ordered = [...findings].sort(
    (a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity],
  );
  const kept = ordered.slice(0, MAX_FINDINGS_RETURNED - 1);
  const omitted = findings.length - kept.length;
  kept.push({
    ruleId: "precedent/findings-truncated",
    severity: "info",
    message:
      `${omitted} further findings were omitted from this result (the decision was ` +
      `computed from all ${findings.length}). Run \`precedent evaluate-bundle --dir <skill>\` ` +
      `to see the full list.`,
  });
  return { findings: kept, omitted };
}

// --------------------------------------------------------------------------
// bundle size
// --------------------------------------------------------------------------

/** The larger of what a file *says* it weighs and what it actually weighs.
 *
 *  Taking the declared `sizeBytes` alone would let `{"sizeBytes": 0}` carry a
 *  50 MB string straight past the cap; taking the content alone would miss the
 *  size of a file the host summarised rather than inlined. The gate wants the
 *  pessimistic number, and both are cheap. */
function fileBytes(file: unknown): number {
  if (!file || typeof file !== "object") return 0;
  const f = file as Record<string, unknown>;
  const declared =
    typeof f.sizeBytes === "number" && Number.isFinite(f.sizeBytes) && f.sizeBytes >= 0
      ? Math.trunc(f.sizeBytes)
      : 0;
  const actual =
    typeof f.content === "string"
      ? f.encoding === "base64"
        ? Math.floor((f.content.length * 3) / 4)
        : Buffer.byteLength(f.content, "utf8")
      : 0;
  return Math.max(declared, actual);
}

export function snapshotBytes(snapshot: unknown): number {
  if (!snapshot || typeof snapshot !== "object") return 0;
  const s = snapshot as Record<string, unknown>;
  let total = fileBytes(s.skillMd);
  if (Array.isArray(s.files)) for (const file of s.files) total += fileBytes(file);
  return total;
}

// --------------------------------------------------------------------------
// the evaluator
// --------------------------------------------------------------------------

/**
 * Grade one `skill_proposal_evaluate` event.
 *
 * Resolves in every case, always with a `decision`. Does not throw.
 */
export async function evaluateProposal(
  event: SkillProposalEvaluateEvent,
  options: EvaluateOptions,
): Promise<EvaluatorResult> {
  const startedAt = Date.now();
  // The config has to exist before anything else is allowed to fail, because
  // every failure verdict below is built out of it — including the one in the
  // catch. A caller that hands over a partial, frozen or hostile options
  // object gets the documented defaults (fail-closed among them) rather than a
  // throw, which OpenClaw would record as an error outcome and apply anyway.
  let opts: EvaluateOptions;
  let config: PrecedentConfig;
  try {
    opts = (options && typeof options === "object" ? options : {}) as EvaluateOptions;
    config = resolveConfig(opts.config);
  } catch {
    opts = {} as EvaluateOptions;
    config = resolveConfig(undefined);
  }
  const log = opts.logger ?? {};

  try {
    if (!config.enabled) {
      // Disabled is a verdict too. Returning `pass` with a stated mode leaves a
      // trace in the proposal record; returning nothing would look identical to
      // an evaluator that ran and found nothing.
      return {
        summary: "pass: the Precedent evaluator is disabled (precedent.enabled = false)",
        findings: [],
        metrics: { "plugin.version": PLUGIN_VERSION, "plugin.enabled": false },
        evaluatorVersion: `${PLUGIN_EVALUATOR_ID}/${PLUGIN_VERSION}`,
        mode: "disabled",
        decision: "pass",
        decisionReason:
          "precedent.enabled is false, so this proposal was not graded. Nothing was " +
          "checked - this is not a clean bill of health.",
      };
    }

    const candidateBytes = snapshotBytes(event?.candidate);
    const baselineBytes = snapshotBytes(event?.baseline);
    const sizeMetrics = {
      "plugin.candidateBytes": candidateBytes,
      "plugin.baselineBytes": baselineBytes,
    };

    if (candidateBytes > config.maxBundleBytes) {
      return failure(
        "bundle-too-large",
        `the candidate bundle is ${candidateBytes} bytes, over the ` +
          `precedent.maxBundleBytes cap of ${config.maxBundleBytes} — raise the cap, or ` +
          `take the large files out of the skill (a bundle over 1 MiB already fails the ` +
          `core's size check, so this one is far past "large").`,
        config,
        sizeMetrics,
      );
    }
    if (baselineBytes > config.maxBundleBytes) {
      return failure(
        "bundle-too-large",
        `the baseline bundle is ${baselineBytes} bytes, over the ` +
          `precedent.maxBundleBytes cap of ${config.maxBundleBytes}. The baseline is the ` +
          `skill as it stands today, so this proposal cannot be diffed against it until ` +
          `the existing skill is trimmed or the cap is raised.`,
        config,
        sizeMetrics,
      );
    }

    let payload: string;
    try {
      payload = JSON.stringify(event);
    } catch (error) {
      return failure(
        "internal-error",
        `the proposal event could not be serialised to JSON ` +
          `(${(error as Error)?.name ?? "Error"}: ${(error as Error)?.message ?? error}). ` +
          `This is a bug in @precedent/openclaw or a malformed event from the host; ` +
          `please report it with this decisionReason.`,
        config,
        sizeMetrics,
      );
    }

    const timeouts = resolveTimeouts(config);
    const childTimeoutMs = opts.childTimeoutMs ?? timeouts.childTimeoutMs;
    const maxStdoutBytes = opts.maxStdoutBytes ?? DEFAULT_MAX_STDOUT_BYTES;
    const env = opts.env ?? process.env;
    const args = ["evaluate-bundle", "--stdin", "--json"];

    // First the CLI on PATH. If it simply is not there — and only then — try
    // the same package through the configured interpreter, which is how it
    // looks when precedent lives in a virtualenv that is not on the host's
    // PATH. Any other failure is reported as-is rather than retried: a CLI
    // that exists and crashed has told us something, and running a second
    // copy would only blur it.
    let run: CliRun = await runCli({
      argv: [config.precedentBin, ...args],
      input: payload,
      timeoutMs: childTimeoutMs,
      maxStdoutBytes,
      env,
      killGraceMs: opts.killGraceMs,
    });
    let usedFallback = false;

    if (run.failure === "enoent") {
      log.debug?.(
        `precedent: "${config.precedentBin}" is not on PATH, trying ` +
          `"${config.pythonBin} -m precedent"`,
      );
      const fallback = await runCli({
        argv: [config.pythonBin, "-m", "precedent", ...args],
        input: payload,
        timeoutMs: childTimeoutMs,
        maxStdoutBytes,
        env,
        killGraceMs: opts.killGraceMs,
      });
      if (fallback.failure !== "enoent") {
        run = fallback;
        usedFallback = true;
      } else {
        return failure(
          "cli-not-found",
          `precedent CLI not found on PATH — pip install precedent-cli (or ` +
            `pipx install precedent-cli), or set precedent.precedentBin to its absolute ` +
            `path, or set precedent.pythonBin to a Python that has it installed. Tried ` +
            `"${config.precedentBin}", then "${config.pythonBin} -m precedent"; neither ` +
            `exists in this process's PATH.`,
          config,
          sizeMetrics,
        );
      }
    }

    const runMetrics = {
      ...sizeMetrics,
      "plugin.cliElapsedMs": run.elapsedMs,
      "plugin.stdoutBytes": run.stdoutBytes,
      "plugin.usedPythonFallback": usedFallback,
    };
    const invocation = run.argv.join(" ");

    if (run.failure === "enoent" || run.failure === "spawn-error") {
      return failure(
        "spawn-failed",
        `could not start the precedent CLI ("${invocation}"): ` +
          `${run.errorCode ?? "unknown error"} ${redact(run.errorMessage ?? "")} — check ` +
          `that the path points at an executable file (chmod +x) and not a directory or ` +
          `a broken symlink.`,
        config,
        runMetrics,
      );
    }

    if (run.failure === "timeout") {
      return failure(
        "timeout",
        `the precedent CLI did not finish within ${childTimeoutMs} ms and was killed ` +
          `("${invocation}"). Raise precedent.timeoutMs, or run ` +
          `\`precedent evaluate-bundle --dir <skill>\` by hand to see where the time goes. ` +
          `Grading is offline and deterministic, so a timeout means an unusually large ` +
          `bundle or a stalled interpreter, never a slow network.`,
        config,
        runMetrics,
      );
    }

    if (run.failure === "stdout-cap") {
      return failure(
        "output-too-large",
        `the precedent CLI wrote more than ${maxStdoutBytes} bytes to stdout and was ` +
          `killed ("${invocation}") — a verdict is never that large. Check that ` +
          `precedent.precedentBin is the precedent CLI itself and not a wrapper that ` +
          `streams logs onto stdout.`,
        config,
        runMetrics,
      );
    }

    if (run.failure === "nonzero-exit") {
      return failure(
        "cli-error",
        `the precedent CLI exited ${run.exitCode ?? "(signal " + run.signal + ")"} instead ` +
          `of writing a verdict ("${invocation}"). It exits 0 for pass, revise and block ` +
          `alike — the decision lives in the document, never in the exit status — so a ` +
          `non-zero exit is the CLI itself failing. stderr: ` +
          `${excerpt(run.stderrTail, 600) || "(empty)"}`,
        config,
        runMetrics,
      );
    }

    const reading = readStdout(run.stdout);
    const document = reading.verdict;
    if (!document) {
      const began = excerpt(run.stdout, 240) || "(empty)";
      if (reading.verdicts > 1) {
        return failure(
          "not-a-verdict",
          `stdout from "${invocation}" carried ${reading.verdicts} documents that ` +
            `each look like a verdict (${run.stdoutBytes} bytes). Exactly one is a ` +
            `verdict and the rest are something printing over it, so which one to ` +
            `honour is not a guess this plugin is allowed to make. stdout began: ` +
            `${began}`,
          config,
          runMetrics,
        );
      }
      if (reading.firstObject) {
        return failure(
          "not-a-verdict",
          `the precedent CLI ("${invocation}") exited 0 and wrote JSON, but that ` +
            `JSON is not an evaluator verdict: ` +
            `${verdictProblem(reading.firstObject)}. An empty or foreign document ` +
            `is NOT an approval — check that precedent.precedentBin points at the ` +
            `precedent CLI itself (\`precedent evaluate-bundle --stdin --json\` ` +
            `writes one line carrying a decision) and not at another program of the ` +
            `same name or a stub. stdout began: ${began}`,
          config,
          runMetrics,
        );
      }
      return failure(
        "unparseable-output",
        `the precedent CLI exited 0 but stdout was not one JSON document ` +
          `(${run.stdoutBytes} bytes). stdout must be JSON and only JSON; a wrapper ` +
          `script, a shell profile or a plugin printing a banner will break it. ` +
          `stdout began: ${began}`,
        config,
        runMetrics,
      );
    }

    const findings = sanitiseFindings(document.findings);
    const coreDecision = isDecision(document.decision) ? document.decision : undefined;
    const coreReason =
      typeof document.decisionReason === "string" ? document.decisionReason : undefined;
    const outcome = decide(findings, coreDecision, coreReason, config.blockOn);
    const capped = capFindings(findings);

    const coreSummary = typeof document.summary === "string" ? document.summary : "";
    const summary = outcome.escalated
      ? `block (precedent.blockOn = "warn"): ${coreSummary || outcome.reason}`
      : coreSummary || `${outcome.decision}: ${outcome.reason}`;

    const result: EvaluatorResult = {
      summary,
      findings: capped.findings,
      metrics: {
        ...sanitiseMetrics(document.metrics),
        ...runMetrics,
        "plugin.version": PLUGIN_VERSION,
        "plugin.blockOn": config.blockOn,
        "plugin.findingsTotal": findings.length,
        "plugin.findingsOmitted": capped.omitted,
        "plugin.escalated": outcome.escalated,
        "plugin.totalElapsedMs": Date.now() - startedAt,
      },
      evaluatorVersion:
        typeof document.evaluatorVersion === "string" && document.evaluatorVersion
          ? document.evaluatorVersion
          : `${PLUGIN_EVALUATOR_ID}/${PLUGIN_VERSION}`,
      mode: typeof document.mode === "string" && document.mode ? document.mode : "static",
      decision: outcome.decision,
      decisionReason: outcome.reason,
    };

    log.info?.(
      `precedent: ${event?.skill?.name ?? "(unnamed skill)"} -> ${outcome.decision} ` +
        `(${findings.length} findings, ${run.elapsedMs} ms)`,
    );
    return result;
  } catch (error) {
    // The last line of defence. Reaching here is a bug in this file, and the
    // one thing we must not do about it is throw.
    const err = error as Error;
    return failure(
      "internal-error",
      `the Precedent plugin itself threw ${err?.name ?? "Error"}: ` +
        `${redact(err?.message ?? String(error))} — this is a bug in ` +
        `@precedent/openclaw. Please report it with this decisionReason; until then, ` +
        `set precedent.enabled to false to stop it gating applies.`,
      config,
      { "plugin.totalElapsedMs": Date.now() - startedAt },
    );
  }
}
