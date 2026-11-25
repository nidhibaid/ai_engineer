"""The precomputed rain climatology table: what is usual for each 0.25 degree cell in the county.

Built once by scripts/build_climatology.py from the Open-Meteo archive (ERA5, Nov-Mar 1990-91 to 2019-20).
Tools only read it; nothing here calls the network.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from elnino_planner.config.loader import COUNTIES_DIR
from elnino_planner.location.geo import haversine_km

ANALOG_SEASONS = ("1982-83", "1997-98", "2015-16", "2023-24")   # past strong El Nino winters


def percentile(values: list[float], q: float) -> float:
    """Linear interpolation between closest ranks (same as numpy's default)."""
    if not values:
        raise ValueError("percentile of an empty list")
    s = sorted(values)
    k = (len(s) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def climatology_path(fips: str, directory: Path = COUNTIES_DIR) -> Path:
    return directory / fips / "climatology.json"


@lru_cache(maxsize=8)
def load_climatology(path: Path) -> dict:
    return json.loads(path.read_text())


def cell_for(table: dict, lat: float, lon: float) -> dict:
    """The cell whose centre is nearest the point."""
    return min(table["cells"], key=lambda c: haversine_km(lat, lon, c["lat"], c["lon"]))
