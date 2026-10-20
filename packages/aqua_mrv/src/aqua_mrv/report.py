"""Build an MRV report: plots → wood densities → aqua_carbon → canonical, hashed record.

Hashing rules (what gets anchored on-chain via `AquaRegistry.submitReport`):
1. The record is a plain JSON tree (dicts, lists, str, int, float, bool, null). Computed floats are
   rounded (per-plot/trace values to 6 decimals, headline results to 4) before hashing, so the
   hash does not depend on last-bit floating-point differences between platforms.
2. `canonical_json` serialises it with RFC 8785 (JCS): keys sorted by UTF-16 code units, no
   whitespace, ECMAScript number formatting, UTF-8. Dict ordering in inputs therefore never matters.
3. `report_hash = SHA-256(canonical_json)`. The hash itself is not part of the record.
4. No volatile values: there is no wall-clock timestamp unless the caller passes `generated_at`,
   which is then frozen into the record. Software versions *are* included on purpose: a different
   engine version is a different calculation.
"""

import hashlib
import math
from dataclasses import dataclass
from typing import Any

import rfc8785

import aqua_carbon
import aqua_mrv
from aqua_carbon.allometry import Plot, Tree, komiyama_agb_kg, komiyama_bgb_kg
from aqua_carbon.ccts import (
    METHODOLOGY,
    METHODOLOGY_SOURCE,
    PlantingCohort,
    VerificationInput,
    VerificationResult,
    compute,
    tree_biomass_from_plots,
)
from aqua_mrv.models import ReportInputs
from aqua_mrv.wood_density import WoodDensityProvenance, lookup, normalise_species

SCHEMA = "aquacreds.mrv_report/1"
STATUS_LABEL = "Estimated — pending accredited verification; testnet registry units"
METHODOLOGY_PROFILE = {
    "id": METHODOLOGY,
    "source": METHODOLOGY_SOURCE,
    "tree_tool": "CDM AR-TOOL14 v04.1 (CCTS adoption BM-T-AR-0004)",
    "allometry": "Komiyama et al. 2005, doi:10.1017/S0266467405002476",
}
_ND = 6  # decimals for per-tree / per-plot values in the record


@dataclass(frozen=True)
class MRVReport:
    record: dict[str, Any]
    result: VerificationResult

    def canonical_json(self) -> bytes:
        return canonical_json(self)

    @property
    def report_hash(self) -> str:
        """Hex SHA-256 of the canonical record (64 chars, no 0x)."""
        return hashlib.sha256(self.canonical_json()).hexdigest()

    def report_hash_bytes32(self) -> bytes:
        """The 32-byte digest, as passed to `AquaRegistry.submitReport(…, bytes32 reportHash, …)`."""
        return hashlib.sha256(self.canonical_json()).digest()

    @property
    def lccc_units(self) -> int:
        """Signed whole lCCC units for `submitReport(…, int256 lccc, …)`: issuable units, or minus
        the units that must be replaced after a reversal."""
        return self.result.lccc_issuable_units - self.result.replacement_required_units

    @property
    def warnings(self) -> list[str]:
        return list(self.record["warnings"])


def canonical_json(report: MRVReport | dict[str, Any]) -> bytes:
    """RFC 8785 canonical bytes of the record (accepts a report or a bare record dict)."""
    record = report.record if isinstance(report, MRVReport) else report
    return rfc8785.dumps(record)


def report_hash(report: MRVReport | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(report)).hexdigest()


def _r(x: float, nd: int = _ND) -> float:
    if not math.isfinite(x):
        raise ValueError(f"Non-finite value {x} cannot be recorded")
    return round(x, nd)


def build_report(inputs: ReportInputs) -> MRVReport:
    warnings: list[str] = []
    densities: dict[str, WoodDensityProvenance] = {}
    density_values: dict[str, float] = {}

    plots: list[Plot] = []
    plot_rows: list[dict[str, Any]] = []
    for rec in inputs.plots:
        trees: list[Tree] = []
        tree_rows: list[dict[str, Any]] = []
        for i, t in enumerate(rec.trees, start=1):
            name = normalise_species(t.species)
            if name not in densities:
                rho, prov = lookup(name)  # raises UnknownSpeciesError for unknown genera
                densities[name], density_values[name] = prov, rho
                if prov.genus_fallback:
                    warnings.append(
                        f"Wood density for {name} uses the {prov.taxon} genus mean "
                        f"({rho} g/cm³, n={prov.n}) because the reference table has no species record"
                    )
            rho = density_values[name]
            trees.append(Tree(name, t.dbh_cm, rho, alive=t.alive, height_m=t.height_m))
            tree_rows.append(
                {
                    "n": i,
                    "species": name,
                    "dbh_cm": t.dbh_cm,
                    "height_m": t.height_m,
                    "alive": t.alive,
                    "wood_density_g_cm3": rho,
                    "agb_kg": _r(komiyama_agb_kg(t.dbh_cm, rho)) if t.alive else 0.0,
                    "bgb_kg": _r(komiyama_bgb_kg(t.dbh_cm, rho)) if t.alive else 0.0,
                }
            )
        plot = Plot(rec.code, rec.stratum, rec.area_m2, tuple(trees))
        plots.append(plot)
        agb, bgb = plot.biomass_t_dm_per_ha()
        plot_rows.append(
            {
                "code": rec.code,
                "stratum": rec.stratum,
                "area_m2": rec.area_m2,
                "lat": rec.lat,
                "lon": rec.lon,
                "live_trees": sum(t.alive for t in rec.trees),
                "dead_trees": sum(not t.alive for t in rec.trees),
                "agb_t_dm_ha": _r(agb),
                "bgb_t_dm_ha": _r(bgb),
                "total_t_dm_ha": _r(agb + bgb),
                "trees": tree_rows,
            }
        )

    missing = set(inputs.strata_area_ha) - {p.stratum for p in inputs.plots}
    if missing:
        raise ValueError(f"Strata without plots: {sorted(missing)}")
    strata_total = sum(inputs.strata_area_ha.values())
    if strata_total > inputs.project.area_ha * (1 + 1e-9):
        raise ValueError(
            f"Stratum areas ({strata_total} ha) exceed the project area ({inputs.project.area_ha} ha)"
        )
    if strata_total < inputs.project.area_ha * (1 - 1e-9):
        warnings.append(
            f"Stratum areas cover {strata_total} of {inputs.project.area_ha} ha; only stratified "
            "area is credited"
        )

    estimate, engine_warnings = tree_biomass_from_plots(plots, dict(inputs.strata_area_ha))
    warnings.extend(engine_warnings)
    result = compute(
        VerificationInput(
            project_start_year=inputs.project.start_year,
            period_start_year=inputs.period.start_year,
            period_end_year=inputs.period.end_year,
            tree_biomass_t_dm=estimate,
            previous_tree_stock_tco2e=inputs.previous.tree_stock_tco2e,
            include_soc=inputs.include_soc,
            planting=tuple(PlantingCohort(c.year, c.area_ha) for c in inputs.planting),
            baseline_removals_tco2e=inputs.deductions.baseline_removals_tco2e,
            leakage_tco2e=inputs.deductions.leakage_tco2e,
            nonco2_emissions_tco2e=inputs.deductions.nonco2_emissions_tco2e,
            previous_cumulative_removals_tco2e=inputs.previous.cumulative_removals_tco2e,
            input_warnings=tuple(warnings),
        )
    )
    calc = result.to_dict()
    trace = calc.pop("trace")

    record: dict[str, Any] = {
        "schema": SCHEMA,
        "status": STATUS_LABEL,
        "generated_at": inputs.generated_at,
        "notes": inputs.notes,
        "methodology": METHODOLOGY_PROFILE,
        "software": {"aqua_carbon": aqua_carbon.__version__, "aqua_mrv": aqua_mrv.__version__},
        "project": inputs.project.model_dump(mode="json"),
        "period": inputs.period.model_dump(mode="json"),
        "inputs": {
            "strata_area_ha": dict(inputs.strata_area_ha),
            "planting": [c.model_dump(mode="json") for c in inputs.planting],
            "include_soc": inputs.include_soc,
            "previous": inputs.previous.model_dump(mode="json"),
            "deductions": inputs.deductions.model_dump(mode="json"),
        },
        "plots": plot_rows,
        "wood_density": {
            name: {"value_g_cm3": density_values[name], **prov.to_dict()}
            for name, prov in sorted(densities.items())
        },
        "evidence": inputs.evidence.model_dump(mode="json") if inputs.evidence else None,
        "satellite": inputs.satellite.model_dump(mode="json") if inputs.satellite else None,
        "biomass_estimate": {
            "mean_t_dm": _r(estimate.mean),
            "half_width_90_t_dm": _r(estimate.half_width),
            "n_plots": estimate.n,
            "df": estimate.df,
        },
        "result": calc,
        "trace": trace["steps"],
        "warnings": trace["warnings"],
    }
    return MRVReport(record=record, result=result)
