"""Small helpers shared by the tools: unit pairs, time parsing, the 'unavailable' result."""
from __future__ import annotations

from datetime import datetime, timezone

from elnino_planner.tools.http_client import FetchOutcome
from elnino_planner.tools.result import FailureReason, Status, ToolResult

FT_PER_M = 3.28084


def ft_m(feet: float | None) -> dict | None:
    """Contract: US and SI side by side. {'ft': 5.4, 'm': 1.65}."""
    if feet is None:
        return None
    return {"ft": round(feet, 2), "m": round(feet / FT_PER_M, 2)}


def parse_utc(text: str) -> datetime:
    """ISO strings ('2026-11-25T11:15:00Z') and CO-OPS GMT strings ('2026-11-25 11:18')."""
    text = text.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text) if "T" in text else datetime.strptime(text, "%Y-%m-%d %H:%M")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def unavailable(source: str, fallback_url: str, out: FetchOutcome | None = None,
                reason: FailureReason | None = None) -> ToolResult:
    return ToolResult(Status.UNAVAILABLE, source, fallback_url=fallback_url,
                      failure_reason=reason or (out and out.reason) or FailureReason.BAD_RESPONSE)
