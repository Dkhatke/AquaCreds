"""Sentinel-2 dry-season (Nov 2020 - Mar 2021) features + Copernicus DEM elevation at every label point.

Usage:  uv run python -u ml/pipelines/features_extract.py [--max-scenes 12] [--workers 16]
                                                        [--max-cells N]  (smoke test)

Inputs : data/processed/labels.parquet               (labels_build.py)
         Sentinel-2 L2A + cop-dem-glo-30 on Microsoft Planetary Computer
Output : data/processed/features.parquet + data/processed/features_summary.json
Cache  : data/interim/features_parts/<config-hash>/<h3>.parquet (one file per H3 cell, so an
         interrupted run resumes where it stopped; the hash changes when the config changes)

How (and why not window reads): pixel values are fetched with the Planetary Computer data API
point endpoint (`/api/data/v1/item/point`), which samples the COG server-side and returns a few
bytes per (point, scene). Measured on this machine, direct COG block reads ran at 0.3-1 MB/s; the
labels touch ~3,600 distinct 512x512 blocks at 10 m (plus ~1,300 at 20 m), i.e. ~90 GB for 12
scenes, which is infeasible. The point API needs ~170k small requests (~2-3 h at ~20 req/s).

The raw DNs are then converted and aggregated with exactly the production code:
`aqua_geo.sentinel2.scale_and_mask` (BOA offset + SCL mask) and `aqua_geo.features.pixel_features`
(indices per scene, then median / p10-p50-p90 / clear count). One difference to production: the
point API samples 20 m bands at their native pixel (nearest), while `sentinel2.load` resamples them
bilinearly to 10 m.

Scene choice per point: scenes whose footprint contains the point, one per acquisition date
(lowest tile cloud cover wins), the `--max-scenes` least cloudy kept.

Memory: only point coordinates and their DN time series are held (a few MB).
"""

import argparse
import hashlib
import json
import resource
import sys
import threading
import time
import warnings
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import h3
import httpx
import numpy as np
import pandas as pd
import pystac
import xarray as xr
from shapely.geometry import Point, Polygon, shape

from aqua_geo import sentinel2 as s2
from aqua_geo.features import FEATURE_VARS, pixel_features
from aqua_geo.network import STAC_TIMEOUT_S

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "data/processed/labels.parquet"
OUT = ROOT / "data/processed/features.parquet"
SUMMARY = ROOT / "data/processed/features_summary.json"
PARTS = ROOT / "data/interim/features_parts"

SEASON_START = date(2020, 11, 1)
SEASON_END = date(2021, 3, 31)
EXCLUDED_LABELS = ("salt_marsh",)  # 15 points: research class, not trained in v1

DATA_API = "https://planetarycomputer.microsoft.com/api/data/v1/item/point"
DEM_COLLECTION = "cop-dem-glo-30"
S2_ASSETS: tuple[str, ...] = tuple(s2.PLANETARY_COMPUTER.asset_names[b] for b in (*s2.BANDS, "scl"))
REQUEST_TIMEOUT_S = 60.0
REQUEST_ATTEMPTS = 6
SEARCH_ATTEMPTS = 5
OUTSIDE = "outside dataset bounds"

PC = s2.PLANETARY_COMPUTER

ID_COLS = ["lon", "lat", "label", "source", "ref_year", "map_derived", "h3", "split"]


# --------------------------------------------------------------------------- pure helpers


@dataclass(frozen=True)
class Scene:
    id: str
    day: str  # YYYY-MM-DD (solar day)
    cloud: float
    footprint: Any  # shapely geometry (WGS84)
    offset: float  # BOA offset to subtract from DNs


def scenes_from_items(items: Sequence[pystac.Item]) -> list[Scene]:
    out = []
    for item in items:
        if item.geometry is None:
            continue
        out.append(
            Scene(
                id=item.id,
                day=str(item.properties["datetime"])[:10],
                cloud=float(item.properties.get("eo:cloud_cover", 100.0)),
                footprint=shape(item.geometry),
                offset=s2.boa_offset(item, PC),
            )
        )
    return out


def select_scenes(scenes: Sequence[Scene], lon: float, lat: float, max_scenes: int) -> list[Scene]:
    """Scenes covering (lon, lat): one per day (least cloudy tile), the `max_scenes` least
    cloudy overall, returned in date order."""
    pt = Point(lon, lat)
    best: dict[str, Scene] = {}
    for sc in scenes:
        if not sc.footprint.covers(pt):
            continue
        cur = best.get(sc.day)
        if cur is None or (sc.cloud, sc.id) < (cur.cloud, cur.id):
            best[sc.day] = sc
    chosen = sorted(best.values(), key=lambda s: (s.cloud, s.day, s.id))[:max_scenes]
    return sorted(chosen, key=lambda s: s.day)


def assemble_raw(
    n_points: int,
    observations: dict[tuple[int, str], Sequence[float]],
    scenes_by_id: dict[str, Scene],
) -> tuple[xr.Dataset, list[float]]:
    """DN cube with dims (time, point) from {(point index, scene id): values in S2_ASSETS order}.

    Unobserved (point, day) cells are DN 0 / SCL 0 (no data), which `scale_and_mask` treats as
    invalid, so they are never counted as clear. Returns the cube and one BOA offset per day.
    """
    days = sorted({scenes_by_id[sid].day for _, sid in observations})
    if not days:
        raise ValueError("no observations")
    t_index = {d: i for i, d in enumerate(days)}
    offsets = [0.0] * len(days)
    for _, sid in observations:
        sc = scenes_by_id[sid]
        offsets[t_index[sc.day]] = sc.offset
    cube = np.zeros((len(S2_ASSETS), len(days), n_points), dtype="uint16")
    for (p, sid), values in observations.items():
        if len(values) != len(S2_ASSETS):
            raise ValueError(f"expected {len(S2_ASSETS)} values, got {len(values)}")
        vals = np.nan_to_num(np.asarray(values, dtype="float64"), nan=0.0)
        cube[:, t_index[scenes_by_id[sid].day], p] = np.clip(vals, 0, 65535).astype("uint16")
    names = [*s2.BANDS, "scl"]
    dims = ("time", "point")
    coords = {"time": np.array(days, dtype="datetime64[ns]"), "point": np.arange(n_points)}
    data = {
        name: (dims, cube[i].astype("uint8") if name == "scl" else cube[i])
        for i, name in enumerate(names)
    }
    return xr.Dataset(data, coords=coords), offsets


def point_features(raw: xr.Dataset, offsets: Sequence[float]) -> pd.DataFrame:
    """Production feature code on a (time, point) DN cube → one row per point."""
    refl = s2.scale_and_mask(raw, offsets, list(s2.BANDS))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices → NaN, handled below
        feats = pixel_features(refl).compute()
    frame = pd.DataFrame({v: np.asarray(feats[v].values) for v in FEATURE_VARS})
    frame["clear_count"] = frame["clear_count"].astype("int16")
    frame["n_scenes"] = (raw["scl"] > 0).sum("time").values.astype("int16")
    return frame


def config_hash(config: dict[str, Any]) -> str:
    blob = json.dumps(config, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


# --------------------------------------------------------------------------- network


class Throttle:
    """Shared client-side rate limit + global back-off. Planetary Computer rate-limits per IP
    (HTTP 429 / "exceeded a rate limit" at ~38 req/s from this machine), and other jobs may share
    the IP, so all threads slow down together when any of them is told to."""

    def __init__(self, rate_per_s: float) -> None:
        self.interval = 1.0 / rate_per_s
        self.lock = threading.Lock()
        self.next_slot = 0.0
        self.pause_until = 0.0
        self.pauses = 0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next_slot, self.pause_until)
            self.next_slot = slot + self.interval
        time.sleep(max(0.0, slot - time.monotonic()))

    def back_off(self, seconds: float) -> None:
        with self.lock:
            until = time.monotonic() + seconds
            if until > self.pause_until:
                self.pause_until = until
                self.pauses += 1


def _is_rate_limit(status: int, text: str) -> bool:
    return status == 429 or "rate limit" in text.lower()


class PointApi:
    """Planetary Computer data-API point reads with bounded timeouts, retries and throttling."""

    def __init__(self, workers: int, rate_per_s: float) -> None:
        self.client = httpx.Client(
            timeout=REQUEST_TIMEOUT_S,
            limits=httpx.Limits(max_connections=workers, max_keepalive_connections=workers),
        )
        self.throttle = Throttle(rate_per_s)
        self.lock = threading.Lock()
        self.requests = 0
        self.retries = 0

    def values(
        self, collection: str, item_id: str, lon: float, lat: float, assets: Sequence[str]
    ) -> list[float] | None:
        """Pixel values (one per asset), None if the point lies outside the item's raster."""
        params = [("collection", collection), ("item", item_id)] + [("assets", a) for a in assets]
        last = ""
        for attempt in range(REQUEST_ATTEMPTS):
            self.throttle.wait()
            with self.lock:
                self.requests += 1
                self.retries += attempt > 0
            try:
                r = self.client.get(f"{DATA_API}/{lon:.7f},{lat:.7f}", params=params)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if r.status_code == 200:
                    return [np.nan if v is None else float(v) for v in r.json()["values"]]
                if OUTSIDE in r.text:
                    return None
                last = f"HTTP {r.status_code}: {r.text[:120]}"
                if _is_rate_limit(r.status_code, r.text):
                    retry_after = r.headers.get("Retry-After", "")
                    self.throttle.back_off(float(retry_after) if retry_after.isdigit() else 30.0)
                    continue
                if r.status_code < 500:
                    break
            time.sleep(min(30.0, 2.0 * 2**attempt))
        raise RuntimeError(f"{collection}/{item_id} @ {lon:.5f},{lat:.5f}: {last}")


def search_items(collection: str, geometry: Polygon, throttle: Throttle) -> list[pystac.Item]:
    from pystac_client import Client

    last: Exception | None = None
    for attempt in range(SEARCH_ATTEMPTS):
        throttle.wait()
        try:
            # sign=False: the data API reads the COGs server-side, so no SAS token is needed.
            if collection == PC.collection:
                return s2.search(geometry, SEASON_START, SEASON_END, max_cloud=80.0, sign=False)
            client = Client.open(PC.stac_url, timeout=STAC_TIMEOUT_S)
            return list(client.search(collections=[collection], intersects=geometry).items())
        except Exception as exc:  # pystac-client raises several unrelated types
            last = exc
            if _is_rate_limit(0, str(exc)):
                throttle.back_off(60.0)
            else:
                time.sleep(5.0 * (attempt + 1))
    raise RuntimeError(f"STAC search {collection} failed: {last}")


def cell_polygon(cell: str) -> Polygon:
    return Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(cell)])


def extract_cell(
    cell: str,
    pts: pd.DataFrame,
    api: PointApi,
    pool: ThreadPoolExecutor,
    max_scenes: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Features for all points of one H3 cell (+ a small per-cell report)."""
    t0 = time.time()
    poly = cell_polygon(cell).buffer(0.01)  # labels can sit just across a cell edge after caps
    scenes = scenes_from_items(search_items(s2.PLANETARY_COMPUTER.collection, poly, api.throttle))
    dem_items = search_items(DEM_COLLECTION, poly, api.throttle)
    by_id = {sc.id: sc for sc in scenes}
    lons, lats = pts.lon.to_numpy(), pts.lat.to_numpy()

    jobs: list[tuple[int, str]] = []
    for p in range(len(pts)):
        jobs += [(p, sc.id) for sc in select_scenes(scenes, lons[p], lats[p], max_scenes)]

    def s2_job(job: tuple[int, str]) -> tuple[tuple[int, str], list[float] | None, str | None]:
        p, sid = job
        try:
            return job, api.values("sentinel-2-l2a", sid, lons[p], lats[p], S2_ASSETS), None
        except RuntimeError as exc:
            return job, None, str(exc)

    def dem_job(p: int) -> tuple[float, str | None]:
        pt = Point(lons[p], lats[p])
        for item in dem_items:
            if item.geometry is not None and shape(item.geometry).covers(pt):
                try:
                    vals = api.values(DEM_COLLECTION, item.id, lons[p], lats[p], ["data"])
                except RuntimeError as exc:
                    return np.nan, str(exc)
                if vals is not None:
                    return vals[0], None
        return np.nan, None  # no DEM tile (open sea): legitimately missing

    obs: dict[tuple[int, str], Sequence[float]] = {}
    errors: list[tuple[int, str]] = []
    for job, values, err in pool.map(s2_job, jobs):
        if err:
            errors.append((job[0], err))
        elif values is not None:
            obs[job] = values
    dem = list(pool.map(dem_job, range(len(pts))))
    errors += [(p, e) for p, (_, e) in enumerate(dem) if e]

    if obs:
        raw, offsets = assemble_raw(len(pts), obs, by_id)
        feats = point_features(raw, offsets)
    else:
        feats = pd.DataFrame({v: np.full(len(pts), np.nan) for v in FEATURE_VARS})
        feats["clear_count"] = np.zeros(len(pts), dtype="int16")
        feats["n_scenes"] = np.zeros(len(pts), dtype="int16")
    feats["elevation"] = np.asarray([v for v, _ in dem], dtype="float32")
    failed_per_point = np.zeros(len(pts), dtype="int16")
    for p, _ in errors:
        failed_per_point[p] += 1
    feats["failed_requests"] = failed_per_point
    out = pd.concat([pts[ID_COLS].reset_index(drop=True), feats], axis=1)
    report = {
        "cell": cell,
        "points": len(pts),
        "scenes_available": len(scenes),
        "requests": len(jobs) + len(pts),
        "failed_requests": len(errors),
        "errors_sample": [e for _, e in errors[:3]],
        "seconds": round(time.time() - t0, 1),
    }
    return out, report


# --------------------------------------------------------------------------- driver


def peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1e6 if sys.platform == "darwin" else rss / 1e3  # bytes on macOS, KiB on Linux


def run(
    labels: pd.DataFrame,
    *,
    max_scenes: int,
    workers: int,
    cell_workers: int,
    rate_per_s: float,
    passes: int,
    parts_dir: Path,
    log: Callable[[str], None] = print,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, str]]]:
    parts_dir.mkdir(parents=True, exist_ok=True)
    cells = labels.groupby("h3")
    todo = [c for c in sorted(cells.groups) if not (parts_dir / f"{c}.parquet").exists()]
    log(f"{len(cells)} cells, {len(todo)} to extract ({len(cells) - len(todo)} cached)")
    api = PointApi(workers, rate_per_s)
    failed: dict[str, str] = {}
    lock = threading.Lock()
    t0 = time.time()
    stop = threading.Event()

    def heartbeat() -> None:
        """Log throughput every 5 min so a stalled run is visible in the log."""
        last = 0
        while not stop.wait(300):
            n = api.requests
            note = "  STALLED: no requests in 5 min" if n == last else ""
            log(f"  heartbeat: {n} requests, {n - last} in last 5 min{note}")
            last = n

    threading.Thread(target=heartbeat, daemon=True).start()

    # A cell with failed requests (rate limits, timeouts) is not cached; it is retried in the next
    # pass after a cool-down. In the last pass whatever was obtained is kept and the failures
    # stay visible per point (`failed_requests`) and in the summary.
    for attempt in range(passes):
        final = attempt == passes - 1
        retry: list[str] = []
        done = [0]
        if attempt:
            log(f"pass {attempt + 1}/{passes}: retrying {len(todo)} cells after a 120 s cool-down")
            time.sleep(120)
        with (
            ThreadPoolExecutor(max_workers=workers) as req_pool,
            ThreadPoolExecutor(max_workers=cell_workers) as cell_pool,
        ):

            def one(
                cell: str,
                final: bool = final,
                retry: list[str] = retry,
                done: list[int] = done,
                total: int = len(todo),
            ) -> None:
                pts = cells.get_group(cell)
                try:
                    frame, report = extract_cell(cell, pts, api, req_pool, max_scenes)
                except Exception as exc:  # recorded in the summary; the run continues
                    with lock:
                        failed[cell] = f"{type(exc).__name__}: {exc}"[:300]
                        retry.append(cell)
                    log(f"  FAILED cell {cell}: {exc}")
                    return
                if report["failed_requests"] and not final:
                    with lock:
                        retry.append(cell)
                    return
                with lock:
                    failed.pop(cell, None)
                frame.attrs = {}
                tmp = parts_dir / f"{cell}.parquet.tmp"
                frame.to_parquet(tmp)
                tmp.rename(parts_dir / f"{cell}.parquet")
                (parts_dir / f"{cell}.json").write_text(json.dumps(report) + "\n")
                with lock:
                    done[0] += 1
                    n = done[0]
                if n % 10 == 0:
                    rate = api.requests / max(time.time() - t0, 1e-6)
                    log(
                        f"  [{n}/{total}] {cell}: {report['points']} pts, "
                        f"{report['requests']} req, {report['seconds']} s | {rate:.1f} req/s, "
                        f"retries {api.retries}, pauses {api.throttle.pauses}, "
                        f"peak {peak_rss_mb():.0f} MB"
                    )

            list(cell_pool.map(one, todo))
        log(f"pass {attempt + 1}: {done[0]} cells written, {len(retry)} to retry")
        todo = sorted(retry)
        if not todo:
            break

    stop.set()
    frames = []
    reports = []
    for cell in sorted(cells.groups):
        part = parts_dir / f"{cell}.parquet"
        if part.exists():
            frames.append(pd.read_parquet(part))
            reports.append(json.loads((parts_dir / f"{cell}.json").read_text()))
    table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return table, reports, [{"cell": c, "error": e} for c, e in sorted(failed.items())]


def summarise(
    table: pd.DataFrame,
    reports: list[dict[str, Any]],
    failed: list[dict[str, str]],
    labels: pd.DataFrame,
    config: dict[str, Any],
    wall_s: float,
) -> dict[str, Any]:
    usable = table["clear_count"] >= 3 if len(table) else pd.Series(dtype=bool)
    return {
        "config": config,
        "points_in": len(labels),
        "points_out": len(table),
        "points_usable": int(usable.sum()),
        "usable_fraction": round(float(usable.mean()), 4) if len(table) else 0.0,
        "cells_in": int(labels.h3.nunique()),
        "cells_out": len(reports),
        "failed_cells": failed,
        "failed_requests": int(sum(r["failed_requests"] for r in reports)),
        "requests": int(sum(r["requests"] for r in reports)),
        "points_with_failed_requests": int((table.get("failed_requests", 0) > 0).sum()),
        "clear_count": {
            "median": float(table.clear_count.median()) if len(table) else 0.0,
            "p10": float(table.clear_count.quantile(0.1)) if len(table) else 0.0,
            "zero": int((table.clear_count == 0).sum()) if len(table) else 0,
        },
        "elevation_missing": int(table.elevation.isna().sum()) if len(table) else 0,
        "usable_by_label": table[usable].label.value_counts().to_dict() if len(table) else {},
        "usable_by_label_and_split": (
            table[usable].groupby(["label", "split"]).size().unstack(fill_value=0).to_dict()
            if len(table)
            else {}
        ),
        "unusable_by_label": table[~usable].label.value_counts().to_dict() if len(table) else {},
        "runtime_s": {
            "this_invocation_wall": round(wall_s, 1),
            "sum_of_cell_seconds": round(sum(r["seconds"] for r in reports), 1),
        },
        "peak_rss_mb_this_invocation": round(peak_rss_mb(), 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--max-scenes", type=int, default=12)
    parser.add_argument("--workers", type=int, default=16, help="concurrent point requests")
    parser.add_argument("--cell-workers", type=int, default=4, help="cells in flight")
    parser.add_argument("--rate", type=float, default=20.0, help="max requests per second")
    parser.add_argument("--passes", type=int, default=3, help="retry passes for failed cells")
    parser.add_argument("--max-cells", type=int, default=0, help="smoke test: first N cells")
    args = parser.parse_args()

    t0 = time.time()
    labels = pd.read_parquet(LABELS)
    labels = labels[~labels.label.isin(EXCLUDED_LABELS)].reset_index(drop=True)
    config = {
        "season": [SEASON_START.isoformat(), SEASON_END.isoformat()],
        "max_scenes": args.max_scenes,
        "max_tile_cloud": 80.0,
        "features": list(FEATURE_VARS),
        "min_clear": 3,
        "dem": DEM_COLLECTION,
        "excluded_labels": list(EXCLUDED_LABELS),
    }
    parts_dir = PARTS / config_hash(config)
    if args.max_cells:
        keep = sorted(labels.h3.unique())[: args.max_cells]
        labels = labels[labels.h3.isin(keep)].reset_index(drop=True)

    table, reports, failed = run(
        labels,
        max_scenes=args.max_scenes,
        workers=args.workers,
        cell_workers=args.cell_workers,
        rate_per_s=args.rate,
        passes=args.passes,
        parts_dir=parts_dir,
        log=lambda m: print(m, flush=True),
    )
    summary = summarise(table, reports, failed, labels, config, time.time() - t0)
    if args.max_cells:
        print(json.dumps(summary, indent=2, default=int))
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(OUT)
    SUMMARY.write_text(json.dumps(summary, indent=2, default=int) + "\n")
    print(json.dumps({k: summary[k] for k in list(summary)[1:9]}, indent=2, default=int))


if __name__ == "__main__":
    main()
