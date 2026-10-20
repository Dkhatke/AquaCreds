/** A non-2xx API response. `detail` is FastAPI's `detail` field (string or validation errors). */
export class ApiError extends Error {
  override readonly name = "ApiError";
  readonly status: number;
  readonly detail: unknown;
  readonly body: unknown;

  constructor(status: number, body: unknown, message?: string) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? (body as { detail: unknown }).detail
        : body;
    super(
      message ?? `API ${status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`,
    );
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

/** The presigned PUT to object storage failed. */
export class UploadError extends Error {
  override readonly name = "UploadError";
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call;
  if (!response.ok) throw new ApiError(response.status, error);
  return data as T;
}
