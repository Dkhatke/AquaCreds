"""Exports of an MRV report: CSV tables, GeoJSON, and a PDF (WeasyPrint from a Jinja2 template).

Exports are views of the hashed record; they carry the report hash but are not themselves hashed.
"""

import csv
import io
import zipfile
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from aqua_mrv.report import MRVReport

PLOT_COLUMNS = (
    "code",
    "stratum",
    "area_m2",
    "lat",
    "lon",
    "live_trees",
    "dead_trees",
    "agb_t_dm_ha",
    "bgb_t_dm_ha",
    "total_t_dm_ha",
)
TREE_COLUMNS = (
    "plot_code",
    "n",
    "species",
    "dbh_cm",
    "height_m",
    "alive",
    "wood_density_g_cm3",
    "agb_kg",
    "bgb_kg",
)
TRACE_COLUMNS = ("key", "label", "value", "unit", "formula", "source")


def _table(columns: tuple[str, ...], rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: "" if row.get(k) is None else row[k] for k in columns})
    return buf.getvalue()


def to_csv(report: MRVReport) -> dict[str, str]:
    """{"plots.csv", "trees.csv", "trace.csv"} → CSV text."""
    plots = report.record["plots"]
    trees = [{"plot_code": p["code"], **t} for p in plots for t in p["trees"]]
    return {
        "plots.csv": _table(PLOT_COLUMNS, plots),
        "trees.csv": _table(TREE_COLUMNS, trees),
        "trace.csv": _table(TRACE_COLUMNS, report.record["trace"]),
    }


def to_csv_zip(report: MRVReport) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in to_csv(report).items():
            zf.writestr(name, text)
    return buf.getvalue()


def to_geojson(report: MRVReport) -> dict[str, Any]:
    """FeatureCollection: the project boundary (if given) plus one Point per plot with coordinates."""
    rec = report.record
    project = rec["project"]
    features: list[dict[str, Any]] = []
    boundary = project.get("boundary")
    if boundary:
        geometry = boundary["geometry"] if boundary.get("type") == "Feature" else boundary
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "kind": "project_boundary",
                    "project_id": project["id"],
                    "name": project["name"],
                    "area_ha": project["area_ha"],
                    "report_hash": report.report_hash,
                },
            }
        )
    for plot in rec["plots"]:
        if plot["lat"] is None:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [plot["lon"], plot["lat"]]},
                "properties": {
                    "kind": "sample_plot",
                    "code": plot["code"],
                    "stratum": plot["stratum"],
                    "area_m2": plot["area_m2"],
                    "total_t_dm_ha": plot["total_t_dm_ha"],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


_env = Environment(
    loader=PackageLoader("aqua_mrv", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _fmt(value: Any, nd: int = 2) -> str:
    if isinstance(value, bool) or value is None:
        return "—" if value is None else str(value)
    if isinstance(value, int | float):
        return f"{value:,.{nd}f}"
    return str(value)


_env.filters["num"] = _fmt


def to_html(report: MRVReport) -> str:
    return _env.get_template("report.html.j2").render(
        r=report.record, report_hash=report.report_hash, lccc_units=report.lccc_units
    )


def to_pdf(report: MRVReport) -> bytes:
    from weasyprint import HTML  # imported lazily: needs the Pango system library

    pdf: bytes = HTML(string=to_html(report)).write_pdf()
    return pdf
