"""Graph node: answer date/time questions from the server clock via the
get_current_time / get_current_date tools — never from the LLM's training data."""

from __future__ import annotations

import re

from app.agents.nodes.helpers import run_tool
from app.agents.state import AgentState
from app.services.stream import emit_status

_COMMON_TZ: dict[str, str] = {
    "UTC": "UTC", "GMT": "GMT",
    "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "MST": "America/Denver", "EST": "America/New_York", "EDT": "America/New_York",
    "CST": "America/Chicago", "IST": "Asia/Kolkata", "PKT": "Asia/Karachi",
    "JST": "Asia/Tokyo", "KST": "Asia/Seoul", "CET": "Europe/Berlin",
    "CEST": "Europe/Berlin", "BST": "Europe/London", "AEST": "Australia/Sydney",
    "AEDT": "Australia/Sydney", "HKT": "Asia/Hong_Kong", "SGT": "Asia/Singapore",
    "GST": "Asia/Dubai", "NZST": "Pacific/Auckland",
}
_CITY_TO_ZONE = {
    "new york": "America/New_York", "los angeles": "America/Los_Angeles",
    "chicago": "America/Chicago", "london": "Europe/London", "paris": "Europe/Paris",
    "berlin": "Europe/Berlin", "istanbul": "Europe/Istanbul", "moscow": "Europe/Moscow",
    "tokyo": "Asia/Tokyo", "karachi": "Asia/Karachi", "islamabad": "Asia/Karachi",
    "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata", "kolkata": "Asia/Kolkata",
    "sydney": "Australia/Sydney", "dubai": "Asia/Dubai", "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai", "singapore": "Asia/Singapore", "hong kong": "Asia/Hong_Kong",
    "riyadh": "Asia/Riyadh", "doha": "Asia/Qatar", "toronto": "America/Toronto",
}


def _extract_timezone(query: str) -> str:
    lowered = query.lower()
    for city, zone in _CITY_TO_ZONE.items():
        if city in lowered:
            return zone
    for token, zone in _COMMON_TZ.items():
        if token.lower() in lowered:
            return zone
    match = re.search(r"[A-Za-z]+(?:/[A-Za-z_][A-Za-z0-9_+-]*)+", query)
    if match:
        return match.group(0)
    return "UTC"


async def datetime_answer(state: AgentState) -> dict:
    query = state.get("query", "")
    tz = _extract_timezone(query)
    emit_status(f"Checking the clock ({tz})…")

    time_payload, _ = await run_tool("get_current_time", timezone=tz)
    date_payload, _ = await run_tool("get_current_date", timezone=tz)

    time_str = time_payload if isinstance(time_payload, str) else ""
    date_str = date_payload if isinstance(date_payload, str) else ""

    result = "Unknown date/time."
    if date_str or time_str:
        parts = [p for p in (f"Local date: {date_str}", f"Local time: {time_str}") if p]
        result = f"Timezone: {tz}. " + ". ".join(parts) + " (from the server clock)."
    return {"datetime_result": result, "answer": result}