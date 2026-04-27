from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .identity import build_tool_key, canonicalize_server_tool, infer_category, short_tool_name


@dataclass(frozen=True)
class ToolSchema:
    name: str
    server: str
    required_args: List[str]
    optional_args: List[str]

    @property
    def key(self) -> str:
        return build_tool_key(self.server, self.name)

    @property
    def short_name(self) -> str:
        return short_tool_name(self.name)

    @property
    def category(self) -> str:
        return infer_category(self.name, [*self.required_args, *self.optional_args])


@dataclass(frozen=True)
class PlannerAction:
    tool: str
    server: str
    arguments: Dict[str, Any]

    @property
    def normalized_server(self) -> str:
        return canonicalize_server_tool(self.server, self.tool)[0]

    @property
    def normalized_tool(self) -> str:
        return canonicalize_server_tool(self.server, self.tool)[1]

    @property
    def key(self) -> str:
        return build_tool_key(self.server, self.tool)

    @property
    def short_name(self) -> str:
        return short_tool_name(self.normalized_tool)

    @property
    def category(self) -> str:
        return infer_category(self.normalized_tool, self.arguments.keys())


@dataclass(frozen=True)
class FinalAction:
    response: str


@dataclass(frozen=True)
class CatalogTool:
    server: str
    tool: str
    description: str
    input_schema: Dict[str, Any]
    health: Dict[str, float]

    @property
    def key(self) -> str:
        return build_tool_key(self.server, self.tool)

    @property
    def short_name(self) -> str:
        return short_tool_name(self.tool)

    @property
    def category(self) -> str:
        schema_props = self.input_schema.get("properties", {}) if isinstance(self.input_schema, dict) else {}
        return infer_category(self.tool, schema_props.keys())


@dataclass(frozen=True)
class ValidationResult:
    status: str
    message: Optional[str] = None
    missing: Optional[List[str]] = None
