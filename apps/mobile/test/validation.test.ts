import { describe, expect, it } from "vitest";
import {
  emptyTree,
  type MeasurementDraft,
  parseDecimal,
  parseIsoDate,
  validateMeasurement,
  validateTree,
} from "../src/domain/validation";

const period = { start_date: "2026-10-01", end_date: "2027-03-31" };
const now = new Date(2026, 9, 28, 12, 0, 0); // 2026-10-28 local

const tree = (over: Partial<ReturnType<typeof emptyTree>> = {}) => ({
  ...emptyTree(),
  species: "Rhizophora mucronata",
  dbhCm: "12.5",
  ...over,
});

const draft = (over: Partial<MeasurementDraft> = {}): MeasurementDraft => ({
  plotId: "plot-1",
  measuredOn: "2026-10-27",
  trees: [tree()],
  notes: "",
  ...over,
});

describe("number and date parsing", () => {
  it("parses strict decimals, with comma keyboards", () => {
    expect(parseDecimal("12")).toBe(12);
    expect(parseDecimal(" 12,5 ")).toBe(12.5);
    expect(parseDecimal(".5")).toBe(0.5);
    for (const bad of ["", "abc", "1e3", "12abc", "-3", "1.2.3", "Infinity"]) {
      expect(parseDecimal(bad)).toBeNull();
    }
  });

  it("accepts only real calendar dates", () => {
    expect(parseIsoDate("2026-02-28")).toBe("2026-02-28");
    expect(parseIsoDate("2026-02-30")).toBeNull();
    expect(parseIsoDate("2026-2-3")).toBeNull();
    expect(parseIsoDate("28/10/2026")).toBeNull();
  });
});

describe("validateTree (mirrors TreeRecord)", () => {
  it("builds the API body with the canonical species name", () => {
    const r = validateTree(
      tree({ species: "  rhizophora   MUCRONATA ", heightM: "6", alive: false }),
    );
    expect(r.body).toEqual({
      species: "Rhizophora mucronata",
      dbh_cm: 12.5,
      height_m: 6,
      alive: false,
    });
  });

  it("accepts genus-only entries the carbon engine can resolve", () => {
    expect(validateTree(tree({ species: "Sonneratia sp." })).body?.species).toBe("Sonneratia sp.");
  });

  it("enforces API ranges: 0 < dbh ≤ 300, 0 < height ≤ 60", () => {
    expect(validateTree(tree({ dbhCm: "300" })).body).toBeDefined();
    expect(validateTree(tree({ dbhCm: "300.1" })).errors.dbhCm?.key).toBe("validation.dbhRange");
    expect(validateTree(tree({ dbhCm: "0" })).errors.dbhCm?.key).toBe("validation.dbhRange");
    expect(validateTree(tree({ dbhCm: "" })).errors.dbhCm?.key).toBe("validation.required");
    expect(validateTree(tree({ dbhCm: "x" })).errors.dbhCm?.key).toBe("validation.number");
    expect(validateTree(tree({ heightM: "60" })).body?.height_m).toBe(60);
    expect(validateTree(tree({ heightM: "61" })).errors.heightM?.key).toBe(
      "validation.heightRange",
    );
    expect(validateTree(tree({ heightM: "" })).body?.height_m).toBeNull();
  });

  it("rejects species without a wood-density record", () => {
    expect(validateTree(tree({ species: "Mangifera indica" })).errors.species?.key).toBe(
      "validation.species",
    );
    expect(validateTree(tree({ species: " " })).errors.species?.key).toBe("validation.required");
  });
});

describe("validateMeasurement (mirrors MeasurementCreate + record_measurement)", () => {
  it("returns the API body when valid", () => {
    const r = validateMeasurement(draft({ notes: "  marker intact " }), period, now);
    expect(r).toEqual({
      ok: true,
      body: {
        measured_on: "2026-10-27",
        trees: [{ species: "Rhizophora mucronata", dbh_cm: 12.5, height_m: null, alive: true }],
        notes: "marker intact",
      },
    });
  });

  it("requires the date inside the period and not in the future", () => {
    const before = validateMeasurement(draft({ measuredOn: "2026-09-30" }), period, now);
    expect(!before.ok && before.errors.measuredOn?.key).toBe("validation.dateOutsidePeriod");
    const future = validateMeasurement(draft({ measuredOn: "2026-10-29" }), period, now);
    expect(!future.ok && future.errors.measuredOn?.key).toBe("validation.dateFuture");
    const bad = validateMeasurement(draft({ measuredOn: "yesterday" }), period, now);
    expect(!bad.ok && bad.errors.measuredOn?.key).toBe("validation.date");
  });

  it("requires a plot, 1–2000 trees and notes ≤ 2000 chars", () => {
    const r = validateMeasurement(
      draft({ plotId: "", trees: [], notes: "x".repeat(2001) }),
      period,
      now,
    );
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.errors.plotId?.key).toBe("validation.plot");
      expect(r.errors.trees?.key).toBe("validation.noTrees");
      expect(r.errors.notes?.key).toBe("validation.notesTooLong");
    }
    const many = validateMeasurement(
      draft({ trees: Array.from({ length: 2001 }, () => tree()) }),
      period,
      now,
    );
    expect(!many.ok && many.errors.trees?.key).toBe("validation.tooManyTrees");
  });

  it("reports per-tree errors by index", () => {
    const r = validateMeasurement(draft({ trees: [tree(), tree({ dbhCm: "-1" })] }), period, now);
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.errors.tree[0]).toEqual({});
      expect(r.errors.tree[1]?.dbhCm?.key).toBe("validation.number");
    }
  });
});
