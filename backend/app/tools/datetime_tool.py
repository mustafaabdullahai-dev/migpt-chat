"""Time/date tool — always from the server clock, never the LLM's training data."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field


class TimeArgs(BaseModel):
    timezone: str = Field(
        default="UTC",
        description="IANA timezone name, e.g. 'UTC', 'Asia/Karachi', 'America/New_York'.",
    )


def _zoned(zone: str) -> ZoneInfo:
    try:
        return ZoneInfo(zone or "UTC")
    except ZoneInfoNotFoundError:
        raise ValueError(f"Unknown timezone: {zone!r}")


def current_time(timezone: str = "UTC") -> str:
    return datetime.now(_zoned(timezone)).strftime("%Y-%m-%d %H:%M:%S %Z (%z)")


def current_date(timezone: str = "UTC") -> str:
    return datetime.now(_zoned(timezone)).strftime("%Y-%m-%d")


def now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")