"""Constants and default factors, each with its source.

`VERIFIED` records whether the value has been checked against the primary document. Unverified
values still work, but every result that uses one carries a warning in its trace.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Factor:
    value: float
    unit: str
    source: str
    verified: bool


# Molecular mass ratio CO2 / C.
CO2_PER_C = Factor(44.0 / 12.0, "tCO2/tC", "Stoichiometry (44/12)", verified=True)

# Carbon fraction of tree dry biomass. AR-TOOL14 v04.1 default ("0.47 unless transparent and
# verifiable information can be provided to justify a different value").
CARBON_FRACTION = Factor(0.47, "tC/t d.m.", "CDM AR-TOOL14 v04.1 (default CF_TREE)", verified=True)

# Default rate of soil organic carbon accrual after planting, and its duration.
SOC_RATE = Factor(0.50, "tC/ha/yr", "BEE BM FR05.001 v1.0 §4.5 eq. (4) default dSOC", verified=True)
SOC_YEARS = 20  # dSOC = 0.50 for t_PLANT..t_PLANT+20, 0 afterwards (BM FR05.001 eq. 4)

# Confidence level used for uncertainty throughout AR-TOOL14.
CONFIDENCE = 0.90

# AR-TOOL14 v04.1 Appendix 2, Table 1: uncertainty U (% of mean, 90% CI) → discount (% of U).
# Upper bounds are inclusive.
UNCERTAINTY_DISCOUNT_TABLE: tuple[tuple[float, float], ...] = (
    (10.0, 0.00),
    (15.0, 0.25),
    (20.0, 0.50),
    (30.0, 0.75),
    (float("inf"), 1.00),
)
UNCERTAINTY_DISCOUNT_SOURCE = "CDM AR-TOOL14 v04.1 Appendix 2, Table 1"

# Target precision for plot inventories (half-width of 90% CI as % of mean): below this no
# discount applies, so it is the sample-size planning target.
TARGET_PRECISION_PCT = 10.0
