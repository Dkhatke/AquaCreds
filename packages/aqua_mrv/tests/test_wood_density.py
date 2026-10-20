import csv
from pathlib import Path

import pytest

from aqua_mrv.wood_density import UnknownSpeciesError, known_taxa, lookup, normalise_species

REPO = Path(__file__).resolve().parents[3]
REFERENCE = REPO / "data" / "reference" / "wood_density_mangroves.csv"
PACKAGED = REPO / "packages/aqua_mrv/src/aqua_mrv/data/wood_density_mangroves.csv"

REQUIRED = (
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
)


def test_packaged_table_is_identical_to_the_reference_copy() -> None:
    assert PACKAGED.read_bytes() == REFERENCE.read_bytes()


def test_reference_rows_carry_source_doi_licence_and_retrieval_date() -> None:
    with REFERENCE.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows
    for row in rows:
        assert row["source_doi"] == "10.5061/dryad.234"
        assert row["licence"] == "CC0-1.0"
        assert row["retrieved"] == "2026-10-19"
        assert int(row["n"]) >= 1
        assert 0.1 < float(row["wood_density_g_cm3"]) < 1.5
        assert row["regions"]


@pytest.mark.parametrize("species", REQUIRED)
def test_required_species_have_species_level_records(species: str) -> None:
    value, prov = lookup(species)
    assert prov.rank == "species"
    assert prov.taxon == species
    assert not prov.genus_fallback
    assert 0.3 < value < 1.0


def test_known_value_and_provenance() -> None:
    # GWDD records for Avicennia marina: 0.52, 0.65, 0.689, 0.732 → mean 0.64775
    value, prov = lookup("Avicennia marina")
    assert value == pytest.approx(0.6478)
    assert prov.n == 4
    assert prov.source_doi == "10.5061/dryad.234"
    assert prov.to_dict()["licence"] == "CC0-1.0"


def test_name_normalisation() -> None:
    assert normalise_species("  avicennia   MARINA ") == "Avicennia marina"
    assert lookup("rhizophora Mucronata") == lookup("Rhizophora mucronata")
    with pytest.raises(UnknownSpeciesError, match="Empty"):
        normalise_species("   ")


def test_genus_fallback_is_flagged() -> None:
    value, prov = lookup("Avicennia sp.")
    assert prov.genus_fallback
    assert prov.rank == "genus"
    assert prov.taxon == "Avicennia"
    assert prov.n > 4
    assert value == pytest.approx(0.6837)


def test_unknown_genus_raises_instead_of_guessing() -> None:
    with pytest.raises(UnknownSpeciesError, match="Casuarina"):
        lookup("Casuarina equisetifolia")


def test_known_taxa_lists_species_and_genera() -> None:
    taxa = known_taxa()
    assert "Avicennia marina" in taxa
    assert "Xylocarpus" in taxa
