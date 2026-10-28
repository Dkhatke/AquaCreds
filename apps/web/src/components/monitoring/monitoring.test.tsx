import type { Schema } from "@aquacreds/api-client";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ClassificationSummary, latestClassification } from "./ClassificationPanel";
import { boundsOf } from "./ProjectMap";
import { niceTicks, toChartPoints } from "./TimeseriesChart";

const base: Schema<"ClassificationOut"> = {
  id: "c1",
  project_id: "p1",
  run_id: null,
  season_start_year: 2024,
  created_at: "2025-04-02T10:00:00Z",
  model_version: "ecosystem_classifier_v1",
  model_sha256: "a".repeat(64),
  features_sha256: "b".repeat(64),
  classes_cog_sha256: "c".repeat(64),
  probability_cog_sha256: "d".repeat(64),
  classes: ["mangrove", "water"],
  class_areas_ha: { mangrove: 30.5, water: 10.25 },
  mangrove_ha: 30.5,
  mangrove_mean_confidence: 0.91,
  low_confidence_share: 0.04,
  low_confidence_threshold: 0.6,
  classified_pixels: 4075,
  unclassified_pixels: 0,
  polygon_pixels: 4075,
  pixel_area_m2: 100,
  areal_scale: 0.9996,
  epsg: 32645,
  provider: "planetary-computer",
  scenes: [{}, {}],
  dem_items: [],
};

describe("classification", () => {
  it("picks the latest season", () => {
    const older = { ...base, id: "old", season_start_year: 2023 };
    expect(latestClassification([older, base])?.id).toBe("c1");
    expect(latestClassification([])).toBeNull();
  });

  it("shows held-out metrics for a known model version and labels it context only", () => {
    render(<ClassificationSummary c={base} />);
    expect(screen.getByText("Mangrove F1")).toBeInTheDocument();
    expect(screen.getByText(/Gulf of Kutch/)).toBeInTheDocument();
    expect(screen.getByText(/Context only: credits come from field plots/)).toBeInTheDocument();
    expect(screen.getByText("30.50 ha")).toBeInTheDocument();
  });

  it("shows no metrics for an unknown model version", () => {
    render(<ClassificationSummary c={{ ...base, model_version: "ecosystem_classifier_v9" }} />);
    expect(screen.getByText("No model card for this version")).toBeInTheDocument();
    expect(screen.queryByText("Mangrove F1")).not.toBeInTheDocument();
  });
});

describe("time series helpers", () => {
  it("drops points without a value and sorts by date", () => {
    const pt = (date: string, p50: number | null) => ({
      date,
      acquired_at: `${date}T05:00:00Z`,
      item_id: date,
      clear_fraction: 0.9,
      cloud_cover: 1,
      valid_pixels: 10,
      mean: p50,
      p10: null,
      p50,
      p90: null,
    });
    const out = toChartPoints([
      pt("2025-02-01", 0.7),
      pt("2025-01-01", 0.6),
      pt("2025-03-01", null),
    ]);
    expect(out.map((p) => p.date)).toEqual(["2025-01-01", "2025-02-01"]);
    expect(out[0]).toMatchObject({ p10: 0.6, p90: 0.6, quality: 0.9 });
  });

  it("makes round ticks", () => {
    expect(niceTicks(0.12, 0.83, 4)).toEqual([0.2, 0.4, 0.6, 0.8]);
  });
});

describe("boundsOf", () => {
  it("computes polygon bounds", () => {
    expect(
      boundsOf({
        type: "Polygon",
        coordinates: [
          [
            [86.8, 20.62],
            [86.82, 20.62],
            [86.82, 20.64],
            [86.8, 20.62],
          ],
        ],
      }),
    ).toEqual([
      [86.8, 20.62],
      [86.82, 20.64],
    ]);
  });
});
