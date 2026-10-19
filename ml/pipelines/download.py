"""Download pinned public datasets with checksum verification.

Usage:  uv run python ml/pipelines/download.py <dataset> [<dataset> ...]
        uv run python ml/pipelines/download.py --list

Each dataset is pinned to an exact Zenodo record + file; the MD5 published by Zenodo is verified
after download, and licence/attribution is appended to data/LICENSES.csv. Run via `dvc repro` so
outputs are versioned (see dvc.yaml).
"""

import argparse
import csv
import hashlib
import json
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LICENSES = ROOT / "data" / "LICENSES.csv"

# Guardrails: never silently fill the disk.
MAX_FILE_MB = 500  # larger files need --allow-large
MIN_FREE_DISK_GB = 10  # always leave at least this much free after the download


@dataclass(frozen=True)
class ZenodoFile:
    record: int
    filename: str
    dest: str
    licence: str
    citation: str
    purpose: str


DATASETS: dict[str, ZenodoFile] = {
    "coasttrain_v1": ZenodoFile(
        record=7080756,
        filename="nick-murray/coastTrain-v1.0.0.zip",
        dest="data/raw/coasttrain/coastTrain-v1.0.0.zip",
        licence="see record (other-open)",
        citation="Murray et al. 2022, coastTrain, Remote Sensing 14:5766, doi:10.3390/rs14225766",
        purpose="Labelled coastal-ecosystem occurrence points (classifier training labels)",
    ),
    "gmw_v4_2020": ZenodoFile(
        record=12756047,
        filename="gmw_mng_2020_v4019_gtiff.zip",
        dest="data/raw/gmw_v4/gmw_mng_2020_v4019_gtiff.zip",
        licence="CC-BY-4.0",
        citation="Global Mangrove Watch v4.0.19 (2024), Zenodo 12756047",
        purpose="2020 mangrove extent at 10 m (positive samples, reference map)",
    ),
    "gmw_v3_1996": ZenodoFile(
        record=6894273,
        filename="gmw_v3_1996_gtiff.zip",
        dest="data/raw/gmw_v3/gmw_v3_1996_gtiff.zip",
        licence="CC-BY-4.0",
        citation="Bunting et al. 2022, Global Mangrove Watch v3.0, Zenodo 6894273",
        purpose="1996 mangrove extent (historical presence for 'degraded mangrove habitat' check)",
    ),
}


def _record_files(record: int) -> dict[str, dict[str, Any]]:
    req = urllib.request.Request(
        f"https://zenodo.org/api/records/{record}", headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed https URL)
        meta = json.load(resp)
    return {
        f["key"]: {"url": f["links"]["self"], "checksum": f["checksum"], "size": f["size"]}
        for f in meta["files"]
    }


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_resumable(url: str, tmp: Path, attempts: int = 8) -> None:
    """Stream to `tmp`, resuming with HTTP Range after dropped connections."""
    if not url.startswith("https://zenodo.org/"):
        raise SystemExit(f"Refusing to download from unexpected URL: {url}")
    total: int | None = None
    for attempt in range(1, attempts + 1):
        have = tmp.stat().st_size if tmp.exists() else 0
        if total is not None and have >= total:
            break
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            req = urllib.request.Request(url, headers=headers)  # noqa: S310 (checked above)
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                if have and resp.status != 206:  # server ignored Range: restart cleanly
                    have = 0
                    tmp.unlink(missing_ok=True)
                length = int(resp.headers.get("Content-Length", 0))
                total = have + length if length else total
                with tmp.open("ab") as out:
                    for chunk in iter(lambda: resp.read(1 << 20), b""):
                        out.write(chunk)
                        have += len(chunk)
                        if total:
                            sys.stdout.write(f"\r  {have / 1e6:7.1f} / {total / 1e6:.1f} MB")
                            sys.stdout.flush()
            print()
            if total is None or have >= total:
                return
            print(f"  connection ended early at {have} bytes; resuming (attempt {attempt})")
        except (OSError, urllib.error.URLError) as exc:
            print(f"\n  network error: {exc}; resuming (attempt {attempt})")
    if total is not None and tmp.stat().st_size < total:
        raise SystemExit(f"Download incomplete after {attempts} attempts")


def check_limits(name: str, size_bytes: int, dest: Path, *, allow_large: bool) -> None:
    size_mb = size_bytes / 1e6
    if size_mb > MAX_FILE_MB and not allow_large:
        raise SystemExit(
            f"[{name}] {size_mb:.0f} MB exceeds the {MAX_FILE_MB} MB safety limit; "
            "re-run with --allow-large if this is intended"
        )
    free_gb = shutil.disk_usage(dest.parent).free / 1e9
    if free_gb - size_bytes / 1e9 < MIN_FREE_DISK_GB:
        raise SystemExit(
            f"[{name}] only {free_gb:.1f} GB free; downloading {size_mb:.0f} MB would leave less "
            f"than {MIN_FREE_DISK_GB} GB"
        )


def download(name: str, *, allow_large: bool = False) -> Path:
    spec = DATASETS[name]
    files = _record_files(spec.record)
    if spec.filename not in files:
        raise SystemExit(f"{spec.filename} not found in Zenodo record {spec.record}")
    algo, expected = files[spec.filename]["checksum"].split(":", 1)
    if algo != "md5":
        raise SystemExit(f"Unexpected checksum algorithm {algo}")

    dest = ROOT / spec.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and _md5(dest) == expected:
        print(f"[{name}] already present and verified: {dest.relative_to(ROOT)}")
        return dest

    check_limits(name, int(files[spec.filename]["size"]), dest, allow_large=allow_large)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(
        f"[{name}] downloading {spec.filename} ({int(files[spec.filename]['size']) / 1e6:.0f} MB) ..."
    )
    _fetch_resumable(files[spec.filename]["url"], tmp)
    actual = _md5(tmp)
    if actual != expected:
        tmp.unlink()
        raise SystemExit(f"[{name}] MD5 mismatch: expected {expected}, got {actual}")
    tmp.replace(dest)
    _record_licence(name, spec, expected)
    print(f"[{name}] OK  md5={expected}  -> {dest.relative_to(ROOT)}")
    return dest


def _record_licence(name: str, spec: ZenodoFile, md5: str) -> None:
    LICENSES.parent.mkdir(parents=True, exist_ok=True)
    rows: dict[str, dict[str, str]] = {}
    if LICENSES.exists():
        with LICENSES.open() as fh:
            rows = {r["dataset"]: r for r in csv.DictReader(fh)}
    rows[name] = {
        "dataset": name,
        "source": f"https://zenodo.org/records/{spec.record}",
        "file": spec.filename,
        "md5": md5,
        "licence": spec.licence,
        "citation": spec.citation,
        "purpose": spec.purpose,
        "downloaded_utc": datetime.now(UTC).strftime("%Y-%m-%d"),
    }
    with LICENSES.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(next(iter(rows.values()))))
        writer.writeheader()
        writer.writerows(sorted(rows.values(), key=lambda r: r["dataset"]))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("datasets", nargs="*", metavar="DATASET", help=", ".join(DATASETS))
    parser.add_argument("--list", action="store_true")
    parser.add_argument(
        "--allow-large", action="store_true", help=f"permit files over {MAX_FILE_MB} MB"
    )
    args = parser.parse_args()
    if args.list or not args.datasets:
        for key, spec in DATASETS.items():
            size = _record_files(spec.record)[spec.filename]["size"] / 1e6
            print(f"{key:15s} {size:7.1f} MB  zenodo:{spec.record:<9d} {spec.purpose}")
        return
    unknown = sorted(set(args.datasets) - set(DATASETS))
    if unknown:
        parser.error(f"unknown dataset(s): {', '.join(unknown)}")
    for name in args.datasets:
        download(name, allow_large=args.allow_large)


if __name__ == "__main__":
    main()
