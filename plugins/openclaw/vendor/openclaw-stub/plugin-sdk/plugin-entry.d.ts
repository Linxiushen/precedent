// Copyright 2026 The Precedent authors.
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0
// SPDX-License-Identifier: Apache-2.0
//
// The slice of `openclaw/plugin-sdk/plugin-entry` that this plugin uses,
// transcribed from OpenClaw main (2026-09-16). Development only: inside a real
// OpenClaw the host's own module is resolved instead, and any drift between
// the two shows up here as a type error.

export interface OpenClawHookOptions {
  /** Stable across restarts. Evaluator registrations run concurrently and
   *  OpenClaw attributes each outcome by this id. */
  registrationId: string;
  /** How long OpenClaw waits for this handler. Default hook timeout: 120 s. */
  timeoutMs?: number;
}

export interface OpenClawLogger {
  debug?(message: string, meta?: unknown): void;
  info?(message: string, meta?: unknown): void;
  warn?(message: string, meta?: unknown): void;
  error?(message: string, meta?: unknown): void;
}

export interface OpenClawPluginApi {
  on<TEvent = unknown, TResult = unknown>(
    event: string,
    handler: (event: TEvent) => TResult | Promise<TResult>,
    options?: OpenClawHookOptions,
  ): unknown;
  readonly logger?: OpenClawLogger;
  /** Resolved plugin config, per the manifest's configSchema. */
  readonly config?: unknown;
  getConfig?(key?: string): unknown;
  readonly pluginId?: string;
}

export interface OpenClawPluginEntry {
  activate(api: OpenClawPluginApi): void | Promise<void>;
  deactivate?(): void | Promise<void>;
}

export declare function definePluginEntry(entry: OpenClawPluginEntry): OpenClawPluginEntry;
