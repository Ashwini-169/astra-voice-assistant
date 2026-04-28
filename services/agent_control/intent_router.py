"""Intent router: returns candidate tool keys for a given query.

Responsibility: return ALL tools whose category could be relevant to the query.
Category filtering (what's allowed NEXT) is handled by transitions.py.
These two systems must NOT conflict — intent_router only gates the FIRST step.
"""
from __future__ import annotations

from typing import Dict, List

from .types import ToolSchema


def _category_matches_query(category: str, query: str) -> bool:
    """Return True if a tool category is relevant to the query."""
    q = query.lower()
    
    # ISSUE 2 FIX: Intent-to-tool binding for time queries
    if category == "time_current" or category == "time_convert" or category == "time":
        # Distinguish between "current time" and "time conversion"
        has_current_intent = any(t in q for t in ("what time", "current time", "time now", "time in", "time at"))
        has_convert_intent = any(t in q for t in ("convert", "from", "to", "between"))
        
        # If asking for current time, prefer get_current_time
        # If asking for conversion, prefer convert_time
        return has_current_intent or has_convert_intent or any(t in q for t in ("time", "clock", "timezone", "zone", "hour", "minute", "date", "today", "now"))
    
    if category == "fetch":
        return "http://" in q or "https://" in q or any(t in q for t in ("url", "webpage", "website", "read", "open"))
    
    # ISSUE 4 FIX: Better storage vs list detection
    if category == "storage":
        # Storage is for WRITING (save, append, create)
        return any(t in q for t in ("save", "append", "store", "write", "create", "add to", "update")) and "obsidian" in q
    
    # ISSUE 4 FIX: Add "list" category for listing operations
    if category == "list" or category == "unknown":
        # List operations: show, list, check, view
        if any(t in q for t in ("list", "show", "check", "view", "files", "vault", "directory", "folder")):
            return True
    
    if category == "search":
        # CRITICAL FIX: Default to search for most queries
        # Only exclude if it's clearly NOT a search query
        exclude_keywords = ["list", "show files", "check files", "view files", "save", "append", "write", "create"]
        if any(kw in q for kw in exclude_keywords):
            return False
        # Include search for general queries, news, research, etc.
        return True
    
    return True  # unknown category — include it


def route_intent(user_query: str, schemas: Dict[str, ToolSchema]) -> List[str]:
    """Return candidate tool keys relevant to the query.

    Returns ALL tools whose category matches the query intent.
    Does NOT restrict to a single category — that is transitions.py's job.
    Falls back to all tools if nothing matches.
    """
    from .capability_map import filter_tools_by_domain
    
    q = (user_query or "").strip().lower()
    if not q:
        return sorted(schemas.keys())

    # CRITICAL FIX: Domain-aware filtering FIRST
    all_tools = sorted(schemas.keys())
    domain_filtered = filter_tools_by_domain(q, all_tools)
    
    # Then apply category matching within domain
    matched = [
        key for key in domain_filtered
        if _category_matches_query(schemas[key].category, q)
    ]

    # Return domain-filtered tools if any matched, otherwise all domain tools
    return matched if matched else domain_filtered
