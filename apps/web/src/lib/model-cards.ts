import v1 from "@/data/model-cards/ecosystem_classifier_v1.json";

export type ModelCard = typeof v1;

const CARDS: Record<string, ModelCard> = { [v1.model_version]: v1 };

/** Held-out metrics for a classifier version, or null when this web build has no card for it
 *  (then the UI says so instead of showing another model's numbers). */
export function modelCard(version: string): ModelCard | null {
  return CARDS[version] ?? null;
}
