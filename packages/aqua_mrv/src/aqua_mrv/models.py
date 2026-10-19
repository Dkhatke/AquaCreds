"""Typed inputs for an MRV report. Everything here ends up, verbatim, in the hashed record."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProjectInfo(_Frozen):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    state: str = Field(min_length=1)
    methodology: Literal["ccts_bm_fr05_001"] = "ccts_bm_fr05_001"  # == aqua_carbon METHODOLOGY
    area_ha: float = Field(gt=0)
    start_year: int
    # GeoJSON Polygon/MultiPolygon geometry (or a Feature wrapping one), WGS84 lon/lat.
    boundary: dict[str, Any] | None = None


class PeriodInfo(_Frozen):
    kind: Literal["verification", "monitoring"] = "verification"
    start_year: int  # t1: project start or previous verification
    end_year: int  # t2

    @model_validator(mode="after")
    def _order(self) -> "PeriodInfo":
        if self.end_year <= self.start_year:
            raise ValueError("Period end_year must be after start_year")
        return self


class TreeRecord(_Frozen):
    species: str = Field(min_length=1)
    dbh_cm: float = Field(gt=0)
    height_m: float | None = Field(default=None, gt=0)
    alive: bool = True


class PlotRecord(_Frozen):
    code: str = Field(min_length=1)
    stratum: str = Field(min_length=1)
    area_m2: float = Field(gt=0)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    trees: tuple[TreeRecord, ...] = ()

    @model_validator(mode="after")
    def _coords(self) -> "PlotRecord":
        if (self.lat is None) != (self.lon is None):
            raise ValueError(f"Plot {self.code}: give both lat and lon, or neither")
        return self


class EvidenceSummary(_Frozen):
    submissions_total: int = Field(ge=0)
    submissions_accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    # Bucket label (e.g. "0.0-0.5") → number of submissions.
    trust_score_distribution: dict[str, int] = Field(default_factory=dict)


class SatelliteSummary(_Frozen):
    """Free-form index/area statistics. Context only: satellites never create credits here."""

    stats: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, str] = Field(default_factory=dict)


class PlantingCohortRecord(_Frozen):
    year: int
    area_ha: float = Field(gt=0)


class PreviousVerification(_Frozen):
    tree_stock_tco2e: float = 0.0  # conservative tree stock certified at t1
    cumulative_removals_tco2e: float = 0.0  # Σ ΔC_AR from project start to t1


class Deductions(_Frozen):
    baseline_removals_tco2e: float = 0.0  # ΔC_BSL (BM-T-AR-0004)
    leakage_tco2e: float = 0.0  # LK (BM-T-AR-0005)
    nonco2_emissions_tco2e: float = 0.0  # GHG_E (BM-T-AR-0002)


class ReportInputs(_Frozen):
    project: ProjectInfo
    period: PeriodInfo
    strata_area_ha: dict[str, float]
    plots: tuple[PlotRecord, ...]
    planting: tuple[PlantingCohortRecord, ...] = ()
    include_soc: bool = True
    previous: PreviousVerification = PreviousVerification()
    deductions: Deductions = Deductions()
    evidence: EvidenceSummary | None = None
    satellite: SatelliteSummary | None = None
    # Deliberately caller-supplied (never `now()`), so rebuilding a report reproduces its hash.
    generated_at: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "ReportInputs":
        if any(a <= 0 for a in self.strata_area_ha.values()):
            raise ValueError("Stratum areas must be positive")
        codes = [p.code for p in self.plots]
        if len(codes) != len(set(codes)):
            raise ValueError("Plot codes must be unique")
        unknown = {p.stratum for p in self.plots} - set(self.strata_area_ha)
        if unknown:
            raise ValueError(f"Plots reference strata without an area: {sorted(unknown)}")
        if self.period.start_year < self.project.start_year:
            raise ValueError("Period starts before the project start year")
        return self
