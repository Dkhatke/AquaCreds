"""Tree-level biomass from field measurements, and plot-level aggregation.

Komiyama, Poungparn & Kato (2005) "Common allometric equations for estimating the tree weight of
mangroves", J. Trop. Ecol. 21:471–477, doi:10.1017/S0266467405002476:

    AGB [kg] = 0.251 · ρ · D^2.46            (n = 104, R² = 0.98)
    BGB [kg] = 0.199 · ρ^0.899 · D^2.22

D = stem diameter at breast height (cm), ρ = wood density (g/cm³).

Wood density is deliberately NOT hard-coded here: it must come from a cited source (Global Wood
Density Database, Zanne et al. 2009) via the caller, so the provenance stays explicit.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from aqua_carbon.constants import Factor

KOMIYAMA_AGB = Factor(
    0.251, "kg", "Komiyama et al. 2005: AGB = 0.251·ρ·D^2.46", verified=True
)  # confirmed via secondary table (doi:10.3390/f13020325); full-text check pending for BGB
KOMIYAMA_BGB = Factor(
    0.199, "kg", "Komiyama et al. 2005: BGB = 0.199·ρ^0.899·D^2.22", verified=False
)


@dataclass(frozen=True)
class Tree:
    species: str
    dbh_cm: float
    wood_density_g_cm3: float
    alive: bool = True
    height_m: float | None = None

    def __post_init__(self) -> None:
        if self.dbh_cm <= 0:
            raise ValueError(f"DBH must be positive, got {self.dbh_cm}")
        if not 0.1 <= self.wood_density_g_cm3 <= 1.5:
            raise ValueError(f"Wood density {self.wood_density_g_cm3} g/cm³ is not plausible")


def komiyama_agb_kg(dbh_cm: float, wood_density: float) -> float:
    return float(KOMIYAMA_AGB.value * wood_density * dbh_cm**2.46)


def komiyama_bgb_kg(dbh_cm: float, wood_density: float) -> float:
    return float(KOMIYAMA_BGB.value * wood_density**0.899 * dbh_cm**2.22)


@dataclass(frozen=True)
class Plot:
    """A fixed-area sample plot. Only living trees count towards stock."""

    plot_id: str
    stratum: str
    area_m2: float
    trees: Sequence[Tree] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.area_m2 <= 0:
            raise ValueError("Plot area must be positive")

    def biomass_t_dm_per_ha(self) -> tuple[float, float]:
        """(AGB, BGB) in tonnes of dry matter per hectare."""
        live = [t for t in self.trees if t.alive]
        agb_kg = sum(komiyama_agb_kg(t.dbh_cm, t.wood_density_g_cm3) for t in live)
        bgb_kg = sum(komiyama_bgb_kg(t.dbh_cm, t.wood_density_g_cm3) for t in live)
        per_ha = 10_000.0 / self.area_m2
        return agb_kg / 1000.0 * per_ha, bgb_kg / 1000.0 * per_ha

    def total_biomass_t_dm_per_ha(self) -> float:
        agb, bgb = self.biomass_t_dm_per_ha()
        return agb + bgb


def plots_by_stratum(plots: Iterable[Plot]) -> dict[str, list[float]]:
    """Total (AGB + BGB) t d.m./ha per plot, grouped by stratum."""
    out: dict[str, list[float]] = {}
    for plot in plots:
        out.setdefault(plot.stratum, []).append(plot.total_biomass_t_dm_per_ha())
    return out
