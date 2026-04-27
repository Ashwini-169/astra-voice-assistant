from __future__ import annotations

import ipaddress
from typing import Dict, Iterable, Tuple
from urllib.parse import urlparse

from .types import PlannerAction


def _is_safe_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False

    host = parsed.hostname
    if not host:
        return False

    lowered = host.lower()
    if lowered in {"localhost"}:
        return False

    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        # Hostname: keep it allowed unless explicitly localhost.
        pass

    return True


def enforce_security(action: PlannerAction, allowed_servers: Iterable[str]) -> Tuple[bool, str | None]:
    allowed = {s.strip().lower() for s in allowed_servers if str(s).strip()}
    if action.server.lower() not in allowed:
        return False, f"server not allowlisted: {action.server}"

    for key, value in action.arguments.items():
        if "url" in str(key).lower():
            if not isinstance(value, str) or not _is_safe_url(value):
                return False, "unsafe url argument blocked"

    return True, None
