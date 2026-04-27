from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


def now_ms() -> float:
    return time.perf_counter() * 1000.0


def emit_event(
    events: List[Dict[str, Any]],
    *,
    step: str,
    tool: str,
    status: str,
    latency: float,
) -> None:
    events.append(
        {
            "step": step,
            "tool": tool,
            "status": status,
            "latency": float(latency),
        }
    )
