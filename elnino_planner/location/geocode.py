"""Text to candidate points: Open-Meteo for place names, US Census for street addresses.

Both return a list of candidates, or None when the service could not be reached. The text is sent
only as a query parameter, and the shared HTTP client never logs parameters.
"""
from __future__ import annotations

from dataclasses import dataclass

from elnino_planner.tools.http_client import HttpClient

OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
CENSUS_GEOCODE = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
PLACE_TTL_S = 24 * 3600   # place names are not personal; addresses are never cached


@dataclass(frozen=True)
class Candidate:
    label: str
    lat: float
    lon: float
    precision: str  # "address" or "place"


def looks_like_address(text: str) -> bool:
    """Street addresses start with a house number; place names do not."""
    return text.strip()[:1].isdigit()


async def geocode_place(client: HttpClient, text: str, state_name: str) -> list[Candidate] | None:
    """Populated places in the state. Exact name matches win, so 'Fresno' drops nearby towns."""
    out = await client.get_json(OPEN_METEO_GEOCODE, ttl_s=PLACE_TTL_S, tool="geocode_place",
                                params={"name": text.strip(), "count": 10, "countryCode": "US",
                                        "format": "json"})
    if not out.ok:
        return None
    places = [r for r in out.json.get("results", [])
              if r.get("admin1") == state_name and str(r.get("feature_code", "")).startswith("PPL")]
    exact = [r for r in places if r["name"].lower() == text.strip().lower()]
    return [Candidate(label=_place_label(r), lat=r["latitude"], lon=r["longitude"], precision="place")
            for r in (exact or places)]


def _place_label(r: dict) -> str:
    county = r.get("admin2")
    return f"{r['name']}, {county}" if county else r["name"]


async def geocode_address(client: HttpClient, text: str) -> list[Candidate] | None:
    out = await client.get_json(CENSUS_GEOCODE, tool="geocode_address",
                                params={"address": text.strip(), "benchmark": "Public_AR_Current",
                                        "format": "json"})
    if not out.ok:
        return None
    matches = (out.json.get("result") or {}).get("addressMatches", [])
    return [Candidate(label=m["matchedAddress"], lat=m["coordinates"]["y"], lon=m["coordinates"]["x"],
                      precision="address") for m in matches]
