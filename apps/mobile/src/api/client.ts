import {
  type AquaClient,
  createAquaClient,
  type LogoutReason,
  unwrap,
} from "@aquacreds/api-client";
import { clockOffsetFromDateHeader, type IssuedNonce } from "../domain/capture";
import { sha256Hex } from "../platform/sha256";
import { secureTokenStore } from "../platform/tokens";
import { appFetch } from "./fetch";

export function createClient(
  baseUrl: string,
  onLogout: (reason: LogoutReason) => void,
): AquaClient {
  return createAquaClient({
    baseUrl,
    tokenStore: secureTokenStore(),
    fetch: appFetch,
    sha256: sha256Hex,
    onLogout,
  });
}

/**
 * Request a capture nonce and estimate the phone's clock offset from the response `Date` header
 * (the typed helper `field.issueNonce` hides the response, so this uses the raw client).
 */
export async function issueNonce(
  client: AquaClient,
  periodId: string,
  now: () => number = Date.now,
): Promise<IssuedNonce> {
  const started = now();
  const call = client.api.POST("/api/v1/periods/{period_id}/nonces", {
    params: { path: { period_id: periodId } },
  });
  const { response } = await call;
  const body = await unwrap(call);
  const offset = clockOffsetFromDateHeader(response.headers.get("date"), started, now());
  return { nonce: body.nonce, expiresAt: body.expires_at, clockOffsetMs: offset ?? 0 };
}
