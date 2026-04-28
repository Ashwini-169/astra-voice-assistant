"""URL and timezone utilities for agent argument normalization."""
from __future__ import annotations

import re
from typing import Optional

_URL_RE = re.compile(r'https?://[^\s\'"<>]+')

_CITY_TZ: dict[str, str] = {
    "london": "Europe/London",
    "uk": "Europe/London",
    "england": "Europe/London",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "rome": "Europe/Rome",
    "madrid": "Europe/Madrid",
    "moscow": "Europe/Moscow",
    "dubai": "Asia/Dubai",
    "india": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "kolkata": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata",
    "chennai": "Asia/Kolkata",
    "pakistan": "Asia/Karachi",
    "karachi": "Asia/Karachi",
    "islamabad": "Asia/Karachi",
    "dhaka": "Asia/Dhaka",
    "bangladesh": "Asia/Dhaka",
    "colombo": "Asia/Colombo",
    "sri lanka": "Asia/Colombo",
    "kathmandu": "Asia/Kathmandu",
    "nepal": "Asia/Kathmandu",
    "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "china": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong",
    "tokyo": "Asia/Tokyo",
    "japan": "Asia/Tokyo",
    "seoul": "Asia/Seoul",
    "korea": "Asia/Seoul",
    "singapore": "Asia/Singapore",
    "bangkok": "Asia/Bangkok",
    "thailand": "Asia/Bangkok",
    "jakarta": "Asia/Jakarta",
    "indonesia": "Asia/Jakarta",
    "sydney": "Australia/Sydney",
    "australia": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "auckland": "Pacific/Auckland",
    "new zealand": "Pacific/Auckland",
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "boston": "America/New_York",
    "washington": "America/New_York",
    "miami": "America/New_York",
    "chicago": "America/Chicago",
    "dallas": "America/Chicago",
    "houston": "America/Chicago",
    "denver": "America/Denver",
    "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "toronto": "America/Toronto",
    "canada": "America/Toronto",
    "vancouver": "America/Vancouver",
    "mexico": "America/Mexico_City",
    "mexico city": "America/Mexico_City",
    "sao paulo": "America/Sao_Paulo",
    "brazil": "America/Sao_Paulo",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "argentina": "America/Argentina/Buenos_Aires",
    "cairo": "Africa/Cairo",
    "egypt": "Africa/Cairo",
    "nairobi": "Africa/Nairobi",
    "kenya": "Africa/Nairobi",
    "johannesburg": "Africa/Johannesburg",
    "south africa": "Africa/Johannesburg",
    "lagos": "Africa/Lagos",
    "nigeria": "Africa/Lagos",
    "utc": "UTC",
    "gmt": "UTC",
}


def map_city_to_timezone(text: str) -> str:
    """Map a city/country name in free text to an IANA timezone string.

    Returns 'UTC' if no match found.
    """
    lowered = text.lower()
    # Longest match wins (avoids 'la' matching 'los angeles' substring)
    best_key = max(
        (_k for _k in _CITY_TZ if _k in lowered),
        key=len,
        default=None,
    )
    return _CITY_TZ[best_key] if best_key else "UTC"


def extract_url_from_query(text: str) -> Optional[str]:
    """Return the first HTTP/HTTPS URL found in text, or None."""
    m = _URL_RE.search(text)
    return m.group(0).rstrip(".,;)") if m else None
