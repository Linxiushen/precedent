// Copyright 2026 The Precedent authors.
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0
// SPDX-License-Identifier: Apache-2.0
//
// index.ts — the plugin entry: one registration, and the wiring around it.
//
// Everything that can fail lives in ./src/evaluate.ts, which does not throw.
// This file is deliberately thin, because it is the one part that runs inside
// OpenClaw's process and the one part the tests cannot fully isolate.

import {
  definePluginEntry,
  type OpenClawPluginApi,
} from "openclaw/plugin-sdk/plugin-entry";

import {
  REGISTRATION_HEADROOM_MS,
  resolveConfig,
  resolveTimeouts,
  type PrecedentConfig,
} from "./src/config.ts";
import {
  PLUGIN_VERSION,
  REGISTRATION_ID,
  evaluateProposal,
  internalErrorResult,
} from "./src/evaluate.ts";
import type {
  EvaluatorResult,
  PluginLogger,
  SkillProposalEvaluateEvent,
} from "./src/types.ts";

export const HOOK_NAME = "skill_proposal_evaluate";

export {
  PLUGIN_VERSION,
  REGISTRATION_ID,
  evaluateProposal,
} from "./src/evaluate.ts";

/**
 * Find the plugin's config on the api, whatever shape the host hands it in.
 *
 * Three shapes are tried because a config read must not be the thing that
 * breaks the gate: `api.config`, `api.getConfig("precedent")`, `api.getConfig()`,
 * and a `{ precedent: { … } }` wrapper is unwrapped. Anything unreadable falls
 * through to `resolveConfig`, which fills in every documented default.
 */
export function readPluginConfig(api: OpenClawPluginApi): PrecedentConfig {
  try {
    const candidates: unknown[] = [];
    if (api?.config !== undefined) candidates.push(api.config);
    if (typeof api?.getConfig === "function") {
      candidates.push(api.getConfig("precedent"));
      candidates.push(api.getConfig());
    }
    for (const candidate of candidates) {
      if (!candidate || typeof candidate !== "object") continue;
      const record = candidate as Record<string, unknown>;
      // A property read can itself throw on a host that backs its config with
      // getters, which is why the whole walk — not just the calls — is inside
      // the try. A config we cannot read is a config we fall back on.
      const nested = record.precedent;
      if (nested && typeof nested === "object" && !("enabled" in record)) {
        return resolveConfig(nested);
      }
      return resolveConfig(record);
    }
  } catch {
    /* a host that throws on a config read still gets a working evaluator */
  }
  return resolveConfig(undefined);
}

function loggerOf(api: OpenClawPluginApi): PluginLogger {
  // Never console: OpenClaw's stdout belongs to OpenClaw.
  return (api?.logger ?? {}) as PluginLogger;
}

/**
 * Register the evaluator. Exported so the tests can drive it with a fake api
 * and assert the registration itself, which is half the contract.
 */
export function activate(api: OpenClawPluginApi): void {
  const log = loggerOf(api);
  const bootConfig = readPluginConfig(api);
  const { registrationTimeoutMs } = resolveTimeouts(bootConfig);
  // The subprocess is never given the whole window: see resolveTimeouts. If
  // OpenClaw's hook timeout won the race, the outcome would be an attributed
  // error — which does not block — so our own deadline must always fire first.
  const childCeilingMs = registrationTimeoutMs - REGISTRATION_HEADROOM_MS;

  const handler = async (
    event: SkillProposalEvaluateEvent,
  ): Promise<EvaluatorResult> => {
    // This function is the one that runs inside OpenClaw, so this is the one
    // that must not throw: a thrown handler is an attributed error outcome,
    // and an attributed error outcome applies the proposal. `evaluateProposal`
    // already resolves in every case; the try is here for the two lines above
    // it, because a host whose config read explodes must not be the reason a
    // skill lands ungraded.
    try {
      // Re-read the config per proposal so a setting change takes effect
      // without a restart. Only the registration's own timeout is fixed at
      // activation, which is why the subprocess deadline is clamped to what we
      // asked for.
      const config = readPluginConfig(api);
      const childTimeoutMs = Math.min(
        resolveTimeouts(config).childTimeoutMs,
        childCeilingMs,
      );
      return await evaluateProposal(event, { config, logger: log, childTimeoutMs });
    } catch (error) {
      try {
        log.error?.(`precedent: the evaluator entry point failed: ${String(error)}`);
      } catch {
        /* a logger that throws does not get to decide the verdict either */
      }
      return internalErrorResult(error);
    }
  };

  api.on<SkillProposalEvaluateEvent, EvaluatorResult>(HOOK_NAME, handler, {
    registrationId: REGISTRATION_ID,
    timeoutMs: registrationTimeoutMs,
  });

  log.info?.(
    `precedent ${PLUGIN_VERSION}: registered "${REGISTRATION_ID}" on ${HOOK_NAME} ` +
      `(enabled=${bootConfig.enabled}, failClosed=${bootConfig.failClosed}, ` +
      `blockOn=${bootConfig.blockOn}, cli="${bootConfig.precedentBin}", ` +
      `subprocess deadline ${childCeilingMs} ms inside a ${registrationTimeoutMs} ms hook)`,
  );

  if (!bootConfig.enabled) {
    log.warn?.(
      "precedent: enabled=false — skill proposals will pass ungraded. This is not " +
        "the same as passing; nothing is being checked.",
    );
  }
  if (!bootConfig.failClosed) {
    log.warn?.(
      "precedent: failClosed=false — if the evaluator itself breaks (missing CLI, " +
        "crash, timeout) proposals will be sent back as `revise` rather than blocked.",
    );
  }
}

export default definePluginEntry({ activate });
