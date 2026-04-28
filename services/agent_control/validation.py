from __future__ import annotations

from typing import Any, Dict, Optional, Set

from .types import PlannerAction, ToolSchema, ValidationResult


VALIDATION_VALID = "valid"
VALIDATION_INVALID_TOOL = "invalid_tool"
VALIDATION_MISSING_ARGS = "missing_args"
VALIDATION_EXTRA_ARGS = "invalid_args"


def validate_action(action: PlannerAction, schema_map: Dict[str, ToolSchema], allowed_tools: Set[str]) -> ValidationResult:
    key = action.key
    if key not in allowed_tools or key not in schema_map:
        return ValidationResult(status=VALIDATION_INVALID_TOOL, message=f"tool not allowed: {key}")

    schema = schema_map[key]
    args = action.arguments

    missing = [name for name in schema.required_args if name not in args]
    if missing:
        return ValidationResult(status=VALIDATION_MISSING_ARGS, message="missing required arguments", missing=missing)

    allowed_args = set(schema.required_args) | set(schema.optional_args)
    extras = sorted([name for name in args.keys() if name not in allowed_args])
    if extras:
        return ValidationResult(status=VALIDATION_EXTRA_ARGS, message=f"unknown arguments: {', '.join(extras)}")

    return ValidationResult(status=VALIDATION_VALID)


def build_user_prompt_for_missing_args(
    tool_name: str,
    category: str,
    missing_args: list[str],
    schema: ToolSchema,
    catalog_tool: Optional[Any] = None,
) -> str:
    """Build a user-friendly prompt asking for missing required arguments."""
    lines = []
    lines.append(f"I need more information to use the {category} tool.")
    lines.append("")
    lines.append("Missing required information:")
    
    # Get argument descriptions from catalog if available
    arg_descriptions = {}
    if catalog_tool and catalog_tool.input_schema and isinstance(catalog_tool.input_schema, dict):
        props = catalog_tool.input_schema.get("properties", {})
        for arg_name, arg_spec in props.items():
            arg_descriptions[arg_name] = arg_spec.get("description", "")
    
    for arg in missing_args:
        desc = arg_descriptions.get(arg, "")
        if desc:
            lines.append(f"  • {arg}: {desc}")
        else:
            lines.append(f"  • {arg}")
    
    lines.append("")
    lines.append("Please provide this information so I can help you.")
    return "\n".join(lines)
