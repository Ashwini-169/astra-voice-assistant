from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Tuple

from services.mcp_docker_bridge import mcp_bridge
from services.mcp_tools import (
    list_servers,
    list_tools,
)

from .types import CatalogTool


class ToolHealthStore:
    """In-memory deterministic health store for tool scoring."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: Dict[str, Dict[str, float]] = {}

    def get(self, key: str, default_availability: float) -> Dict[str, float]:
        with self._lock:
            row = self._state.get(key)
            if row is None:
                row = {
                    "availability": float(default_availability),
                    "latency": 0.0,
                    "failure_rate": 0.0,
                    "calls": 0.0,
                    "failures": 0.0,
                }
                self._state[key] = row
            return {
                "availability": float(row["availability"]),
                "latency": float(row["latency"]),
                "failure_rate": float(row["failure_rate"]),
            }

    def record(self, key: str, ok: bool, latency_ms: float) -> None:
        with self._lock:
            row = self._state.setdefault(
                key,
                {"availability": 1.0, "latency": 0.0, "failure_rate": 0.0, "calls": 0.0, "failures": 0.0},
            )
            row["calls"] += 1.0
            if not ok:
                row["failures"] += 1.0
            calls = max(1.0, row["calls"])
            row["failure_rate"] = row["failures"] / calls
            if row["latency"] == 0.0:
                row["latency"] = float(latency_ms)
            else:
                row["latency"] = (row["latency"] * 0.7) + (float(latency_ms) * 0.3)
            row["availability"] = max(0.0, min(1.0, 1.0 - row["failure_rate"]))


def _normalize_schema(schema: Any) -> Dict[str, Any]:
    if isinstance(schema, dict):
        return dict(schema)
    return {}


def _schema_from_params(required: List[str], optional: List[str]) -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    for arg in required + optional:
        props[arg] = {"type": "string"}
    return {
        "type": "object",
        "properties": props,
        "required": required,
    }


def _infer_builtin_schema(server: str, tool_name: str) -> Dict[str, Any]:
    # Explicitly derived from existing builtin tool interfaces in services/mcp_tools.py.
    if server == "browser-search" and tool_name == "search_web":
        return _schema_from_params(required=["query"], optional=["limit"])
    if server == "browser-search" and tool_name == "read_page":
        return _schema_from_params(required=["url"], optional=[])
    if server == "file-search" and tool_name == "search_files":
        return _schema_from_params(required=["query"], optional=["limit", "path"])
    if server == "music-control":
        if tool_name == "set_volume":
            return _schema_from_params(required=["value"], optional=[])
        return _schema_from_params(required=[], optional=[])
    return {}


async def load_catalog(health_store: ToolHealthStore) -> Tuple[List[CatalogTool], List[Dict[str, Any]]]:
    """Load dynamic MCP tool catalog from current MCP registries."""
    discovered: List[CatalogTool] = []
    unavailable: List[Dict[str, Any]] = []

    servers = list_servers()

    for group in ("builtin", "custom"):
        for server_cfg in servers.get(group, []):
            server = str(server_cfg.get("name", "")).strip()
            if not server:
                continue
            enabled = bool(server_cfg.get("enabled", True))
            if not enabled:
                unavailable.append(
                    {
                        "server": server,
                        "type": group,
                        "status": "disabled",
                        "reason": "server disabled",
                    }
                )
                continue

            tool_payload = list_tools(server)
            for tool_name in tool_payload.get("tools", []):
                inferred_schema = _infer_builtin_schema(server, str(tool_name)) if group == "builtin" else {}
                key = CatalogTool(
                    server=server,
                    tool=str(tool_name),
                    description=str(server_cfg.get("description", "")),
                    input_schema=inferred_schema,
                    health={},
                ).key
                health = health_store.get(key, 1.0)
                discovered.append(
                    CatalogTool(
                        server=server,
                        tool=str(tool_name),
                        description=str(server_cfg.get("description", "")),
                        input_schema=inferred_schema,
                        health=health,
                    )
                )

    for docker_server in mcp_bridge.list_servers():
        name = str(docker_server.get("name", "")).strip()
        status = str(docker_server.get("status", "stopped"))
        if not name:
            continue
        if status != "running":
            unavailable.append(
                {
                    "server": name,
                    "type": "docker",
                    "status": status,
                    "reason": "docker server not running",
                }
            )

    for row in mcp_bridge.list_all_tools():
        server = str(row.get("server", "")).strip()
        tool = str(row.get("tool", "")).strip()
        if not server or not tool:
            continue
        schema = _normalize_schema(row.get("schema", {}))
        key = CatalogTool(
            server=server,
            tool=tool,
            description=str(row.get("description", "")),
            input_schema=schema,
            health={},
        ).key
        health = health_store.get(key, 1.0)
        discovered.append(
            CatalogTool(
                server=server,
                tool=tool,
                description=str(row.get("description", "")),
                input_schema=schema,
                health=health,
            )
        )

    return discovered, unavailable


def score_tool(row: CatalogTool) -> float:
    """Higher score means better candidate for selection.

    Combines runtime health metrics with configured capability priority
    so policy-preferred tools rank above equally-healthy alternatives.
    """
    from .capability_registry import priority_for_tool
    availability = float(row.health.get("availability", 1.0))
    latency = float(row.health.get("latency", 0.0))
    failure_rate = float(row.health.get("failure_rate", 0.0))
    latency_penalty = min(1.0, latency / 5000.0)
    health_score = (availability * 1.0) - (failure_rate * 0.7) - (latency_penalty * 0.3)
    policy_priority = priority_for_tool(row.server, row.category)
    # Blend: 60% health, 40% policy priority
    return (health_score * 0.6) + (policy_priority * 0.4)
