"""Capability-based planner: LLM outputs a capability + arguments, not a tool name.

The LLM selects from abstract capabilities (search, fetch, time, storage, summarize).
The resolver maps capability → best available tool via capability_registry priority.
This eliminates alias mismatch errors and decouples LLM reasoning from tool names.

Output schema:
  Tool step:  {"action": "tool", "capability": "search", "arguments": {"query": "..."}}
  Final step: {"action": "final", "response": "..."}
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .types import FinalAction, PlannerAction, ToolSchema


class _CapabilitySuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(default="tool")
    capability: str          # abstract: search / fetch / time / storage / summarize
    arguments: Dict[str, Any]


class _FinalSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    response: str


def _extract_json(raw: str) -> Dict[str, Any]:
    candidate = (raw or "").strip()
    if candidate.startswith("```"):
        first = candidate.find("{")
        last = candidate.rfind("}")
        if first >= 0 and last > first:
            candidate = candidate[first : last + 1]
    return json.loads(candidate)


def _capability_to_tool(capability: str, candidates: List[str], schema_map: Dict[str, ToolSchema]) -> Optional[PlannerAction]:
    """Resolve an abstract capability to the best available tool in candidates."""
    from .capability_map import get_tools_for_capability, validate_capability_exists
    from .capability_registry import priority_for_tool

    cap = capability.strip().lower()
    
    # CRITICAL FIX: Use hard capability map instead of fuzzy matching
    if not validate_capability_exists(cap):
        # Capability not registered - fail fast
        return None
    
    # Get tools from hard map
    mapped_tools = get_tools_for_capability(cap, candidates)
    if not mapped_tools:
        # No tools available for this capability
        return None
    
    # Pick highest-priority tool
    best = max(mapped_tools, key=lambda k: priority_for_tool(schema_map[k].server, schema_map[k].category))
    schema = schema_map[best]
    return PlannerAction(server=schema.server, tool=schema.name, arguments={})


def build_planner_prompt(
    *,
    user_query: str,
    candidates: List[str],
    schema_map: Dict[str, ToolSchema],
    trace: List[Dict[str, Any]],
    correction_error: Optional[str] = None,
    catalog: Optional[List] = None,
    session_context: str = "",
) -> str:
    from .capability_map import get_all_capabilities
    
    # CRITICAL FIX: Use registered capabilities only
    registered_capabilities = get_all_capabilities()
    # Filter to only capabilities that have available tools
    available_capabilities = []
    for cap in registered_capabilities:
        from .capability_map import get_tools_for_capability
        if get_tools_for_capability(cap, candidates):
            available_capabilities.append(cap)
    
    if not available_capabilities:
        # No capabilities available - should not happen
        available_capabilities = ["unknown"]

    lines: List[str] = []
    lines.append("You are a deterministic capability planner.")
    lines.append("Return ONLY one JSON object — no explanation, no markdown.")
    lines.append("")
    lines.append("CRITICAL CONSTRAINT: You MUST choose from AVAILABLE CAPABILITIES listed below.")
    lines.append("DO NOT output any capability not in the list. DO NOT invent new capabilities.")
    lines.append("")
    lines.append("If enough info exists in trace to answer, return:")
    lines.append('  {"action": "final", "response": "..."}')
    lines.append("Otherwise return:")
    lines.append('  {"action": "tool", "capability": "<one of the capabilities below>", "arguments": {...}}')
    lines.append("")

    if session_context:
        lines.append("SESSION CONTEXT (use this to fill arguments):")
        lines.append(session_context)
        lines.append("")

    lines.append(f"AVAILABLE CAPABILITIES (ONLY THESE): {available_capabilities}")
    lines.append("")
    lines.append("CAPABILITY DESCRIPTIONS:")

    catalog_map = {row.key: row for row in catalog} if catalog else {}

    for cap in available_capabilities:
        cap_tools = [k for k in candidates if schema_map[k].category == cap]
        if not cap_tools:
            continue
        # Show arguments from the best tool in this capability
        best_key = cap_tools[0]
        schema = schema_map[best_key]
        cat_tool = catalog_map.get(best_key)
        desc = cat_tool.description if cat_tool and cat_tool.description else ""
        lines.append(f"\n  capability: {cap}")
        if desc:
            lines.append(f"  best used for: {desc}")
        lines.append(f"  required arguments: {schema.required_args}")
        lines.append(f"  optional arguments: {schema.optional_args}")
        if cat_tool and cat_tool.input_schema and isinstance(cat_tool.input_schema, dict):
            props = cat_tool.input_schema.get("properties", {})
            for arg_name, arg_spec in props.items():
                arg_type = arg_spec.get("type", "any")
                arg_desc = arg_spec.get("description", "")
                req = "required" if arg_name in schema.required_args else "optional"
                if arg_desc:
                    lines.append(f"    - {arg_name} ({arg_type}, {req}): {arg_desc}")
                else:
                    lines.append(f"    - {arg_name} ({arg_type}, {req})")

    lines.append("")
    lines.append("WORKFLOW RULES (STRICT):")
    lines.append("- search: returns URLs only. NEVER return final after search alone.")
    lines.append("- After search: use SESSION CONTEXT search results to pick a URL, then call fetch.")
    lines.append("- After fetch: you have article content. Summarize and return final.")
    lines.append("- time: returns direct answer. Return final immediately after.")
    lines.append("- storage: saves data. Use after fetch or summarize. Return final after.")
    lines.append("- Only return final when trace contains fetched content, time result, or storage confirmation.")
    lines.append("- NEVER use search capability with url argument — use fetch instead.")
    lines.append("")
    lines.append("DECISION EXAMPLES:")
    lines.append('  "tell me about Iran war" → step1: {"action":"tool","capability":"search","arguments":{"query":"Iran America war latest"}}')
    lines.append('  (after search) → step2: {"action":"tool","capability":"fetch","arguments":{"url":"<URL from SESSION CONTEXT search results>"}}')
    lines.append('  (after fetch) → step3: {"action":"final","response":"<summary of fetched content>"}')
    lines.append('  "time in Tokyo" → {"action":"tool","capability":"time","arguments":{"timezone":"Asia/Tokyo"}}')
    lines.append('  "save to obsidian" → {"action":"tool","capability":"storage","arguments":{"filepath":"Report.md","content":"..."}}')
    lines.append("")

    if correction_error:
        lines.append(f"PREVIOUS_ERROR: {correction_error}")
        lines.append("Fix the error by:")
        lines.append("1. Choosing a capability from AVAILABLE CAPABILITIES list")
        lines.append("2. Using correct arguments for that capability")
        lines.append("3. Following WORKFLOW RULES")
        lines.append("Return valid JSON only.")
        lines.append("")

    lines.append(f"USER_QUERY: {user_query}")
    lines.append(f"TRACE: {json.dumps(trace, default=str)}")
    lines.append("")
    lines.append("REMINDER: Output ONLY a capability from this list: " + str(available_capabilities))
    return "\n".join(lines)


def parse_planner_output(
    raw: str,
    candidates: List[str],
    schema_map: Dict[str, ToolSchema],
) -> Tuple[Optional[PlannerAction], Optional[FinalAction], Optional[str]]:
    try:
        obj = _extract_json(raw)
    except Exception as exc:  # pylint: disable=broad-except
        return None, None, f"invalid_json:{exc}"

    action = str(obj.get("action", "tool")).strip().lower()
    if action == "final":
        try:
            final = _FinalSuggestion(**obj)
        except ValidationError as exc:
            return None, None, f"invalid_final_schema:{exc.errors()}"
        return None, FinalAction(response=final.response), None

    # Accept both old "tool" field and new "capability" field for backward compat
    capability = str(obj.get("capability") or obj.get("tool") or "").strip().lower()
    arguments = obj.get("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}

    if not capability:
        return None, None, "missing_capability_field"

    # Resolve capability → concrete PlannerAction
    resolved = _capability_to_tool(capability, candidates, schema_map)
    if resolved is None:
        return None, None, f"no_tool_for_capability:{capability}"

    return (
        PlannerAction(
            tool=resolved.tool,
            server=resolved.server,
            arguments=arguments,
        ),
        None,
        None,
    )


def suggest_action(
    *,
    llm_call: Callable[[str], str],
    user_query: str,
    candidates: List[str],
    schema_map: Dict[str, ToolSchema],
    trace: List[Dict[str, Any]],
    correction_error: Optional[str] = None,
    catalog: Optional[List] = None,
    session_context: str = "",
) -> Tuple[Optional[PlannerAction], Optional[FinalAction], Optional[str]]:
    prompt = build_planner_prompt(
        user_query=user_query,
        candidates=candidates,
        schema_map=schema_map,
        trace=trace,
        correction_error=correction_error,
        catalog=catalog,
        session_context=session_context,
    )
    raw = llm_call(prompt)
    return parse_planner_output(raw, candidates, schema_map)
