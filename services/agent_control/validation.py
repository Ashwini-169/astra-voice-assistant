from __future__ import annotations

from typing import Dict, Set

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
