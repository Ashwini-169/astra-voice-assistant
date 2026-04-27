from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Set

from .catalog import ToolHealthStore, load_catalog, score_tool
from .execution_result import execution_ok, execution_status
from .executor import execute_action
from .intent_router import route_intent
from .observability import emit_event, now_ms
from .planner import suggest_action
from .response_utils import final_response_from_result
from .resolution import build_tool_index, resolve_action_server
from .schema import normalize_schemas
from .security import enforce_security
from .transitions import get_allowed_categories
from .types import PlannerAction
from .validation import (
    VALIDATION_MISSING_ARGS,
    VALIDATION_VALID,
    validate_action,
)

MAX_STEPS = 4
MAX_CORRECTION_RETRIES = 2

_HEALTH = ToolHealthStore()
logger = logging.getLogger(__name__)


def _compact(value: Any, max_chars: int = 1200) -> Any:
    if isinstance(value, dict):
        return {str(k): _compact(v, max_chars=max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_compact(item, max_chars=max_chars) for item in value[:20]]
    text = str(value)
    if len(text) > max_chars:
        return f"{text[:max_chars]}...<truncated>"
    return value


def _debug(stage: str, **payload: Any) -> None:
    logger.info(
        "agent_debug %s %s",
        stage,
        json.dumps(_compact(payload), sort_keys=True, default=str),
    )


def should_stop(state: Dict[str, Any]) -> bool:
    if not state["steps"]:
        return False
    last_result = state["results"][-1]
    if isinstance(last_result, dict) and last_result.get("ok"):
        return True
    return False


def _allowlist(discovered_servers: Set[str]) -> Set[str]:
    raw = os.getenv("AGENT_ALLOWED_MCP_SERVERS", "").strip()
    if not raw:
        return set(discovered_servers)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _fallback_response(reason: str) -> Dict[str, Any]:
    response = "No eligible tools are available right now."
    _debug("fallback", reason=reason)
    return {
        "success": True,
        "error": None,
        "result": {"response": response, "steps": [{"error": reason}]},
        "status": "ok",
        "response": response,
        "steps": [{"error": reason}],
    }


async def run_phase2_agent_loop(
    *,
    user_query: str,
    max_steps: int,
    llm_call: Callable[[str], str],
    execute_fn: Optional[Callable[[PlannerAction], Any]] = None,
) -> Dict[str, Any]:
    bounded_steps = min(max(int(max_steps), 1), MAX_STEPS)
    _debug("start", query=user_query, requested_steps=max_steps, bounded_steps=bounded_steps)

    catalog, unavailable = await load_catalog(_HEALTH)
    _debug(
        "catalog_loaded",
        discovered_count=len(catalog),
        unavailable=unavailable,
        tools=[
            {"key": row.key, "category": row.category, "schema": bool(row.input_schema)}
            for row in catalog
        ],
    )
    schema_map, rejected = normalize_schemas(catalog)
    _debug(
        "schemas_normalized",
        schema_count=len(schema_map),
        rejected=rejected,
        schemas=[
            {
                "key": key,
                "category": schema.category,
                "required": schema.required_args,
                "optional": schema.optional_args,
            }
            for key, schema in schema_map.items()
        ],
    )
    if not schema_map:
        return _fallback_response("no_schema_valid_tools")
    tool_index = build_tool_index(schema_map)
    _debug("tool_index_built", aliases={alias: keys for alias, keys in tool_index.items()})

    discovered_servers = {schema.server.lower() for schema in schema_map.values()}
    allowed_servers = _allowlist(discovered_servers)
    _debug("allowlist", discovered_servers=sorted(discovered_servers), allowed_servers=sorted(allowed_servers))

    candidates = route_intent(user_query, schema_map)
    _debug("intent_routed", raw_candidates=candidates)
    candidates = [c for c in candidates if c in schema_map]
    if not candidates:
        return _fallback_response("no_intent_candidates")

    candidates = sorted(
        candidates,
        key=lambda key: score_tool(next(row for row in catalog if row.key == key)),
        reverse=True,
    )
    _debug(
        "candidates_scored",
        candidates=[
            {"key": key, "category": schema_map[key].category}
            for key in candidates
        ],
    )

    state: Dict[str, Any] = {
        "query": user_query,
        "steps": [],
        "results": [],
        "used_actions": set(),
        "final_answer": None,
    }
    events: List[Dict[str, Any]] = []
    final_response = ""

    for step in range(1, bounded_steps + 1):
        correction_error: Optional[str] = None
        action: Optional[PlannerAction] = None

        if state["steps"]:
            allowed_categories = get_allowed_categories(state)
            filtered_candidates = [c for c in candidates if schema_map[c].category in allowed_categories]
        else:
            allowed_categories = sorted({schema_map[c].category for c in candidates})
            filtered_candidates = list(candidates)
        _debug(
            "step_candidates",
            step=step,
            allowed_categories=allowed_categories,
            filtered_candidates=[
                {"key": key, "category": schema_map[key].category}
                for key in filtered_candidates
            ],
            prior_steps=len(state["steps"]),
        )
        if not filtered_candidates:
            _debug("step_stop_no_candidates", step=step, allowed_categories=allowed_categories)
            break

        for attempt in range(MAX_CORRECTION_RETRIES + 1):
            plan_start = now_ms()
            suggested, final, parse_error = suggest_action(
                llm_call=llm_call,
                user_query=user_query,
                candidates=filtered_candidates,
                schema_map=schema_map,
                trace=state["steps"],
                correction_error=correction_error,
                catalog=catalog,
            )
            emit_event(
                events,
                step="plan",
                tool="",
                status="ok" if not parse_error else "error",
                latency=now_ms() - plan_start,
            )
            _debug(
                "planner_result",
                step=step,
                attempt=attempt,
                parse_error=parse_error,
                suggested={
                    "server": suggested.server,
                    "tool": suggested.tool,
                    "arguments": suggested.arguments,
                }
                if suggested is not None
                else None,
                final=final.response if final is not None else None,
            )

            if final is not None:
                final_response = final.response
                _debug("planner_final", step=step, attempt=attempt, response=final_response)
                break

            if parse_error or suggested is None:
                correction_error = parse_error or "planner output invalid"
                if attempt >= MAX_CORRECTION_RETRIES:
                    state["steps"].append({"step": step, "error": "planner_parse_error", "detail": correction_error})
                    _debug("planner_parse_failed", step=step, attempt=attempt, detail=correction_error)
                    continue
                # Retry with correction guidance; do not dereference a missing suggestion.
                _debug("planner_retry", step=step, attempt=attempt, correction_error=correction_error)
                continue

            action = PlannerAction(
                tool=suggested.tool.strip().lower(),
                server=suggested.server.strip().lower(),
                arguments=dict(suggested.arguments or {}),
            )
            action = resolve_action_server(
                action,
                tool_index=tool_index,
                catalog=catalog,
                candidate_keys=filtered_candidates,
            )
            _debug(
                "action_resolved",
                step=step,
                attempt=attempt,
                planner_action={
                    "server": suggested.server,
                    "tool": suggested.tool,
                    "arguments": suggested.arguments,
                },
                resolved_action={
                    "server": action.server,
                    "tool": action.tool,
                    "key": action.key,
                    "category": action.category,
                    "arguments": action.arguments,
                },
            )

            action_key = (action.server, action.tool, json.dumps(action.arguments, sort_keys=True, default=str))
            if action_key in state["used_actions"]:
                emit_event(
                    events,
                    step="dedup",
                    tool=action.key,
                    status="duplicate",
                    latency=0.0,
                )
                _debug("dedup_blocked", step=step, action_key=action_key)
                break

            validation = validate_action(action, schema_map, set(filtered_candidates))
            _debug(
                "validation_result",
                step=step,
                action=action.key,
                status=validation.status,
                message=validation.message,
                missing=validation.missing,
            )
            if validation.status != VALIDATION_VALID:
                emit_event(
                    events,
                    step="recover",
                    tool=action.key,
                    status=validation.status,
                    latency=0.0,
                )
                if validation.status == VALIDATION_MISSING_ARGS:
                    correction_error = f"missing_args:{validation.missing or []}"
                    if attempt >= MAX_CORRECTION_RETRIES:
                        state["steps"].append(
                            {
                                "step": step,
                                "error": "invalid_action_contract",
                                "detail": validation.message,
                                "missing": validation.missing,
                                "action": {
                                    "server": action.server,
                                    "tool": action.tool,
                                    "arguments": action.arguments,
                                },
                            }
                        )
                        _debug("validation_failed_final", step=step, detail=validation.message)
                        continue

                state["steps"].append(
                    {
                        "step": step,
                        "error": "invalid_action_contract",
                        "detail": validation.message,
                        "action": {
                            "server": action.server,
                            "tool": action.tool,
                            "arguments": action.arguments,
                        },
                    }
                )
                _debug("validation_failed", step=step, detail=validation.message)
                action = None
                break

            ok, sec_error = enforce_security(action, allowed_servers)
            _debug("security_result", step=step, action=action.key, ok=ok, error=sec_error)
            if not ok:
                state["steps"].append(
                    {
                        "step": step,
                        "error": "security_blocked",
                        "detail": sec_error,
                        "action": {
                            "server": action.server,
                            "tool": action.tool,
                            "arguments": action.arguments,
                        },
                    }
                )
                _debug("security_blocked", step=step, action=action.key, detail=sec_error)
                action = None
                break

            break

        if final_response:
            break

        if action is None:
            _debug("step_no_action", step=step)
            continue

        exec_start = now_ms()
        if execute_fn is None:
            exec_result, latency_ms = await execute_action(action)
        else:
            out = execute_fn(action)
            if hasattr(out, "__await__"):
                exec_result = await out
            else:
                exec_result = out
        latency_ms = now_ms() - exec_start
        key = action.key
        exec_ok = execution_ok(exec_result)
        exec_status = execution_status(exec_result)
        _HEALTH.record(key, exec_ok, latency_ms)
        _debug(
            "execution_result",
            step=step,
            action=key,
            latency_ms=latency_ms,
            ok=exec_ok,
            status=exec_status,
            status_code=exec_result.get("status_code"),
            error=exec_result.get("error"),
            result=exec_result.get("result"),
        )

        emit_event(
            events,
            step="execute",
            tool=key,
            status=exec_status,
            latency=latency_ms,
        )

        state["steps"].append(
            {
                "step": step,
                "tool": {
                    "server": action.server,
                    "name": action.tool,
                    "full": action.key,
                    "short": action.short_name,
                    "category": action.category,
                },
                "arguments": action.arguments,
                "result": exec_result,
                "latency_ms": int(latency_ms),
            }
        )
        state["results"].append(exec_result)
        state["used_actions"].add(action_key)

        if should_stop(state):
            final_response = final_response_from_result(state["results"][-1])
            _debug("final_from_tool_result", step=step, response=final_response)
            emit_event(events, step="final", tool="", status="ok", latency=0.0)
            result = {"response": final_response, "steps": state["steps"], "events": events, "unavailable": unavailable, "rejected": rejected}
            return {
                "success": True,
                "error": None,
                "result": result,
                "status": "ok",
                "response": final_response,
                "steps": state["steps"],
            }

    if not final_response:
        final_response = "I completed the deterministic tool-selection flow."
        _debug("final_default", response=final_response, steps=len(state["steps"]))

    emit_event(events, step="final", tool="", status="ok", latency=0.0)
    result = {"response": final_response, "steps": state["steps"], "events": events, "unavailable": unavailable, "rejected": rejected}
    return {
        "success": True,
        "error": None,
        "result": result,
        "status": "ok",
        "response": final_response,
        "steps": state["steps"],
    }
