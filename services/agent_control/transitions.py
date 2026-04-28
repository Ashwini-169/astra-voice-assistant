"""State transition rules: what tool categories are allowed after each step.

Driven by capability_registry.category_chain — no hardcoded tool names.
First step allows all categories so intent_router + planner decide freely.
"""
from __future__ import annotations

from typing import Any, Dict, List

_ALL_CATEGORIES = ["search", "fetch", "time", "time_current", "time_convert", "storage", "summarize", "list", "unknown"]


def get_allowed_categories(state: Dict[str, Any]) -> List[str]:
    """Return allowed tool categories for the next step based on execution history."""
    from .capability_registry import category_chain

    if not state["steps"]:
        # First step: all categories allowed — planner + intent_router decide
        return list(_ALL_CATEGORIES)

    # Find last successfully executed step (skip error steps)
    last_category = ""
    for step in reversed(state["steps"]):
        if "error" in step:
            continue
        tool = step.get("tool", {})
        if isinstance(tool, dict):
            last_category = str(tool.get("category", "")).strip().lower()
        else:
            last_category = str(tool).strip().lower()
        if last_category:
            break

    if not last_category:
        return list(_ALL_CATEGORIES)

    # ISSUE 1 FIX: Prevent same category repetition (search → search loop)
    # Block repeating the same category unless it's fetch (for retry)
    if last_category == "search":
        # After search, ONLY allow fetch (force progression)
        return ["fetch"]
    
    chain = category_chain(last_category)
    try:
        idx = chain.index(last_category)
        remaining = chain[idx + 1:]
        # Always include storage as a valid next step (user can save at any point)
        if "storage" not in remaining:
            remaining = list(remaining) + ["storage"]
        # Allow fetch retry after a failed fetch
        if last_category == "fetch" and "fetch" not in remaining:
            remaining = ["fetch"] + list(remaining)
        return remaining if remaining else list(_ALL_CATEGORIES)
    except ValueError:
        return ["fetch", "summarize", "storage"]
