"""A dummy tool that exercises the whole stack. Real tools (WP-2+) copy this shape.

Point it at any JSON endpoint. It returns the envelope; it never raises on upstream trouble.
"""
from __future__ import annotations

from elnino_planner.tools.http_client import HttpClient
from elnino_planner.tools.result import FailureReason, Status, ToolResult

DUMMY_SOURCE = "Dummy source (https://example.invalid)"
DUMMY_FALLBACK = "https://example.invalid/check-here"


async def dummy_tool(client: HttpClient, url: str, *, ttl_s: float = 0) -> ToolResult:
    out = await client.get_json(url, ttl_s=ttl_s, tool="dummy")
    if not out.ok:
        return ToolResult(Status.UNAVAILABLE, DUMMY_SOURCE, fallback_url=DUMMY_FALLBACK,
                          failure_reason=out.reason, caveats=["Dummy source unavailable."])
    if not isinstance(out.json, dict) or "value" not in out.json:
        return ToolResult(Status.UNAVAILABLE, DUMMY_SOURCE, fallback_url=DUMMY_FALLBACK,
                          failure_reason=FailureReason.BAD_RESPONSE)
    return ToolResult(Status.OK, DUMMY_SOURCE, fetched_at=out.fetched_at,
                      data={"value": out.json["value"]}, from_cache=out.from_cache)
