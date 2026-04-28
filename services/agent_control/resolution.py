"""Tool resolution: maps planner symbolic output to canonical server.tool keys.

The planner outputs a tool name (possibly an alias like "search" or "search_web").
This module resolves it to the correct server.tool key from the live catalog,
using the capability registry priority order when multiple servers offer the same tool.
"""
from __future__ import annotations

from typing import Dict, Iterable, List

from .capability_registry import priority_for_tool
from .identity import build_tool_key, canonicalize_server_tool
from .types import CatalogTool, PlannerAction, ToolSchema


def build_tool_index(schema_map: Dict[str, ToolSchema]) -> Dict[str, List[str]]:
    """Build alias → [canonical_key, ...] index from the schema map."""
    index: Dict[str, List[str]] = {}
    for key, schema in schema_map.items():
        for alias in {schema.name, schema.short_name, key}:
            normalized_alias = str(alias).strip().lower()
            if not normalized_alias:
                continue
            index.setdefault(normalized_alias, [])
            if key not in index[normalized_alias]:
                index[normalized_alias].append(key)
    return index


def _best_key_by_priority(keys: List[str], schema_map: Dict[str, ToolSchema]) -> str:
    """Among multiple matching keys, return the one with highest capability priority."""
    def _score(key: str) -> float:
        schema = schema_map.get(key)
        if schema is None:
            return 0.0
        return priority_for_tool(schema.server, schema.category)

    return max(keys, key=_score)


def resolve_action_server(
    action: PlannerAction,
    *,
    tool_index: Dict[str, List[str]],
    catalog: Iterable[CatalogTool],
    candidate_keys: Iterable[str],
    schema_map: Dict[str, ToolSchema] | None = None,
) -> PlannerAction:
    """Resolve a planner action to a canonical (server, tool) pair.

    Resolution order:
    1. Exact key match in candidate_set
    2. Alias lookup in tool_index, filtered to candidate_set
    3. Alias lookup in tool_index, expanded to full catalog (candidate_set may be stale)
    4. Direct catalog scan by tool name
    5. Return as-is (let validation catch it)
    """
    catalog_list = list(catalog)
    candidate_set = {str(key).strip().lower() for key in candidate_keys if str(key).strip()}

    # Build full schema_map from catalog if not provided
    if schema_map is None:
        schema_map = {}

    aliases = [
        build_tool_key(action.server, action.tool),
        str(action.tool or "").strip().lower(),
        canonicalize_server_tool(action.server, action.tool)[1],
    ]

    # Pass 1: match within candidate_set
    for alias in aliases:
        matches = [key for key in tool_index.get(alias, []) if key in candidate_set]
        if matches:
            best = _best_key_by_priority(matches, schema_map)
            server, tool = best.split(".", 1)
            return PlannerAction(server=server, tool=tool, arguments=action.arguments)

    # Pass 2: match anywhere in tool_index (candidate_set may be filtered too tightly)
    for alias in aliases:
        matches = tool_index.get(alias, [])
        if matches:
            best = _best_key_by_priority(matches, schema_map)
            server, tool = best.split(".", 1)
            return PlannerAction(server=server, tool=tool, arguments=action.arguments)

    # Pass 3: scan full catalog by tool name
    normalized_tool = canonicalize_server_tool(action.server, action.tool)[1]
    catalog_matches: List[CatalogTool] = [
        row for row in catalog_list
        if row.tool.strip().lower() == normalized_tool or row.short_name == normalized_tool
    ]
    if catalog_matches:
        # Pick highest-priority server for this tool
        best_row = max(
            catalog_matches,
            key=lambda r: priority_for_tool(r.server, r.category),
        )
        return PlannerAction(server=best_row.server.lower(), tool=best_row.tool.lower(), arguments=action.arguments)

    # Pass 4: return as-is, validation will handle it
    return PlannerAction(
        server=canonicalize_server_tool(action.server, action.tool)[0],
        tool=normalized_tool,
        arguments=action.arguments,
    )
