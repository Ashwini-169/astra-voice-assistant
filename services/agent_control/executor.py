from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Tuple

from fastapi import HTTPException

from services.llm_models import MCPToolCallRequest
from services.mcp_docker_bridge import mcp_bridge
from services.mcp_tools import call_tool, is_tool_allowed

from .types import PlannerAction


def _classify_error(message: str, status_code: int) -> str:
    msg = (message or "").lower()
    if status_code in {401, 403}:
        return "auth"
    if status_code in {408, 504}:
        return "timeout"
    if status_code in {400, 422}:
        return "validation"
    if status_code in {404}:
        return "validation"
    if any(token in msg for token in ("timed out", "timeout")):
        return "timeout"
    if any(token in msg for token in ("connection", "refused", "unreachable")):
        return "connection"
    if status_code >= 500:
        return "server"
    return "server"


async def execute_action(action: PlannerAction) -> Tuple[Dict[str, Any], float]:
    started = time.perf_counter()

    def _run_sync() -> Dict[str, Any]:
        if not is_tool_allowed(action.server, action.tool):
            return {
                "status": "error",
                "type": "validation",
                "message": f"tool disabled: {action.server}.{action.tool}",
                "ok": False,
                "status_code": 400,
                "error_type": "validation",
                "error": f"tool disabled: {action.server}.{action.tool}",
                "result": None,
            }

        docker_server = mcp_bridge.get_server(action.server)
        if docker_server is not None:
            if docker_server.status != "running":
                return {
                    "status": "error",
                    "type": "connection",
                    "message": f"docker server unavailable: {action.server}",
                    "ok": False,
                    "status_code": 503,
                    "error_type": "connection",
                    "error": f"docker server unavailable: {action.server}",
                    "result": None,
                }
            out = mcp_bridge.call_tool(action.server, action.tool, action.arguments)
        else:
            req = MCPToolCallRequest(server=action.server, tool=action.tool, arguments=action.arguments)
            out = call_tool(req)

        if isinstance(out, dict) and out.get("ok") is False:
            status_code = int(out.get("status_code", 502))
            message = str(out.get("error", "tool execution failed"))
            error_type = str(out.get("error_type", _classify_error(message, status_code)))
            return {
                "status": "error",
                "type": error_type,
                "message": message,
                "ok": False,
                "status_code": status_code,
                "error_type": error_type,
                "error": message,
                "result": out.get("result"),
            }

        if isinstance(out, dict) and "error" in out:
            status_code = int(out.get("status_code", 502))
            message = str(out.get("error", "tool execution failed"))
            error_type = _classify_error(message, status_code)
            return {
                "status": "error",
                "type": error_type,
                "message": message,
                "ok": False,
                "status_code": status_code,
                "error_type": error_type,
                "error": message,
                "result": out.get("result") if isinstance(out, dict) else None,
            }

        return {
            "status": "ok",
            "ok": True,
            "status_code": 200,
            "error_type": None,
            "error": None,
            "result": out,
        }

    try:
        result = await asyncio.to_thread(_run_sync)
    except HTTPException as exc:
        message = str(exc.detail)
        error_type = _classify_error(message, int(exc.status_code))
        result = {
            "status": "error",
            "type": error_type,
            "message": message,
            "ok": False,
            "status_code": int(exc.status_code),
            "error_type": error_type,
            "error": message,
            "result": None,
        }
    except Exception as exc:  # pylint: disable=broad-except
        message = str(exc)
        error_type = _classify_error(message, 500)
        result = {
            "status": "error",
            "type": error_type,
            "message": message,
            "ok": False,
            "status_code": 500,
            "error_type": error_type,
            "error": message,
            "result": None,
        }

    latency_ms = (time.perf_counter() - started) * 1000.0
    return result, latency_ms
