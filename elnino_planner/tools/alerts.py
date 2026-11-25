"""Contract 2.1 get_active_alerts: NWS watches, warnings and advisories at the point.

Works anywhere in the US, so the out-of-coverage response uses it too.
"""
from __future__ import annotations

from elnino_planner.config.alerts import MVP_ALERT_KEYWORDS
from elnino_planner.location.resolve import Location
from elnino_planner.tools.common import parse_utc, unavailable
from elnino_planner.tools.http_client import HttpClient
from elnino_planner.tools.result import Status, ToolResult

URL = "https://api.weather.gov/alerts/active"
SOURCE = "National Weather Service (https://www.weather.gov)"
FALLBACK = "https://www.weather.gov"
TTL_S = 120
EMPTY_CAVEAT = "No alerts are active right now. Alerts can be issued at any time."
SEVERITY_RANK = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3}

FIELDS = ("event", "severity", "urgency", "certainty", "headline", "description", "instruction",
          "onset", "expires", "sender")


def relevant_to_mvp(event: str) -> bool:
    e = event.lower()
    return any(k in e for k in MVP_ALERT_KEYWORDS)


def _alert(feature: dict) -> dict:
    p = feature.get("properties", {})
    alert = {k: p.get(k) for k in FIELDS}
    alert["sender"] = p.get("senderName") or p.get("sender")
    alert["onset"] = p.get("onset") or p.get("effective")
    alert["url"] = p.get("@id") or feature.get("id")
    alert["relevant_to_mvp"] = relevant_to_mvp(p.get("event") or "")
    return alert


def _sort_key(a: dict):
    onset = parse_utc(a["onset"]).timestamp() if a.get("onset") else float("inf")
    return SEVERITY_RANK.get(a.get("severity"), 4), onset


async def get_active_alerts(client: HttpClient, loc: Location) -> ToolResult:
    out = await client.get_json(URL, params={"point": f"{loc.lat:.4f},{loc.lon:.4f}"},
                                ttl_s=TTL_S, tool="get_active_alerts")
    if not out.ok or not isinstance(out.json, dict) or "features" not in out.json:
        return unavailable(SOURCE, FALLBACK, out)
    alerts = sorted((_alert(f) for f in out.json["features"]), key=_sort_key)
    return ToolResult(Status.OK, SOURCE, fetched_at=out.fetched_at, data={"alerts": alerts},
                      caveats=[] if alerts else [EMPTY_CAVEAT], from_cache=out.from_cache)
