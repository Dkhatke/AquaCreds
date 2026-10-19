"""AquaCreds geospatial feature pipeline.

The SAME functions are used to build training features (ml/pipelines) and to analyse projects in
production (worker `geo` queue), so features can never silently diverge between the two.
"""

__version__ = "0.1.0"
