import { z } from "zod";

/*
 * Contracts that other workers are adding to the API in parallel; they are not in
 * `@aquacreds/api-client` yet. Responses are validated at runtime so a contract change fails
 * loudly instead of rendering garbage. TODO: replace with `Schema<"…">` types once the client is
 * regenerated with these routes (see PR description, "API gaps").
 */

/** `POST /api/v1/demo/session` */
export const DemoSession = z.object({
  access_token: z.string().min(1),
  expires_in: z.number().int().positive(),
  demo: z.literal(true),
  org_id: z.string(),
  project_ids: z.array(z.string()),
  banner: z.string(),
});
export type DemoSession = z.infer<typeof DemoSession>;

/** `GET /api/v1/demo/status` */
export const DemoStatus = z.object({
  enabled: z.boolean(),
  banner: z.string().nullish(),
});
export type DemoStatus = z.infer<typeof DemoStatus>;

/** `GET /api/v1/projects/{id}/tilejson?layer=classification&cid=…` (TileJSON 3.0, signed URLs). */
export const TileJson = z.object({
  tilejson: z.string(),
  tiles: z.array(z.url()).min(1),
  minzoom: z.number().optional(),
  maxzoom: z.number().optional(),
  bounds: z.tuple([z.number(), z.number(), z.number(), z.number()]).optional(),
  attribution: z.string().optional(),
  name: z.string().optional(),
});
export type TileJson = z.infer<typeof TileJson>;
