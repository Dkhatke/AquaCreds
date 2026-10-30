import createClient, { type Client } from "openapi-fetch";
import { ApiError, unwrap } from "./errors";
import { memoryTokenStore, type TokenSet, type TokenStore } from "./tokens";
import type {
  AlertsQuery,
  EvidencePointsQuery,
  MeasurementCreate,
  MeasurementsQuery,
  MfaChallenge,
  ModelFamily,
  ProjectCreate,
  ProjectListQuery,
  ProjectUpdate,
  paths,
  Schema,
  SubmissionCreate,
  SubmissionsQuery,
  TimeseriesQuery,
  TokenPair,
} from "./types";
import { presignedUpload, type Sha256Fn, type UploadBody } from "./upload";

export type LogoutReason = "user" | "refresh_failed" | "no_refresh_token";

export interface AquaClientOptions {
  /** API origin, e.g. `https://api.aquacreds.in` or `http://localhost:8000` (no `/api/v1`). */
  baseUrl: string;
  tokenStore?: TokenStore;
  /** Called after tokens are cleared, so the app can route to its sign-in screen. */
  onLogout?: (reason: LogoutReason) => void;
  /** Called whenever a new token pair is stored (login or refresh). */
  onTokens?: (tokens: TokenSet) => void;
  /** Base fetch (defaults to `globalThis.fetch`); used for API calls and storage uploads. */
  fetch?: typeof fetch;
  /** SHA-256 implementation for uploads (defaults to SubtleCrypto). */
  sha256?: Sha256Fn;
}

const AUTH_PATH = "/api/v1/auth/";
// A 401 from these means bad credentials or an expired MFA challenge, never an expired session.
const NO_REFRESH = ["login", "refresh", "register", "logout", "mfa/verify"].map(
  (p) => `${AUTH_PATH}${p}`,
);

/** Options for create calls the field app retries (see `Idempotency-Key` in the API docs). */
export interface IdempotentOptions {
  /** A UUID per logical request, persisted with the queued item: a retry within 24 h with the
   *  same key returns the original response instead of a 409. */
  idempotencyKey?: string;
}

const idemHeader = (o?: IdempotentOptions) =>
  o?.idempotencyKey ? { header: { "Idempotency-Key": o.idempotencyKey } } : {};

function toTokenSet(pair: TokenPair): TokenSet {
  return { accessToken: pair.access_token, refreshToken: pair.refresh_token };
}

/**
 * Typed AquaCreds client. `api` is the raw openapi-fetch client (every endpoint, fully typed);
 * the grouped helpers cover the main flows and throw `ApiError` on non-2xx responses.
 */
export function createAquaClient(options: AquaClientOptions) {
  const baseUrl = options.baseUrl.replace(/\/+$/, "");
  const store = options.tokenStore ?? memoryTokenStore();
  const baseFetch: typeof fetch = options.fetch ?? ((input, init) => globalThis.fetch(input, init));

  async function storeTokens(pair: TokenPair): Promise<TokenSet> {
    const tokens = toTokenSet(pair);
    await store.set(tokens);
    options.onTokens?.(tokens);
    return tokens;
  }

  async function forceLogout(reason: LogoutReason): Promise<void> {
    await store.clear();
    options.onLogout?.(reason);
  }

  // ------------------------------------------------------------- single-flight refresh
  // Refresh tokens rotate and a replayed one revokes the whole family server-side, so concurrent
  // 401s must share one refresh call. Resolves to the new access token, or null if logged out.
  let inflight: Promise<string | null> | null = null;

  async function doRefresh(): Promise<string | null> {
    const current = await store.get();
    if (!current?.refreshToken) {
      await forceLogout("no_refresh_token");
      return null;
    }
    const res = await baseFetch(`${baseUrl}${AUTH_PATH}refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: current.refreshToken }),
    });
    if (res.ok) {
      const tokens = await storeTokens((await res.json()) as TokenPair);
      return tokens.accessToken;
    }
    if (res.status >= 500) {
      // Server trouble is not proof the session is dead: keep tokens, surface the error.
      throw new ApiError(res.status, await res.json().catch(() => null), "Token refresh failed");
    }
    await forceLogout("refresh_failed");
    return null;
  }

  function refreshOnce(): Promise<string | null> {
    inflight ??= doRefresh().finally(() => {
      inflight = null;
    });
    return inflight;
  }

  function withBearer(request: Request, token: string | undefined): Request {
    const next = new Request(request);
    if (token) next.headers.set("Authorization", `Bearer ${token}`);
    return next;
  }

  async function authFetch(request: Request): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (NO_REFRESH.includes(path)) return baseFetch(request);

    const sentWith = (await store.get())?.accessToken;
    const retry = request.clone(); // body is consumed by the first attempt
    const res = await baseFetch(withBearer(request, sentWith));
    if (res.status !== 401) return res;

    const now = await store.get();
    if (!now) return res; // anonymous caller: nothing to refresh or log out
    // Another request may already have refreshed while this one was in flight.
    const latest = now.accessToken;
    const token = latest && latest !== sentWith ? latest : await refreshOnce();
    if (!token) return res;
    return baseFetch(withBearer(retry, token));
  }

  const api: Client<paths> = createClient<paths>({ baseUrl, fetch: authFetch });

  // ------------------------------------------------------------- uploads
  function upload<T>(
    requestTicket: () => Promise<Schema<"UploadTicket">>,
    body: UploadBody,
    confirm: (r: { objectKey: string; sha256: string }) => Promise<T>,
    signal?: AbortSignal,
  ): Promise<T> {
    return presignedUpload({
      requestTicket,
      body,
      confirm,
      fetch: baseFetch,
      ...(options.sha256 ? { sha256: options.sha256 } : {}),
      ...(signal ? { signal } : {}),
    });
  }

  const pid = (project_id: string) => ({ params: { path: { project_id } } });
  const perid = (period_id: string) => ({ params: { path: { period_id } } });

  return {
    api,
    tokens: store,
    /** Single-flight refresh; resolves to the new access token or null if the session ended. */
    refresh: refreshOnce,

    auth: {
      /**
       * Password step. Returns the stored token pair, or an `MfaChallenge` (no tokens stored) when
       * the account has two-factor auth: then call `verifyMfa(challenge.mfa_token, code)`.
       */
      async login(email: string, password: string): Promise<TokenPair | MfaChallenge> {
        const res = await unwrap(api.POST("/api/v1/auth/login", { body: { email, password } }));
        if ("mfa_required" in res) return res;
        await storeTokens(res);
        return res;
      },
      /** Second step: a TOTP or recovery code for the challenge from `login`. Stores the pair. */
      async verifyMfa(mfaToken: string, code: string): Promise<TokenPair> {
        const pair = await unwrap(
          api.POST("/api/v1/auth/mfa/verify", { body: { mfa_token: mfaToken, code } }),
        );
        await storeTokens(pair);
        return pair;
      },
      mfaStatus: () => unwrap(api.GET("/api/v1/auth/mfa")),
      register: (body: Schema<"RegisterIn">) => unwrap(api.POST("/api/v1/auth/register", { body })),
      me: () => unwrap(api.GET("/api/v1/auth/me")),
      /** Revokes the refresh-token family server-side (best effort), then clears local tokens. */
      async logout(): Promise<void> {
        const current = await store.get();
        try {
          if (current?.refreshToken) {
            await api.POST("/api/v1/auth/logout", {
              body: { refresh_token: current.refreshToken },
            });
          }
        } finally {
          await forceLogout("user");
        }
      },
    },

    projects: {
      list: (query: ProjectListQuery = {}) =>
        unwrap(api.GET("/api/v1/projects", { params: { query } })),
      get: (projectId: string) => unwrap(api.GET("/api/v1/projects/{project_id}", pid(projectId))),
      create: (body: ProjectCreate) => unwrap(api.POST("/api/v1/projects", { body })),
      update: (projectId: string, body: ProjectUpdate) =>
        unwrap(api.PATCH("/api/v1/projects/{project_id}", { ...pid(projectId), body })),
      submit: (projectId: string) =>
        unwrap(api.POST("/api/v1/projects/{project_id}/submit", pid(projectId))),
      /** Presigned upload of a tenure document, then attach it to the project. */
      uploadDocument: (
        projectId: string,
        file: UploadBody,
        meta: Schema<"UploadRequest">,
        signal?: AbortSignal,
      ) =>
        upload(
          () =>
            unwrap(
              api.POST("/api/v1/projects/{project_id}/documents/upload-url", {
                ...pid(projectId),
                body: meta,
              }),
            ),
          file,
          ({ objectKey, sha256 }) =>
            unwrap(
              api.POST("/api/v1/projects/{project_id}/documents", {
                ...pid(projectId),
                body: { object_key: objectKey, sha256, kind: meta.kind, filename: meta.filename },
              }),
            ),
          signal,
        ),
    },

    field: {
      listPeriods: (projectId: string) =>
        unwrap(api.GET("/api/v1/projects/{project_id}/periods", pid(projectId))),
      createPeriod: (projectId: string, body: Schema<"PeriodCreate">) =>
        unwrap(api.POST("/api/v1/projects/{project_id}/periods", { ...pid(projectId), body })),
      /** Single-use capture nonce; request it when the camera opens, before the shot. */
      issueNonce: (periodId: string) =>
        unwrap(api.POST("/api/v1/periods/{period_id}/nonces", perid(periodId))),
      /**
       * Pre-issue up to 20 single-use nonces for offline capture (valid 72 h by default, bound
       * to this user and period). Photos taken with one score a lower-weight nonce check.
       */
      issueNonceBatch: (periodId: string, count = 10) =>
        unwrap(
          api.POST("/api/v1/periods/{period_id}/nonces/batch", {
            ...perid(periodId),
            body: { count },
          }),
        ),
      photoUploadUrl: (periodId: string, contentType: "image/jpeg" | "image/png") =>
        unwrap(
          api.POST("/api/v1/periods/{period_id}/photos/upload-url", {
            ...perid(periodId),
            body: { content_type: contentType },
          }),
        ),
      createSubmission: (periodId: string, body: SubmissionCreate, options?: IdempotentOptions) =>
        unwrap(
          api.POST("/api/v1/periods/{period_id}/submissions", {
            params: { path: { period_id: periodId }, ...idemHeader(options) },
            body,
          }),
        ),
      /** Presigned photo upload, then create the geotagged submission with the hash. */
      uploadPhotoSubmission: (
        periodId: string,
        file: UploadBody,
        contentType: "image/jpeg" | "image/png",
        meta: Omit<SubmissionCreate, "object_key" | "sha256">,
        signal?: AbortSignal,
        options?: IdempotentOptions,
      ) =>
        upload(
          () =>
            unwrap(
              api.POST("/api/v1/periods/{period_id}/photos/upload-url", {
                ...perid(periodId),
                body: { content_type: contentType },
              }),
            ),
          file,
          ({ objectKey, sha256 }) =>
            unwrap(
              api.POST("/api/v1/periods/{period_id}/submissions", {
                params: { path: { period_id: periodId }, ...idemHeader(options) },
                body: { ...meta, object_key: objectKey, sha256 },
              }),
            ),
          signal,
        ),
      getSubmission: (submissionId: string) =>
        unwrap(
          api.GET("/api/v1/submissions/{submission_id}", {
            params: { path: { submission_id: submissionId } },
          }),
        ),
      /** One page (default 100, max 500) in arrival order; filter by `sha256` to find a photo. */
      listSubmissions: (periodId: string, query: SubmissionsQuery = {}) =>
        unwrap(
          api.GET("/api/v1/periods/{period_id}/submissions", {
            params: { path: { period_id: periodId }, query },
          }),
        ),
      /**
       * Field photos as GeoJSON map points. `snapped` (default): plot centre or a ~0.5 km grid
       * cell, never the photo GPS. `raw` is for the field team and staff only (403 otherwise).
       */
      evidencePoints: (projectId: string, query: EvidencePointsQuery = {}) =>
        unwrap(
          api.GET("/api/v1/projects/{project_id}/evidence-points", {
            params: { path: { project_id: projectId }, query },
          }),
        ),
      listPlots: (projectId: string) =>
        unwrap(api.GET("/api/v1/projects/{project_id}/plots", pid(projectId))),
      recordMeasurement: (
        periodId: string,
        plotId: string,
        body: MeasurementCreate,
        options?: IdempotentOptions,
      ) =>
        unwrap(
          api.POST("/api/v1/periods/{period_id}/plots/{plot_id}/measurements", {
            params: { path: { period_id: periodId, plot_id: plotId }, ...idemHeader(options) },
            body,
          }),
        ),
      /** Measurements recorded in a period (optionally `{ plot_id }`), oldest first. */
      listMeasurements: (periodId: string, query: MeasurementsQuery = {}) =>
        unwrap(
          api.GET("/api/v1/periods/{period_id}/measurements", {
            params: { path: { period_id: periodId }, query },
          }),
        ),
    },

    public: {
      /** Landing-page aggregates (projects, area, credits, indexer freshness) in one call. */
      stats: () => unwrap(api.GET("/api/v1/public/stats")),
    },

    models: {
      list: () => unwrap(api.GET("/api/v1/models")),
      family: (family: ModelFamily) =>
        unwrap(api.GET("/api/v1/models/{family}", { params: { path: { family } } })),
      /** Held-out metrics + model card of one version, e.g. a classification's `model_version`. */
      version: (family: ModelFamily, version: string) =>
        unwrap(
          api.GET("/api/v1/models/{family}/{version}", { params: { path: { family, version } } }),
        ),
    },

    analysis: {
      timeseries: (projectId: string, query: TimeseriesQuery = {}) =>
        unwrap(
          api.GET("/api/v1/projects/{project_id}/timeseries", {
            params: { path: { project_id: projectId }, query },
          }),
        ),
      alerts: (projectId: string, query: AlertsQuery = {}) =>
        unwrap(
          api.GET("/api/v1/projects/{project_id}/alerts", {
            params: { path: { project_id: projectId }, query },
          }),
        ),
      acknowledgeAlert: (alertId: string) =>
        unwrap(
          api.POST("/api/v1/alerts/{alert_id}/acknowledge", {
            params: { path: { alert_id: alertId } },
          }),
        ),
      runs: (projectId: string) =>
        unwrap(api.GET("/api/v1/projects/{project_id}/analysis-runs", pid(projectId))),
    },

    chain: {
      status: () => unwrap(api.GET("/api/v1/chain/status")),
      project: (projectId: string) =>
        unwrap(api.GET("/api/v1/projects/{project_id}/chain", pid(projectId))),
      transaction: (txId: string) =>
        unwrap(
          api.GET("/api/v1/chain/transactions/{tx_id}", { params: { path: { tx_id: txId } } }),
        ),
    },
  };
}

export type AquaClient = ReturnType<typeof createAquaClient>;
