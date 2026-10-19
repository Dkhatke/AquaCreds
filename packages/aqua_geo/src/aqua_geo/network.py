"""Network hardening for remote raster/STAC access.

GDAL and pystac-client wait forever on a stalled HTTP connection by default. A single stuck read
froze a 60-minute label build, so every remote read in AquaCreds goes through these settings:
bounded timeouts, a few retries, and no directory listing on open (COGs don't need it).
"""

from typing import Any

GDAL_NETWORK_ENV: dict[str, str] = {
    "GDAL_HTTP_CONNECTTIMEOUT": "10",  # seconds to establish a connection
    "GDAL_HTTP_TIMEOUT": "60",  # seconds for a whole request
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "2",  # seconds, doubled by GDAL on each retry
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.jp2,.TIF",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "VSI_CACHE": "TRUE",
}

STAC_TIMEOUT_S = 30

_configured = False


def configure_odc_stac() -> None:
    """Apply GDAL_NETWORK_ENV to odc-stac's reader threads (idempotent)."""
    global _configured
    if _configured:
        return
    import odc.stac

    # configure_rio(**params) forwards arbitrary GDAL options; mypy cannot type free-form kwargs.
    odc.stac.configure_rio(cloud_defaults=True, **GDAL_NETWORK_ENV)  # type: ignore[arg-type]
    _configured = True


def rasterio_env() -> Any:
    """Context manager for direct rasterio reads: `with rasterio_env(): rasterio.open(url)`."""
    import rasterio

    return rasterio.Env(**GDAL_NETWORK_ENV)
