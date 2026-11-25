"""The result envelope every tool returns (03_tool_contracts.md, section 1)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Status(str, Enum):
    OK = "ok"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"
    OUT_OF_COVERAGE = "out_of_coverage"


class FailureReason(str, Enum):
    """Why a call became `unavailable`. Extra to the contract: for troubleshooting and tests only."""
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"    # HTTP 5xx after the retry
    CLIENT_ERROR = "client_error"        # HTTP 4xx: our request is wrong, not retried
    NETWORK_ERROR = "network_error"      # DNS, connection refused, TLS
    BAD_RESPONSE = "bad_response"        # 200 but body is not the JSON we expect


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ToolResult:
    status: Status
    source: str                                   # provider name + public URL a resident can open
    fetched_at: datetime | None = None            # time of the upstream response, not the cache read
    data: dict[str, Any] | None = None            # present only when status == OK
    caveats: list[str] = field(default_factory=list)
    fallback_url: str | None = None               # shown when status == UNAVAILABLE
    failure_reason: FailureReason | None = None   # set only when status == UNAVAILABLE
    from_cache: bool = False

    def __post_init__(self) -> None:
        if self.status != Status.OK and self.data is not None:
            raise ValueError("data must be absent unless status is ok")
        if self.status == Status.OK and self.data is None:
            raise ValueError("status ok requires data")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["failure_reason"] = self.failure_reason.value if self.failure_reason else None
        d["fetched_at"] = self.fetched_at.isoformat() if self.fetched_at else None
        return d
