import type { Schema, SubmissionCreate } from "@aquacreds/api-client";

export type OutboxKind = "photo" | "measurement";

/**
 * queued ──start──▶ sending ──accepted──▶ awaiting_checks ──checked──▶ done   (photo)
 *                      │                └──────────────(measurement)──▶ done
 *                      ├──error (retryable)──▶ retry_wait ──(due)──▶ sending
 *                      ├──error (permanent)──▶ failed ──retry──▶ queued
 *                      └──error (auth) / interrupted ──▶ queued
 */
export type OutboxStatus =
  | "queued"
  | "sending"
  | "retry_wait"
  | "awaiting_checks"
  | "done"
  | "failed";

export type PhotoContentType = "image/jpeg" | "image/png";
export type SubmissionMeta = Omit<SubmissionCreate, "object_key" | "sha256">;

export interface PhotoPayload {
  /** file:// URI in the app's document directory (moved there from the camera cache). */
  fileUri: string;
  contentType: PhotoContentType;
  /** SHA-256 (lowercase hex) of the exact file bytes, computed once right after capture. */
  sha256: string;
  sizeBytes: number;
  meta: SubmissionMeta;
}

export interface MeasurementPayload {
  plotId: string;
  plotCode: string;
  body: Schema<"MeasurementCreate">;
}

export interface CheckResult {
  name: string;
  outcome: "pass" | "warn" | "fail" | "skipped";
  reason: string;
  hard: boolean;
}

/** What the server said about the item (photo: the evidence checks; measurement: recorded). */
export interface ServerResult {
  status: "pending_checks" | "checked" | "rejected" | "recorded";
  trustScore: number | null;
  checks: CheckResult[];
}

interface ItemBase {
  id: string;
  projectId: string;
  periodId: string;
  status: OutboxStatus;
  attempts: number;
  /** Epoch ms when a `retry_wait` item becomes due. */
  nextAttemptAt: number | null;
  lastError: string | null;
  /** Server id (submission id / measurement id) once accepted. */
  serverId: string | null;
  result: ServerResult | null;
  createdAt: number;
  updatedAt: number;
}

export interface PhotoItem extends ItemBase {
  kind: "photo";
  payload: PhotoPayload;
}

export interface MeasurementItem extends ItemBase {
  kind: "measurement";
  payload: MeasurementPayload;
}

export type OutboxItem = PhotoItem | MeasurementItem;

export type NewOutboxItem =
  | { kind: "photo"; projectId: string; periodId: string; payload: PhotoPayload }
  | { kind: "measurement"; projectId: string; periodId: string; payload: MeasurementPayload };
