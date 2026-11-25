"""Settings read from environment variables (and a .env file if python-dotenv is present).

Nothing here is a secret except API keys, which WP-0 does not need. `describe()` is safe to print.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # .env is optional; real environment variables always win.
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

PACKAGE_DIR = Path(__file__).resolve().parent


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not a number") from exc


@dataclass(frozen=True)
class Settings:
    county_fips: str = "06085"
    app_name: str = "StormSeasonPlanner"
    contact: str = ""              # shown in the User-Agent; from PLANNER_CONTACT
    http_timeout_s: float = 10.0   # contract: 10 seconds
    http_retries: int = 1          # contract: one retry on timeout or 5xx
    log_level: str = "INFO"
    llm_model: str = ""            # used from WP-5; named here so it is configured in one place

    @property
    def user_agent(self) -> str:
        return f"{self.app_name}/0.1 ({self.contact})" if self.contact else f"{self.app_name}/0.1"

    def problems(self) -> list[str]:
        """Things that will bite later. Empty list means fine for WP-0."""
        out = []
        if not self.contact:
            out.append("PLANNER_CONTACT is empty: NWS requires a contact in the User-Agent (set it before WP-2).")
        if not self.llm_model:
            out.append("PLANNER_LLM_MODEL is empty: not needed until WP-5.")
        return out

    def describe(self) -> dict:
        return {
            "county_fips": self.county_fips,
            "user_agent": self.user_agent,
            "http_timeout_s": self.http_timeout_s,
            "http_retries": self.http_retries,
            "log_level": self.log_level,
            "llm_model": self.llm_model or "(unset)",
        }


def load_settings() -> Settings:
    return Settings(
        county_fips=os.getenv("PLANNER_COUNTY_FIPS", "06085"),
        contact=os.getenv("PLANNER_CONTACT", ""),
        http_timeout_s=_float("PLANNER_HTTP_TIMEOUT_S", 10.0),
        http_retries=int(_float("PLANNER_HTTP_RETRIES", 1)),
        log_level=os.getenv("PLANNER_LOG_LEVEL", "INFO").upper(),
        llm_model=os.getenv("PLANNER_LLM_MODEL", ""),
    )
