"""Build real JPEGs with EXIF GPS / time for evidence tests."""

import io

import numpy as np
from PIL import ExifTags, Image


def _dms(value: float) -> tuple[float, float, float]:
    value = abs(value)
    d = int(value)
    m = int((value - d) * 60)
    s = round((value - d - m / 60) * 3600, 4)
    return float(d), float(m), s


def make_jpeg(
    *,
    seed: int = 0,
    lat: float | None = None,
    lon: float | None = None,
    when: str | None = None,
    offset: str | None = None,
    size: int = 128,
    brightness: float = 1.0,
) -> bytes:
    rng = np.random.default_rng(seed)
    # Smooth random field (blurred noise) so pHash is stable under small edits.
    base = rng.random((8, 8, 3))
    arr = np.kron(base, np.ones((size // 8, size // 8, 1)))
    arr = np.clip(arr * 255 * brightness, 0, 255).astype("uint8")
    img = Image.fromarray(arr, "RGB")

    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "TestCam"
    exif[ExifTags.Base.Model] = "FieldPhone 1"
    if when:
        sub = exif.get_ifd(ExifTags.IFD.Exif)
        sub[ExifTags.Base.DateTimeOriginal] = when
        if offset:
            sub[ExifTags.Base.OffsetTimeOriginal] = offset
    if lat is not None and lon is not None:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        gps[ExifTags.GPS.GPSLatitudeRef] = "N" if lat >= 0 else "S"
        gps[ExifTags.GPS.GPSLatitude] = _dms(lat)
        gps[ExifTags.GPS.GPSLongitudeRef] = "E" if lon >= 0 else "W"
        gps[ExifTags.GPS.GPSLongitude] = _dms(lon)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, exif=exif)
    return buf.getvalue()
