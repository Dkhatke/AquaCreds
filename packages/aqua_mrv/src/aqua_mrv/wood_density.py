"""Wood density (ρ, g/cm³) for Indian mangrove species, with provenance.

Values come from the Global Wood Density Database (Zanne et al. 2009, Dryad,
doi:10.5061/dryad.234, CC0); see `data/reference/README.md` for how the table was built. A species
missing from the table falls back to its genus mean (flagged `genus_fallback=True`); an unknown
genus raises `UnknownSpeciesError`. Nothing is ever guessed.
"""

import csv
from dataclasses import asdict, dataclass
from functools import cache
from importlib import resources
from typing import Any

DATA_FILE = "wood_density_mangroves.csv"


class UnknownSpeciesError(LookupError):
    """No species or genus wood-density record exists for this taxon."""


@dataclass(frozen=True)
class WoodDensityProvenance:
    taxon: str  # the table row actually used (species or genus)
    rank: str  # "species" | "genus"
    n: int  # number of GWDD records averaged
    sd_g_cm3: float | None
    genus_fallback: bool
    regions: str
    source: str
    source_doi: str
    licence: str
    retrieved: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalise_species(name: str) -> str:
    """'  avicennia   MARINA ' → 'Avicennia marina'; 'Avicennia sp.' → 'Avicennia sp.'."""
    parts = name.split()
    if not parts:
        raise UnknownSpeciesError("Empty species name")
    genus = parts[0].capitalize()
    return " ".join([genus, *(p.lower() for p in parts[1:])])


@cache
def _table() -> dict[str, tuple[float, WoodDensityProvenance]]:
    text = resources.files("aqua_mrv").joinpath("data", DATA_FILE).read_text(encoding="utf-8")
    out: dict[str, tuple[float, WoodDensityProvenance]] = {}
    for row in csv.DictReader(text.splitlines()):
        prov = WoodDensityProvenance(
            taxon=row["taxon"],
            rank=row["rank"],
            n=int(row["n"]),
            sd_g_cm3=float(row["sd_g_cm3"]) if row["sd_g_cm3"] else None,
            genus_fallback=row["genus_fallback"] == "true",
            regions=row["regions"],
            source=row["source"],
            source_doi=row["source_doi"],
            licence=row["licence"],
            retrieved=row["retrieved"],
        )
        key = row["taxon"] if row["rank"] == "species" else f"{row['taxon']}*"
        out[key] = (float(row["wood_density_g_cm3"]), prov)
    return out


def lookup(species: str) -> tuple[float, WoodDensityProvenance]:
    """Wood density (g/cm³) and its provenance for a species name."""
    name = normalise_species(species)
    table = _table()
    if name in table:
        return table[name]
    genus = name.split()[0]
    if f"{genus}*" in table:
        return table[f"{genus}*"]
    raise UnknownSpeciesError(
        f"No wood density for {species!r}: genus {genus!r} is not in {DATA_FILE} "
        "(add a cited GWDD record rather than guessing)"
    )


def known_taxa() -> list[str]:
    return sorted(k.rstrip("*") for k in _table())
