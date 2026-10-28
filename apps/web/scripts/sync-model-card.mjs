// Copies the held-out metrics of the production classifier from the model card
// (ml/model_cards/<version>_metrics.json, written by `dvc repro train_classifier`) into the web app.
// The API does not expose model metrics yet (see PR "API gaps"); a unit test fails if this copy
// drifts from the source. Usage: pnpm --filter @aquacreds/web sync:model-card
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
export const SOURCE = join(here, "../../../ml/model_cards/ecosystem_classifier_v1_metrics.json");
export const TARGET = join(here, "../src/data/model-cards/ecosystem_classifier_v1.json");

export function extract(raw) {
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
    source: "ml/model_cards/ecosystem_classifier_v1_metrics.json",
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
  const out = extract(readFileSync(SOURCE, "utf8"));
  writeFileSync(TARGET, `${JSON.stringify(out, null, 2)}\n`);
  console.log(
    `wrote ${TARGET} (${out.model_version}, source sha256 ${out.source_sha256.slice(0, 12)})`,
  );
}
