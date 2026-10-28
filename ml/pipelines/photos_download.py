"""Download openly licensed iNaturalist photos for the field-photo verifier (Model C, Phase 1f).

Usage::

    uv run python -u ml/pipelines/photos_download.py list    # query the API → data/photos_manifest.csv
    uv run python -u ml/pipelines/photos_download.py fetch   # download + resize the images
    uv run python -u ml/pipelines/photos_download.py summary # counts per class / licence

What it collects (fixed here, before any evaluation):

* **Positives** (`label = mangrove`): research-grade observations of the 11 Indian mangrove genera in
  `data/reference/wood_density_mangroves.csv`. India first, then Asia, then the rest of the world, up
  to the per-genus quota.
* **Hard negatives** (`label = non_mangrove`): coastal look-alikes. Real taxa where iNaturalist has
  them (Prosopis/Neltuma juliflora, coconut, Casuarina, salt-marsh succulents, beach-dune creepers,
  coastal trees, grasses). Scenes that iNaturalist does not index (mudflats, cities) use *habitat
  proxies*: shorebirds/mudskippers photographed on mud, and the house crow (mostly urban photos).
  These proxies are documented as such in the model card. No iNaturalist proxy exists for
  aquaculture ponds; that gap is documented, not papered over.

Rules (the user's standing limits):

* Only photos whose own licence is CC0, CC BY or CC BY-NC; each row records the licence code, the
  iNaturalist attribution string, the observer and the observation URL.
* API ≤ 1 request/s with a descriptive User-Agent; page responses are cached on disk so a re-run
  resumes without re-querying. Image fetches are also spaced ≥ 1 s apart.
* ≤ ~3,000 images, resized to ≤ 512 px (iNaturalist "medium" = 500 px), one photo per observation,
  at most `MAX_PER_OBSERVER` per class so a few prolific observers cannot dominate.
* Size guards: a single image response > `MAX_IMAGE_BYTES` is refused, and the run stops before
  the cumulative download exceeds `DOWNLOAD_BUDGET_BYTES`.

Images go to `data/raw/photos/images/` (git-ignored). The manifest `data/photos_manifest.csv` is
small and committed.
"""

import argparse
import csv
import hashlib
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "photos"
IMAGES = RAW / "images"
API_CACHE = RAW / "api_cache"
MANIFEST = ROOT / "data" / "photos_manifest.csv"
DOWNLOAD_LOG = RAW / "download_log.json"

API = "https://api.inaturalist.org/v1/"
USER_AGENT = (
    "AquaCreds-photo-verifier/0.1 (blue-carbon MRV research; github.com/Dkhatke/AquaCreds; "
    "1 req/s)"
)
ALLOWED_LICENCES = ("cc0", "cc-by", "cc-by-nc")
REQUEST_INTERVAL_S = 1.05
MAX_IMAGE_BYTES = 3 * 1024 * 1024
DOWNLOAD_BUDGET_BYTES = 900 * 1024 * 1024  # images only; the task-wide cap is 2 GB
MAX_SIDE_PX = 512
MAX_PER_OBSERVER = 30
PER_PAGE = 200

INDIA = 6681
ASIA = 97395
REGION_TIERS: tuple[tuple[str, int | None], ...] = (("india", INDIA), ("asia", ASIA), ("world", None))

MANIFEST_FIELDS = (
    "photo_id",
    "observation_id",
    "class_key",
    "label",
    "genus",
    "taxon_name",
    "taxon_id",
    "proxy",
    "region_tier",
    "country_india",
    "state",
    "lat",
    "lon",
    "observed_on",
    "observer_id",
    "observer_login",
    "licence",
    "attribution",
    "observation_url",
    "photo_url",
    "file",
    "width",
    "height",
    "bytes",
    "sha256",
)


@dataclass(frozen=True)
class ClassSpec:
    key: str
    label: str  # "mangrove" | "non_mangrove"
    taxa: tuple[str, ...]  # iNaturalist scientific names (searched at any rank)
    quota: int
    genus: str = ""  # positives only
    proxy: str = ""  # non-empty when the taxon stands in for a scene type

    @property
    def is_positive(self) -> bool:
        return self.label == "mangrove"


POSITIVE_GENERA = (
    "Avicennia",
    "Rhizophora",
    "Bruguiera",
    "Sonneratia",
    "Excoecaria",
    "Ceriops",
    "Kandelia",
    "Aegiceras",
    "Heritiera",
    "Lumnitzera",
    "Xylocarpus",
)
GENUS_QUOTA = 155

CLASSES: tuple[ClassSpec, ...] = (
    *(ClassSpec(g.lower(), "mangrove", (g,), GENUS_QUOTA, genus=g) for g in POSITIVE_GENERA),
    ClassSpec("prosopis", "non_mangrove", ("Neltuma juliflora",), 160),
    ClassSpec("coconut", "non_mangrove", ("Cocos nucifera",), 150),
    ClassSpec("casuarina", "non_mangrove", ("Casuarina equisetifolia",), 150),
    ClassSpec(
        "salt_marsh", "non_mangrove", ("Suaeda", "Salicornia", "Sesuvium portulacastrum"), 160
    ),
    ClassSpec("beach_dune", "non_mangrove", ("Ipomoea pes-caprae", "Spinifex littoreus"), 110),
    ClassSpec("coastal_trees", "non_mangrove", ("Thespesia populnea", "Pandanus"), 110),
    ClassSpec("grassland", "non_mangrove", ("Saccharum spontaneum",), 110),
    ClassSpec(
        "mudflat_proxy",
        "non_mangrove",
        ("Calidris", "Boleophthalmus"),
        110,
        proxy="mudflat: shorebirds / mudskippers photographed on open mud",
    ),
    ClassSpec(
        "urban_proxy",
        "non_mangrove",
        ("Corvus splendens",),
        100,
        proxy="urban: house crow observations (mostly towns and cities)",
    ),
)


# ---------------------------------------------------------------- pure helpers (unit-tested)


def licence_ok(code: str | None) -> bool:
    return (code or "").lower() in ALLOWED_LICENCES


def medium_url(url: str) -> str:
    """iNaturalist photo URLs end in `<size>.<ext>`; "medium" is ≤ 500 px on the long side."""
    head, _, tail = url.rpartition("/")
    ext = tail.rsplit(".", 1)[1] if "." in tail else "jpg"
    return f"{head}/medium.{ext}"


def resize_jpeg(data: bytes, max_side: int = MAX_SIDE_PX) -> tuple[bytes, int, int]:
    """Decode, drop EXIF/alpha, shrink so the long side ≤ max_side, re-encode as JPEG q90."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        img.draft("RGB", (max_side, max_side))
        rgb = img.convert("RGB")
        rgb.thumbnail((max_side, max_side))
        out = io.BytesIO()
        rgb.save(out, "JPEG", quality=90, optimize=True)
        return out.getvalue(), rgb.width, rgb.height


def observation_row(
    obs: dict[str, Any], spec: ClassSpec, tier: str, states: dict[int, str]
) -> dict[str, Any] | None:
    """Manifest row for the first openly licensed photo of an observation (or None)."""
    photos = [p for p in obs.get("photos") or [] if licence_ok(p.get("license_code"))]
    if not photos or obs.get("quality_grade") != "research":
        return None
    photo = photos[0]
    taxon = obs.get("taxon") or {}
    user = obs.get("user") or {}
    lat = lon = ""
    loc = obs.get("location")
    if loc and "," in loc:
        lat, lon = (s.strip() for s in loc.split(",", 1))
    place_ids = set(obs.get("place_ids") or [])
    state = next((name for pid, name in states.items() if pid in place_ids), "")
    genus = spec.genus or ""
    if spec.is_positive:
        name = str(taxon.get("name", ""))
        if not name.split(" ")[0] == spec.genus:
            return None  # e.g. a hybrid or a taxon swap; never guess the genus
    return {
        "photo_id": int(photo["id"]),
        "observation_id": int(obs["id"]),
        "class_key": spec.key,
        "label": spec.label,
        "genus": genus,
        "taxon_name": taxon.get("name", ""),
        "taxon_id": taxon.get("id", ""),
        "proxy": spec.proxy,
        "region_tier": tier,
        "country_india": INDIA in place_ids,
        "state": state,
        "lat": lat,
        "lon": lon,
        "observed_on": obs.get("observed_on") or "",
        "observer_id": user.get("id", ""),
        "observer_login": user.get("login", ""),
        "licence": str(photo["license_code"]).lower(),
        "attribution": photo.get("attribution", ""),
        "observation_url": f"https://www.inaturalist.org/observations/{obs['id']}",
        "photo_url": medium_url(str(photo["url"])),
        "file": f"images/{spec.key}/{photo['id']}.jpg",
        "width": "",
        "height": "",
        "bytes": "",
        "sha256": "",
    }


def select_rows(
    candidates: Iterable[dict[str, Any]],
    quota: int,
    max_per_observer: int = MAX_PER_OBSERVER,
    taken: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Fill a class quota in order, one photo per observation, observer cap, no duplicate photos."""
    chosen: list[dict[str, Any]] = []
    seen_obs = {r["observation_id"] for r in taken}
    seen_photos = {r["photo_id"] for r in taken}
    per_observer = Counter(r["observer_id"] for r in taken)
    budget = quota - len(seen_obs)
    for row in candidates:
        if budget <= 0:
            break
        if row["observation_id"] in seen_obs or row["photo_id"] in seen_photos:
            continue
        if per_observer[row["observer_id"]] >= max_per_observer:
            continue
        chosen.append(row)
        seen_obs.add(row["observation_id"])
        seen_photos.add(row["photo_id"])
        per_observer[row["observer_id"]] += 1
        budget -= 1
    return chosen


def read_manifest(path: Path = MANIFEST) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_manifest(rows: list[dict[str, Any]], path: Path = MANIFEST) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


# ---------------------------------------------------------------- network (rate-limited)


class Client:
    """iNaturalist API client: ≥ REQUEST_INTERVAL_S between requests, on-disk page cache."""

    def __init__(self, cache_dir: Path = API_CACHE) -> None:
        self.cache_dir = cache_dir
        self._last = 0.0

    def _wait(self) -> None:
        delay = self._last + REQUEST_INTERVAL_S - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()

    def get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode(sorted(params.items()))
        key = hashlib.sha256(f"{path}?{query}".encode()).hexdigest()[:24]
        cached = self.cache_dir / f"{key}.json"
        if cached.exists():
            data: dict[str, Any] = json.loads(cached.read_text())
            return data
        self._wait()
        req = urllib.request.Request(  # noqa: S310 (fixed https API)
            f"{API}{path}?{query}", headers={"User-Agent": USER_AGENT}
        )
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                    data = json.load(resp)
                break
            except (TimeoutError, ConnectionError, urllib.error.URLError) as exc:
                if attempt == 3:
                    raise
                print(f"  retry {path} after {type(exc).__name__}", flush=True)
                time.sleep(5 * (attempt + 1))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data))
        return data

    def get_bytes(self, url: str, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
        self._wait()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            length = resp.headers.get("Content-Length")
            if length is not None and int(length) > max_bytes:
                raise ValueError(f"{url} is {int(length)} bytes (> {max_bytes}); refused")
            data: bytes = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"{url} exceeds {max_bytes} bytes; refused")
        return data


def taxon_id(client: Client, name: str) -> int:
    data = client.get_json("taxa", {"q": name, "is_active": "true", "per_page": 10})
    for result in data["results"]:
        if result["name"] == name:
            return int(result["id"])
    raise LookupError(f"iNaturalist has no active taxon named {name!r}")


def iter_observations(
    client: Client, taxon_ids: list[int], place_id: int | None, max_pages: int = 25
) -> Iterator[dict[str, Any]]:
    """Research-grade, openly licensed observations with photos, oldest id first (stable paging)."""
    id_above = 0
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "taxon_id": ",".join(map(str, taxon_ids)),
            "quality_grade": "research",
            "photo_license": ",".join(ALLOWED_LICENCES),
            "photos": "true",
            "per_page": PER_PAGE,
            "order_by": "id",
            "order": "asc",
            "id_above": id_above,
        }
        if place_id is not None:
            params["place_id"] = place_id
        results = client.get_json("observations", params)["results"]
        yield from results
        if len(results) < PER_PAGE:
            return
        id_above = int(results[-1]["id"])


def resolve_states(client: Client, place_ids: set[int]) -> dict[int, str]:
    states: dict[int, str] = {}
    ids = sorted(place_ids)
    for i in range(0, len(ids), 100):
        batch = ids[i : i + 100]
        data = client.get_json("places/" + ",".join(map(str, batch)), {})
        for place in data["results"]:
            # Only place ids seen on Indian observations are passed in, so admin level 10 = state/UT.
            ancestors = place.get("ancestor_place_ids") or [INDIA]
            if place.get("admin_level") == 10 and INDIA in ancestors:
                states[int(place["id"])] = str(place["name"])
    return states


def cmd_list(client: Client) -> list[dict[str, Any]]:
    # Pass 1: Indian observations of every class, to learn which place ids are Indian states.
    india_obs: dict[str, list[dict[str, Any]]] = {}
    ids_by_class: dict[str, list[int]] = {}
    for spec in CLASSES:
        ids_by_class[spec.key] = [taxon_id(client, name) for name in spec.taxa]
        india_obs[spec.key] = list(iter_observations(client, ids_by_class[spec.key], INDIA, 10))
    place_ids = {
        pid for obs in india_obs.values() for o in obs for pid in (o.get("place_ids") or [])
    }
    states = resolve_states(client, place_ids)
    print(f"{len(states)} Indian states/UTs seen", flush=True)

    rows: list[dict[str, Any]] = []
    for spec in CLASSES:
        chosen: list[dict[str, Any]] = []
        for tier, place in REGION_TIERS:
            if len(chosen) >= spec.quota:
                break
            obs = (
                india_obs[spec.key]
                if place == INDIA
                else iter_observations(client, ids_by_class[spec.key], place)
            )
            candidates = (r for o in obs if (r := observation_row(o, spec, tier, states)))
            chosen += select_rows(candidates, spec.quota, taken=chosen)
        tiers = Counter(r["region_tier"] for r in chosen)
        print(f"{spec.key:14s} {len(chosen):4d}  {dict(tiers)}", flush=True)
        rows += chosen
    write_manifest(rows)
    print(f"manifest: {len(rows)} rows → {MANIFEST.relative_to(ROOT)}")
    return rows


def _load_log() -> dict[str, int]:
    if DOWNLOAD_LOG.exists():
        data: dict[str, int] = json.loads(DOWNLOAD_LOG.read_text())
        return data
    return {"bytes_downloaded": 0, "bytes_stored": 0, "files": 0, "failed": 0}


def cmd_fetch(
    client: Client,
    rows: list[dict[str, Any]],
    get_bytes: Callable[[str], bytes] | None = None,
    checkpoint_every: int = 50,
) -> dict[str, int]:
    """Resumable: rows whose file exists with the recorded SHA-256 are skipped."""
    fetch = get_bytes or client.get_bytes
    log = _load_log()
    done_since_checkpoint = 0
    for i, row in enumerate(rows):
        path = RAW / row["file"]
        if row.get("sha256") and path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]:
                continue
        if path.exists() and not row.get("sha256"):  # written but manifest not checkpointed
            data = path.read_bytes()
        else:
            if log["bytes_downloaded"] + MAX_IMAGE_BYTES > DOWNLOAD_BUDGET_BYTES:
                write_manifest(rows)
                raise RuntimeError(
                    f"download budget {DOWNLOAD_BUDGET_BYTES} bytes would be exceeded; stopping"
                )
            try:
                raw = fetch(row["photo_url"])
            except (OSError, ValueError) as exc:
                log["failed"] += 1
                print(f"  failed photo {row['photo_id']}: {exc}", flush=True)
                continue
            log["bytes_downloaded"] += len(raw)
            data, _, _ = resize_jpeg(raw)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            log["bytes_stored"] += len(data)
            log["files"] += 1
        _, width, height = _image_size(data)
        row.update(
            width=width,
            height=height,
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        done_since_checkpoint += 1
        if done_since_checkpoint >= checkpoint_every:
            write_manifest(rows)
            DOWNLOAD_LOG.write_text(json.dumps(log, indent=1))
            done_since_checkpoint = 0
            print(f"  {i + 1}/{len(rows)}  downloaded {log['bytes_downloaded'] / 1e6:.1f} MB")
    write_manifest(rows)
    DOWNLOAD_LOG.parent.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_LOG.write_text(json.dumps(log, indent=1))
    return log


def _image_size(data: bytes) -> tuple[None, int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        return None, img.width, img.height


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    have = [r for r in rows if r.get("sha256")]
    return {
        "rows": len(rows),
        "images": len(have),
        "per_class": dict(sorted(Counter(r["class_key"] for r in have).items())),
        "per_label": dict(sorted(Counter(r["label"] for r in have).items())),
        "per_licence": dict(sorted(Counter(r["licence"] for r in have).items())),
        "per_tier": dict(sorted(Counter(r["region_tier"] for r in have).items())),
        "india_by_state": dict(
            Counter(r["state"] or "(unknown)" for r in have if str(r["country_india"]) == "True")
        ),
        "observers": len({r["observer_id"] for r in have}),
        "stored_mb": round(sum(int(r["bytes"] or 0) for r in have) / 1e6, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["list", "fetch", "summary"])
    args = parser.parse_args(argv)
    client = Client()
    if args.command == "list":
        cmd_list(client)
    elif args.command == "fetch":
        rows = read_manifest()
        if not rows:
            print("no manifest; run `list` first", file=sys.stderr)
            return 1
        log = cmd_fetch(client, rows)
        print(json.dumps(log, indent=1))
    print(json.dumps(summary(read_manifest()), indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
