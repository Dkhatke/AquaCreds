import { ApiError } from "@aquacreds/api-client";
import { describe, expect, it } from "vitest";
import { ringsOf } from "@/components/registry/BoundaryThumb";
import { parseBoundary } from "@/components/registry/NewProjectForm";
import { problemMessages } from "./api-problems";
import { explorerTxUrl, explorerUrlFor, TX_STATUS } from "./chain-links";

describe("problemMessages", () => {
  it("passes through a string detail", () => {
    expect(problemMessages(new ApiError(403, { detail: "Organisation admin required" }))).toEqual([
      "Organisation admin required",
    ]);
  });
  it("lists submission issues verbatim", () => {
    const err = new ApiError(422, {
      detail: {
        code: "submission_blocked",
        issues: [
          { code: "a", message: "Required tenure document missing: land_title" },
          { code: "b", message: "Overlaps X by 1 ha" },
        ],
      },
    });
    expect(problemMessages(err)).toEqual([
      "Required tenure document missing: land_title",
      "Overlaps X by 1 ha",
    ]);
  });
  it("names the overlapping projects", () => {
    const err = new ApiError(409, {
      detail: {
        code: "boundary_overlap",
        message: "Boundary overlaps an existing project",
        conflicts: [{ project_id: "1", name: "Other", overlap_ha: 3.2 }],
      },
    });
    expect(problemMessages(err)[0]).toContain("Other (3.2 ha overlap)");
  });
  it("formats FastAPI validation arrays with the field", () => {
    const err = new ApiError(422, { detail: [{ loc: ["body", "name"], msg: "too short" }] });
    expect(problemMessages(err)).toEqual(["name: too short"]);
  });
  it("falls back to the status", () => {
    expect(problemMessages(new ApiError(500, {}))).toEqual(["Request failed (HTTP 500)"]);
  });
});

describe("chain links", () => {
  it("links the explorer only on Amoy", () => {
    expect(explorerTxUrl(80002, "0xabc")).toBe("https://amoy.polygonscan.com/tx/0xabc");
    expect(explorerTxUrl(31337, "0xabc")).toBeNull();
    expect(explorerUrlFor(31337, "https://example.test/tx/1")).toBeNull();
    expect(explorerUrlFor(80002, "https://example.test/tx/1")).toBe("https://example.test/tx/1");
  });
  it("covers every relayer status", () => {
    expect(Object.keys(TX_STATUS).sort()).toEqual(
      ["confirmed", "failed", "queued", "reverted", "submitted", "waiting_for_gas"].sort(),
    );
    expect(TX_STATUS.waiting_for_gas.status).toBe("review");
  });
});

describe("boundary handling", () => {
  const poly = {
    type: "Polygon",
    coordinates: [
      [
        [0, 0],
        [1, 0],
        [1, 1],
        [0, 0],
      ],
    ],
  };
  it("accepts geometry, Feature and single-feature collections", () => {
    expect(parseBoundary(JSON.stringify(poly)).type).toBe("Polygon");
    expect(
      parseBoundary(JSON.stringify({ type: "Feature", geometry: poly, properties: {} })).type,
    ).toBe("Polygon");
    expect(
      parseBoundary(
        JSON.stringify({
          type: "FeatureCollection",
          features: [{ type: "Feature", geometry: poly }],
        }),
      ).type,
    ).toBe("Polygon");
  });
  it("rejects bad input with a clear message", () => {
    expect(() => parseBoundary("{")).toThrow("not valid JSON");
    expect(() => parseBoundary(JSON.stringify({ type: "Point", coordinates: [0, 0] }))).toThrow(
      "Polygon or MultiPolygon",
    );
    expect(() =>
      parseBoundary(JSON.stringify({ type: "FeatureCollection", features: [] })),
    ).toThrow("exactly one");
  });
  it("extracts rings and rejects unusable geometry", () => {
    expect(ringsOf(poly)?.[0]).toHaveLength(4);
    expect(
      ringsOf({ type: "MultiPolygon", coordinates: [poly.coordinates, poly.coordinates] }),
    ).toHaveLength(2);
    expect(ringsOf({ type: "Point" })).toBeNull();
    expect(ringsOf(null)).toBeNull();
  });
});
