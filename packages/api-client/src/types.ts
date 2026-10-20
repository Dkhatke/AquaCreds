import type { components, operations, paths } from "./schema";

export type { components, operations, paths };

/** A named schema from the API, e.g. `Schema<"ProjectOut">`. */
export type Schema<Name extends keyof components["schemas"]> = components["schemas"][Name];

type GetQuery<P extends keyof paths> = paths[P] extends {
  get: { parameters: { query?: infer Q } };
}
  ? NonNullable<Q>
  : never;

export type TokenPair = Schema<"TokenPair">;
/** Returned by login instead of tokens when the account has two-factor auth enabled. */
export type MfaChallenge = Schema<"MfaChallenge">;
export type MfaStatusOut = Schema<"MfaStatusOut">;
export type UserOut = Schema<"UserOut">;
export type ProjectOut = Schema<"ProjectOut">;
export type ProjectCreate = Schema<"ProjectCreate">;
export type ProjectUpdate = Schema<"ProjectUpdate">;
export type UploadTicket = Schema<"UploadTicket">;
export type SubmissionCreate = Schema<"SubmissionCreate">;
export type SubmissionOut = Schema<"SubmissionOut">;
export type TimeseriesOut = Schema<"TimeseriesOut">;
export type AlertOut = Schema<"AlertOut">;
export type ChainStatusOut = Schema<"ChainStatusOut">;

export type ProjectListQuery = GetQuery<"/api/v1/projects">;
export type TimeseriesQuery = GetQuery<"/api/v1/projects/{project_id}/timeseries">;
export type AlertsQuery = GetQuery<"/api/v1/projects/{project_id}/alerts">;
