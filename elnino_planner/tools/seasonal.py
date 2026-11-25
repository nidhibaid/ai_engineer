"""Contract 2.6 get_seasonal_outlook: what the season may bring, as odds, with past El Nino winters.

One Open-Meteo seasonal call (ECMWF EC46 weeks, SEAS5 months, 51 members) at the point snapped to a
0.25 degree grid. "Share of members wetter than normal" counts members whose total for the period beats
the model's own normal, which is the ensemble mean minus the anomaly the API reports.
Never returns storm dates, future rain amounts or statements of certainty.

Known limitation (checked 25 Nov 2026): for weeks 1-3 the members' own totals match the API's weekly
mean closely; by weeks 4-5 they run 15-25% lower, probably where the daily series changes model.
Shares for later weeks are therefore rougher. Record this in the WP-7 calibration.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from elnino_planner.config.schema import CountyPack
from elnino_planner.flags.climatology import ANALOG_SEASONS, cell_for, climatology_path, load_climatology
from elnino_planner.location.resolve import Location
from elnino_planner.tools.common import unavailable
from elnino_planner.tools.http_client import HttpClient
from elnino_planner.tools.result import Status, ToolResult, utcnow

URL = "https://seasonal-api.open-meteo.com/v1/seasonal"
SOURCE = "Open-Meteo seasonal forecast, ECMWF EC46 and SEAS5 (https://open-meteo.com)"
FALLBACK = "https://www.cpc.ncep.noaa.gov/products/predictions/long_range/seasonal.php?lead=1"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0), forecasts from ECMWF"
ENSO_FILE = Path(__file__).resolve().parents[1] / "config" / "enso_status.json"
TTL_S = 24 * 3600
GRID = 0.25
WETTER_SHARE, DRIER_SHARE = 0.60, 0.40   # provisional (contract 2.6)
CAVEATS = [
    "A seasonal outlook describes odds for the region, not specific storms or dates.",
    "The model grid is about 36 km, so this is the same for most of the county.",
    "This El Niño may be stronger than any of the past winters shown, and 2015–16 brought near- or "
    "below-normal rain to much of California despite a strong El Niño.",
]
RESOLUTION_NOTE = ("Seasonal odds come from a global model with a grid of about 36 km and are not "
                   "adjusted for local conditions. Past winters come from ERA5 at about 25 km.")
STALE_ENSO = "The El Niño status shown may be out of date; check the Climate Prediction Center link."


def tendency_word(share: float) -> str:
    if share >= WETTER_SHARE:
        return "odds tilt wetter"
    if share <= DRIER_SHARE:
        return "odds tilt drier"
    return "no clear tilt"


def snap(lat: float, lon: float) -> tuple[float, float]:
    return round(round(lat / GRID) * GRID, 2), round(round(lon / GRID) * GRID, 2)


def member_series(daily: dict) -> list[list[float | None]]:
    return [v for k, v in daily.items() if k == "precipitation_sum" or k.startswith("precipitation_sum_member")]


def share_wetter(daily_dates: list[date], members: list[list], start: date, end: date, normal: float) -> float | None:
    """Share of members whose total over [start, end) beats `normal`. None unless every day is covered."""
    idx = [i for i, d in enumerate(daily_dates) if start <= d < end]
    if len(idx) != (end - start).days:
        return None
    totals = [sum(m[i] for i in idx) for m in members if all(m[i] is not None for i in idx)]
    return sum(t > normal for t in totals) / len(totals) if totals else None


def periods(block: dict, length) -> list[tuple[date, date, float]]:
    """(start, end, model normal) for each period that has both a mean and an anomaly."""
    out = []
    for t, mean, anom in zip(block["time"], block["precipitation_mean"], block["precipitation_anomaly"]):
        if mean is not None and anom is not None:
            start = date.fromisoformat(t)
            out.append((start, length(start), mean - anom))
    return out


def next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def summary_sentence(months: list[dict]) -> str:
    words = [m["tendency"] for m in months]
    if not words:
        return "No seasonal odds are available right now."
    top = max(set(words), key=words.count)
    if words.count(top) * 2 <= len(words):
        return "The odds are mixed across the coming months."
    return {"odds tilt wetter": "For most of the coming months, odds tilt wetter than normal.",
            "odds tilt drier": "For most of the coming months, odds tilt drier than normal.",
            "no clear tilt": "For most of the coming months, there is no clear tilt toward wetter or drier."}[top]


def load_enso(today: date) -> tuple[dict, bool]:
    enso = {k: v for k, v in json.loads(ENSO_FILE.read_text()).items() if not k.startswith("_")}
    return enso, date.fromisoformat(enso["next_update"]) < today


async def get_seasonal_outlook(client: HttpClient, loc: Location, pack: CountyPack,
                               now: datetime | None = None) -> ToolResult:
    if not loc.in_coverage:
        return ToolResult(Status.OUT_OF_COVERAGE, SOURCE)
    lat, lon = snap(*loc.rounded)
    out = await client.get_json(URL, ttl_s=TTL_S, tool="get_seasonal_outlook", params={
        "latitude": lat, "longitude": lon, "daily": "precipitation_sum",
        "weekly": "precipitation_mean,precipitation_anomaly",
        "monthly": "precipitation_mean,precipitation_anomaly"})
    body = out.json if out.ok and isinstance(out.json, dict) else {}
    if not all(k in body for k in ("daily", "weekly", "monthly")):
        return unavailable(SOURCE, FALLBACK, out)

    days = [date.fromisoformat(t) for t in body["daily"]["time"]]
    members = member_series(body["daily"])
    weeks, months = [], []
    for start, end, normal in periods(body["weekly"], lambda s: s + timedelta(days=7)):
        share = share_wetter(days, members, start, end, normal)
        if share is not None:
            weeks.append({"week": len(weeks) + 1, "starts_on": start.isoformat(),
                          "share_wetter_pct": round(100 * share), "tendency": tendency_word(share)})
    for start, end, normal in periods(body["monthly"], next_month):
        share = share_wetter(days, members, start, end, normal)
        if share is not None:
            months.append({"month": start.strftime("%B"), "share_wetter_pct": round(100 * share),
                           "tendency": tendency_word(share)})

    cell = cell_for(load_climatology(climatology_path(pack.county_fips)), lat, lon)
    analogs = [{"winter": s, "rain_pct_of_normal": cell["analogs"][s]["pct_of_normal"],
                "heavy_rain_days": cell["analogs"][s]["heavy_days"],
                "heavy_rain_days_normal": cell["heavy_days_normal"]} for s in ANALOG_SEASONS]

    today = (now or utcnow()).date()
    enso, stale = load_enso(today)
    data = {
        "enso": enso,
        "summary": summary_sentence(months),
        "outlook_weeks": weeks,
        "outlook_months": months,
        "members": len(members),
        "analogs": analogs,          # one entry per winter; never averaged
        "resolution_note": RESOLUTION_NOTE,
        "attribution": ATTRIBUTION,
    }
    return ToolResult(Status.OK, SOURCE, fetched_at=out.fetched_at, data=data,
                      caveats=CAVEATS + ([STALE_ENSO] if stale else []), from_cache=out.from_cache)
