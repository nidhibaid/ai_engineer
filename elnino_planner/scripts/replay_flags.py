"""Run the forecast flag rules over the replay events in docs/scope/05_eval_plan.md (admin, run by hand).

    uv run python -m elnino_planner.scripts.replay_flags

Archive view: ERA5 daily rain and gusts, Nov 2022-Mar 2023 and Feb 2017, through `daily_flags`.
Forecast view: Open-Meteo Previous Runs at 1-7 day leads for 31 Dec 2022, 9 Jan 2023 and 4 Feb 2024.
Raw responses go to evals/replay/flags/raw/ (reused on the next run) and a summary to
evals/replay/flags/results.json. About 150 Open-Meteo calls on a fresh run. No retries.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

from elnino_planner.config.loader import load_county_pack
from elnino_planner.flags.climatology import cell_for, climatology_path, load_climatology
from elnino_planner.flags.rules import KMH_PER_MPH, daily_flags
from elnino_planner.location.geo import round_point
from elnino_planner.settings import load_settings

OUT = Path(__file__).resolve().parents[2] / "evals" / "replay" / "flags"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"

# Test points from the eval plan, rounded to two decimals as the live tool would use them.
POINTS = {name: round_point(lat, lon) for name, (lat, lon) in {
    "alviso": (37.4264, -121.9744), "downtown_san_jose": (37.3382, -121.8863),
    "palo_alto": (37.4419, -122.1430), "mountain_view_baylands": (37.4300, -122.0800),
    "morgan_hill": (37.1305, -121.6544), "san_martin": (37.0850, -121.6100), "gilroy": (37.0058, -121.5683),
}.items()}

ARCHIVE_WINDOWS = {"winter_2022_23": ("2022-11-01", "2023-03-31"), "feb_2017": ("2017-02-01", "2017-02-28")}
EVENT_DAYS = ["2022-12-31", "2023-01-09"]            # pass: heavy_rain_day or stronger at most points
DRY_WEEK = ("2022-02-01", "2022-02-07")              # pass: no flags (record-dry Jan-Feb 2022)
COYOTE_2017 = "2017-02-21"                           # finding only
FORECAST_EVENTS = {                                   # (window, event day, model)
    "ar_2022_12_31": (("2022-12-29", "2023-01-01"), "2022-12-31", "gfs_seamless"),
    "ar_2023_01_09": (("2023-01-07", "2023-01-10"), "2023-01-09", "gfs_seamless"),
    "storm_2024_02_04": (("2024-02-02", "2024-02-05"), "2024-02-04", "best_match"),
}
RAIN_FLAGS = {"heavy_rain_day", "very_heavy_rain_day", "prolonged_rain"}


def get(client: httpx.Client, url: str, params: dict, cache_name: str) -> dict:
    path = OUT / "raw" / f"{cache_name}.json"
    if path.exists():
        return json.loads(path.read_text())
    resp = client.get(url, params=params)
    if resp.status_code != 200:
        raise SystemExit(f"{cache_name}: HTTP {resp.status_code} {resp.text[:300]}")
    path.write_text(resp.text)
    time.sleep(1.0)
    return resp.json()


def archive_flags(body: dict, cell: dict) -> dict[str, list[str]]:
    d = body["daily"]
    gusts = [None if g is None else g / KMH_PER_MPH for g in d["wind_gusts_10m_max"]]
    return dict(zip(d["time"], daily_flags(d["precipitation_sum"], gusts, cell)))


def hourly_to_daily(hourly: dict, lead: int) -> tuple[list[str], list, list]:
    rain, gust = defaultdict(float), defaultdict(float)
    for t, r, g in zip(hourly["time"], hourly[f"precipitation_previous_day{lead}"],
                       hourly.get(f"wind_gusts_10m_previous_day{lead}", [None] * len(hourly["time"]))):
        day = t[:10]
        rain[day] += r or 0.0
        gust[day] = max(gust[day], g or 0.0)
    days = sorted(rain)
    return days, [rain[d] for d in days], [gust[d] / KMH_PER_MPH for d in days]


def main() -> int:
    settings = load_settings()
    pack = load_county_pack(settings.county_fips)
    table = load_climatology(climatology_path(pack.county_fips))
    (OUT / "raw").mkdir(parents=True, exist_ok=True)
    results: dict = {"archive": {}, "forecast": {}, "points": POINTS}

    with httpx.Client(timeout=60, headers={"User-Agent": settings.user_agent}) as client:
        for name, (lat, lon) in POINTS.items():
            cell = cell_for(table, lat, lon)
            flags = {}
            for window, (start, end) in ARCHIVE_WINDOWS.items():
                body = get(client, ARCHIVE, {"latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
                                             "daily": "precipitation_sum,wind_gusts_10m_max", "models": "era5",
                                             "timezone": pack.timezone}, f"archive_{window}_{name}")
                flags.update(archive_flags(body, cell))
            dry = [f for d, f in flags.items() if DRY_WEEK[0] <= d <= DRY_WEEK[1] and f]
            season = {d: f for d, f in flags.items() if "2022-11-01" <= d <= "2023-03-31" and f}
            results["archive"][name] = {
                "event_days": {d: flags.get(d, []) for d in EVENT_DAYS},
                "season_flag_days": len(season),
                "season_rain_flag_days": sum(bool(set(f) & RAIN_FLAGS) for f in season.values()),
                "coyote_2017_02_21": flags.get(COYOTE_2017, []),
                "dry_week_flags": dry,
            }
            for event, ((start, end), day, model) in FORECAST_EVENTS.items():
                hourly_vars = [f"{v}_previous_day{n}" for n in range(1, 8)
                               for v in ("precipitation", "wind_gusts_10m")]
                body = get(client, PREVIOUS_RUNS, {"latitude": lat, "longitude": lon, "start_date": start,
                                                   "end_date": end, "hourly": ",".join(hourly_vars),
                                                   "models": model, "timezone": pack.timezone},
                           f"previous_runs_{event}_{name}")
                by_lead = {}
                for lead in range(1, 8):
                    if all(v is None for v in body["hourly"].get(f"precipitation_previous_day{lead}", [None])):
                        continue
                    days, rains, gusts = hourly_to_daily(body["hourly"], lead)
                    by_lead[lead] = dict(zip(days, daily_flags(rains, gusts, cell))).get(day, [])
                results["forecast"].setdefault(event, {})[name] = by_lead

    # Archive (the pass condition is about the archive data) and a dry week (the false-alarm check)
    n = len(POINTS)
    for d in EVENT_DAYS:
        hits = sum(bool(set(r["event_days"][d]) & RAIN_FLAGS) for r in results["archive"].values())
        print(f"{d}: rain flag at {hits}/{n} points  ->  {'PASS' if hits > n / 2 else 'FAIL'} (needs most points)")
    dry = sum(bool(r["dry_week_flags"]) for r in results["archive"].values())
    print(f"dry week {DRY_WEEK[0]}..{DRY_WEEK[1]}: flags at {dry}/{n} points  ->  {'PASS' if dry == 0 else 'FAIL'}")
    print("Nov 2022-Mar 2023 flagged days per point (false-alarm context):",
          {k: v["season_flag_days"] for k, v in results["archive"].items()})
    print("21 Feb 2017 (finding only):", {k: v["coyote_2017_02_21"] for k, v in results["archive"].items()})
    for event, per_point in results["forecast"].items():
        if not any(per_point.values()):
            print(f"{event}: no archived forecasts in Previous Runs for this model and date (cannot replay)")
            continue
        first = {p: max((lead for lead, f in leads.items() if set(f) & RAIN_FLAGS), default="none")
                 for p, leads in per_point.items()}
        print(f"{event}: longest lead (days) with a rain flag, per point: {first}")
    feb = results["forecast"]["storm_2024_02_04"]
    ok = sum(any("strong_wind" in f or "damaging_wind" in f for lead, f in leads.items() if lead <= 3) and
             any(set(f) & RAIN_FLAGS for lead, f in leads.items() if lead <= 3) for leads in feb.values())
    print(f"4 Feb 2024: strong wind and a rain flag by day 3 at {ok}/{n} points")
    (OUT / "results.json").write_text(json.dumps(results, indent=1))
    print(f"Wrote {OUT / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
