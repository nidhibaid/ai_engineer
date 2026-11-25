"""One live call per WP-2 tool (opt-in, run by hand). Prints status and a short summary, never coordinates.

    uv run python -m elnino_planner.scripts.smoke_wp2                 # default test pin
    uv run python -m elnino_planner.scripts.smoke_wp2 37.3382 -121.8863
"""
from __future__ import annotations

import asyncio
import sys

from elnino_planner.config.loader import load_county_pack
from elnino_planner.location.resolve import LocationResolver
from elnino_planner.logging_setup import setup_logging
from elnino_planner.settings import load_settings
from elnino_planner.tools.alerts import get_active_alerts
from elnino_planner.tools.bay import get_bay_water_level
from elnino_planner.tools.creek import get_creek_status
from elnino_planner.tools.flood_zone import get_flood_zone
from elnino_planner.tools.http_client import HttpClient

DEFAULT_PIN = (37.4264, -121.9744)


def summary(name: str, res) -> str:
    d = res.data or {}
    if name == "alerts":
        info = ", ".join(a["event"] for a in d.get("alerts", [])) or "none active"
    elif name == "flood_zone":
        info = f"zone {d.get('zone')} sfha={d.get('in_sfha')}" if d else ""
    elif name == "creek":
        info = (f"{d.get('gauge_id')} {d.get('distance_km')} km, {d.get('observed_category')}, "
                f"forecast={d.get('has_forecast')}") if d else ""
    else:
        info = (f"residual {d['residual']} flags={d['flags']}") if d else ""
    reason = f" ({res.failure_reason.value})" if res.failure_reason else ""
    return f"{name:11} {res.status.value}{reason}  {info}"


async def main(argv: list[str]) -> int:
    settings = load_settings()
    setup_logging("WARNING")
    pack = load_county_pack(settings.county_fips)
    lat, lon = (float(argv[0]), float(argv[1])) if len(argv) == 2 else DEFAULT_PIN
    loc = LocationResolver(pack).from_point(lat, lon)
    print(f"location: city={loc.city} in_coverage={loc.in_coverage}")
    client = HttpClient(settings)
    try:
        results = await asyncio.gather(
            get_active_alerts(client, loc), get_flood_zone(client, loc, pack),
            get_creek_status(client, loc, pack), get_bay_water_level(client, loc))
    finally:
        await client.aclose()
    for name, res in zip(("alerts", "flood_zone", "creek", "bay"), results):
        print(summary(name, res))
    return 0 if all(r.status.value in ("ok", "no_data") for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
