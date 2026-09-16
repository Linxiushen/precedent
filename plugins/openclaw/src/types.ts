// Copyright 2026 The Precedent authors.
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0
// SPDX-License-Identifier: Apache-2.0
//
// types.ts — the `skill_proposal_evaluate` seam, transcribed.
//
// These are OpenClaw's shapes, not ours: the event handed to an evaluator and
// the result document it returns. They are written down here once so that the
// rest of the package can be read without the host's source open beside it, and
// so that a change on the OpenClaw side shows up as a type error rather than as
// a field that quietly stopped being read.
//
// Verified against OpenClaw main, 2026-09-16.

/** One file inside a bundle. Text is UTF-8; anything binary is base64. */
export interface BundleFile {
  path: string;
  content: string;
  encoding: "utf8" | "base64";
  sha256: string;
  sizeBytes: number;
}

/** A complete skill at one revision: its SKILL.md, its other files, its digest. */
export interface BundleSnapshot {
  skillMd: BundleFile;
  files: BundleFile[];
  treeSha256: string;
}

export interface ProposalRef {
  id: string;
  kind: "create" | "update";
  revision: number;
  revisionSha256: string;
  targetCurrentSha256?: string;
}

export interface SkillRef {
  name: string;
  skillKey: string;
  description: string;
  source?: string;
}

/** The event OpenClaw hands every registered evaluator before an apply. */
export interface SkillProposalEvaluateEvent {
  correlationId?: string;
  proposal: ProposalRef;
  skill: SkillRef;
  candidate: BundleSnapshot;
  /** Present on update proposals; carries the complete current skill. */
  baseline?: BundleSnapshot;
  reason: "created" | "revised" | "manual" | "apply";
}

export type Severity = "info" | "warn" | "critical";

/** `pass` applies, `revise` sends it back, `block` vetoes the apply. */
export type Decision = "pass" | "revise" | "block";

export interface Finding {
  ruleId: string;
  severity: Severity;
  message: string;
  file?: string;
  line?: number;
}

/** The document an evaluator returns. Every field is optional to OpenClaw; we
 *  always fill `decision` and `decisionReason`, because a result without them
 *  is indistinguishable from an evaluator that did not really run. */
export interface EvaluatorResult {
  summary?: string;
  findings?: Finding[];
  metrics?: Record<string, string | number | boolean>;
  evaluatorVersion?: string;
  mode?: string;
  decision?: Decision;
  decisionReason?: string;
}

export const SEVERITY_RANK: Record<Severity, number> = {
  info: 0,
  warn: 1,
  critical: 2,
};

/** A structural log sink. The host's logger satisfies this; so does a no-op. */
export interface PluginLogger {
  debug?(message: string, meta?: unknown): void;
  info?(message: string, meta?: unknown): void;
  warn?(message: string, meta?: unknown): void;
  error?(message: string, meta?: unknown): void;
}
