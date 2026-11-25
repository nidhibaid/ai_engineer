"""Unofficial "unusual for here" flags (contract 2.5). Pure functions shared by the live forecast tool
and the replay harness, so both apply exactly the same rules.

All thresholds are provisional until WP-7 calibration.
"""
from __future__ import annotations

KMH_PER_MPH = 1.609344
MM_PER_IN = 25.4

STRONG_WIND_MPH = 45.0     # NWS Bay Area Wind Advisory gusts start at 46 mph; check in WP-7
DAMAGING_WIND_MPH = 58.0   # NWS High Wind Warning gust criterion
WETTER_RATIO, DRIER_RATIO = 1.25, 0.75   # days 8-16 total vs the usual total for those dates


def rain_flag(rain_mm: float | None, cell: dict) -> str | None:
    if rain_mm is None:
        return None
    if rain_mm >= cell["wet_p99_mm"]:
        return "very_heavy_rain_day"
    if rain_mm >= cell["wet_p95_mm"]:
        return "heavy_rain_day"
    return None


def wind_flag(gust_mph: float | None) -> str | None:
    if gust_mph is None:
        return None
    if gust_mph >= DAMAGING_WIND_MPH:
        return "damaging_wind"
    if gust_mph >= STRONG_WIND_MPH:
        return "strong_wind"
    return None


def prolonged_rain_ends(rains_mm: list[float | None], cell: dict) -> set[int]:
    """Indices of days that end a 3-day window whose total reaches the local 99th percentile."""
    ends = set()
    for i in range(2, len(rains_mm)):
        window = rains_mm[i - 2:i + 1]
        if None not in window and sum(window) >= cell["three_day_p99_mm"]:
            ends.add(i)
    return ends


def daily_flags(rains_mm: list[float | None], gusts_mph: list[float | None], cell: dict) -> list[list[str]]:
    """One list of flags per day. A day ending a prolonged-rain window also gets `prolonged_rain`."""
    prolonged = prolonged_rain_ends(rains_mm, cell)
    out = []
    for i, (rain, gust) in enumerate(zip(rains_mm, gusts_mph)):
        flags = [f for f in (rain_flag(rain, cell), wind_flag(gust)) if f]
        if i in prolonged:
            flags.append("prolonged_rain")
        out.append(flags)
    return out


def tendency(total_mm: float, usual_mm: float) -> str:
    if usual_mm <= 0:
        return "near_usual"
    ratio = total_mm / usual_mm
    if ratio >= WETTER_RATIO:
        return "wetter_than_usual"
    if ratio <= DRIER_RATIO:
        return "drier_than_usual"
    return "near_usual"
