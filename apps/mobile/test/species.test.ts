import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { findSpecies, normaliseSpecies, SPECIES, searchSpecies } from "../src/domain/species";

const csvPath = fileURLToPath(
  new URL("../../../data/reference/wood_density_mangroves.csv", import.meta.url),
);

describe("species list", () => {
  it("matches data/reference/wood_density_mangroves.csv exactly", () => {
    const rows = readFileSync(csvPath, "utf8").trim().split("\n").slice(1);
    const expected = rows.map((line) => {
      const [taxon = "", rank = "", genus = ""] = line.split(",");
      return { value: rank === "genus" ? `${taxon} sp.` : taxon, genus, rank };
    });
    expect(SPECIES).toEqual(expected);
  });

  it("normalises like the server and searches", () => {
    expect(normaliseSpecies("  avicennia   MARINA ")).toBe("Avicennia marina");
    expect(findSpecies("AVICENNIA MARINA")?.value).toBe("Avicennia marina");
    expect(findSpecies("Avicennia")).toBeUndefined();
    const rhiz = searchSpecies("rhiz", 10).map((s) => s.value);
    // Prefix matches first, then substring matches.
    expect(rhiz.slice(0, 4)).toEqual([
      "Rhizophora mucronata",
      "Rhizophora apiculata",
      "Rhizophora stylosa",
      "Rhizophora sp.",
    ]);
    expect(rhiz).toContain("Bruguiera gymnorhiza");
    expect(searchSpecies("marina")[0]?.value).toBe("Avicennia marina");
  });
});
