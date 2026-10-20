"""Command line: `python -m aqua_mrv.cli build --input example.json --out-dir out/`.

Writes into the output directory:
- `mrv_record.json`  the exact RFC 8785 bytes that are hashed (`sha256sum` of it = report hash)
- `report.json`      {report_hash, report_hash_bytes32, lccc_units, record} (pretty-printed)
- `report.pdf`, `plots.csv`, `trees.csv`, `trace.csv`, `report.geojson`
"""

import argparse
import json
import sys
from pathlib import Path

from aqua_mrv.exports import to_csv, to_geojson, to_pdf
from aqua_mrv.models import ReportInputs
from aqua_mrv.report import MRVReport, build_report


def write_outputs(report: MRVReport, out_dir: Path, *, pdf: bool = True) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def put(name: str, data: bytes | str) -> None:
        path = out_dir / name
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
        written.append(path)

    put("mrv_record.json", report.canonical_json())
    summary = {
        "report_hash": report.report_hash,
        "report_hash_bytes32": "0x" + report.report_hash_bytes32().hex(),
        "lccc_units": report.lccc_units,
        "record": report.record,
    }
    put("report.json", json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    for name, text in to_csv(report).items():
        put(name, text)
    put("report.geojson", json.dumps(to_geojson(report), indent=2) + "\n")
    if pdf:
        put("report.pdf", to_pdf(report))
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aqua_mrv", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="Build an MRV report from a JSON input file")
    build.add_argument("--input", required=True, type=Path)
    build.add_argument("--out-dir", required=True, type=Path)
    build.add_argument("--no-pdf", action="store_true", help="Skip the PDF (no Pango needed)")
    args = parser.parse_args(argv)

    inputs = ReportInputs.model_validate_json(args.input.read_text(encoding="utf-8"))
    report = build_report(inputs)
    for path in write_outputs(report, args.out_dir, pdf=not args.no_pdf):
        print(path)
    print(f"report_hash 0x{report.report_hash}  lccc_units {report.lccc_units}")
    for w in report.warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
