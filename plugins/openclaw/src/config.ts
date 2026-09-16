// Copyright 2026 The Precedent authors.
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0
// SPDX-License-Identifier: Apache-2.0
//
// config.ts — the manifest's configSchema, resolved into something total.
//
// Every field has a default and every reader goes through `resolveConfig`, so a
// missing, misspelled or wrongly-typed setting degrades to the documented
// default instead of throwing. That is not politeness: this plugin's whole job
// is to never throw, and a config read is the first place it could.

export interface PrecedentConfig {
  enabled: boolean;
  pythonBin: string;
  precedentBin: string;
  failClosed: boolean;
  timeoutMs: number;
  blockOn: "critical" | "warn";
  maxBundleBytes: number;
}

export const DEFAULT_CONFIG: PrecedentConfig = {
  enabled: true,
  pythonBin: "python3",
  precedentBin: "precedent",
  failClosed: true,
  timeoutMs: 60_000,
  blockOn: "critical",
  maxBundleBytes: 2_000_000,
};

/** OpenClaw's own default hook timeout. A registration above this is pointless. */
export const HOST_HOOK_TIMEOUT_MS = 120_000;

/** How much longer OpenClaw is asked to wait than the subprocess is given. */
export const REGISTRATION_HEADROOM_MS = 15_000;

/** The largest registration we will ask for, kept under the host's own limit. */
export const MAX_REGISTRATION_TIMEOUT_MS = HOST_HOOK_TIMEOUT_MS - 5_000; // 115 s

/** Most stdout we will read from the CLI before giving up on it. The result
 *  document for a 2 MB bundle is a few hundred KB at worst; a CLI that writes
 *  more than this is broken or is not the CLI we think it is. */
export const DEFAULT_MAX_STDOUT_BYTES = 4_000_000;

/** Bytes of the child's stderr kept for the failure message. */
export const STDERR_TAIL_BYTES = 4_096;

/** Findings carried back to the host. The decision is computed from the *full*
 *  set before this cap applies, so capping can never hide a block. */
export const MAX_FINDINGS_RETURNED = 200;

function bool(value: unknown, fallback: boolean): boolean {
  if (typeof value === "boolean") return value;
  if (value === "true") return true;
  if (value === "false") return false;
  return fallback;
}

function str(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : fallback;
}

function int(value: unknown, fallback: number, min: number, max: number): number {
  const n = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(n)));
}

/** Turn whatever the host handed us into a complete, in-range config. */
export function resolveConfig(raw: unknown): PrecedentConfig {
  const source = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const blockOn = source.blockOn === "warn" ? "warn" : "critical";
  return {
    enabled: bool(source.enabled, DEFAULT_CONFIG.enabled),
    pythonBin: str(source.pythonBin, DEFAULT_CONFIG.pythonBin),
    precedentBin: str(source.precedentBin, DEFAULT_CONFIG.precedentBin),
    failClosed: bool(source.failClosed, DEFAULT_CONFIG.failClosed),
    timeoutMs: int(source.timeoutMs, DEFAULT_CONFIG.timeoutMs, 1_000,
                   MAX_REGISTRATION_TIMEOUT_MS - REGISTRATION_HEADROOM_MS),
    blockOn,
    maxBundleBytes: int(source.maxBundleBytes, DEFAULT_CONFIG.maxBundleBytes,
                        1_024, Number.MAX_SAFE_INTEGER),
  };
}

export interface ResolvedTimeouts {
  /** Hard wall-clock limit on the grading subprocess. */
  childTimeoutMs: number;
  /** What we ask OpenClaw to wait, via the registration's `timeoutMs`. */
  registrationTimeoutMs: number;
}

/**
 * Two timeouts, and the gap between them is load-bearing.
 *
 * If OpenClaw's hook timeout fired at the same moment as ours, the two would
 * race — and OpenClaw winning means an *attributed error outcome*, which does
 * not block. The proposal would then land unreviewed precisely because our
 * grader was slow, which is the one failure this plugin exists to prevent. So
 * the subprocess is always given strictly less time than the host is asked for,
 * leaving room for our own fail-closed document to be built and returned.
 */
export function resolveTimeouts(config: PrecedentConfig): ResolvedTimeouts {
  const ceiling = MAX_REGISTRATION_TIMEOUT_MS - REGISTRATION_HEADROOM_MS;
  const childTimeoutMs = Math.min(Math.max(config.timeoutMs, 1_000), ceiling);
  return {
    childTimeoutMs,
    registrationTimeoutMs: childTimeoutMs + REGISTRATION_HEADROOM_MS,
  };
}
