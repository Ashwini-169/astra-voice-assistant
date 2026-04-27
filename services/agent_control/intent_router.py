from __future__ import annotations

from typing import Dict, List

from .types import ToolSchema


def _matches_search(tool: ToolSchema) -> bool:
    name = tool.name.lower()
    return "query" in tool.required_args or "search" in name


def _matches_fetch(tool: ToolSchema) -> bool:
    name = tool.name.lower()
    return "url" in tool.required_args or "fetch" in name or "read" in name


def _matches_storage(tool: ToolSchema) -> bool:
    name = tool.name.lower()
    return "filepath" in tool.required_args or "save" in name or "append" in name or "write" in name


def _matches_time(tool: ToolSchema) -> bool:
    name = tool.name.lower()
    category = tool.category.lower()
    return "time" in name or "time" in category or "timezone" in name or "convert" in name


def _matches_file(tool: ToolSchema) -> bool:
    name = tool.name.lower()
    return "file" in name or "path" in tool.required_args


def route_intent(user_query: str, schemas: Dict[str, ToolSchema]) -> List[str]:
    """Deterministic keyword/rule based intent routing (no LLM)."""
    q = (user_query or "").strip().lower()
    if not q:
        return []

    tools = list(schemas.items())

    # Time queries - highest priority for time-related tools
    if any(token in q for token in ("time", "clock", "timezone", "zone", "hour", "minute", "date", "today", "now")):
        candidates = [key for key, schema in tools if _matches_time(schema)]
        if candidates:
            return sorted(candidates)

    # URL/fetch queries
    if "http://" in q or "https://" in q or "url" in q or "webpage" in q or "website" in q:
        candidates = [key for key, schema in tools if _matches_fetch(schema)]
        if candidates:
            return sorted(candidates)

    # File queries
    if any(token in q for token in ("file", "folder", "directory", "path")):
        candidates = [key for key, schema in tools if _matches_file(schema)]
        if candidates:
            return sorted(candidates)

    # Storage queries
    if any(token in q for token in ("note", "save", "append", "store", "write")):
        candidates = [key for key, schema in tools if _matches_storage(schema)]
        if candidates:
            return sorted(candidates)

    # Search queries - lower priority, only if no specialized tool matches
    if any(token in q for token in ("news", "latest", "headline", "search", "find", "who", "what", "tell me about")):
        candidates = [key for key, schema in tools if _matches_search(schema)]
        if candidates:
            return sorted(candidates)

    # Fallback: return all tools sorted
    return sorted([key for key, _ in tools])
