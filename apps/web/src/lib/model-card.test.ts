import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
// @ts-expect-error: plain ESM script without type declarations
import { extract, SOURCE } from "../../scripts/sync-model-card.mjs";
import copy from "../data/model-cards/ecosystem_classifier_v1.json";
import { modelCard } from "./model-cards";

describe("model card copy", () => {
  it.skipIf(!existsSync(SOURCE))(
    "matches ml/model_cards (run `pnpm sync:model-card` if this fails)",
    () => {
      expect(extract(readFileSync(SOURCE, "utf8"))).toEqual(copy);
    },
  );

  it("is only returned for its own model version", () => {
    expect(modelCard("ecosystem_classifier_v1")?.heldout.n).toBeGreaterThan(0);
    expect(modelCard("ecosystem_classifier_v2")).toBeNull();
  });
});
