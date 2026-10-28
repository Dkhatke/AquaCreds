/**
 * Mangrove taxa accepted by the carbon engine (`aqua_mrv.wood_density.lookup`), taken from
 * data/reference/wood_density_mangroves.csv (Zanne et al. 2009 GWDD, CC0). Genus rows become
 * "<Genus> sp.", which the server resolves to the genus-mean wood density (flagged genus_fallback).
 * test/species.test.ts fails if this list drifts from the CSV.
 */
export interface SpeciesOption {
  /** Value sent to the API (`TreeRecord.species`). */
  value: string;
  genus: string;
  rank: "species" | "genus";
}

export const SPECIES: readonly SpeciesOption[] = [
  { value: "Avicennia marina", genus: "Avicennia", rank: "species" },
  { value: "Avicennia officinalis", genus: "Avicennia", rank: "species" },
  { value: "Avicennia alba", genus: "Avicennia", rank: "species" },
  { value: "Rhizophora mucronata", genus: "Rhizophora", rank: "species" },
  { value: "Rhizophora apiculata", genus: "Rhizophora", rank: "species" },
  { value: "Bruguiera gymnorhiza", genus: "Bruguiera", rank: "species" },
  { value: "Bruguiera cylindrica", genus: "Bruguiera", rank: "species" },
  { value: "Ceriops tagal", genus: "Ceriops", rank: "species" },
  { value: "Ceriops decandra", genus: "Ceriops", rank: "species" },
  { value: "Sonneratia apetala", genus: "Sonneratia", rank: "species" },
  { value: "Sonneratia alba", genus: "Sonneratia", rank: "species" },
  { value: "Sonneratia caseolaris", genus: "Sonneratia", rank: "species" },
  { value: "Excoecaria agallocha", genus: "Excoecaria", rank: "species" },
  { value: "Heritiera fomes", genus: "Heritiera", rank: "species" },
  { value: "Kandelia candel", genus: "Kandelia", rank: "species" },
  { value: "Aegiceras corniculatum", genus: "Aegiceras", rank: "species" },
  { value: "Lumnitzera racemosa", genus: "Lumnitzera", rank: "species" },
  { value: "Xylocarpus granatum", genus: "Xylocarpus", rank: "species" },
  { value: "Rhizophora stylosa", genus: "Rhizophora", rank: "species" },
  { value: "Bruguiera parviflora", genus: "Bruguiera", rank: "species" },
  { value: "Bruguiera sexangula", genus: "Bruguiera", rank: "species" },
  { value: "Sonneratia griffithii", genus: "Sonneratia", rank: "species" },
  { value: "Heritiera littoralis", genus: "Heritiera", rank: "species" },
  { value: "Lumnitzera littorea", genus: "Lumnitzera", rank: "species" },
  { value: "Xylocarpus moluccensis", genus: "Xylocarpus", rank: "species" },
  { value: "Aegiceras sp.", genus: "Aegiceras", rank: "genus" },
  { value: "Avicennia sp.", genus: "Avicennia", rank: "genus" },
  { value: "Bruguiera sp.", genus: "Bruguiera", rank: "genus" },
  { value: "Ceriops sp.", genus: "Ceriops", rank: "genus" },
  { value: "Excoecaria sp.", genus: "Excoecaria", rank: "genus" },
  { value: "Heritiera sp.", genus: "Heritiera", rank: "genus" },
  { value: "Kandelia sp.", genus: "Kandelia", rank: "genus" },
  { value: "Lumnitzera sp.", genus: "Lumnitzera", rank: "genus" },
  { value: "Rhizophora sp.", genus: "Rhizophora", rank: "genus" },
  { value: "Sonneratia sp.", genus: "Sonneratia", rank: "genus" },
  { value: "Xylocarpus sp.", genus: "Xylocarpus", rank: "genus" },
];

const BY_VALUE = new Map(SPECIES.map((s) => [s.value.toLowerCase(), s]));

/** Same normalisation as the server: "  rhizophora   MUCRONATA " → "Rhizophora mucronata". */
export function normaliseSpecies(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const [genus, ...rest] = parts;
  if (!genus) return "";
  return [
    genus.charAt(0).toUpperCase() + genus.slice(1).toLowerCase(),
    ...rest.map((p) => p.toLowerCase()),
  ].join(" ");
}

export function findSpecies(name: string): SpeciesOption | undefined {
  return BY_VALUE.get(normaliseSpecies(name).toLowerCase());
}

/** Case-insensitive prefix/substring search for the species picker (species first, then genera). */
export function searchSpecies(query: string, limit = 8): SpeciesOption[] {
  const q = query.trim().toLowerCase();
  if (!q) return SPECIES.slice(0, limit);
  const starts = SPECIES.filter((s) => s.value.toLowerCase().startsWith(q));
  const contains = SPECIES.filter(
    (s) => !s.value.toLowerCase().startsWith(q) && s.value.toLowerCase().includes(q),
  );
  return [...starts, ...contains].slice(0, limit);
}
