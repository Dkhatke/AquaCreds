import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from aqua_mrv import cli
from aqua_mrv.exports import (
    PLOT_COLUMNS,
    TRACE_COLUMNS,
    TREE_COLUMNS,
    to_csv,
    to_csv_zip,
    to_geojson,
    to_html,
)
from aqua_mrv.models import ReportInputs
from aqua_mrv.report import STATUS_LABEL, MRVReport, build_report


def _pdf_backend_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except OSError:  # Pango/HarfBuzz system libraries missing
        return False
    return True


needs_pango = pytest.mark.skipif(
    not _pdf_backend_available(),
    reason="WeasyPrint needs the Pango system library (apt: libpango-1.0-0 libpangoft2-1.0-0)",
)


@pytest.fixture
def report(example_doc: dict[str, Any]) -> MRVReport:
    return build_report(ReportInputs.model_validate(example_doc))


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_csv_tables(report: MRVReport, example_doc: dict[str, Any]) -> None:
    tables = to_csv(report)
    assert set(tables) == {"plots.csv", "trees.csv", "trace.csv"}
    plots = _rows(tables["plots.csv"])
    trees = _rows(tables["trees.csv"])
    trace = _rows(tables["trace.csv"])
    assert tuple(plots[0]) == PLOT_COLUMNS
    assert tuple(trees[0]) == TREE_COLUMNS
    assert tuple(trace[0]) == TRACE_COLUMNS
    assert len(plots) == len(example_doc["plots"])
    assert len(trees) == sum(len(p["trees"]) for p in example_doc["plots"])
    assert trace[-1]["key"] == "tccc"
    assert float(trace[-2]["value"]) == pytest.approx(report.record["result"]["lccc"], abs=1e-3)

    with zipfile.ZipFile(io.BytesIO(to_csv_zip(report))) as zf:
        assert sorted(zf.namelist()) == ["plots.csv", "trace.csv", "trees.csv"]
        assert zf.read("plots.csv").decode() == tables["plots.csv"]


def test_geojson(report: MRVReport, example_doc: dict[str, Any]) -> None:
    gj = to_geojson(report)
    json.dumps(gj)  # serialisable
    assert gj["type"] == "FeatureCollection"
    kinds = [f["properties"]["kind"] for f in gj["features"]]
    assert kinds.count("project_boundary") == 1
    assert kinds.count("sample_plot") == len(example_doc["plots"])
    boundary = gj["features"][0]
    assert boundary["geometry"]["type"] == "Polygon"
    assert boundary["properties"]["report_hash"] == report.report_hash
    ring = boundary["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    for f in gj["features"][1:]:
        lon, lat = f["geometry"]["coordinates"]
        assert -180 <= lon <= 180
        assert -90 <= lat <= 90

    shapely_geometry = pytest.importorskip("shapely.geometry")
    for f in gj["features"]:
        assert shapely_geometry.shape(f["geometry"]).is_valid
    poly = shapely_geometry.shape(boundary["geometry"])
    assert all(poly.contains(shapely_geometry.shape(f["geometry"])) for f in gj["features"][1:])


def test_geojson_feature_boundary_and_plots_without_coordinates(
    example_doc: dict[str, Any],
) -> None:
    doc = dict(example_doc)
    doc["project"] = {
        **example_doc["project"],
        "boundary": {
            "type": "Feature",
            "properties": {},
            "geometry": example_doc["project"]["boundary"],
        },
    }
    doc["plots"] = [{k: v for k, v in p.items() if k not in {"lat", "lon"}} for p in doc["plots"]]
    gj = to_geojson(build_report(ReportInputs.model_validate(doc)))
    assert [f["geometry"]["type"] for f in gj["features"]] == ["Polygon"]

    doc["project"] = {k: v for k, v in example_doc["project"].items() if k != "boundary"}
    assert to_geojson(build_report(ReportInputs.model_validate(doc)))["features"] == []


def test_html_has_label_hash_and_trace(report: MRVReport) -> None:
    html = to_html(report)
    assert STATUS_LABEL in html
    assert report.report_hash in html
    assert "AR-TOOL14" in html
    assert "genus fallback" in html


@needs_pango
def test_pdf_renders_with_hash_and_lccc(report: MRVReport) -> None:
    from aqua_mrv.exports import to_pdf

    pdf = to_pdf(report)
    assert pdf.startswith(b"%PDF")
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    text = "\n".join(page.extract_text() for page in reader.pages)
    compact = "".join(text.split())
    assert report.report_hash in compact
    assert str(report.lccc_units) in compact
    assert "Estimated" in text
    assert "testnet registry units" in text


@needs_pango
def test_cli_build_writes_all_outputs(example_path: Path, tmp_path: Path) -> None:
    assert cli.main(["build", "--input", str(example_path), "--out-dir", str(tmp_path)]) == 0
    names = {p.name for p in tmp_path.iterdir()}
    assert names == {
        "mrv_record.json",
        "report.json",
        "report.pdf",
        "plots.csv",
        "trees.csv",
        "trace.csv",
        "report.geojson",
    }
    summary = json.loads((tmp_path / "report.json").read_text())
    digest = hashlib.sha256((tmp_path / "mrv_record.json").read_bytes()).hexdigest()
    assert summary["report_hash"] == digest
    assert summary["report_hash_bytes32"] == "0x" + digest
    assert isinstance(summary["lccc_units"], int)


def test_cli_without_pdf(example_path: Path, tmp_path: Path, capsys: Any) -> None:
    assert (
        cli.main(["build", "--input", str(example_path), "--out-dir", str(tmp_path), "--no-pdf"])
        == 0
    )
    assert not (tmp_path / "report.pdf").exists()
    out = capsys.readouterr()
    assert "report_hash 0x" in out.out
    assert "genus mean" in out.err
