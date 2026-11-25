"""Small geometry helpers: distance, point-in-polygon for GeoJSON, nearest item.

Pure Python so the coverage check never sends a point anywhere and needs no GIS library.
Coordinates are (lat, lon) in our code; GeoJSON stores [lon, lat].
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence, TypeVar

EARTH_RADIUS_KM = 6371.0088

T = TypeVar("T")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _in_ring(lat: float, lon: float, ring: Sequence[Sequence[float]]) -> bool:
    """Ray casting on one linear ring of [lon, lat] pairs."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _in_polygon(lat: float, lon: float, rings: Sequence) -> bool:
    """First ring is the outer boundary; the rest are holes."""
    if not rings or not _in_ring(lat, lon, rings[0]):
        return False
    return not any(_in_ring(lat, lon, hole) for hole in rings[1:])


def point_in_geometry(lat: float, lon: float, geometry: dict) -> bool:
    """GeoJSON Polygon or MultiPolygon."""
    kind, coords = geometry["type"], geometry["coordinates"]
    if kind == "Polygon":
        return _in_polygon(lat, lon, coords)
    if kind == "MultiPolygon":
        return any(_in_polygon(lat, lon, poly) for poly in coords)
    raise ValueError(f"unsupported geometry type: {kind}")


def round_point(lat: float, lon: float, places: int = 2) -> tuple[float, float]:
    """Privacy rule: weather, tide and seasonal calls use a point rounded to two decimals."""
    return round(lat, places), round(lon, places)


def nearest(lat: float, lon: float, items: Iterable[T]) -> tuple[T, float] | None:
    """Closest item that has `.lat` and `.lon`, with its distance in km. None if `items` is empty."""
    best: tuple[T, float] | None = None
    for item in items:
        d = haversine_km(lat, lon, item.lat, item.lon)
        if best is None or d < best[1]:
            best = (item, d)
    return best
