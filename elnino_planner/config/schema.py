"""County pack schema. Everything county-specific lives in a pack, never in code or prompts."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TideStation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    role: str  # "primary" or "flood_threshold_reference"


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


class CountyPack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    county_fips: str = Field(pattern=r"^\d{5}$")
    name: str
    boundary_file: str | None = None       # GeoJSON; to obtain in WP-1
    cities: list[str] = []                  # boundaries to obtain in WP-1
    tide_stations: list[TideStation]
    creek_gauge_bbox: BBox
    creek_gauge_allowlist: list[str] = []
    utilities: list[Contact] = []
    alert_system: Contact
    contacts: list[Contact]
    flood_zone_lookup_url: str
    corpus_manifest: str
    retrieval_scopes: list[str]
    unverified: list[str] = []              # fields a human must still confirm; printed by the check script
