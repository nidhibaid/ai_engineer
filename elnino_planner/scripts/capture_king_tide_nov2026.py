"""One-off capture of raw NWS and CO-OPS responses during the Nov 2026 king tides.

These become WP-2 contract fixtures and the eval-plan replay for 24-26 Nov 2026
(05_eval_plan.md). NWS active alerts vanish once they expire, so run this while the
Coastal Flood Warning is in effect, and again on 26 Nov.

    uv run python -m planner.scripts.capture_king_tide_nov2026
    uv run python -m planner.scripts.capture_king_tide_nov2026 --out evals/replay/2026-11-king-tides

Each run writes to <out>/<UTC stamp>/: one <name>.json per successful request plus manifest.json.
Points are rounded to two decimals (privacy rule); station ids come from the county pack.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from elnino_planner.config.loader import load_county_pack
from elnino_planner.logging_setup import setup_logging
from elnino_planner.settings import Settings, load_settings
from elnino_planner.tools.http_client import HttpClient

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "evals" / "replay" / "2026-11-king-tides"

NWS_ACTIVE = "https://api.weather.gov/alerts/active"
NWS_ALERTS = "https://api.weather.gov/alerts"
COOPS = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"

# Bay-shoreline test points, already rounded to two decimals.
PLACES: dict[str, tuple[float, float]] = {
    "alviso": (37.43, -121.97),
    "mountain_view_baylands": (37.43, -122.08),
    "palo_alto_baylands": (37.45, -122.11),
}
WINDOW_BEGIN, WINDOW_END = "20261120", "20261127"
NWS_RECENT_START = "2026-11-20T00:00:00Z"


@dataclass
class CaptureRequest:
    name: str
    url: str
    params: dict[str, Any] = field(default_factory=dict)


def build_requests(station_ids: list[str], app_name: str) -> list[CaptureRequest]:
    reqs: list[CaptureRequest] = []
    for place, (lat, lon) in PLACES.items():
        point = f"{lat:.2f},{lon:.2f}"
        reqs.append(CaptureRequest(f"nws_active_{place}", NWS_ACTIVE, {"point": point}))
        reqs.append(CaptureRequest(f"nws_recent_{place}", NWS_ALERTS,
                                   {"point": point, "start": NWS_RECENT_START}))

    base = {"datum": "MLLW", "units": "english", "time_zone": "gmt", "format": "json",
            "application": app_name}
    for sid in station_ids:
        window = {"station": sid, "begin_date": WINDOW_BEGIN, "end_date": WINDOW_END}
        reqs.append(CaptureRequest(f"coops_{sid}_water_level", COOPS,
                                   {**base, **window, "product": "water_level"}))
        reqs.append(CaptureRequest(f"coops_{sid}_predictions_6min", COOPS,
                                   {**base, **window, "product": "predictions", "interval": "6"}))
        reqs.append(CaptureRequest(f"coops_{sid}_predictions_hilo_2026", COOPS,
                                   {**base, "station": sid, "begin_date": "20260101",
                                    "end_date": "20261231", "product": "predictions",
                                    "interval": "hilo"}))
    return reqs


def _coops_error(body: Any) -> str | None:
    """CO-OPS answers HTTP 200 with {"error": {"message": ...}} for bad queries or no data."""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("message", "unknown error"))
    return None


def _nws_events(body: Any) -> list[str]:
    feats = body.get("features", []) if isinstance(body, dict) else []
    return sorted({f.get("properties", {}).get("event", "?") for f in feats})


async def capture(client: HttpClient, requests: list[CaptureRequest], run_dir: Path) -> list[dict]:
    run_dir.mkdir(parents=True, exist_ok=True)
    outcomes = await asyncio.gather(*(client.get_json(r.url, params=r.params, tool=r.name)
                                      for r in requests))
    manifest: list[dict] = []
    for req, out in zip(requests, outcomes):
        entry: dict[str, Any] = {
            "name": req.name, "url": req.url, "params": req.params, "ok": out.ok,
            "http_status": out.http_status,
            "reason": out.reason.value if out.reason else None,
            "fetched_at": out.fetched_at.isoformat() if out.fetched_at else None,
            "bytes": 0,
        }
        if out.ok:
            text = json.dumps(out.json, indent=1, ensure_ascii=False)
            (run_dir / f"{req.name}.json").write_text(text, encoding="utf-8")
            entry["bytes"] = len(text.encode("utf-8"))
            if err := _coops_error(out.json):
                entry["upstream_message"] = err
            if req.url.startswith("https://api.weather.gov"):
                entry["nws_events"] = _nws_events(out.json)
        manifest.append(entry)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def summarize(manifest: list[dict]) -> str:
    ok = sum(e["ok"] for e in manifest)
    lines = [f"{ok}/{len(manifest)} requests succeeded"]
    for e in manifest:
        if not e["ok"]:
            lines.append(f"  FAILED {e['name']}: {e['reason']} (HTTP {e['http_status']})")
        elif "upstream_message" in e:
            lines.append(f"  NOTE   {e['name']}: CO-OPS says {e['upstream_message']!r}")
        elif "nws_events" in e:
            lines.append(f"  {e['name']}: {', '.join(e['nws_events']) or 'no alerts'}")
        else:
            lines.append(f"  {e['name']}: {e['bytes']:,} bytes")
    return "\n".join(lines)


async def run(settings: Settings, out_root: Path, *, transport=None, stamp: str | None = None
              ) -> tuple[Path, list[dict]]:
    pack = load_county_pack(settings.county_fips)
    station_ids = [s.id for s in pack.tide_stations]
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_root / stamp
    client = HttpClient(settings, transport=transport)
    try:
        manifest = await capture(client, build_requests(station_ids, settings.app_name), run_dir)
    finally:
        await client.aclose()
    return run_dir, manifest


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(settings.log_level)
    for problem in settings.problems():
        if "PLANNER_CONTACT" in problem:
            print(f"warning: {problem}", file=sys.stderr)

    run_dir, manifest = await run(settings, args.out)
    print(f"Saved to {run_dir}")
    print(summarize(manifest))
    return 0 if any(e["ok"] for e in manifest) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
