"""Logging that is safe by construction.

Privacy rule (spec section 4): logs hold tool names, durations and failure states only.
Never pass coordinates, addresses, needs or chat text to the logger. Use `log_event`, which
takes a fixed set of fields, rather than free-form f-strings.
"""
from __future__ import annotations

import logging

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format=_FORMAT)
    # httpx logs full URLs (including query strings, i.e. coordinates) at INFO. Never allow that.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, event: str, *, tool: str, status: str, duration_ms: float,
              reason: str | None = None, attempts: int | None = None, cached: bool | None = None,
              host: str | None = None, http_status: int | None = None) -> None:
    """One line, key=value, grep-friendly: `grep 'tool=dummy' app.log | grep status=unavailable`."""
    parts = [f"event={event}", f"tool={tool}", f"status={status}", f"duration_ms={duration_ms:.0f}"]
    for k, v in (("reason", reason), ("attempts", attempts), ("cached", cached),
                 ("host", host), ("http_status", http_status)):
        if v is not None:
            parts.append(f"{k}={v}")
    logger.info(" ".join(parts))
