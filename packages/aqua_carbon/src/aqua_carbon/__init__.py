"""AquaCreds carbon engine.

Deterministic, pure-Python implementation of the carbon accounting chain. No ML model ever
produces credits: ML outputs (areas, strata, auxiliary biomass) feed in as *inputs* with their own
uncertainty, and every number this package produces carries a step-by-step trace with sources.

Primary methodology profile: India CCTS **BM FR05.001 v1.0** (afforestation/reforestation of
degraded mangrove habitats), which adopts CDM AR-AM0014 and the tree-carbon tool **AR-TOOL14**
(CCTS adoption: BM-T-AR-0004).
"""

__version__ = "0.1.0"
