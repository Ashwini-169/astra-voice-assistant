"""Hard capability-to-tool mapping registry.

This is the SINGLE SOURCE OF TRUTH for capability → tool binding.
No LLM guessing, no fuzzy matching, no fallbacks.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# HARD CAPABILITY MAP: capability → list of tool keys (in priority order)
CAPABILITY_MAP: Dict[str, List[str]] = {
    # Search capabilities
    "search": [
        "browser-search.search_web",
        "file-search.search_files",
    ],
    "search_web": [
        "browser-search.search_web",
    ],
    "search_obsidian": [
        "obsidian.obsidian_simple_search",
        "obsidian.obsidian_complex_search",
    ],
    
    # Fetch capabilities
    "fetch": [
        "browser-search.read_page",
        "fetch.fetch",
    ],
    
    # Time capabilities
    "time_current": [
        "time.get_current_time",
    ],
    "time_convert": [
        "time.convert_time",
    ],
    "time": [  # Generic fallback
        "time.get_current_time",
        "time.convert_time",
    ],
    
    # List capabilities
    "list": [
        "obsidian.obsidian_list_files_in_vault",
        "obsidian.obsidian_list_files_in_dir",
    ],
    
    # Storage capabilities
    "storage": [
        "obsidian.obsidian_append_content",
        "obsidian.obsidian_patch_content",
        "obsidian.obsidian_delete_file",
    ],
    
    # Fetch file contents (READ operation)
    "fetch_file": [
        "obsidian.obsidian_get_file_contents",
    ],
}

# Domain-specific tool prefixes
DOMAIN_PREFIXES: Dict[str, List[str]] = {
    "obsidian": ["obsidian."],
    "web": ["browser-search.", "fetch."],
    "files": ["file-search."],
    "time": ["time."],
    "music": ["music-control."],
}


def get_tools_for_capability(capability: str, available_tools: List[str]) -> List[str]:
    """Get available tools for a capability, in priority order.
    
    Returns empty list if capability not found or no tools available.
    NO FALLBACKS - fail fast if capability not mapped.
    """
    mapped_tools = CAPABILITY_MAP.get(capability, [])
    if not mapped_tools:
        return []
    
    # Return only tools that are actually available
    return [tool for tool in mapped_tools if tool in available_tools]


def filter_tools_by_domain(query: str, available_tools: List[str]) -> List[str]:
    """Filter tools by domain detected in query.
    
    If domain detected (e.g., 'obsidian'), restrict to only those tools.
    Otherwise return all tools (NO FILTERING).
    """
    query_lower = query.lower()
    
    # CRITICAL FIX: Only filter if domain is EXPLICITLY mentioned
    # Don't filter for general queries
    for domain, prefixes in DOMAIN_PREFIXES.items():
        # Require explicit domain mention
        if domain in query_lower:
            # Strict domain filtering
            filtered = [
                tool for tool in available_tools
                if any(tool.startswith(prefix) for prefix in prefixes)
            ]
            if filtered:
                return filtered
    
    # NO DOMAIN DETECTED: Return ALL tools (don't filter)
    return available_tools


def validate_capability_exists(capability: str) -> bool:
    """Check if capability is registered in the map."""
    return capability in CAPABILITY_MAP


def get_all_capabilities() -> List[str]:
    """Get list of all registered capabilities."""
    return sorted(CAPABILITY_MAP.keys())
