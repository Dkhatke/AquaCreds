import { useId } from "react";

type Ring = [number, number][];

/** Outer rings of a GeoJSON Polygon / MultiPolygon, or null when the geometry is not usable. */
export function ringsOf(geometry: unknown): Ring[] | null {
  if (!geometry || typeof geometry !== "object") return null;
  const g = geometry as { type?: unknown; coordinates?: unknown };
  const polys =
    g.type === "Polygon"
      ? [g.coordinates]
      : g.type === "MultiPolygon" && Array.isArray(g.coordinates)
        ? g.coordinates
        : null;
  if (!polys) return null;
  const rings: Ring[] = [];
  for (const poly of polys) {
    const outer = Array.isArray(poly) ? poly[0] : null;
    if (!Array.isArray(outer)) return null;
    const ring: Ring = [];
    for (const pt of outer) {
      if (!Array.isArray(pt) || typeof pt[0] !== "number" || typeof pt[1] !== "number") return null;
      ring.push([pt[0], pt[1]]);
    }
    if (ring.length < 3) return null;
    rings.push(ring);
  }
  return rings.length ? rings : null;
}

/**
 * Plot of a project boundary (equirectangular, scaled by cos(latitude) so shapes are not
 * stretched). Drawn from the API's own geometry; no basemap, so no third-party requests.
 */
export function BoundaryThumb({
  geometry,
  name,
  width = 96,
  height = 64,
  className,
}: {
  geometry: unknown;
  name: string;
  width?: number;
  height?: number;
  className?: string;
}) {
  const titleId = useId();
  const rings = ringsOf(geometry);
  if (!rings) {
    return (
      <div
        className="flex items-center justify-center rounded-md border border-dashed border-line text-[10px] text-fg-muted"
        style={{ width, height }}
      >
        No boundary
      </div>
    );
  }
  const pts = rings.flat();
  const lons = pts.map((p) => p[0]);
  const lats = pts.map((p) => p[1]);
  const w = Math.min(...lons);
  const e = Math.max(...lons);
  const s = Math.min(...lats);
  const n = Math.max(...lats);
  const k = Math.cos((((s + n) / 2) * Math.PI) / 180);
  const spanX = Math.max((e - w) * k, 1e-9);
  const spanY = Math.max(n - s, 1e-9);
  const pad = 4;
  const scale = Math.min((width - 2 * pad) / spanX, (height - 2 * pad) / spanY);
  const ox = (width - spanX * scale) / 2;
  const oy = (height - spanY * scale) / 2;
  const d = rings
    .map(
      (ring) =>
        `${ring
          .map(
            ([lon, lat], i) =>
              `${i ? "L" : "M"}${(ox + (lon - w) * k * scale).toFixed(1)},${(oy + (n - lat) * scale).toFixed(1)}`,
          )
          .join("")}Z`,
    )
    .join("");
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-labelledby={titleId}
      className={className ?? "rounded-md border border-line bg-panel-raised"}
    >
      <title id={titleId}>{`Boundary of ${name}`}</title>
      <path
        d={d}
        fill="var(--color-lagoon-mint)"
        fillOpacity={0.28}
        stroke="var(--color-lagoon-mint)"
        strokeWidth={1.5}
        strokeLinejoin="round"
        fillRule="evenodd"
      />
    </svg>
  );
}
