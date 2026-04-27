from __future__ import annotations

from typing import Dict, List, Tuple

from .types import CatalogTool, ToolSchema


class SchemaError(RuntimeError):
    pass


def normalize_schemas(catalog: List[CatalogTool]) -> Tuple[Dict[str, ToolSchema], List[Dict[str, str]]]:
    """Normalize tool schemas into a strict internal contract.

    Tools without a valid `input_schema` are rejected deterministically.
    """
    normalized: Dict[str, ToolSchema] = {}
    rejected: List[Dict[str, str]] = []

    for row in catalog:
        schema = row.input_schema
        if not isinstance(schema, dict) or not schema:
            rejected.append(
                {
                    "server": row.server,
                    "tool": row.tool,
                    "reason": "missing_schema",
                }
            )
            continue

        properties = schema.get("properties")
        if not isinstance(properties, dict):
            rejected.append(
                {
                    "server": row.server,
                    "tool": row.tool,
                    "reason": "invalid_schema_properties",
                }
            )
            continue

        required = schema.get("required", [])
        if not isinstance(required, list):
            rejected.append(
                {
                    "server": row.server,
                    "tool": row.tool,
                    "reason": "invalid_schema_required",
                }
            )
            continue

        all_props = [str(k) for k in properties.keys()]
        req = [str(k) for k in required]
        opt = [name for name in all_props if name not in req]

        normalized[row.key] = ToolSchema(name=row.tool, server=row.server, required_args=req, optional_args=opt)

    return normalized, rejected
