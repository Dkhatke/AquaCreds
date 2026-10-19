"""Typed helpers for PostGIS geometry columns."""

from geoalchemy2.elements import WKBElement
from geoalchemy2.shape import to_shape
from shapely.geometry import Point


def to_point(element: WKBElement) -> Point:
    """Decode a POINT column; raises if the stored geometry is not a point."""
    geom = to_shape(element)
    if not isinstance(geom, Point):
        raise TypeError(f"Expected a Point, got {geom.geom_type}")
    return geom
