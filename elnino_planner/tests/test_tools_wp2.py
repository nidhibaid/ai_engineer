"""WP-2 contract tests for the four official-source tools, against responses recorded 24-25 Nov 2026."""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

from elnino_planner.config.loader import load_county_pack
from elnino_planner.location.resolve import LocationResolver
from elnino_planner.settings import Settings
from elnino_planner.tools.alerts import EMPTY_CAVEAT, get_active_alerts, relevant_to_mvp
from elnino_planner.tools.bay import get_bay_water_level, king_tide_threshold
from elnino_planner.tools.creek import choose_gauge, get_creek_status
from elnino_planner.tools.flood_zone import NEEDS_POINT, get_flood_zone, plain_language
from elnino_planner.tools.http_client import HttpClient
from elnino_planner.tools.result import FailureReason, Status

FIX = Path(__file__).parent / "fixtures" / "tools"
PACK = load_county_pack("06085")
RESOLVER = LocationResolver(PACK)
ALVISO = RESOLVER.from_point(37.4264, -121.9744)
DOWNTOWN_SJ = RESOLVER.from_point(37.3382, -121.8863)
FRESNO = RESOLVER.from_point(36.7378, -119.7871)
PALO_ALTO_CENTROID = RESOLVER.from_point(37.4419, -122.1430, precision="place", label="Palo Alto")
NOW = datetime(2026, 11, 25, 10, 30, tzinfo=timezone.utc)   # during the king-tide Coastal Flood Warning


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def recorded(req: httpx.Request) -> httpx.Response:
    host, path, q = req.url.host, req.url.path, req.url.params
    if host == "api.weather.gov":
        return httpx.Response(200, json=load("nws_active_alviso.json"))
    if host == "hazards.fema.gov":
        lon = float(q["geometry"].split(",")[0])
        return httpx.Response(200, json=load("fema_alviso.json" if lon < -121.95 else "fema_downtown_sj.json"))
    if host == "api.water.noaa.gov":
        parts = path.rstrip("/").split("/")
        if parts[-1] == "gauges":
            return httpx.Response(200, json=load("nwps_gauges_bbox.json"))
        if parts[-1] == "stageflow":
            return httpx.Response(200, json=load(f"nwps_stageflow_{parts[-2]}.json"))
        return httpx.Response(200, json=load(f"nwps_gauge_{parts[-1]}.json"))
    if host == "api.tidesandcurrents.noaa.gov":
        if q["product"] == "water_level":
            return httpx.Response(200, json=load("coops_9414523_water_level.json"))
        kind = "6min" if q["interval"] == "6" else "hilo_2026"
        return httpx.Response(200, json=load(f"coops_9414523_predictions_{kind}.json"))
    raise AssertionError(f"unexpected request to {host}")


def client(handler=recorded) -> HttpClient:
    return HttpClient(Settings(contact="test@example.com"), transport=httpx.MockTransport(handler))


def always(status: int, body=None):
    return lambda req: httpx.Response(status, json=body) if body is not None else httpx.Response(status)


class Base(unittest.IsolatedAsyncioTestCase):
    async def run_tool(self, coro_fn, handler=recorded):
        c = client(handler)
        try:
            return await coro_fn(c)
        finally:
            await c.aclose()


class Alerts(Base):
    async def test_coastal_flood_warning_during_king_tides(self):
        res = await self.run_tool(lambda c: get_active_alerts(c, ALVISO))
        self.assertEqual(res.status, Status.OK)
        events = [a["event"] for a in res.data["alerts"]]
        self.assertIn("Coastal Flood Warning", events)
        first = res.data["alerts"][0]
        for key in ("event", "severity", "urgency", "certainty", "headline", "description", "instruction",
                    "onset", "expires", "sender", "url", "relevant_to_mvp"):
            self.assertIn(key, first)
        self.assertTrue(first["relevant_to_mvp"])
        self.assertEqual(res.caveats, [])
        self.assertIsNotNone(res.fetched_at)

    async def test_works_outside_county(self):
        res = await self.run_tool(lambda c: get_active_alerts(c, FRESNO))
        self.assertEqual(res.status, Status.OK)

    async def test_empty_list_has_caveat(self):
        res = await self.run_tool(lambda c: get_active_alerts(c, ALVISO), always(200, {"features": []}))
        self.assertEqual(res.data["alerts"], [])
        self.assertEqual(res.caveats, [EMPTY_CAVEAT])

    async def test_ordering_by_severity_then_onset(self):
        feats = [{"properties": {"event": e, "severity": s, "onset": o}} for e, s, o in [
            ("Wind Advisory", "Moderate", "2026-11-24T10:00:00Z"),
            ("Coastal Flood Warning", "Severe", "2026-11-24T12:00:00Z"),
            ("High Surf Warning", "Severe", "2026-11-24T08:00:00Z")]]
        res = await self.run_tool(lambda c: get_active_alerts(c, ALVISO), always(200, {"features": feats}))
        self.assertEqual([a["event"] for a in res.data["alerts"]],
                         ["High Surf Warning", "Coastal Flood Warning", "Wind Advisory"])

    async def test_unavailable(self):
        res = await self.run_tool(lambda c: get_active_alerts(c, ALVISO), always(503))
        self.assertEqual(res.status, Status.UNAVAILABLE)
        self.assertEqual(res.failure_reason, FailureReason.UPSTREAM_ERROR)
        self.assertIsNotNone(res.fallback_url)

    def test_relevance(self):
        self.assertTrue(relevant_to_mvp("Coastal Flood Warning"))
        self.assertTrue(relevant_to_mvp("High Wind Watch"))
        self.assertFalse(relevant_to_mvp("Excessive Heat Warning"))


class FloodZone(Base):
    async def test_alviso_is_ae(self):
        res = await self.run_tool(lambda c: get_flood_zone(c, ALVISO, PACK))
        self.assertEqual(res.status, Status.OK)
        self.assertEqual(res.data["zone"], "AE")
        self.assertTrue(res.data["in_sfha"])
        self.assertEqual(res.data["base_flood_elevation"], {"ft": 12.0, "m": 3.66})
        self.assertIn("king tides", res.caveats[0])

    async def test_downtown_zone_d_has_no_bfe(self):
        res = await self.run_tool(lambda c: get_flood_zone(c, DOWNTOWN_SJ, PACK))
        self.assertEqual(res.data["zone"], "D")
        self.assertFalse(res.data["in_sfha"])
        self.assertIsNone(res.data["base_flood_elevation"])    # -9999 sentinel
        self.assertIn("not been studied", res.data["plain_language"])

    async def test_place_centroid_gets_lookup_link(self):
        res = await self.run_tool(lambda c: get_flood_zone(c, PALO_ALTO_CENTROID, PACK), always(500))
        self.assertEqual(res.status, Status.NO_DATA)
        self.assertEqual(res.fallback_url, PACK.flood_zone_lookup_url)
        self.assertEqual(res.caveats, [NEEDS_POINT])

    async def test_unmapped_point_and_outage(self):
        res = await self.run_tool(lambda c: get_flood_zone(c, ALVISO, PACK), always(200, {"features": []}))
        self.assertEqual(res.status, Status.NO_DATA)
        res = await self.run_tool(lambda c: get_flood_zone(c, ALVISO, PACK), always(200, {"error": {}}))
        self.assertEqual(res.status, Status.UNAVAILABLE)
        self.assertEqual(res.fallback_url, PACK.flood_zone_lookup_url)

    async def test_out_of_coverage(self):
        res = await self.run_tool(lambda c: get_flood_zone(c, FRESNO, PACK))
        self.assertEqual(res.status, Status.OUT_OF_COVERAGE)

    def test_shaded_x(self):
        self.assertIn("0.2%", plain_language("X", "0.2 PCT ANNUAL CHANCE FLOOD HAZARD"))
        self.assertIn("minimal", plain_language("X", "AREA OF MINIMAL FLOOD HAZARD"))


class Creek(Base):
    async def test_alviso_prefers_gauge_with_forecast(self):
        res = await self.run_tool(lambda c: get_creek_status(c, ALVISO, PACK))
        self.assertEqual(res.status, Status.OK)
        d = res.data
        self.assertEqual(d["gauge_id"], "CMIC1")   # Alviso Slough has no flood categories, so it does not qualify
        self.assertTrue(d["has_forecast"])
        self.assertEqual(d["observed_category"], "no_flooding")
        self.assertEqual(d["forecast_peak_stage"]["ft"], 5.45)
        self.assertEqual(set(d["thresholds"]), {"action", "minor", "moderate", "major"})
        self.assertIn("km", res.caveats[0])

    async def test_downtown_gauge_without_forecast(self):
        res = await self.run_tool(lambda c: get_creek_status(c, DOWNTOWN_SJ, PACK))
        d = res.data
        self.assertEqual(d["gauge_id"], "CYYC1")
        self.assertFalse(d["has_forecast"])
        self.assertIsNone(d["forecast_peak_stage"])
        self.assertIsNone(d["forecast_category"])
        self.assertEqual(d["observed_stage"]["ft"], 4.68)

    def test_selection_rules(self):
        def g(lid, lat, obs="no_flooding", fcst="fcst_not_current"):
            return {"lid": lid, "latitude": lat, "longitude": 0.0,
                    "status": {"observed": {"floodCategory": obs}, "forecast": {"floodCategory": fcst}}}
        km = 1 / 111.2   # degrees of latitude per km
        gauges = [g("NEAR", 1 * km), g("FCST", 1.4 * km, fcst="no_flooding"), g("DEAD", 0.1 * km, obs="obs_not_current")]
        self.assertEqual(choose_gauge(gauges, 0, 0)[0]["lid"], "FCST")      # within 1.5x of nearest
        gauges[1]["latitude"] = 1.6 * km
        self.assertEqual(choose_gauge(gauges, 0, 0)[0]["lid"], "NEAR")      # too far to be preferred
        self.assertIsNone(choose_gauge([g("FAR", 6 * km)], 0, 0))           # beyond 5 km

    async def test_no_gauge_nearby(self):
        res = await self.run_tool(lambda c: get_creek_status(c, ALVISO, PACK), always(200, {"gauges": []}))
        self.assertEqual(res.status, Status.NO_DATA)

    async def test_unavailable(self):
        res = await self.run_tool(lambda c: get_creek_status(c, ALVISO, PACK), always(502))
        self.assertEqual(res.status, Status.UNAVAILABLE)


class Bay(Base):
    async def test_king_tide_week(self):
        res = await self.run_tool(lambda c: get_bay_water_level(c, ALVISO, now=NOW))
        self.assertEqual(res.status, Status.OK)
        d = res.data
        self.assertEqual(d["station_id"], "9414523")
        self.assertGreaterEqual(d["residual"]["ft"], 0.5)        # about 1 ft above predictions that week
        self.assertIn("water_above_predictions", d["flags"])
        self.assertTrue(d["king_tide_window"])
        self.assertIn("king_tide_window", d["flags"])
        self.assertGreater(len(d["next_high_tides"]), 0)
        self.assertGreaterEqual(d["highest_predicted"]["height"]["ft"], d["king_tide_threshold"]["ft"])
        self.assertIn("Redwood City", res.caveats[0])
        self.assertTrue(all(t["time"] > NOW.isoformat() for t in d["next_high_tides"]))

    async def test_window_length_respected(self):
        res = await self.run_tool(lambda c: get_bay_water_level(c, ALVISO, days_ahead=1, now=NOW))
        self.assertLessEqual(len(res.data["next_high_tides"]), 2)

    async def test_coops_error_body_is_unavailable(self):
        res = await self.run_tool(lambda c: get_bay_water_level(c, ALVISO, now=NOW),
                                  always(200, {"error": {"message": "No data was found."}}))
        self.assertEqual(res.status, Status.UNAVAILABLE)
        self.assertEqual(res.failure_reason, FailureReason.BAD_RESPONSE)

    async def test_out_of_coverage(self):
        res = await self.run_tool(lambda c: get_bay_water_level(c, FRESNO, now=NOW))
        self.assertEqual(res.status, Status.OUT_OF_COVERAGE)

    def test_threshold(self):
        self.assertEqual(king_tide_threshold([float(i) for i in range(100)]), 98.0)   # top 2 of 100
        self.assertIsNone(king_tide_threshold([]))


if __name__ == "__main__":
    unittest.main()
