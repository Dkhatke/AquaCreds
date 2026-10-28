import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
// @ts-expect-error: plain ESM script without type declarations
import { extract, SOURCES, STEMS } from "../../scripts/sync-model-card.mjs";
import v1 from "../data/model-cards/ecosystem_classifier_v1.json";
import v1_1 from "../data/model-cards/ecosystem_classifier_v1_1.json";
import { modelCard, PRODUCTION_MODEL_VERSION } from "./model-cards";

const COPIES: Record<string, unknown> = {
  ecosystem_classifier_v1: v1,
  ecosystem_classifier_v1_1: v1_1,
};

describe("model card copy", () => {
  it("covers every synced version", () => {
    expect(Object.keys(COPIES).sort()).toEqual([...STEMS].sort());
  });

  for (const stem of STEMS as string[]) {
    it.skipIf(!existsSync(SOURCES[stem]))(
      `${stem} matches ml/model_cards (run \`pnpm sync:model-card\` if this fails)`,
      () => {
        expect(extract(readFileSync(SOURCES[stem], "utf8"), stem)).toEqual(COPIES[stem]);
      },
    );
  }

  it("is only returned for its own model version", () => {
    expect(modelCard("ecosystem_classifier_v1")?.model_version).toBe("ecosystem_classifier_v1");
    expect(modelCard("ecosystem_classifier_v1.1")?.model_version).toBe("ecosystem_classifier_v1.1");
    expect(modelCard("ecosystem_classifier_v1.1")?.source).toBe(
      "ml/model_cards/ecosystem_classifier_v1_1_metrics.json",
    );
    expect(modelCard("ecosystem_classifier_v2")).toBeNull();
  });

  it("has a card for the production model", () => {
    expect(PRODUCTION_MODEL_VERSION).toBe("ecosystem_classifier_v1.1");
    expect(modelCard(PRODUCTION_MODEL_VERSION)?.heldout.n).toBe(1133);
  });
});
