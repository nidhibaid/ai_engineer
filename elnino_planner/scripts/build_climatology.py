"""Build the rain climatology table from the Open-Meteo archive (admin, run once; about 15 minutes).

    uv run python -m elnino_planner.scripts.build_climatology            # county from settings
    uv run python -m elnino_planner.scripts.build_climatology --max-cells 2   # small trial

For every ERA5 0.25 degree cell that touches the county it downloads daily rain for Nov-Mar of the
30 base winters (1990-91 to 2019-20) plus the analog winters outside that range, and writes
config/counties/<fips>/climatology.json. Each winter is one request (about 11 Open-Meteo calls), paced
to stay under the free per-minute limit. Raw responses are cached on disk, so a stopped run resumes
where it left off. It does not retry a failed request: fix the cause and run it again.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from elnino_planner.config.loader import COUNTIES_DIR, load_county_pack
from elnino_planner.flags.climatology import ANALOG_SEASONS, climatology_path, percentile
from elnino_planner.location.geo import point_in_geometry
from elnino_planner.settings import load_settings

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
GRID = 0.25
BASE_SEASONS = [f"{y}-{str(y + 1)[2:]}" for y in range(1990, 2020)]   # 1990-91 .. 2019-20
WET_DAY_MM = 1.0
PAUSE_S = 2.0   # each request costs about 11 calls; 2 s apart is about 330 calls/minute (limit 600)
MONTHS = (11, 12, 1, 2, 3)


def season_dates(season: str) -> tuple[str, str]:
    start = int(season[:4])
    return f"{start}-11-01", f"{start + 1}-03-31"


def county_cells(boundary: dict, step: float = 0.02) -> list[tuple[float, float]]:
    """Grid cells (as centre points) that contain any part of the county, found by sampling it finely."""
    geom = boundary["features"][0]["geometry"]
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    lons = [p[0] for poly in polys for p in poly[0]]
    lats = [p[1] for poly in polys for p in poly[0]]
    cells = set()
    lat = min(lats)
    while lat <= max(lats):
        lon = min(lons)
        while lon <= max(lons):
            if point_in_geometry(lat, lon, geom):
                cells.add((round(round(lat / GRID) * GRID, 2), round(round(lon / GRID) * GRID, 2)))
            lon += step
        lat += step
    return sorted(cells)


def fetch_season(client: httpx.Client, cache: Path, lat: float, lon: float, season: str, tz: str) -> dict:
    path = cache / f"{lat}_{lon}_{season}.json"
    if path.exists():
        return json.loads(path.read_text())
    start, end = season_dates(season)
    resp = client.get(ARCHIVE, params={"latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
                                       "daily": "precipitation_sum", "models": "era5", "timezone": tz})
    if resp.status_code != 200:
        raise SystemExit(f"archive request failed with HTTP {resp.status_code} for {season}: {resp.text[:200]}\n"
                         f"Cached seasons are kept in {cache}; run again later to resume.")
    path.write_text(resp.text)
    time.sleep(PAUSE_S)
    return resp.json()


def daily_values(body: dict) -> list[tuple[date, float]]:
    d = body["daily"]
    return [(date.fromisoformat(t), v) for t, v in zip(d["time"], d["precipitation_sum"]) if v is not None]


def summarize_cell(lat: float, lon: float, seasons: dict[str, list[tuple[date, float]]]) -> dict:
    base = [seasons[s] for s in BASE_SEASONS]
    rains = [v for days in base for _, v in days]
    wet = [v for v in rains if v >= WET_DAY_MM]
    three_day = [sum(v for _, v in days[i - 2:i + 1]) for days in base for i in range(2, len(days))]
    p95 = percentile(wet, 95)
    by_month = {m: [v for days in base for d, v in days if d.month == m] for m in MONTHS}
    totals = [sum(v for _, v in days) for days in base]
    normal = sum(totals) / len(totals)
    heavy_normal = sum(sum(v >= p95 for _, v in days) for days in base) / len(base)

    analogs = {}
    for s in ANALOG_SEASONS:
        total = sum(v for _, v in seasons[s])
        analogs[s] = {"total_mm": round(total, 1), "pct_of_normal": round(100 * total / normal),
                      "heavy_days": sum(v >= p95 for _, v in seasons[s])}
    return {
        "lat": lat, "lon": lon,
        "wet_p95_mm": round(p95, 1),
        "wet_p99_mm": round(percentile(wet, 99), 1),
        "three_day_p99_mm": round(percentile(three_day, 99), 1),
        "mean_daily_mm_by_month": {str(m): round(sum(v) / len(v), 2) for m, v in by_month.items()},
        "season_total_normal_mm": round(normal, 1),
        "heavy_days_normal": round(heavy_normal, 1),
        "analogs": analogs,
    }


def build(fips: str, max_cells: int | None, cache: Path) -> Path:
    pack = load_county_pack(fips)
    boundary = json.loads((COUNTIES_DIR / pack.boundary_file).read_text())
    cells = county_cells(boundary)[:max_cells]
    seasons = sorted(set(BASE_SEASONS) | set(ANALOG_SEASONS))
    cache.mkdir(parents=True, exist_ok=True)
    print(f"{len(cells)} cells x {len(seasons)} winters = {len(cells) * len(seasons)} requests "
          f"(about {len(cells) * len(seasons) * 11:,} Open-Meteo calls). Cache: {cache}")

    settings = load_settings()
    out_cells = []
    with httpx.Client(timeout=60, headers={"User-Agent": settings.user_agent}) as client:
        for n, (lat, lon) in enumerate(cells, 1):
            data = {s: daily_values(fetch_season(client, cache, lat, lon, s, pack.timezone)) for s in seasons}
            out_cells.append(summarize_cell(lat, lon, data))
            print(f"  cell {n}/{len(cells)} done")

    table = {
        "source": "Open-Meteo Historical Weather API (ERA5, models=era5), daily precipitation_sum, local days",
        "base_period": "Nov-Mar, winters 1990-91 to 2019-20",
        "wet_day_mm": WET_DAY_MM,
        "grid_deg": GRID,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attribution": "Weather data by Open-Meteo.com (CC BY 4.0), ERA5 from Copernicus/ECMWF",
        "cells": out_cells,
    }
    path = climatology_path(fips)
    path.write_text(json.dumps(table, indent=1))
    print(f"Wrote {path}")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("fips", nargs="?", default=None)
    ap.add_argument("--max-cells", type=int, default=None)
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "elnino_climatology_cache")
    args = ap.parse_args()
    build(args.fips or load_settings().county_fips, args.max_cells, args.cache)
    sys.exit(0)
