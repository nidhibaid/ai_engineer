"""WP-1 acceptance: Alviso, downtown San Jose and Palo Alto resolve; Fresno and Santa Cruz are out of coverage.

Pins run entirely offline. Text lookups replay geocoder responses recorded on 2026-11-25.
"""
import json
import unittest
from pathlib import Path

import httpx

from elnino_planner.config.loader import load_county_pack
from elnino_planner.location.geo import haversine_km, point_in_geometry
from elnino_planner.location.geocode import looks_like_address
from elnino_planner.location.resolve import LocationResolver
from elnino_planner.settings import Settings
from elnino_planner.tools.http_client import HttpClient

FIXTURES = Path(__file__).parent / "fixtures" / "location"
RESOLVER = LocationResolver(load_county_pack("06085"))

# name: (lat, lon, expected city or None for unincorporated, nearest gauge)
IN_COUNTY = {
    "alviso": (37.4264, -121.9744, "San Jose", "ALVC1"),
    "downtown_san_jose": (37.3382, -121.8863, "San Jose", "CYYC1"),
    "palo_alto_san_francisquito": (37.4419, -122.1430, "Palo Alto", "CHJC1"),
    "mountain_view_downtown": (37.3894, -122.0819, "Mountain View", "CAAC1"),
    # Nearest gauge is 7 km away: beyond get_creek_status's 5 km limit, so WP-2 returns no_data here.
    "mountain_view_baylands": (37.4300, -122.0800, "Mountain View", "CHJC1"),
    "morgan_hill": (37.1305, -121.6544, "Morgan Hill", "CYTC1"),
    "san_martin_unincorporated": (37.0850, -121.6100, None, "CYTC1"),
}
OUT_OF_COUNTY = {"fresno": (36.7378, -119.7871), "santa_cruz": (36.9741, -122.0308)}


class Pins(unittest.TestCase):
    def test_in_county_points(self):
        for name, (lat, lon, city, gauge) in IN_COUNTY.items():
            with self.subTest(name):
                loc = RESOLVER.from_point(lat, lon)
                self.assertTrue(loc.in_coverage)
                self.assertEqual(loc.county_fips, "06085")
                self.assertEqual(loc.city, city)
                self.assertEqual(loc.creek_gauge.id, gauge)
                self.assertEqual(loc.tide_station.id, "9414523")   # only primary stations are candidates
                self.assertTrue(loc.has_exact_point)

    def test_out_of_county_points(self):
        for name, (lat, lon) in OUT_OF_COUNTY.items():
            with self.subTest(name):
                loc = RESOLVER.from_point(lat, lon)
                self.assertFalse(loc.in_coverage)
                self.assertIsNone(loc.county_fips)
                self.assertIsNone(loc.tide_station)
                self.assertEqual(loc.jurisdictions, set())    # no local guidance out of coverage

    def test_jurisdictions_and_rounding(self):
        loc = RESOLVER.from_point(37.42641, -121.97444)
        self.assertEqual(loc.jurisdictions, {"san_jose", "06085", "CA", "US"})
        self.assertEqual(loc.rounded, (37.43, -121.97))
        unincorporated = RESOLVER.from_point(37.085, -121.61)
        self.assertEqual(unincorporated.jurisdictions, {"06085", "CA", "US"})

    def test_exact_point_never_in_repr(self):
        text = repr(RESOLVER.from_point(37.42641, -121.97444))
        self.assertNotIn("37.42641", text)
        self.assertNotIn("121.97444", text)


class Geometry(unittest.TestCase):
    def test_polygon_with_hole(self):
        square = [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]
        geom = {"type": "Polygon", "coordinates": square}
        self.assertTrue(point_in_geometry(2, 2, geom))
        self.assertFalse(point_in_geometry(5, 5, geom))     # in the hole
        self.assertFalse(point_in_geometry(11, 5, geom))
        multi = {"type": "MultiPolygon", "coordinates": [square, [[[20, 20], [21, 20], [21, 21], [20, 21], [20, 20]]]]}
        self.assertTrue(point_in_geometry(20.5, 20.5, multi))

    def test_haversine(self):
        # San Francisco to Redwood City tide stations, about 39 km
        self.assertAlmostEqual(haversine_km(37.8063, -122.4659, 37.5068, -122.2119), 40.0, delta=1.5)

    def test_address_detection(self):
        self.assertTrue(looks_like_address("200 E Santa Clara St, San Jose"))
        self.assertFalse(looks_like_address("Alviso"))


def fixture_handler(overrides: dict | None = None):
    """Serve recorded geocoder responses, keyed by the query text."""
    def handler(req: httpx.Request) -> httpx.Response:
        if overrides and req.url.host in overrides:
            return overrides[req.url.host]
        if req.url.host == "geocoding-api.open-meteo.com":
            name = req.url.params["name"].lower().replace(" ", "_")
            path = FIXTURES / f"openmeteo_{name}.json"
        elif "Santa Clara St" in req.url.params.get("address", ""):
            path = FIXTURES / "census_sj_city_hall.json"
        else:
            path = FIXTURES / "census_no_match.json"
        return httpx.Response(200, json=json.loads(path.read_text()))
    return handler


class Text(unittest.IsolatedAsyncioTestCase):
    async def resolve(self, text, overrides=None):
        client = HttpClient(Settings(contact="test@example.com"),
                            transport=httpx.MockTransport(fixture_handler(overrides)))
        try:
            return await RESOLVER.from_text(client, text)
        finally:
            await client.aclose()

    async def test_place_names_in_county(self):
        for text, city in [("Alviso", "San Jose"), ("San Jose", "San Jose"), ("Palo Alto", "Palo Alto"),
                           ("Morgan Hill", "Morgan Hill")]:
            with self.subTest(text):
                res = await self.resolve(text)
                self.assertEqual(res.status, "ok")
                self.assertTrue(res.location.in_coverage)
                self.assertEqual(res.location.city, city)
                self.assertFalse(res.location.has_exact_point)   # centroid: flood zone needs a pin

    async def test_place_names_out_of_coverage(self):
        for text in ("Fresno", "Santa Cruz"):
            with self.subTest(text):
                res = await self.resolve(text)
                self.assertEqual(res.status, "ok")
                self.assertFalse(res.location.in_coverage)

    async def test_ambiguous_place(self):
        res = await self.resolve("Mountain View")   # Santa Clara County and Contra Costa
        self.assertEqual(res.status, "ambiguous")
        self.assertEqual(len(res.candidates), 2)
        picked = RESOLVER.from_candidate(next(c for c in res.candidates if "Santa Clara" in c.label))
        self.assertEqual(picked.city, "Mountain View")

    async def test_street_address(self):
        res = await self.resolve("200 E Santa Clara St, San Jose, CA 95113")
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.location.precision, "address")
        self.assertEqual(res.location.city, "San Jose")
        self.assertTrue(res.location.has_exact_point)

    async def test_no_match_and_unavailable(self):
        self.assertEqual((await self.resolve("1 Nowhere Road, Nowhere, CA")).status, "not_found")
        self.assertEqual((await self.resolve("   ")).status, "not_found")
        down = {"geocoding-api.open-meteo.com": httpx.Response(503)}
        self.assertEqual((await self.resolve("Alviso", down)).status, "unavailable")


if __name__ == "__main__":
    unittest.main()
