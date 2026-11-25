"""Contract 2.2 get_flood_zone: the FEMA mapped flood zone at the exact point.

Needs a pin or an address. A place centroid gets `no_data` plus the county's flood-zone lookup link.
"""
from __future__ import annotations

from elnino_planner.config.schema import CountyPack
from elnino_planner.location.resolve import Location
from elnino_planner.tools.common import ft_m, unavailable
from elnino_planner.tools.http_client import HttpClient
from elnino_planner.tools.result import Status, ToolResult

URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
SOURCE = "FEMA National Flood Hazard Layer (https://msc.fema.gov/portal/home)"
FALLBACK = "https://msc.fema.gov/portal/home"
SESSION_TTL_S = 3600   # in-memory only; the exact point is never written anywhere
CAVEAT = ("FEMA maps show the 1%-annual-chance flood. They do not include king tides, storm-drain "
          "backups, or changes since the map was made. Being outside a mapped zone does not mean no flood risk.")
NOT_MAPPED = "FEMA has no flood zone mapped at this point."
NEEDS_POINT = "Flood-zone status needs a map pin or a street address."

# Written once, reviewed by a person (status: DRAFT until reviewed). Never model-generated.
PLAIN_LANGUAGE = {
    "A": "High-risk flood area (1% chance of flooding each year). No base flood elevation has been set.",
    "AE": "High-risk flood area (1% chance of flooding each year), with a base flood elevation set.",
    "AH": "High-risk area for shallow ponding, usually 1 to 3 feet deep (1% chance each year).",
    "AO": "High-risk area for shallow sheet flow, usually 1 to 3 feet deep (1% chance each year).",
    "AR": "High-risk area that a flood-control system, being restored, used to protect.",
    "A99": "High-risk area that a federal flood-protection system under construction will protect.",
    "V": "High-risk coastal area with storm waves (1% chance each year).",
    "VE": "High-risk coastal area with storm waves (1% chance each year), with a base flood elevation set.",
    "X_SHADED": "Moderate-risk area: 0.2% chance of flooding each year, or protected by a levee.",
    "X": "Area of minimal mapped flood risk. Flooding can still happen here.",
    "D": "Flood risk here has not been studied. It is undetermined, not low.",
    "OPEN WATER": "Open water.",
}


def plain_language(zone: str, subtype: str | None) -> str:
    if zone == "X" and subtype and ("0.2" in subtype or "LEVEE" in subtype.upper()):
        return PLAIN_LANGUAGE["X_SHADED"]
    return PLAIN_LANGUAGE.get(zone, f"FEMA zone {zone}.")


def map_url(loc: Location) -> str:
    return f"https://msc.fema.gov/portal/search?AddressQuery={loc.lat:.5f}%2C{loc.lon:.5f}"


async def get_flood_zone(client: HttpClient, loc: Location, pack: CountyPack) -> ToolResult:
    if not loc.in_coverage:
        return ToolResult(Status.OUT_OF_COVERAGE, SOURCE)
    if not loc.has_exact_point:
        return ToolResult(Status.NO_DATA, SOURCE, caveats=[NEEDS_POINT], fallback_url=pack.flood_zone_lookup_url)

    out = await client.get_json(URL, ttl_s=SESSION_TTL_S, tool="get_flood_zone", params={
        "geometry": f"{loc.lon},{loc.lat}", "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE",
        "returnGeometry": "false", "f": "json"})
    if not out.ok or not isinstance(out.json, dict) or "features" not in out.json:
        return unavailable(SOURCE, pack.flood_zone_lookup_url, out)
    if not out.json["features"]:
        return ToolResult(Status.NO_DATA, SOURCE, fetched_at=out.fetched_at, caveats=[NOT_MAPPED, CAVEAT],
                          fallback_url=pack.flood_zone_lookup_url, from_cache=out.from_cache)

    # Overlapping polygons are rare; prefer one inside the special flood hazard area.
    attrs = sorted((f["attributes"] for f in out.json["features"]), key=lambda a: a.get("SFHA_TF") != "T")[0]
    zone, subtype = attrs.get("FLD_ZONE") or "", attrs.get("ZONE_SUBTY")
    bfe = attrs.get("STATIC_BFE")
    data = {
        "zone": zone,
        "zone_subtype": subtype,
        "in_sfha": attrs.get("SFHA_TF") == "T",
        "base_flood_elevation": ft_m(bfe) if bfe is not None and bfe > -9000 else None,   # -9999 = none
        "plain_language": plain_language(zone, subtype),
        "map_url": map_url(loc),
    }
    return ToolResult(Status.OK, SOURCE, fetched_at=out.fetched_at, data=data, caveats=[CAVEAT],
                      from_cache=out.from_cache)
