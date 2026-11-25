"""Turn a pin or typed text into the session's Location: coverage, city, nearest tide station and gauge.

    resolver = LocationResolver(load_county_pack("06085"))
    loc = resolver.from_point(37.43, -121.97)                   # map pin
    res = await resolver.from_text(http_client, "Palo Alto")    # place name or street address

The exact point stays in memory (it is hidden from repr so it cannot slip into a log line).
Tools that must not see it use `Location.rounded`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from elnino_planner.config.loader import COUNTIES_DIR
from elnino_planner.config.schema import CountyPack
from elnino_planner.location.geo import nearest, point_in_geometry, round_point
from elnino_planner.location.geocode import Candidate, geocode_address, geocode_place, looks_like_address
from elnino_planner.tools.http_client import HttpClient


@dataclass(frozen=True)
class Nearby:
    id: str
    name: str
    distance_km: float


@dataclass(frozen=True)
class Location:
    lat: float = field(repr=False)
    lon: float = field(repr=False)
    precision: str               # "pin", "address" or "place" (a place is a centroid, not a home)
    label: str
    in_coverage: bool
    county_fips: str | None = None
    city: str | None = None      # None inside the county means unincorporated
    city_key: str | None = None
    state_code: str | None = None
    tide_station: Nearby | None = None
    creek_gauge: Nearby | None = None   # nearest in the starting set; live status is checked in WP-2

    @property
    def rounded(self) -> tuple[float, float]:
        return round_point(self.lat, self.lon)

    @property
    def has_exact_point(self) -> bool:
        """Flood-zone lookups need a pin or an address, not a place centroid."""
        return self.precision in ("pin", "address")

    @property
    def jurisdictions(self) -> set[str]:
        """Codes `search_guidance` may return chunks for. Utilities are added in WP-4."""
        if not self.in_coverage:
            return set()
        codes = {self.county_fips, self.state_code, "US"}
        if self.city_key:
            codes.add(self.city_key)
        return codes


@dataclass
class Resolution:
    status: str                  # "ok", "ambiguous", "not_found" or "unavailable"
    location: Location | None = None
    candidates: list[Candidate] = field(default_factory=list)   # filled when ambiguous


class LocationResolver:
    def __init__(self, pack: CountyPack, counties_dir: Path = COUNTIES_DIR):
        self.pack = pack
        self.boundary = json.loads((counties_dir / pack.boundary_file).read_text())["features"][0]["geometry"]
        self.cities = json.loads((counties_dir / pack.cities_file).read_text())["features"]
        self.tide_stations = [s for s in pack.tide_stations if s.role == "primary"]

    def from_point(self, lat: float, lon: float, precision: str = "pin", label: str = "Dropped pin") -> Location:
        if not point_in_geometry(lat, lon, self.boundary):
            return Location(lat, lon, precision, label, in_coverage=False)
        city = next((c["properties"] for c in self.cities if point_in_geometry(lat, lon, c["geometry"])), None)
        return Location(
            lat, lon, precision, label, in_coverage=True,
            county_fips=self.pack.county_fips, state_code=self.pack.state_code,
            city=city["name"] if city else None, city_key=city["key"] if city else None,
            tide_station=self._nearby(lat, lon, self.tide_stations),
            creek_gauge=self._nearby(lat, lon, self.pack.creek_gauges),
        )

    async def from_text(self, client: HttpClient, text: str) -> Resolution:
        if not text.strip():
            return Resolution("not_found")
        if looks_like_address(text):
            found = await geocode_address(client, text)
        else:
            found = await geocode_place(client, text, self.pack.state_name)
        if found is None:
            return Resolution("unavailable")
        if not found:
            return Resolution("not_found")
        if len(found) > 1:
            return Resolution("ambiguous", candidates=found)
        return Resolution("ok", location=self.from_candidate(found[0]))

    def from_candidate(self, c: Candidate) -> Location:
        """Also used when the person picks one of the ambiguous candidates."""
        return self.from_point(c.lat, c.lon, precision=c.precision, label=c.label)

    @staticmethod
    def _nearby(lat: float, lon: float, items) -> Nearby | None:
        hit = nearest(lat, lon, items)
        if hit is None:
            return None
        item, km = hit
        return Nearby(item.id, item.name, round(km, 1))
