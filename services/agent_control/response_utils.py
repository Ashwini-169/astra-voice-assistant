from __future__ import annotations

from typing import Any, Dict


def final_response_from_result(exec_result: Dict[str, Any]) -> str:
    payload = exec_result.get("result", "")
    if isinstance(payload, dict):
        return str(payload.get("answer") or payload.get("response") or payload)
    return str(payload)
