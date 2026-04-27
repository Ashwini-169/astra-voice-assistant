from __future__ import annotations

from typing import Dict, Iterable, List

from .identity import build_tool_key, canonicalize_server_tool
from .types import CatalogTool, PlannerAction, ToolSchema


def build_tool_index(schema_map: Dict[str, ToolSchema]) -> Dict[str, List[str]]:
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


def resolve_action_server(
    action: PlannerAction,
    *,
    tool_index: Dict[str, List[str]],
    catalog: Iterable[CatalogTool],
    candidate_keys: Iterable[str],
) -> PlannerAction:
    candidate_set = {str(key).strip().lower() for key in candidate_keys if str(key).strip()}
    aliases = [
        build_tool_key(action.server, action.tool),
        str(action.tool or "").strip().lower(),
        canonicalize_server_tool(action.server, action.tool)[1],
    ]

    for alias in aliases:
        matches = [key for key in tool_index.get(alias, []) if key in candidate_set]
        if matches:
            resolved_key = matches[0]
            resolved_server, resolved_tool = resolved_key.split(".", 1)
            return PlannerAction(server=resolved_server, tool=resolved_tool, arguments=action.arguments)

    normalized_tool = canonicalize_server_tool(action.server, action.tool)[1]
    for row in catalog:
        if row.key not in candidate_set:
            continue
        if row.tool.strip().lower() == normalized_tool or row.short_name == normalized_tool:
            return PlannerAction(server=row.server.lower(), tool=row.tool.lower(), arguments=action.arguments)

    return PlannerAction(
        server=canonicalize_server_tool(action.server, action.tool)[0],
        tool=normalized_tool,
        arguments=action.arguments,
    )
