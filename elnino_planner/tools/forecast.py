"""Contract 2.5 get_forecast_flags: daily rain and wind for the next 7 or 16 days, with unofficial flags.

Days 1-7 are returned day by day. Days 8-16 come back only as one low-confidence tendency word,
never as daily values. Uses the rounded point and the precomputed climatology table.
"""
from __future__ import annotations

from datetime import date

from elnino_planner.config.schema import CountyPack
from elnino_planner.flags.climatology import cell_for, climatology_path, load_climatology
from elnino_planner.flags.rules import KMH_PER_MPH, MM_PER_IN, daily_flags, tendency
from elnino_planner.location.resolve import Location
from elnino_planner.tools.common import unavailable
from elnino_planner.tools.http_client import HttpClient
from elnino_planner.tools.result import Status, ToolResult

URL = "https://api.open-meteo.com/v1/forecast"
SOURCE = "Open-Meteo forecast (https://open-meteo.com)"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
TTL_S = 3600
DETAIL_DAYS = 7
CAVEAT = ("These flags compare the forecast with what is usual for this location. "
          "They are not official warnings and do not predict flooding.")
EXTENDED_CAVEAT = "Forecasts this far ahead often change."
OUT_OF_SEASON = "The comparison with usual rain covers November to March only."


def rain(mm: float | None) -> dict | None:
    return None if mm is None else {"mm": round(mm, 1), "in": round(mm / MM_PER_IN, 2)}


def gust(kmh: float | None) -> dict | None:
    return None if kmh is None else {"kmh": round(kmh), "mph": round(kmh / KMH_PER_MPH)}


def fallback_url(loc: Location) -> str:
    lat, lon = loc.rounded
    return f"https://forecast.weather.gov/MapClick.php?lat={lat}&lon={lon}"


def extended_tendency(dates: list[date], rains_mm: list[float | None], cell: dict) -> str | None:
    """Days 8-16 total versus the usual total for those calendar days. None outside Nov-Mar."""
    usual_by_month = cell["mean_daily_mm_by_month"]
    if any(str(d.month) not in usual_by_month for d in dates):
        return None
    usual = sum(usual_by_month[str(d.month)] for d in dates)
    return tendency(sum(r or 0.0 for r in rains_mm), usual)


async def get_forecast_flags(client: HttpClient, loc: Location, pack: CountyPack, days: int = 7) -> ToolResult:
    if not loc.in_coverage:
        return ToolResult(Status.OUT_OF_COVERAGE, SOURCE)
    days = 16 if days > DETAIL_DAYS else DETAIL_DAYS
    lat, lon = loc.rounded
    out = await client.get_json(URL, ttl_s=TTL_S, tool="get_forecast_flags", params={
        "latitude": lat, "longitude": lon, "timezone": pack.timezone, "forecast_days": days,
        "daily": "precipitation_sum,precipitation_probability_max,wind_gusts_10m_max"})
    d = out.json.get("daily") if out.ok and isinstance(out.json, dict) else None
    if not d or not d.get("time"):
        return unavailable(SOURCE, fallback_url(loc), out)

    cell = cell_for(load_climatology(climatology_path(pack.county_fips)), lat, lon)
    dates = [date.fromisoformat(t) for t in d["time"]]
    rains, probs, gusts_kmh = d["precipitation_sum"], d["precipitation_probability_max"], d["wind_gusts_10m_max"]
    n = min(DETAIL_DAYS, len(dates))
    gusts_mph = [None if g is None else g / KMH_PER_MPH for g in gusts_kmh[:n]]
    flags = daily_flags(rains[:n], gusts_mph, cell)

    daily = [{"date": dates[i].isoformat(), "rain": rain(rains[i]), "rain_probability_pct": probs[i],
              "max_gust": gust(gusts_kmh[i]), "flags": flags[i]} for i in range(n)]
    data = {
        "daily": daily,
        "flags": sorted({f for day in flags for f in day}),
        "totals": {"rain_3_day": rain(sum(r or 0.0 for r in rains[:3])),
                   "rain_7_day": rain(sum(r or 0.0 for r in rains[:n]))},
        "extended": None,
        "climatology_cell": {"lat": cell["lat"], "lon": cell["lon"]},
        "attribution": ATTRIBUTION,
    }
    caveats = [CAVEAT]
    if days == 16 and len(dates) > DETAIL_DAYS:
        word = extended_tendency(dates[DETAIL_DAYS:], rains[DETAIL_DAYS:], cell)
        data["extended"] = {"days": "8-16", "tendency": word, "confidence": "low"}
        caveats.append(EXTENDED_CAVEAT)
        if word is None:
            caveats.append(OUT_OF_SEASON)
    return ToolResult(Status.OK, SOURCE, fetched_at=out.fetched_at, data=data, caveats=caveats,
                      from_cache=out.from_cache)
