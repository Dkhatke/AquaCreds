"""Build `wood_density_mangroves.csv` from the Global Wood Density Database (GWDD v1).

Source: Zanne et al. 2009, Global Wood Density Database, Dryad, doi:10.5061/dryad.234 (CC0 1.0),
data paper Chave et al. 2009, Ecol. Lett. 12:351–366, doi:10.1111/j.1461-0248.2009.01285.x.

The Dryad file endpoint required an authenticated API token when this was built, so the records are
read from the verbatim copy of the GWDD shipped as `wdData` in the CRAN package BIOMASS
(2.2.7-1; same 16,467 records, columns family/genus/species/wd/region/referenceNumber/regionId).

Usage (one-off, not part of the runtime; `rdata` is only needed here):

    curl -LO https://cran.r-project.org/src/contrib/BIOMASS_2.2.7-1.tar.gz
    tar -xzf BIOMASS_2.2.7-1.tar.gz BIOMASS/data/wdData.rda
    uv run --no-project --with rdata --with pandas python -I \
        data/reference/build_wood_density.py BIOMASS/data/wdData.rda 2026-10-19

Writes `data/reference/wood_density_mangroves.csv` and the identical package copy
`packages/aqua_mrv/src/aqua_mrv/data/wood_density_mangroves.csv`.
"""

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = (
    ROOT / "data/reference/wood_density_mangroves.csv",
    ROOT / "packages/aqua_mrv/src/aqua_mrv/data/wood_density_mangroves.csv",
)
SOURCE = "Zanne et al. 2009 Global Wood Density Database (via BIOMASS 2.2.7-1 wdData)"
SOURCE_DOI = "10.5061/dryad.234"

# Indian mangrove species (true mangroves recorded on Indian coasts). The first 18 are the
# required set; the rest are other Indian species of the same genera that GWDD covers.
SPECIES = (
    "Avicennia marina",
    "Avicennia officinalis",
    "Avicennia alba",
    "Rhizophora mucronata",
    "Rhizophora apiculata",
    "Bruguiera gymnorhiza",
    "Bruguiera cylindrica",
    "Ceriops tagal",
    "Ceriops decandra",
    "Sonneratia apetala",
    "Sonneratia alba",
    "Sonneratia caseolaris",
    "Excoecaria agallocha",
    "Heritiera fomes",
    "Kandelia candel",
    "Aegiceras corniculatum",
    "Lumnitzera racemosa",
    "Xylocarpus granatum",
    "Rhizophora stylosa",
    "Bruguiera parviflora",
    "Bruguiera sexangula",
    "Sonneratia griffithii",
    "Heritiera littoralis",
    "Lumnitzera littorea",
    "Xylocarpus moluccensis",
)

FIELDS = (
    "taxon",
    "rank",
    "genus",
    "wood_density_g_cm3",
    "n",
    "sd_g_cm3",
    "min_g_cm3",
    "max_g_cm3",
    "genus_fallback",
    "regions",
    "gwdd_reference_numbers",
    "source",
    "source_doi",
    "licence",
    "retrieved",
)


def _row(
    taxon: str, rank: str, genus: str, recs: list[dict[str, Any]], fallback: bool, retrieved: str
) -> dict[str, Any]:
    values = [float(r["wd"]) for r in recs]
    return {
        "taxon": taxon,
        "rank": rank,
        "genus": genus,
        "wood_density_g_cm3": f"{statistics.fmean(values):.4f}",
        "n": len(values),
        "sd_g_cm3": f"{statistics.stdev(values):.4f}" if len(values) > 1 else "",
        "min_g_cm3": f"{min(values):.3f}",
        "max_g_cm3": f"{max(values):.3f}",
        "genus_fallback": str(fallback).lower(),
        "regions": ";".join(sorted({str(r["region"]) for r in recs})),
        "gwdd_reference_numbers": ";".join(
            str(n) for n in sorted({int(float(r["referenceNumber"])) for r in recs})
        ),
        "source": SOURCE,
        "source_doi": SOURCE_DOI,
        "licence": "CC0-1.0",
        "retrieved": retrieved,
    }


def main(rda_path: str, retrieved: str) -> None:
    import rdata  # type: ignore[import-not-found]  # build-time only

    frame = rdata.conversion.convert(rdata.parser.parse_file(rda_path))["wdData"]
    records = frame.to_dict(orient="records")
    by_species: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_genus: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        by_species[f"{rec['genus']} {rec['species']}"].append(rec)
        by_genus[str(rec["genus"])].append(rec)

    rows = []
    for name in SPECIES:
        genus = name.split()[0]
        if by_species.get(name):
            rows.append(_row(name, "species", genus, by_species[name], False, retrieved))
        else:  # species absent from GWDD: genus mean, flagged
            rows.append(_row(name, "species", genus, by_genus[genus], True, retrieved))
    for genus in sorted({s.split()[0] for s in SPECIES}):
        rows.append(_row(genus, "genus", genus, by_genus[genus], True, retrieved))

    for out in OUTPUTS:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
