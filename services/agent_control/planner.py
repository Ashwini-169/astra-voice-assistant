from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .types import FinalAction, PlannerAction, ToolSchema


class _ToolSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(default="tool")
    tool: str
    server: str | None = None
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


def build_planner_prompt(
    *,
    user_query: str,
    candidates: List[str],
    schema_map: Dict[str, ToolSchema],
    trace: List[Dict[str, Any]],
    correction_error: Optional[str] = None,
    catalog: Optional[List] = None,
) -> str:
    lines: List[str] = []
    lines.append("You are a deterministic tool planner with full awareness of all available tools.")
    lines.append("Return ONLY one JSON object.")
    lines.append("If enough info exists in trace, return: {\"action\":\"final\",\"response\":\"...\"}")
    lines.append("Otherwise return: {\"action\":\"tool\",\"tool\":\"...\",\"arguments\":{...}}")
    lines.append("The system resolves the server. Prefer the candidate tool name exactly as listed.")
    lines.append("Do not return fields outside this schema.")
    lines.append("")
    lines.append("TOOL SELECTION STRATEGY:")
    lines.append("1. Prefer specialized tools over generic tools (e.g., time tools over search for time queries)")
    lines.append("2. Match tool category to query intent (time -> time tools, content -> fetch tools, knowledge -> search tools)")
    lines.append("3. Avoid web scraping when direct API tools exist")
    lines.append("4. Use search tools only when no specialized tool matches the query")
    lines.append("5. If trace shows successful execution with complete information, return final response")
    lines.append("")
    lines.append("AVAILABLE TOOLS (dynamically loaded from MCP registries):")
    
    catalog_map = {}
    if catalog:
        catalog_map = {row.key: row for row in catalog}
    
    for key in candidates:
        schema = schema_map[key]
        lines.append(f"\nTool: {schema.name}")
        lines.append(f"  Server: {schema.server}")
        lines.append(f"  Category: {schema.category}")
        
        if key in catalog_map:
            cat_tool = catalog_map[key]
            if cat_tool.description:
                lines.append(f"  Best used for: {cat_tool.description}")
            
            if cat_tool.input_schema and isinstance(cat_tool.input_schema, dict):
                props = cat_tool.input_schema.get("properties", {})
                if props:
                    lines.append("  Arguments:")
                    for arg_name, arg_spec in props.items():
                        is_required = arg_name in schema.required_args
                        req_label = "required" if is_required else "optional"
                        arg_type = arg_spec.get("type", "any")
                        arg_desc = arg_spec.get("description", "")
                        if arg_desc:
                            lines.append(f"    - {arg_name} ({arg_type}, {req_label}): {arg_desc}")
                        else:
                            lines.append(f"    - {arg_name} ({arg_type}, {req_label})")
        else:
            lines.append(f"  Required arguments: {schema.required_args}")
            lines.append(f"  Optional arguments: {schema.optional_args}")
    lines.append("")
    if correction_error:
        lines.append(f"PREVIOUS_ERROR: {correction_error}")
        lines.append("Fix only the invalid tool/arguments and return valid JSON.")
        lines.append("")
    lines.append(f"USER_QUERY: {user_query}")
    lines.append(f"TRACE: {json.dumps(trace)}")
    lines.append("")
    lines.append("DECISION PROCESS:")
    lines.append("1. Identify the primary intent from USER_QUERY (time, content fetch, search, etc.)")
    lines.append("2. Match intent to tool category - select the most specialized tool available")
    lines.append("3. Extract required arguments from USER_QUERY")
    lines.append("4. Verify all required arguments can be provided")
    lines.append("5. Return tool selection with complete arguments")
    lines.append("")
    lines.append("EXAMPLES:")
    lines.append("- Query about current time -> use time category tools (get_current_time, convert_time)")
    lines.append("- Query about webpage content -> use fetch category tools (read_page, fetch)")
    lines.append("- Query about general knowledge -> use search category tools (search_web)")
    lines.append("- Query about file content -> use file category tools (search_files, read_file)")
    return "\n".join(lines)


def parse_planner_output(raw: str) -> Tuple[Optional[PlannerAction], Optional[FinalAction], Optional[str]]:
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

    try:
        tool_obj = _ToolSuggestion(**obj)
    except ValidationError as exc:
        return None, None, f"invalid_tool_schema:{exc.errors()}"

    if tool_obj.action.lower() != "tool":
        return None, None, "unsupported_action"

    return (
        PlannerAction(
            tool=tool_obj.tool.strip(),
            server=(tool_obj.server or "").strip(),
            arguments=dict(tool_obj.arguments or {}),
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
) -> Tuple[Optional[PlannerAction], Optional[FinalAction], Optional[str]]:
    prompt = build_planner_prompt(
        user_query=user_query,
        candidates=candidates,
        schema_map=schema_map,
        trace=trace,
        correction_error=correction_error,
        catalog=catalog,
    )
    raw = llm_call(prompt)
    return parse_planner_output(raw)
