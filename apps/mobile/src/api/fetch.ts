import { fetch as expoFetch } from "expo/fetch";

/**
 * The API client sends `Request` objects for API calls (global fetch handles those), and a plain
 * `(url, init)` PUT with a `Uint8Array` body for the presigned storage upload. React Native's
 * XHR-based global fetch cannot send typed-array bodies reliably, so binary bodies go through
 * `expo/fetch` (native, WinterCG-compliant), which sends the bytes as-is.
 */
export const appFetch: typeof fetch = (input, init) => {
  const body = init?.body;
  if (!(input instanceof Request) && (body instanceof Uint8Array || body instanceof ArrayBuffer)) {
    const url = input instanceof URL ? input.toString() : input;
    const { signal, headers, method } = init ?? {};
    return expoFetch(url, {
      method: method ?? "PUT",
      body,
      ...(headers ? { headers: headers as Record<string, string> } : {}),
      ...(signal ? { signal } : {}),
    }) as unknown as Promise<Response>;
  }
  return globalThis.fetch(input, init);
};
