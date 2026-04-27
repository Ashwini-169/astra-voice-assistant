from __future__ import annotations

from typing import Any, Dict, List


def get_allowed_categories(state: Dict[str, Any]) -> List[str]:
    if not state["steps"]:
        return ["search"]
    last_tool = state["steps"][-1].get("tool", {})
    if isinstance(last_tool, dict):
        last_category = str(last_tool.get("category", "")).strip().lower()
    else:
        last_category = str(last_tool).strip().lower()
    if last_category == "search":
        return ["fetch", "summarize"]
    if last_category == "fetch":
        return ["summarize"]
    return []
