// Copies the held-out metrics of each classifier version from its model card
// (ml/model_cards/<stem>_metrics.json, written by `dvc repro train_classifier`) into the web app.
// v1 stays because classifications recorded as v1 should still show v1's metrics; v1.1 is the
// production model.
// The API does not expose model metrics yet (see PR "API gaps"); a unit test fails if this copy
// drifts from the source. Usage: pnpm --filter @aquacreds/web sync:model-card
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
// File stems: the version with "." replaced by "_" (ecosystem_classifier_v1.1 → …_v1_1).
export const STEMS = ["ecosystem_classifier_v1", "ecosystem_classifier_v1_1"];
export const sourceOf = (stem) => `ml/model_cards/${stem}_metrics.json`;
export const SOURCES = Object.fromEntries(
  STEMS.map((stem) => [stem, join(here, "../../..", sourceOf(stem))]),
);
export const TARGETS = Object.fromEntries(
  STEMS.map((stem) => [stem, join(here, `../src/data/model-cards/${stem}.json`)]),
);

export function extract(raw, stem) {
  const card = JSON.parse(raw);
  const winner = card.models[card.winner];
  const h = winner.heldout;
  const perClass = Object.fromEntries(
    Object.entries(h.per_class).map(([k, v]) => [
      k,
      { f1: v.f1, precision: v.precision, recall: v.recall, support: v.support },
    ]),
  );
  return {
    model_version: card.model_version,
    algorithm: card.winner,
    source: sourceOf(stem),
    source_sha256: createHash("sha256").update(raw).digest("hex"),
    heldout: {
      region: "Gulf of Kutch (68.0–70.8°E, 22–24°N), never used for training or tuning",
      n: h.n,
      accuracy: h.accuracy,
      macro_f1: h.macro_f1,
      mangrove_vs_rest_f1: h.mangrove_vs_rest_f1,
      per_class: perClass,
    },
    cv_macro_f1_mean: winner.cv.macro_f1.mean ?? null,
    note: "Spatial CV scores are slightly optimistic (same folds for tuning); the held-out region is the clean estimate.",
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  for (const stem of STEMS) {
    const out = extract(readFileSync(SOURCES[stem], "utf8"), stem);
    writeFileSync(TARGETS[stem], `${JSON.stringify(out, null, 2)}\n`);
    console.log(
      `wrote ${TARGETS[stem]} (${out.model_version}, source sha256 ${out.source_sha256.slice(0, 12)})`,
    );
  }
}
