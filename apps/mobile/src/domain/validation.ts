/**
 * Plot-measurement form validation. Mirrors the API (services/api/.../schemas/field.py
 * `TreeRecord`, `MeasurementCreate` and `record_measurement`) so a queued item is not rejected
 * hours later when the phone finds signal, plus one stricter rule: species must be a taxon the
 * carbon engine has a wood density for (the API itself only checks the length).
 */
import type { Schema } from "@aquacreds/api-client";
import type { StringKey } from "../i18n";
import { findSpecies } from "./species";

export type MeasurementBody = Schema<"MeasurementCreate">;
export type TreeBody = Schema<"TreeRecord">;

export const LIMITS = {
  dbhMaxCm: 300,
  heightMaxM: 60,
  maxTrees: 2000,
  notesMax: 2000,
  speciesMin: 3,
  speciesMax: 80,
} as const;

export interface TreeDraft {
  species: string;
  dbhCm: string;
  heightM: string;
  alive: boolean;
}

export interface MeasurementDraft {
  plotId: string;
  measuredOn: string;
  trees: TreeDraft[];
  notes: string;
}

export interface PeriodWindow {
  start_date: string;
  end_date: string;
}

export interface FieldError {
  key: StringKey;
  params?: Record<string, string | number>;
}

export interface TreeErrors {
  species?: FieldError;
  dbhCm?: FieldError;
  heightM?: FieldError;
}

export interface MeasurementErrors {
  plotId?: FieldError;
  measuredOn?: FieldError;
  trees?: FieldError;
  notes?: FieldError;
  tree: TreeErrors[];
}

export type ValidationResult =
  | { ok: true; body: MeasurementBody }
  | { ok: false; errors: MeasurementErrors };

/** Strict decimal: accepts "12", "12.5", "12,5" (comma keyboards); rejects "", "1e3", "12abc". */
export function parseDecimal(raw: string): number | null {
  const s = raw.trim().replace(",", ".");
  if (!/^\d+(\.\d+)?$|^\.\d+$/.test(s)) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** A real calendar date in YYYY-MM-DD, or null. */
export function parseIsoDate(raw: string): string | null {
  const m = ISO_DATE.exec(raw.trim());
  if (!m) return null;
  const [, y, mo, d] = m;
  const date = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d)));
  if (
    date.getUTCFullYear() !== Number(y) ||
    date.getUTCMonth() !== Number(mo) - 1 ||
    date.getUTCDate() !== Number(d)
  ) {
    return null;
  }
  return `${y}-${mo}-${d}`;
}

/** Local calendar date of `now` as YYYY-MM-DD. */
export function localIsoDate(now: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

export function validateTree(tree: TreeDraft): { errors: TreeErrors; body?: TreeBody } {
  const errors: TreeErrors = {};
  const species = findSpecies(tree.species);
  if (!tree.species.trim()) errors.species = { key: "validation.required" };
  else if (!species) errors.species = { key: "validation.species" };

  const dbh = parseDecimal(tree.dbhCm);
  if (!tree.dbhCm.trim()) errors.dbhCm = { key: "validation.required" };
  else if (dbh === null) errors.dbhCm = { key: "validation.number" };
  else if (!(dbh > 0 && dbh <= LIMITS.dbhMaxCm)) errors.dbhCm = { key: "validation.dbhRange" };

  let height: number | null = null;
  if (tree.heightM.trim()) {
    height = parseDecimal(tree.heightM);
    if (height === null) errors.heightM = { key: "validation.number" };
    else if (!(height > 0 && height <= LIMITS.heightMaxM)) {
      errors.heightM = { key: "validation.heightRange" };
    }
  }

  if (Object.keys(errors).length > 0 || !species || dbh === null) return { errors };
  return {
    errors,
    body: { species: species.value, dbh_cm: dbh, height_m: height, alive: tree.alive },
  };
}

export function validateMeasurement(
  draft: MeasurementDraft,
  period: PeriodWindow,
  now: Date,
): ValidationResult {
  const errors: MeasurementErrors = { tree: [] };
  let failed = false;

  if (!draft.plotId) {
    errors.plotId = { key: "validation.plot" };
    failed = true;
  }

  const date = parseIsoDate(draft.measuredOn);
  if (!draft.measuredOn.trim()) {
    errors.measuredOn = { key: "validation.required" };
  } else if (!date) {
    errors.measuredOn = { key: "validation.date" };
  } else if (date < period.start_date || date > period.end_date) {
    errors.measuredOn = {
      key: "validation.dateOutsidePeriod",
      params: { start: period.start_date, end: period.end_date },
    };
  } else if (date > localIsoDate(now)) {
    errors.measuredOn = { key: "validation.dateFuture" };
  }
  if (errors.measuredOn) failed = true;

  if (draft.trees.length === 0) {
    errors.trees = { key: "validation.noTrees" };
    failed = true;
  } else if (draft.trees.length > LIMITS.maxTrees) {
    errors.trees = { key: "validation.tooManyTrees" };
    failed = true;
  }

  if (draft.notes.length > LIMITS.notesMax) {
    errors.notes = { key: "validation.notesTooLong" };
    failed = true;
  }

  const trees: TreeBody[] = [];
  for (const tree of draft.trees) {
    const result = validateTree(tree);
    errors.tree.push(result.errors);
    if (result.body) trees.push(result.body);
    else failed = true;
  }

  if (failed || !date) return { ok: false, errors };
  return { ok: true, body: { measured_on: date, trees, notes: draft.notes.trim() } };
}

export function emptyTree(): TreeDraft {
  return { species: "", dbhCm: "", heightM: "", alive: true };
}
