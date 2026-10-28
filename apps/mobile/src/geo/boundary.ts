/**
 * Local (offline) geofence for the capture screen. It only *warns* the field agent; the binding
 * check is the server's PostGIS geofence in the worker (services/worker/.../evidence.py
 * `check_geofence`), which this mirrors: inside → pass; outside by ≤ accuracy + 10 m → warn;
 * further → hard fail.
 */

export type Position = [lon: number, lat: number, ...rest: number[]];
export type Ring = Position[];
export type PolygonCoords = Ring[];

export interface PolygonGeometry {
  type: "Polygon";
  coordinates: PolygonCoords;
}
export interface MultiPolygonGeometry {
  type: "MultiPolygon";
  coordinates: PolygonCoords[];
}
export type BoundaryGeometry = PolygonGeometry | MultiPolygonGeometry;

/** Same constant as the server (`GEOFENCE_EXTRA_M`). */
export const GEOFENCE_EXTRA_M = 10;
const EARTH_RADIUS_M = 6_371_008.8;

function isPosition(v: unknown): v is Position {
  return (
    Array.isArray(v) &&
    v.length >= 2 &&
    typeof v[0] === "number" &&
    typeof v[1] === "number" &&
    Number.isFinite(v[0]) &&
    Number.isFinite(v[1])
  );
}

function isRing(v: unknown): v is Ring {
  return Array.isArray(v) && v.length >= 4 && v.every(isPosition);
}

function isPolygonCoords(v: unknown): v is PolygonCoords {
  return Array.isArray(v) && v.length >= 1 && v.every(isRing);
}

/** Narrow the API's untyped `boundary` object (GeoJSON) to a polygonal geometry, or null. */
export function parseBoundary(value: unknown): BoundaryGeometry | null {
  if (typeof value !== "object" || value === null) return null;
  const g = value as { type?: unknown; coordinates?: unknown };
  if (g.type === "Polygon" && isPolygonCoords(g.coordinates)) {
    return { type: "Polygon", coordinates: g.coordinates };
  }
  if (
    g.type === "MultiPolygon" &&
    Array.isArray(g.coordinates) &&
    g.coordinates.length >= 1 &&
    g.coordinates.every(isPolygonCoords)
  ) {
    return { type: "MultiPolygon", coordinates: g.coordinates as PolygonCoords[] };
  }
  return null;
}

/** Ray casting (even-odd). Points exactly on an edge count as inside (like PostGIS ST_Covers). */
export function pointInRing(lon: number, lat: number, ring: Ring): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i] as Position;
    const [xj, yj] = ring[j] as Position;
    if (onSegment(lon, lat, xi, yi, xj, yj)) return true;
    const crosses = yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi;
    if (crosses) inside = !inside;
  }
  return inside;
}

function onSegment(px: number, py: number, ax: number, ay: number, bx: number, by: number) {
  const cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax);
  if (Math.abs(cross) > 1e-12) return false;
  return (
    px >= Math.min(ax, bx) - 1e-12 &&
    px <= Math.max(ax, bx) + 1e-12 &&
    py >= Math.min(ay, by) - 1e-12 &&
    py <= Math.max(ay, by) + 1e-12
  );
}

function pointInPolygonCoords(lon: number, lat: number, polygon: PolygonCoords): boolean {
  const [outer, ...holes] = polygon;
  if (!outer || !pointInRing(lon, lat, outer)) return false;
  // On a hole's edge is still on the polygon's boundary, so covered.
  return !holes.some((hole) => pointInRing(lon, lat, hole) && !onRingEdge(lon, lat, hole));
}

function onRingEdge(lon: number, lat: number, ring: Ring): boolean {
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i] as Position;
    const [xj, yj] = ring[j] as Position;
    if (onSegment(lon, lat, xi, yi, xj, yj)) return true;
  }
  return false;
}

function polygons(geometry: BoundaryGeometry): PolygonCoords[] {
  return geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
}

export function pointInBoundary(lon: number, lat: number, geometry: BoundaryGeometry): boolean {
  return polygons(geometry).some((p) => pointInPolygonCoords(lon, lat, p));
}

/**
 * Approximate distance in metres from a point to the nearest boundary edge (local equirectangular
 * projection around the point; error well under 1% at project scales of a few km).
 */
export function distanceToBoundaryM(lon: number, lat: number, geometry: BoundaryGeometry): number {
  const kx = (Math.PI / 180) * EARTH_RADIUS_M * Math.cos((lat * Math.PI) / 180);
  const ky = (Math.PI / 180) * EARTH_RADIUS_M;
  let best = Number.POSITIVE_INFINITY;
  for (const polygon of polygons(geometry)) {
    for (const ring of polygon) {
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [x1, y1] = ring[j] as Position;
        const [x2, y2] = ring[i] as Position;
        const ax = (x1 - lon) * kx;
        const ay = (y1 - lat) * ky;
        const bx = (x2 - lon) * kx;
        const by = (y2 - lat) * ky;
        best = Math.min(best, distanceToSegment(ax, ay, bx, by));
      }
    }
  }
  return best;
}

/** Distance from the origin to segment AB. */
function distanceToSegment(ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const tRaw = len2 === 0 ? 0 : -(ax * dx + ay * dy) / len2;
  const t = Math.max(0, Math.min(1, tRaw));
  return Math.hypot(ax + t * dx, ay + t * dy);
}

export type GeofenceResult =
  | { state: "inside" }
  | { state: "near"; outsideM: number }
  | { state: "outside"; outsideM: number };

/** Mirror of the server's `check_geofence` thresholds. */
export function geofence(
  lon: number,
  lat: number,
  accuracyM: number,
  geometry: BoundaryGeometry,
): GeofenceResult {
  if (pointInBoundary(lon, lat, geometry)) return { state: "inside" };
  const outsideM = distanceToBoundaryM(lon, lat, geometry);
  return outsideM <= accuracyM + GEOFENCE_EXTRA_M
    ? { state: "near", outsideM }
    : { state: "outside", outsideM };
}
