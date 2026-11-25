"""County pack schema. Everything county-specific lives in a pack, never in code or prompts."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TideStation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    role: str  # "primary" or "flood_threshold_reference"
    lat: float
    lon: float


class CreekGauge(BaseModel):
    """A NWPS gauge from the starting set. Live status is checked by get_creek_status (WP-2)."""
    model_config = ConfigDict(extra="forbid")
    id: str          # NWPS lid
    name: str
    lat: float
    lon: float


class BBox(BaseModel):
    model_config = ConfigDict(extra="forbid")
    xmin: float
    ymin: float
    xmax: float
    ymax: float


class Contact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    phone: str | None = None
    url: str | None = None
    verified: bool = False  # spec: confirm each number before launch (WP-8)
    verified_on: str | None = None  # ISO date the phone/URL was last checked by a person


class CountyPack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    county_fips: str = Field(pattern=r"^\d{5}$")
    name: str
    state_name: str                         # as geocoders spell it, e.g. for filtering place names
    state_code: str                         # jurisdiction code used in the corpus
    # GeoJSON files, relative to the counties/ directory. Built by scripts/build_county_boundaries.py.
    boundary_file: str
    cities_file: str
    tide_stations: list[TideStation]
    creek_gauge_bbox: BBox
    creek_gauges: list[CreekGauge] = []
    utilities: list[Contact] = []
    alert_system: Contact
    contacts: list[Contact]
    flood_zone_lookup_url: str
    # Link-outs for information that changes too often for the corpus (sandbag sites,
    # warming centers, outage maps). Shown in the report; never retrieved or summarized.
    resource_links: list[Contact] = []
    corpus_manifest: str
    retrieval_scopes: list[str]
    unverified: list[str] = []              # fields a human must still confirm; printed by the check script
