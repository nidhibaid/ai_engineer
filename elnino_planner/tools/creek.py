"""Contract 2.3 get_creek_status: stage and official NWS flood category at the nearest monitored creek.

Three NWPS calls: the gauge list for the county's bounding box (no point is sent), then the chosen
gauge's detail (status and thresholds) and its stage/flow series (forecast peak).
Flood categories are the NWS's own; this tool computes no thresholds.
"""
from __future__ import annotations

from elnino_planner.config.schema import CountyPack
from elnino_planner.location.geo import haversine_km
from elnino_planner.location.resolve import Location
from elnino_planner.tools.common import ft_m, unavailable
from elnino_planner.tools.http_client import HttpClient
from elnino_planner.tools.result import Status, ToolResult

API = "https://api.water.noaa.gov/nwps/v1/gauges"
SOURCE = "NOAA National Water Prediction Service (https://water.noaa.gov)"
FALLBACK = "https://water.noaa.gov"
LIST_TTL_S, GAUGE_TTL_S = 24 * 3600, 15 * 60
MAX_KM, FORECAST_PREFERENCE = 5.0, 1.5
CATEGORIES = ("no_flooding", "action", "minor", "moderate", "major")   # anything else does not qualify
NO_FORECAST = ("fcst_not_current", "out_of_service", "not_defined", "")
NO_GAUGE = f"No monitored creek gauge with current readings within {MAX_KM:g} km."
FORECAST_MISSING = "The forecast series for this gauge could not be loaded."


def caveat(distance_km: float) -> str:
    return (f"This is the nearest monitored creek, {distance_km:.1f} km ({distance_km * 0.621371:.1f} mi) away. "
            "It may not be the creek closest to you, and small creeks and storm drains are not monitored.")


def _has_forecast(gauge: dict) -> bool:
    return (gauge.get("status", {}).get("forecast", {}).get("floodCategory") or "") not in NO_FORECAST


def choose_gauge(gauges: list[dict], lat: float, lon: float) -> tuple[dict, float] | None:
    """Nearest qualifying gauge within 5 km; one with a forecast wins if within 1.5x the nearest distance."""
    near = sorted(
        (haversine_km(lat, lon, g["latitude"], g["longitude"]), g) for g in gauges
        if g.get("status", {}).get("observed", {}).get("floodCategory") in CATEGORIES
    )
    near = [(d, g) for d, g in near if d <= MAX_KM]
    if not near:
        return None
    best_d = near[0][0]
    with_fcst = [(d, g) for d, g in near if _has_forecast(g) and d <= best_d * FORECAST_PREFERENCE]
    d, g = (with_fcst or near)[0]
    return g, d


def _stage(value) -> float | None:
    return None if value is None or value <= -999 else value   # NWPS uses -999 for missing


async def get_creek_status(client: HttpClient, loc: Location, pack: CountyPack) -> ToolResult:
    if not loc.in_coverage:
        return ToolResult(Status.OUT_OF_COVERAGE, SOURCE)
    b = pack.creek_gauge_bbox
    listing = await client.get_json(API, ttl_s=LIST_TTL_S, tool="get_creek_status", params={
        "bbox.xmin": b.xmin, "bbox.ymin": b.ymin, "bbox.xmax": b.xmax, "bbox.ymax": b.ymax, "srid": "EPSG_4326"})
    if not listing.ok or not isinstance(listing.json, dict) or "gauges" not in listing.json:
        return unavailable(SOURCE, FALLBACK, listing)

    chosen = choose_gauge(listing.json["gauges"], loc.lat, loc.lon)
    if chosen is None:
        return ToolResult(Status.NO_DATA, SOURCE, fetched_at=listing.fetched_at, caveats=[NO_GAUGE],
                          fallback_url=FALLBACK)
    gauge, distance = chosen
    lid = gauge["lid"]

    detail = await client.get_json(f"{API}/{lid}", ttl_s=GAUGE_TTL_S, tool="get_creek_status")
    if not detail.ok or not isinstance(detail.json, dict) or "status" not in detail.json:
        return unavailable(SOURCE, FALLBACK, detail)
    status = detail.json["status"]
    obs, fcst = status.get("observed", {}), status.get("forecast", {})
    has_forecast = (fcst.get("floodCategory") or "") not in NO_FORECAST
    thresholds = {name: ft_m(c.get("stage"))
                  for name, c in ((detail.json.get("flood") or {}).get("categories") or {}).items()
                  if c and c.get("stage") is not None}

    caveats = [caveat(distance)]
    peak = None
    if has_forecast:
        series = await client.get_json(f"{API}/{lid}/stageflow", ttl_s=GAUGE_TTL_S, tool="get_creek_status")
        points = ((series.json or {}).get("forecast") or {}).get("data") if series.ok else None
        points = [p for p in points or [] if _stage(p.get("primary")) is not None]
        if points:
            peak = max(points, key=lambda p: p["primary"])
        else:
            caveats.append(FORECAST_MISSING)

    data = {
        "gauge_id": lid,
        "gauge_name": detail.json.get("name") or gauge.get("name"),
        "distance_km": round(distance, 1),
        "distance_mi": round(distance * 0.621371, 1),
        "observed_stage": ft_m(_stage(obs.get("primary"))),
        "observed_at": obs.get("validTime"),
        "observed_category": obs.get("floodCategory"),
        "forecast_peak_stage": ft_m(peak["primary"]) if peak else None,
        "forecast_peak_at": peak["validTime"] if peak else None,
        "forecast_category": fcst.get("floodCategory") if has_forecast else None,
        "has_forecast": has_forecast,
        "thresholds": thresholds,
        "gauge_url": f"https://water.noaa.gov/gauges/{lid}",
    }
    return ToolResult(Status.OK, SOURCE, fetched_at=detail.fetched_at, data=data, caveats=caveats,
                      from_cache=detail.from_cache)
