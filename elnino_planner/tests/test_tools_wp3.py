"""WP-3: flag rules, get_forecast_flags and get_seasonal_outlook against responses recorded 25 Nov 2026."""
import json
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

from elnino_planner.config.loader import load_county_pack
from elnino_planner.flags.climatology import ANALOG_SEASONS, climatology_path, load_climatology, percentile
from elnino_planner.flags.rules import daily_flags, tendency
from elnino_planner.location.resolve import LocationResolver
from elnino_planner.settings import Settings
from elnino_planner.tools.forecast import CAVEAT, EXTENDED_CAVEAT, get_forecast_flags
from elnino_planner.tools.http_client import HttpClient
from elnino_planner.tools.result import Status
from elnino_planner.tools.seasonal import CAVEATS, get_seasonal_outlook, tendency_word

FIX = Path(__file__).parent / "fixtures" / "tools"
REPLAY_RAW = Path(__file__).resolve().parents[2] / "evals" / "replay" / "flags" / "raw"
PACK = load_county_pack("06085")
RESOLVER = LocationResolver(PACK)
DOWNTOWN_SJ = RESOLVER.from_point(37.3382, -121.8863)
FRESNO = RESOLVER.from_point(36.7378, -119.7871)
CELL = {"wet_p95_mm": 20.0, "wet_p99_mm": 35.0, "three_day_p99_mm": 50.0}
NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text())


def serve(body):
    return lambda req: httpx.Response(200, json=body)


class Base(unittest.IsolatedAsyncioTestCase):
    async def call(self, fn, handler):
        c = HttpClient(Settings(contact="test@example.com"), transport=httpx.MockTransport(handler))
        try:
            return await fn(c)
        finally:
            await c.aclose()


class Rules(unittest.TestCase):
    def test_rain_wind_and_prolonged(self):
        flags = daily_flags([5, 21, 36, 10, 0], [20, 46, 60, None, 10], CELL)
        self.assertEqual(flags[0], [])
        self.assertEqual(flags[1], ["heavy_rain_day", "strong_wind"])
        self.assertEqual(flags[2], ["very_heavy_rain_day", "damaging_wind", "prolonged_rain"])   # 5+21+36 >= 50
        self.assertEqual(flags[3], ["prolonged_rain"])                                           # 21+36+10
        self.assertEqual(flags[4], [])

    def test_tendency_and_percentile(self):
        self.assertEqual(tendency(30, 20), "wetter_than_usual")
        self.assertEqual(tendency(10, 20), "drier_than_usual")
        self.assertEqual(tendency(20, 20), "near_usual")
        self.assertEqual(percentile([1, 2, 3, 4, 5], 50), 3)
        self.assertAlmostEqual(percentile(list(range(101)), 95), 95)

    def test_climatology_table_covers_county(self):
        table = load_climatology(climatology_path("06085"))
        self.assertGreaterEqual(len(table["cells"]), 10)
        for c in table["cells"]:
            self.assertLess(c["wet_p95_mm"], c["wet_p99_mm"])
            self.assertEqual(set(c["analogs"]), set(ANALOG_SEASONS))
            self.assertEqual(set(c["mean_daily_mm_by_month"]), {"11", "12", "1", "2", "3"})


class Forecast(Base):
    async def test_recorded_16_day(self):
        res = await self.call(lambda c: get_forecast_flags(c, DOWNTOWN_SJ, PACK, days=16),
                              serve(load("openmeteo_forecast_16d.json")))
        self.assertEqual(res.status, Status.OK)
        d = res.data
        self.assertEqual(len(d["daily"]), 7)                    # no daily values for days 8-16
        self.assertEqual(set(d["extended"]), {"days", "tendency", "confidence"})
        self.assertEqual(d["extended"]["confidence"], "low")
        self.assertIn(d["extended"]["tendency"], ("wetter_than_usual", "near_usual", "drier_than_usual"))
        self.assertEqual(res.caveats, [CAVEAT, EXTENDED_CAVEAT])
        day = d["daily"][0]
        self.assertEqual(set(day), {"date", "rain", "rain_probability_pct", "max_gust", "flags"})
        self.assertEqual(set(day["rain"]), {"mm", "in"})
        self.assertEqual(set(day["max_gust"]), {"kmh", "mph"})

    async def test_storm_day_is_flagged(self):
        body = load("openmeteo_forecast_16d.json")
        body["daily"]["precipitation_sum"][2] = 90.0           # far above any local 99th percentile
        body["daily"]["wind_gusts_10m_max"][2] = 100.0         # 62 mph
        res = await self.call(lambda c: get_forecast_flags(c, DOWNTOWN_SJ, PACK, days=7), serve(body))
        self.assertIn("very_heavy_rain_day", res.data["daily"][2]["flags"])
        self.assertIn("damaging_wind", res.data["daily"][2]["flags"])
        self.assertIsNone(res.data["extended"])
        self.assertEqual(res.caveats, [CAVEAT])

    async def test_unavailable_and_out_of_coverage(self):
        res = await self.call(lambda c: get_forecast_flags(c, DOWNTOWN_SJ, PACK), lambda r: httpx.Response(503))
        self.assertEqual(res.status, Status.UNAVAILABLE)
        self.assertIn("forecast.weather.gov", res.fallback_url)
        res = await self.call(lambda c: get_forecast_flags(c, FRESNO, PACK), lambda r: httpx.Response(503))
        self.assertEqual(res.status, Status.OUT_OF_COVERAGE)


DATE_OR_AMOUNT = re.compile(r"\d|\bmm\b|\binch|\bin\.\b|January|February|March|April|May|June|July|"
                            r"August|September|October|November|December|\bwill\b|certain", re.IGNORECASE)


class Seasonal(Base):
    async def outlook(self):
        return await self.call(lambda c: get_seasonal_outlook(c, DOWNTOWN_SJ, PACK, now=NOW),
                               serve(load("openmeteo_seasonal.json")))

    async def test_recorded_outlook(self):
        res = await self.outlook()
        self.assertEqual(res.status, Status.OK)
        d = res.data
        self.assertEqual(d["members"], 51)
        self.assertGreater(len(d["outlook_weeks"]), 0)
        self.assertGreater(len(d["outlook_months"]), 0)
        for p in d["outlook_weeks"] + d["outlook_months"]:
            self.assertTrue(0 <= p["share_wetter_pct"] <= 100)
            self.assertIn(p["tendency"], ("odds tilt wetter", "odds tilt drier", "no clear tilt"))
        self.assertEqual([a["winter"] for a in d["analogs"]], list(ANALOG_SEASONS))   # each winter, no average
        self.assertEqual(res.caveats[:3], CAVEATS)
        self.assertEqual(d["enso"]["status"], "El Niño Advisory")

    async def test_summary_fields_have_no_dates_or_amounts(self):
        d = (await self.outlook()).data
        summary_fields = [d["summary"]] + [p["tendency"] for p in d["outlook_weeks"] + d["outlook_months"]]
        for text in summary_fields:
            self.assertIsNone(DATE_OR_AMOUNT.search(text), text)
        self.assertNotIn("_mm", json.dumps(d["outlook_weeks"] + d["outlook_months"]))   # no future amounts

    async def test_stale_enso_is_flagged(self):
        res = await self.call(lambda c: get_seasonal_outlook(c, DOWNTOWN_SJ, PACK,
                                                             now=datetime(2027, 1, 1, tzinfo=timezone.utc)),
                              serve(load("openmeteo_seasonal.json")))
        self.assertEqual(len(res.caveats), 4)

    async def test_unavailable(self):
        res = await self.call(lambda c: get_seasonal_outlook(c, DOWNTOWN_SJ, PACK), serve({"error": True}))
        self.assertEqual(res.status, Status.UNAVAILABLE)

    def test_tendency_words(self):
        self.assertEqual(tendency_word(0.6), "odds tilt wetter")
        self.assertEqual(tendency_word(0.4), "odds tilt drier")
        self.assertEqual(tendency_word(0.5), "no clear tilt")


@unittest.skipUnless((REPLAY_RAW / "archive_winter_2022_23_alviso.json").exists(), "run scripts/replay_flags first")
class Replay(unittest.TestCase):
    """The 31 Dec 2022 atmospheric river, from the ERA5 archive, must raise a rain flag at Alviso."""
    def test_new_years_eve_2022(self):
        from elnino_planner.flags.climatology import cell_for
        from elnino_planner.scripts.replay_flags import archive_flags
        body = json.loads((REPLAY_RAW / "archive_winter_2022_23_alviso.json").read_text())
        cell = cell_for(load_climatology(climatology_path("06085")), 37.43, -121.97)
        flags = archive_flags(body, cell)
        self.assertTrue({"heavy_rain_day", "very_heavy_rain_day", "prolonged_rain"} & set(flags["2022-12-31"]))


if __name__ == "__main__":
    unittest.main()
