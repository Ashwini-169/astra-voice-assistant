from __future__ import annotations

from typing import Any, Dict


def execution_ok(exec_result: Dict[str, Any]) -> bool:
    if exec_result.get("ok") is True:
        return True
    return str(exec_result.get("status", "")).lower() == "ok"


def execution_status(exec_result: Dict[str, Any]) -> str:
    if "status" in exec_result:
        return str(exec_result.get("status"))
    return "ok" if execution_ok(exec_result) else "error"
