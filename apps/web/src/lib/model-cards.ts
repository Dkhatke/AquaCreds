import v1 from "@/data/model-cards/ecosystem_classifier_v1.json";
import v1_1 from "@/data/model-cards/ecosystem_classifier_v1_1.json";

export type ModelCard = typeof v1;

/** The classifier new classifications use (aqua_worker.model_store.CURRENT_ECOSYSTEM_CLASSIFIER). */
export const PRODUCTION_MODEL_VERSION = "ecosystem_classifier_v1.1";

// v1's files were lost and production never ran it, but a classification recorded as v1 still
// shows v1's own metrics.
const CARDS: Record<string, ModelCard> = Object.fromEntries(
  [v1, v1_1].map((card) => [card.model_version, card]),
);

/** Held-out metrics for a classifier version, or null when this web build has no card for it
 *  (then the UI says so instead of showing another model's numbers). */
export function modelCard(version: string): ModelCard | null {
  return CARDS[version] ?? null;
}
