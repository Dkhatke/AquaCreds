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
/** Explore Demo: a read-only demo session (Bearer `access_token`, no refresh token). */
export type DemoSessionOut = Schema<"DemoSessionOut">;
export type DemoStatusOut = Schema<"DemoStatusOut">;
export type ListingOut = Schema<"ListingOut">;
/** TileJSON 3.0 for a classification layer; `tiles` are signed URLs valid until `expires_at`. */
export type TileJSON = Schema<"TileJSON">;
export type ClassificationLegend = Schema<"ClassificationLegend">;

export type PublicStats = Schema<"PublicStats">;
export type ModelCardSummary = Schema<"ModelCardSummary">;
export type ModelFamilyOut = Schema<"ModelFamilyOut">;
export type ModelFamily = paths["/api/v1/models/{family}"]["get"]["parameters"]["path"]["family"];
/** GeoJSON FeatureCollection of field photos for the map (snapped unless `precision=raw`). */
export type EvidencePoints = Schema<"EvidencePoints">;
export type NonceOut = Schema<"NonceOut">;
export type NonceBatchOut = Schema<"NonceBatchOut">;
export type MeasurementCreate = Schema<"MeasurementCreate">;
export type MeasurementOut = Schema<"MeasurementOut">;
export type PlotOut = Schema<"PlotOut">;

export type ProjectListQuery = GetQuery<"/api/v1/projects">;
export type SubmissionsQuery = GetQuery<"/api/v1/periods/{period_id}/submissions">;
export type EvidencePointsQuery = GetQuery<"/api/v1/projects/{project_id}/evidence-points">;
export type MeasurementsQuery = GetQuery<"/api/v1/periods/{period_id}/measurements">;
export type TimeseriesQuery = GetQuery<"/api/v1/projects/{project_id}/timeseries">;
export type AlertsQuery = GetQuery<"/api/v1/projects/{project_id}/alerts">;
export type TileJSONQuery = GetQuery<"/api/v1/projects/{project_id}/tilejson">;
