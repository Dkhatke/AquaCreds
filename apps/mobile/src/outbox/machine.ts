/**
 * Outbox state machine (pure). Every status change goes through `transition`, so the rules live in
 * one tested place; the store persists the result and the sync engine produces the events.
 */
import { ApiError, UploadError } from "@aquacreds/api-client";
import type { OutboxItem, OutboxStatus, ServerResult } from "./types";

export const BACKOFF_BASE_MS = 15_000;
export const BACKOFF_MAX_MS = 30 * 60_000;

/** A failure, classified by what the outbox should do about it. */
export type SyncFailure =
  /** Offline, timeouts, 5xx, 429, storage hiccups: try again later. */
  | { kind: "retryable"; message: string }
  /** The server refused the content; retrying unchanged cannot succeed. */
  | { kind: "permanent"; message: string }
  /** The session ended (refresh failed): pause until the user signs in again. */
  | { kind: "auth"; message: string };

export type OutboxEvent =
  | { type: "start" }
  | { type: "accepted"; serverId: string; result: ServerResult }
  | { type: "checked"; result: ServerResult }
  | { type: "failed"; failure: SyncFailure }
  | { type: "interrupted" }
  | { type: "retry" };

export class InvalidTransition extends Error {
  override readonly name = "InvalidTransition";
  constructor(from: OutboxStatus, event: OutboxEvent["type"]) {
    super(`Outbox: cannot apply "${event}" in state "${from}"`);
  }
}

/** Exponential backoff with ±20% jitter: 15 s, 30 s, 60 s … capped at 30 min. */
export function backoffMs(attempts: number, random: () => number = Math.random): number {
  const exp = BACKOFF_BASE_MS * 2 ** Math.max(0, attempts - 1);
  const capped = Math.min(BACKOFF_MAX_MS, exp);
  const jitter = 1 + (random() * 0.4 - 0.2);
  return Math.round(Math.min(BACKOFF_MAX_MS, capped * jitter));
}

export function isDue(item: OutboxItem, now: number): boolean {
  return (
    item.status === "queued" ||
    (item.status === "retry_wait" && (item.nextAttemptAt === null || item.nextAttemptAt <= now))
  );
}

/** Server results that end the item's life in the outbox. */
export function isFinalResult(result: ServerResult): boolean {
  return result.status !== "pending_checks";
}

export function transition(
  item: OutboxItem,
  event: OutboxEvent,
  now: number,
  random: () => number = Math.random,
): OutboxItem {
  const base = { ...item, updatedAt: now };
  switch (event.type) {
    case "start":
      if (!isDue(item, now)) throw new InvalidTransition(item.status, event.type);
      return { ...base, status: "sending", attempts: item.attempts + 1, nextAttemptAt: null };

    case "accepted": {
      if (item.status !== "sending") throw new InvalidTransition(item.status, event.type);
      const final = item.kind === "measurement" || isFinalResult(event.result);
      return {
        ...base,
        status: final ? "done" : "awaiting_checks",
        serverId: event.serverId,
        result: event.result,
        lastError: null,
      };
    }

    case "checked":
      if (item.status !== "awaiting_checks") throw new InvalidTransition(item.status, event.type);
      return {
        ...base,
        status: isFinalResult(event.result) ? "done" : "awaiting_checks",
        result: event.result,
      };

    case "failed": {
      if (item.status !== "sending") throw new InvalidTransition(item.status, event.type);
      const { failure } = event;
      if (failure.kind === "retryable") {
        return {
          ...base,
          status: "retry_wait",
          lastError: failure.message,
          nextAttemptAt: now + backoffMs(item.attempts, random),
        };
      }
      if (failure.kind === "auth") {
        // Not the item's fault: don't count the attempt, wait for sign-in.
        return {
          ...base,
          status: "queued",
          attempts: Math.max(0, item.attempts - 1),
          lastError: failure.message,
        };
      }
      return { ...base, status: "failed", lastError: failure.message, nextAttemptAt: null };
    }

    case "interrupted":
      // The app died mid-send. Safe to resend: an unconsumed nonce can be reused, and a consumed
      // one is recovered by matching the SHA-256 (see sync.ts).
      if (item.status !== "sending") throw new InvalidTransition(item.status, event.type);
      return { ...base, status: "queued" };

    case "retry":
      if (item.status !== "failed" && item.status !== "retry_wait") {
        throw new InvalidTransition(item.status, event.type);
      }
      return { ...base, status: "queued", nextAttemptAt: null };
  }
}

/** HTTP statuses worth retrying unchanged. */
const RETRYABLE_HTTP = new Set([408, 425, 429, 500, 502, 503, 504]);

export function detailMessage(err: ApiError): string {
  const d = err.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((e) =>
        typeof e === "object" && e !== null && "msg" in e
          ? String((e as { msg: unknown }).msg)
          : "",
      )
      .filter(Boolean)
      .join("; ");
  }
  return err.message;
}

/** Errors raised on the device before anything reaches the network (missing/changed file). */
export class LocalItemError extends Error {
  override readonly name = "LocalItemError";
}

/** Map an exception from a sync attempt to what the outbox should do. */
export function classifyError(err: unknown): SyncFailure {
  if (err instanceof LocalItemError) return { kind: "permanent", message: err.message };
  if (err instanceof ApiError) {
    if (err.status === 401) return { kind: "auth", message: "Session expired" };
    if (RETRYABLE_HTTP.has(err.status) || err.status >= 500) {
      return { kind: "retryable", message: `Server error ${err.status}` };
    }
    return { kind: "permanent", message: detailMessage(err) || `HTTP ${err.status}` };
  }
  if (err instanceof UploadError) {
    // Presigned PUTs fail with 403 once the URL expires; a new ticket is issued on the next try.
    return { kind: "retryable", message: `Storage upload failed (HTTP ${err.status})` };
  }
  // fetch() rejects with TypeError when offline; AbortError on timeouts.
  const message = err instanceof Error ? err.message : String(err);
  return { kind: "retryable", message: message || "Network error" };
}
