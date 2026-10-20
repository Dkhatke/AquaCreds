"""Write composites as Cloud-Optimised GeoTIFFs (one band per variable, named in the band
descriptions), so maps and reports read them with HTTP range requests and never recompute."""

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import xarray as xr


def write_cog(
    ds: xr.Dataset,
    path: str | Path,
    *,
    epsg: int,
    variables: Sequence[str] | None = None,
) -> list[str]:
    """Write the (y, x) float variables of `ds` to `path` as a multi-band float32 COG
    (DEFLATE, NaN no-data). Returns the band names in order."""
    import rioxarray  # noqa: F401  (registers the .rio accessor)

    names = list(variables) if variables is not None else [str(v) for v in ds.data_vars]
    if not names:
        raise ValueError("nothing to write")
    stack = xr.concat(
        [ds[name].astype("float32").drop_vars("spatial_ref", errors="ignore") for name in names],
        dim="band",
    ).assign_coords(band=np.arange(1, len(names) + 1))
    stack = stack.transpose("band", "y", "x")
    stack.attrs = {"long_name": tuple(names)}
    stack = stack.rio.write_crs(f"EPSG:{epsg}").rio.write_nodata(np.nan, encoded=False)
    stack.rio.to_raster(
        str(path),
        driver="COG",
        dtype="float32",
        compress="DEFLATE",
        predictor=3,
        blocksize=512,
        overview_resampling="average",
    )
    return names


def write_class_cog(
    labels: xr.DataArray,
    path: str | Path,
    *,
    epsg: int,
    classes: Sequence[str],
    nodata: int = 255,
) -> None:
    """Write a (y, x) uint8 class-index raster as a single-band COG (DEFLATE, `nodata` for
    pixels without a prediction). Overviews use nearest-neighbour resampling (a categorical
    map must never be averaged). The class names are stored as GDAL metadata tags
    `class_<index>=<name>` so the file is self-describing."""
    import rioxarray  # noqa: F401  (registers the .rio accessor)

    if labels.dims != ("y", "x"):
        raise ValueError(f"labels must have dims (y, x), got {labels.dims}")
    if len(classes) >= nodata:
        raise ValueError("too many classes for a uint8 raster with this nodata value")
    band = labels.astype("uint8").drop_vars("spatial_ref", errors="ignore")
    band.attrs = {f"class_{i}": name for i, name in enumerate(classes)}
    band = band.rio.write_crs(f"EPSG:{epsg}").rio.write_nodata(nodata, encoded=False)
    band.rio.to_raster(
        str(path),
        driver="COG",
        dtype="uint8",
        compress="DEFLATE",
        predictor=2,
        blocksize=512,
        overview_resampling="nearest",
    )
