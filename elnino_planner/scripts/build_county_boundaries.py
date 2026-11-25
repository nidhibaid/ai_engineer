"""Download the county boundary and its incorporated cities from Census TIGERweb (admin, run once).

    uv run python -m elnino_planner.scripts.build_county_boundaries            # county from settings
    uv run python -m elnino_planner.scripts.build_county_boundaries 06085

Writes config/counties/<fips>/boundary.geojson and cities.geojson. The app only reads these files;
it never calls TIGERweb, so a pin is checked for coverage without leaving the server.
"""
from __future__ import annotations

import json
import re
import sys

import httpx

from elnino_planner.config.loader import COUNTIES_DIR
from elnino_planner.location.geo import point_in_geometry
from elnino_planner.settings import load_settings

TIGERWEB = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"
COUNTY_LAYER = f"{TIGERWEB}/State_County/MapServer/1/query"                    # current Counties
PLACE_LAYER = f"{TIGERWEB}/Places_CouSub_ConCity_SubMCD/MapServer/4/query"     # current Incorporated Places
SIMPLIFY_DEG = 0.0001  # about 10 m; keeps files small without moving boundaries noticeably


def city_key(name: str) -> str:
    """'San Jose' -> 'san_jose'. Used as the city jurisdiction code in the corpus."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _query(client: httpx.Client, url: str, **params) -> dict:
    base = {"outSR": "4326", "maxAllowableOffset": SIMPLIFY_DEG, "f": "geojson"}
    resp = client.get(url, params={**base, **params})
    resp.raise_for_status()
    return resp.json()


def _vertices(geometry: dict) -> list[list[float]]:
    polys = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return [pt for poly in polys for ring in poly for pt in ring]


def mostly_inside(city_geom: dict, county_geom: dict) -> bool:
    """Bounding-box queries also return neighbouring cities; keep a city if most of its vertices are inside."""
    pts = _vertices(city_geom)
    inside = sum(point_in_geometry(lat, lon, county_geom) for lon, lat in pts)
    return inside > len(pts) / 2


def build(fips: str) -> None:
    with httpx.Client(timeout=60, headers={"User-Agent": "StormSeasonPlanner/0.1 (boundary build)"}) as client:
        county = _query(client, COUNTY_LAYER, where=f"GEOID='{fips}'", outFields="GEOID,NAME")
        if len(county["features"]) != 1:
            raise SystemExit(f"expected one county for {fips}, got {len(county['features'])}")
        geom = county["features"][0]["geometry"]
        pts = _vertices(geom)
        lons, lats = [p[0] for p in pts], [p[1] for p in pts]
        envelope = f"{min(lons)},{min(lats)},{max(lons)},{max(lats)}"
        places = _query(client, PLACE_LAYER, where=f"STATE='{fips[:2]}'", geometry=envelope,
                        geometryType="esriGeometryEnvelope", inSR="4326",
                        spatialRel="esriSpatialRelIntersects", outFields="GEOID,BASENAME")

    cities = []
    for f in places["features"]:
        if mostly_inside(f["geometry"], geom):
            name = f["properties"]["BASENAME"]
            cities.append({"type": "Feature", "geometry": f["geometry"],
                           "properties": {"name": name, "key": city_key(name),
                                          "geoid": f["properties"]["GEOID"]}})
    cities.sort(key=lambda c: c["properties"]["name"])

    out = COUNTIES_DIR / fips
    out.mkdir(exist_ok=True)
    (out / "boundary.geojson").write_text(json.dumps(county))
    (out / "cities.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": cities}))
    print(f"{county['features'][0]['properties']['NAME']}: {len(cities)} cities -> {out}")
    print("  " + ", ".join(c["properties"]["name"] for c in cities))


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else load_settings().county_fips)
