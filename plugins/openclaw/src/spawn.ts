// Copyright 2026 The Precedent authors.
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0
// SPDX-License-Identifier: Apache-2.0
//
// spawn.ts — the process boundary, and every way it can go wrong.
//
// One function, `runCli`, and it resolves. It never rejects and it never leaves
// a child or a timer behind. Everything a caller needs to build a verdict out
// of a failed crossing — which failure, how long it took, what the child said
// on stderr — comes back in the resolved value.
//
// The four ways this crossing fails in the field, all of them handled here:
//   * the binary is not there            → "enoent"
//   * the binary is there but unusable   → "spawn-error"  (EACCES, EISDIR, …)
//   * the child never finishes           → "timeout"      (SIGTERM, then SIGKILL)
//   * the child talks forever            → "stdout-cap"
//   * the child dies but its stdout does not close, because something it
//     spawned inherited the pipe         → "timeout", forced (see forceSettle)
// plus the ordinary one, a non-zero exit, which for this CLI is itself a bug:
// `precedent evaluate-bundle` exits 0 for pass, revise and block alike.

import { spawn } from "node:child_process";
import { Buffer } from "node:buffer";

import { STDERR_TAIL_BYTES } from "./config.ts";

export type CliFailureKind =
  | "enoent"
  | "spawn-error"
  | "timeout"
  | "nonzero-exit"
  | "stdout-cap";

export interface CliRun {
  /** True only when the child exited 0 having written no more than the cap. */
  ok: boolean;
  failure?: CliFailureKind;
  stdout: string;
  stdoutBytes: number;
  /** Last few KB of the child's stderr, for the failure message. */
  stderrTail: string;
  exitCode: number | null;
  signal: NodeJS.Signals | null;
  /** libuv error code when the spawn itself failed (ENOENT, EACCES, …). */
  errorCode?: string;
  errorMessage?: string;
  elapsedMs: number;
  /** What we actually executed, for the diagnostic. */
  argv: string[];
}

export interface RunCliOptions {
  /** `[executable, ...args]`; the executable is PATH-resolved from `env`. */
  argv: string[];
  /** Written to the child's stdin, which is then closed. */
  input: string;
  timeoutMs: number;
  maxStdoutBytes: number;
  env?: NodeJS.ProcessEnv;
  cwd?: string;
  /** Grace between SIGTERM and SIGKILL when a timeout fires. */
  killGraceMs?: number;
}

/** Keep the tail, not the head: the useful part of stderr is the last thing
 *  said before the child gave up. */
function tail(chunks: Buffer[], limit: number): string {
  const joined = Buffer.concat(chunks);
  const slice = joined.length > limit ? joined.subarray(joined.length - limit) : joined;
  return slice.toString("utf8").trim();
}

/**
 * Run a command, feed it `input`, and come back with what happened.
 *
 * Resolves in every case. The only `throw` reachable from here would be a bug
 * in this function, and the caller wraps the call anyway.
 */
export function runCli(options: RunCliOptions): Promise<CliRun> {
  const [executable, ...args] = options.argv;
  const killGraceMs = options.killGraceMs ?? 2_000;
  const started = Date.now();

  return new Promise<CliRun>((resolve) => {
    let settled = false;
    let timedOut = false;
    let cappedOut = false;
    let stdoutBytes = 0;
    const stdoutChunks: Buffer[] = [];
    const stderrChunks: Buffer[] = [];
    let timer: NodeJS.Timeout | undefined;
    let killTimer: NodeJS.Timeout | undefined;
    let forceTimer: NodeJS.Timeout | undefined;

    const finish = (partial: Omit<CliRun, "elapsedMs" | "argv" | "stdout" | "stdoutBytes" | "stderrTail">) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (killTimer) clearTimeout(killTimer);
      if (forceTimer) clearTimeout(forceTimer);
      resolve({
        ...partial,
        stdout: Buffer.concat(stdoutChunks).toString("utf8"),
        stdoutBytes,
        stderrTail: tail(stderrChunks, STDERR_TAIL_BYTES),
        elapsedMs: Date.now() - started,
        argv: options.argv,
      });
    };

    let child;
    try {
      child = spawn(executable, args, {
        env: options.env ?? process.env,
        cwd: options.cwd,
        stdio: ["pipe", "pipe", "pipe"],
        // No shell. The bundle's own bytes are on stdin, never on a command
        // line, and nothing we run goes through an interpreter that could
        // expand them.
        shell: false,
      });
    } catch (error) {
      // spawn() can throw synchronously on a malformed executable argument.
      const err = error as NodeJS.ErrnoException;
      finish({
        ok: false,
        failure: err.code === "ENOENT" ? "enoent" : "spawn-error",
        exitCode: null,
        signal: null,
        errorCode: err.code,
        errorMessage: err.message,
      });
      return;
    }

    const stopChild = (signal: NodeJS.Signals) => {
      try {
        child.kill(signal);
      } catch {
        /* already gone */
      }
    };

    /**
     * Answer without the child's permission.
     *
     * "close" fires when the process has ended *and* its stdio has closed — so
     * a child that spawned something which inherited its stdout can be dead
     * while the pipe stays open, and this promise would never settle. The hook
     * would then run to OpenClaw's own timeout, and a hook timeout is an
     * attributed error outcome: it does NOT block. One leaked file descriptor
     * inside the grader would quietly disable the gate. So after the kill
     * there is a last deadline: let go of the pipes and answer anyway.
     */
    const forceSettle = (failure: CliFailureKind) => {
      try {
        child.stdout?.destroy();
        child.stderr?.destroy();
        child.stdin?.destroy();
        child.unref?.();
      } catch {
        /* nothing left to let go of */
      }
      finish({ ok: false, failure, exitCode: null, signal: null });
    };

    timer = setTimeout(() => {
      timedOut = true;
      stopChild("SIGTERM");
      // A child that ignores SIGTERM does not get to keep the slot.
      killTimer = setTimeout(() => {
        stopChild("SIGKILL");
        forceTimer = setTimeout(() => forceSettle("timeout"), killGraceMs);
        forceTimer.unref?.();
      }, killGraceMs);
      killTimer.unref?.();
    }, options.timeoutMs);
    timer.unref?.();

    child.stdout?.on("data", (chunk: Buffer) => {
      stdoutBytes += chunk.length;
      if (stdoutBytes > options.maxStdoutBytes) {
        if (!cappedOut) {
          cappedOut = true;
          stopChild("SIGKILL");
          // Same reasoning as the timeout path: a flood we killed still has to
          // produce an answer even if the pipe outlives the process.
          forceTimer = setTimeout(() => forceSettle("stdout-cap"), killGraceMs);
          forceTimer.unref?.();
        }
        return; // stop accumulating; we already know this is not a verdict
      }
      stdoutChunks.push(chunk);
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      stderrChunks.push(chunk);
      // Bound the retained stderr so a chatty child cannot grow the heap.
      if (stderrChunks.length > 64) {
        const merged = tail(stderrChunks, STDERR_TAIL_BYTES);
        stderrChunks.length = 0;
        stderrChunks.push(Buffer.from(merged, "utf8"));
      }
    });

    // If the child dies before reading its input, the write fails with EPIPE.
    // That is not an error worth reporting: the exit status is.
    child.stdin?.on("error", () => { /* EPIPE / ECONNRESET */ });
    try {
      child.stdin?.end(options.input);
    } catch {
      /* same */
    }

    child.on("error", (error: NodeJS.ErrnoException) => {
      finish({
        ok: false,
        failure: error.code === "ENOENT" ? "enoent" : "spawn-error",
        exitCode: null,
        signal: null,
        errorCode: error.code,
        errorMessage: error.message,
      });
    });

    // "close", not "exit": we want the pipes drained before we read them.
    child.on("close", (code, signal) => {
      if (cappedOut) {
        finish({ ok: false, failure: "stdout-cap", exitCode: code, signal });
      } else if (timedOut) {
        finish({ ok: false, failure: "timeout", exitCode: code, signal });
      } else if (code === 0) {
        finish({ ok: true, exitCode: code, signal });
      } else {
        finish({ ok: false, failure: "nonzero-exit", exitCode: code, signal });
      }
    });
  });
}
