import { ApiError } from "@aquacreds/api-client";

/**
 * The API reports problems in several shapes (`detail` is a string, a FastAPI validation array, or
 * an object such as `{code, message}`, `{code, issues[]}` or `{code, message, conflicts[]}`).
 * This flattens any of them to the messages the API actually sent, so forms can show them as is.
 */
export function problemMessages(err: unknown): string[] {
  if (!(err instanceof ApiError))
    return [err instanceof Error ? err.message : "Something went wrong"];
  const out: string[] = [];
  const visit = (d: unknown): void => {
    if (typeof d === "string") out.push(d);
    else if (Array.isArray(d)) {
      for (const item of d) {
        if (item && typeof item === "object" && "msg" in item) {
          const loc = "loc" in item && Array.isArray(item.loc) ? item.loc.slice(1).join(".") : "";
          out.push(`${loc ? `${loc}: ` : ""}${String(item.msg)}`);
        } else visit(item);
      }
    } else if (d && typeof d === "object") {
      const o = d as { message?: unknown; conflicts?: unknown; issues?: unknown };
      if (typeof o.message === "string") {
        const conflicts = Array.isArray(o.conflicts)
          ? o.conflicts
              .map((c) =>
                c && typeof c === "object" && "name" in c
                  ? `${String(c.name)} (${String((c as { overlap_ha?: unknown }).overlap_ha ?? "?")} ha overlap)`
                  : null,
              )
              .filter(Boolean)
          : [];
        out.push(conflicts.length ? `${o.message}: ${conflicts.join(", ")}` : o.message);
      }
      if (Array.isArray(o.issues)) visit(o.issues);
    }
  };
  visit(err.detail);
  return out.length ? out : [`Request failed (HTTP ${err.status})`];
}
