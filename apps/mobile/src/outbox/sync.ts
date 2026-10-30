/**
 * Sync engine: drains the outbox through the API, one item at a time (low-end phones, thin
 * coastal bandwidth). Platform specifics (file access, hashing) are injected so the engine runs in
 * unit tests against the real `@aquacreds/api-client` with a mocked fetch.
 *
 * Integrity: the photo's SHA-256 is computed once from the file bytes right after capture. Before
 * upload the bytes are re-read and re-hashed; on mismatch the item fails (the file changed on the
 * device) and nothing is sent. The exact verified bytes are what is PUT to storage, and the server
 * re-hashes the stored object and compares (`create_submission`).
 */
import { ApiError, type AquaClient, type SubmissionOut, unwrap } from "@aquacreds/api-client";
import { t } from "../i18n";
import { classifyError, LocalItemError } from "./machine";
import type { OutboxStore } from "./store";
import type { CheckResult, MeasurementItem, OutboxItem, PhotoItem, ServerResult } from "./types";

export interface SyncDeps {
  client: AquaClient;
  store: OutboxStore;
  /** Read a file's bytes; throw `LocalItemError` if it is gone. */
  readFile: (uri: string) => Promise<Uint8Array>;
  sha256: (bytes: Uint8Array) => Promise<string>;
  deleteFile: (uri: string) => Promise<void>;
  now: () => number;
}

export interface SyncSummary {
  sent: number;
  retrying: number;
  failed: number;
  checked: number;
  /** The session ended: the user must sign in again before anything else can sync. */
  needsSignIn: boolean;
}

const OUTCOMES = new Set(["pass", "warn", "fail", "skipped"]);

function toChecks(raw: SubmissionOut["checks"]): CheckResult[] {
  if (!raw) return [];
  return raw.map((c) => ({
    name: typeof c["name"] === "string" ? c["name"] : "unknown",
    outcome: (OUTCOMES.has(String(c["outcome"]))
      ? c["outcome"]
      : "skipped") as CheckResult["outcome"],
    reason: typeof c["reason"] === "string" ? c["reason"] : "",
    hard: c["hard"] === true,
  }));
}

export function submissionResult(sub: SubmissionOut): ServerResult {
  return { status: sub.status, trustScore: sub.trust_score, checks: toChecks(sub.checks) };
}

const NONCE_USED = "Capture nonce already used";

async function sendPhoto(deps: SyncDeps, item: PhotoItem): Promise<SubmissionOut> {
  const { payload } = item;
  const bytes = await deps.readFile(payload.fileUri);
  const hash = await deps.sha256(bytes);
  if (hash !== payload.sha256) throw new LocalItemError(t("errors.fileChanged"));
  try {
    return await deps.client.field.uploadPhotoSubmission(
      item.periodId,
      bytes,
      payload.contentType,
      payload.meta,
    );
  } catch (err) {
    // A previous attempt may have succeeded server-side with the response lost (signal dropped).
    // The nonce is then consumed; find our submission by its hash instead of failing.
    if (err instanceof ApiError && err.status === 409 && err.detail === NONCE_USED) {
      // Filter by hash server-side: the list is paginated (100 per page by default).
      const mine = (
        await deps.client.field.listSubmissions(item.periodId, { sha256: payload.sha256 })
      ).find((s) => s.sha256 === payload.sha256);
      if (mine) return mine;
      throw new LocalItemError(t("errors.nonceUsed"));
    }
    throw err;
  }
}

async function sendMeasurement(deps: SyncDeps, item: MeasurementItem): Promise<string> {
  const m = await unwrap(
    deps.client.api.POST("/api/v1/periods/{period_id}/plots/{plot_id}/measurements", {
      params: { path: { period_id: item.periodId, plot_id: item.payload.plotId } },
      body: item.payload.body,
    }),
  );
  return m.id;
}

async function finish(deps: SyncDeps, item: OutboxItem): Promise<void> {
  if (item.kind === "photo" && item.status === "done") {
    // The server holds the photo and its hash now; free the phone's storage.
    await deps.deleteFile(item.payload.fileUri).catch(() => undefined);
  }
}

export class SyncEngine {
  private running: Promise<SyncSummary> | null = null;

  constructor(private readonly deps: SyncDeps) {}

  /** Run one pass. Concurrent callers share the pass already in flight. */
  sync(): Promise<SyncSummary> {
    this.running ??= this.pass().finally(() => {
      this.running = null;
    });
    return this.running;
  }

  private async pass(): Promise<SyncSummary> {
    const { store } = this.deps;
    const summary: SyncSummary = {
      sent: 0,
      retrying: 0,
      failed: 0,
      checked: 0,
      needsSignIn: false,
    };

    for (const due of await store.due(this.deps.now())) {
      let item = await store.apply(due, { type: "start" });
      try {
        if (item.kind === "photo") {
          const sub = await sendPhoto(this.deps, item);
          item = await store.apply(item, {
            type: "accepted",
            serverId: sub.id,
            result: submissionResult(sub),
          });
        } else {
          const id = await sendMeasurement(this.deps, item);
          item = await store.apply(item, {
            type: "accepted",
            serverId: id,
            result: { status: "recorded", trustScore: null, checks: [] },
          });
        }
        summary.sent++;
        await finish(this.deps, item);
      } catch (err) {
        const failure = classifyError(err);
        item = await store.apply(item, { type: "failed", failure });
        if (failure.kind === "auth") {
          summary.needsSignIn = true;
          return summary;
        }
        if (failure.kind === "retryable") summary.retrying++;
        else summary.failed++;
      }
    }

    for (const waiting of await store.awaitingChecks()) {
      if (!waiting.serverId) continue;
      try {
        const sub = await this.deps.client.field.getSubmission(waiting.serverId);
        const item = await store.apply(waiting, { type: "checked", result: submissionResult(sub) });
        if (item.status === "done") {
          summary.checked++;
          await finish(this.deps, item);
        }
      } catch (err) {
        if (classifyError(err).kind === "auth") {
          summary.needsSignIn = true;
          return summary;
        }
        // Polling is best effort; the next pass asks again.
      }
    }
    return summary;
  }
}
