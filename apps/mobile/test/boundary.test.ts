import { describe, expect, it } from "vitest";
import {
  type BoundaryGeometry,
  distanceToBoundaryM,
  geofence,
  parseBoundary,
  pointInBoundary,
} from "../src/geo/boundary";

// ~1.1 km square near Bhitarkanika with a 200 m square hole.
const square: BoundaryGeometry = {
  type: "Polygon",
  coordinates: [
    [
      [86.9, 20.7],
      [86.91, 20.7],
      [86.91, 20.71],
      [86.9, 20.71],
      [86.9, 20.7],
    ],
    [
      [86.904, 20.704],
      [86.906, 20.704],
      [86.906, 20.706],
      [86.904, 20.706],
      [86.904, 20.704],
    ],
  ],
};

const multi: BoundaryGeometry = {
  type: "MultiPolygon",
  coordinates: [
    square.type === "Polygon" ? square.coordinates : [],
    [
      [
        [87.0, 20.8],
        [87.01, 20.8],
        [87.005, 20.81],
        [87.0, 20.8],
      ],
    ],
  ],
};

describe("pointInBoundary", () => {
  it("finds points inside, outside and in holes", () => {
    expect(pointInBoundary(86.902, 20.702, square)).toBe(true);
    expect(pointInBoundary(86.92, 20.702, square)).toBe(false);
    expect(pointInBoundary(86.905, 20.705, square)).toBe(false); // in the hole
  });

  it("treats edges and vertices as covered (like ST_Covers)", () => {
    expect(pointInBoundary(86.9, 20.705, square)).toBe(true);
    expect(pointInBoundary(86.91, 20.71, square)).toBe(true);
    expect(pointInBoundary(86.904, 20.705, square)).toBe(true); // on the hole's edge
  });

  it("handles multipolygons, including a triangle part", () => {
    expect(pointInBoundary(87.005, 20.803, multi)).toBe(true);
    expect(pointInBoundary(87.0005, 20.809, multi)).toBe(false);
    expect(pointInBoundary(86.902, 20.702, multi)).toBe(true);
  });
});

describe("distance and geofence", () => {
  it("measures metres to the nearest edge", () => {
    // 0.001° of longitude at 20.7°N ≈ 104 m.
    const d = distanceToBoundaryM(86.911, 20.705, square);
    expect(d).toBeGreaterThan(100);
    expect(d).toBeLessThan(108);
  });

  it("mirrors the server thresholds: inside / near (accuracy + 10 m) / outside", () => {
    expect(geofence(86.902, 20.702, 5, square)).toEqual({ state: "inside" });
    const near = geofence(86.9101, 20.705, 5, square); // ~10 m outside, accuracy 5 → limit 15
    expect(near.state).toBe("near");
    const far = geofence(86.911, 20.705, 5, square);
    expect(far.state).toBe("outside");
    expect(geofence(86.911, 20.705, 100, square).state).toBe("near");
  });
});

describe("parseBoundary", () => {
  it("accepts GeoJSON polygons and rejects anything else", () => {
    expect(parseBoundary(square)).toEqual(square);
    expect(parseBoundary(multi)?.type).toBe("MultiPolygon");
    expect(parseBoundary({ type: "Point", coordinates: [1, 2] })).toBeNull();
    expect(
      parseBoundary({
        type: "Polygon",
        coordinates: [
          [
            [0, 0],
            [1, 1],
          ],
        ],
      }),
    ).toBeNull();
    expect(parseBoundary(null)).toBeNull();
    expect(parseBoundary("x")).toBeNull();
  });
});
