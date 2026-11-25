"""Contract 2.4 get_bay_water_level: is the bay running above predicted tides, and when are the next highs.

Four CO-OPS calls at the county's primary tide station (no point is sent): the last 24 hours observed,
6-minute predictions for the same period, high/low predictions for the window, and the year's
high/low predictions (for the king-tide threshold). Times are UTC; the interface shows local time.
No flood/no-flood call is made here; coastal flooding comes only from NWS alerts.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from elnino_planner.location.resolve import Location
from elnino_planner.tools.common import ft_m, parse_utc, unavailable
from elnino_planner.tools.http_client import FetchOutcome, HttpClient
from elnino_planner.tools.result import FailureReason, Status, ToolResult, utcnow

API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
SOURCE = "NOAA Tides and Currents (https://tidesandcurrents.noaa.gov)"
OBS_TTL_S, PRED_TTL_S = 10 * 60, 24 * 3600
ABOVE_PREDICTIONS_FT = 0.5    # provisional (contract 2.4); calibrated in WP-7
KING_TIDE_TOP_FRACTION = 0.02
HIGH_TYPES = ("H", "HH")


def caveat(station: str, distance_km: float) -> str:
    return (f"Measured at {station}, {distance_km:.1f} km ({distance_km * 0.621371:.1f} mi) from you. "
            "Water levels in sloughs and at the south end of the bay can differ. "
            "Check for a National Weather Service coastal flood advisory.")


def station_url(station_id: str) -> str:
    return f"https://tidesandcurrents.noaa.gov/stationhome.html?id={station_id}"


def king_tide_threshold(year_highs: list[float]) -> float | None:
    """Height of the lowest predicted high that is still in the top 2% for the year."""
    if not year_highs:
        return None
    k = max(1, round(len(year_highs) * KING_TIDE_TOP_FRACTION))
    return sorted(year_highs, reverse=True)[k - 1]


def _rows(out: FetchOutcome, key: str) -> list[dict] | None:
    """CO-OPS answers 200 with {"error": ...} for bad queries, so check the body too."""
    if not out.ok or not isinstance(out.json, dict) or not isinstance(out.json.get(key), list):
        return None
    return [r for r in out.json[key] if r.get("v") not in (None, "")]


async def get_bay_water_level(client: HttpClient, loc: Location, days_ahead: int = 7,
                              now: datetime | None = None) -> ToolResult:
    if not loc.in_coverage or loc.tide_station is None:
        return ToolResult(Status.OUT_OF_COVERAGE, SOURCE)
    now = now or utcnow()
    days_ahead = min(max(days_ahead, 1), 16)
    sid = loc.tide_station.id
    fallback = station_url(sid)
    base = {"station": sid, "datum": "MLLW", "units": "english", "time_zone": "gmt", "format": "json",
            "application": client.settings.app_name}

    def day(d: datetime) -> str:
        return d.strftime("%Y%m%d")

    obs_out = await client.get_json(API, ttl_s=OBS_TTL_S, tool="get_bay_water_level",
                                    params={**base, "product": "water_level", "range": 24})
    pred_out = await client.get_json(API, ttl_s=PRED_TTL_S, tool="get_bay_water_level", params={
        **base, "product": "predictions", "interval": "6",
        "begin_date": day(now - timedelta(days=1)), "end_date": day(now)})
    window_out = await client.get_json(API, ttl_s=PRED_TTL_S, tool="get_bay_water_level", params={
        **base, "product": "predictions", "interval": "hilo",
        "begin_date": day(now), "end_date": day(now + timedelta(days=days_ahead))})
    year_out = await client.get_json(API, ttl_s=PRED_TTL_S, tool="get_bay_water_level", params={
        **base, "product": "predictions", "interval": "hilo",
        "begin_date": f"{now.year}0101", "end_date": f"{now.year}1231"})

    obs, pred = _rows(obs_out, "data"), _rows(pred_out, "predictions")
    window, year = _rows(window_out, "predictions"), _rows(year_out, "predictions")
    for out, rows in ((obs_out, obs), (pred_out, pred), (window_out, window), (year_out, year)):
        if rows is None:
            return unavailable(SOURCE, fallback, out)

    since = now - timedelta(hours=24)
    obs = [r for r in obs if since <= parse_utc(r["t"]) <= now]
    if not obs:
        return unavailable(SOURCE, fallback, reason=FailureReason.BAD_RESPONSE)
    predicted_at = {r["t"]: float(r["v"]) for r in pred}
    residuals = [float(r["v"]) - predicted_at[r["t"]] for r in obs if r["t"] in predicted_at]
    residual = sum(residuals) / len(residuals) if residuals else None
    latest = obs[-1]

    end = now + timedelta(days=days_ahead)
    highs = [r for r in window if r.get("type") in HIGH_TYPES and now < parse_utc(r["t"]) <= end]
    threshold = king_tide_threshold([float(r["v"]) for r in year if r.get("type") in HIGH_TYPES])
    king_tide = threshold is not None and any(float(r["v"]) >= threshold for r in highs)
    top = max(highs, key=lambda r: float(r["v"])) if highs else None

    flags = []
    if residual is not None and residual >= ABOVE_PREDICTIONS_FT:
        flags.append("water_above_predictions")
    if king_tide:
        flags.append("king_tide_window")

    def tide(r: dict) -> dict:
        return {"time": parse_utc(r["t"]).isoformat(), "height": ft_m(float(r["v"]))}

    data = {
        "station_id": sid,
        "station_name": loc.tide_station.name,
        "distance_km": loc.tide_station.distance_km,
        "distance_mi": round(loc.tide_station.distance_km * 0.621371, 1),
        "datum": "MLLW",
        "observed": ft_m(float(latest["v"])),
        "observed_at": parse_utc(latest["t"]).isoformat(),
        "predicted_now": ft_m(predicted_at.get(latest["t"])),
        "residual": ft_m(residual),
        "residual_basis": f"mean of {len(residuals)} six-minute readings over the last 24 hours",
        "next_high_tides": [tide(r) for r in highs],
        "highest_predicted": tide(top) if top else None,
        "king_tide_threshold": ft_m(threshold),
        "king_tide_window": king_tide,
        "flags": flags,
        "station_url": fallback,
    }
    return ToolResult(Status.OK, SOURCE, fetched_at=obs_out.fetched_at, data=data,
                      caveats=[caveat(loc.tide_station.name, loc.tide_station.distance_km)],
                      from_cache=obs_out.from_cache)
