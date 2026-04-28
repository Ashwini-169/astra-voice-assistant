from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Set

from .catalog import ToolHealthStore, load_catalog, score_tool
from .agent_memory import AgentSessionMemory, load_rag_context, save_agent_result
from .execution_result import execution_ok, execution_status
from .executor import execute_action, MCP_TOOL_TIMEOUT_SEC
from .intent_router import route_intent
from .observability import emit_event, now_ms
from .planner import suggest_action
from .response_utils import final_response_from_result, summarize_fetch_result
from .resolution import build_tool_index, resolve_action_server
from .schema import normalize_schemas
from .security import enforce_security
from .transitions import get_allowed_categories
from .types import PlannerAction
from .url_utils import extract_url_from_query, map_city_to_timezone
from .validation import (
    VALIDATION_MISSING_ARGS,
    VALIDATION_VALID,
    build_user_prompt_for_missing_args,
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
    """Return True only when the last step produced a final-quality result.

    Search results are NOT final — they are URLs that need fetch_content
    to retrieve actual content before the agent can answer.
    Only fetch/summarize category results with ok=True are considered done.
    """
    if not state["steps"]:
        return False
    last_step = state["steps"][-1]
    last_tool = last_step.get("tool", {})
    if isinstance(last_tool, dict):
        last_category = str(last_tool.get("category", "")).strip().lower()
    else:
        last_category = str(last_tool).strip().lower()
    # Search results are intermediate — never stop here
    if last_category == "search":
        return False
    last_result = state["results"][-1]
    return isinstance(last_result, dict) and execution_ok(last_result)


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

    # ── Session memory: RAG retrieval at turn start ───────────────────
    session = AgentSessionMemory()
    session.rag_context = await asyncio.to_thread(load_rag_context, user_query)
    _debug("rag_loaded", rag_context=session.rag_context[:200])

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
        "last_error": None,  # Track last error for recovery
        "error_count": 0,    # Count repeated errors
    }
    events: List[Dict[str, Any]] = []
    final_response = ""

    # ── DIRECT URL DETECTION: skip search, go straight to fetch ────────
    direct_url = extract_url_from_query(user_query)
    if direct_url:
        fetch_candidates = [k for k in schema_map if schema_map[k].category == "fetch"]
        if fetch_candidates:
            best_key = sorted(fetch_candidates, key=lambda k: score_tool(next(r for r in catalog if r.key == k)), reverse=True)[0]
            _debug("direct_url_detected", url=direct_url, tool=best_key)
            bounded_steps = min(bounded_steps + 1, MAX_STEPS)  # ensure room for summarize

    for step in range(1, bounded_steps + 1):
        correction_error: Optional[str] = None
        action: Optional[PlannerAction] = None
        last_successful_category = ""
        last_step_category = ""
        for s in reversed(state["steps"]):
            if not last_step_category:
                t = s.get("tool", {})
                last_step_category = str(t.get("category", "") if isinstance(t, dict) else t).lower()
            if "error" not in s and not last_successful_category:
                t = s.get("tool", {})
                last_successful_category = str(t.get("category", "") if isinstance(t, dict) else t).lower()
            if last_step_category and last_successful_category:
                break

        # ── ERROR RECOVERY: detect repeated errors and force next capability ──
        if state["last_error"] and state["error_count"] >= 2:
            _debug(
                "error_recovery_triggered",
                step=step,
                last_error=state["last_error"],
                error_count=state["error_count"],
            )
            if last_successful_category == "search":
                _debug("force_fetch_after_repeated_errors", step=step)
                state["last_error"] = None
                state["error_count"] = 0

        if state["steps"]:
            allowed_categories = get_allowed_categories(state)
            filtered_candidates = [c for c in candidates if schema_map[c].category in allowed_categories]

            if last_successful_category == "search":
                # After search: expand to ALL fetch tools from schema_map, not just initial candidates
                fetch_candidates = [k for k in schema_map.keys() if schema_map[k].category == "fetch"]
                if fetch_candidates:
                    filtered_candidates = fetch_candidates
                    _debug("workflow_enforce_fetch", step=step, fetch_candidates=fetch_candidates)
                elif not filtered_candidates:
                    # No fetch tools available — log error and stop
                    _debug("no_fetch_tools_available", step=step, allowed_categories=allowed_categories)
                    state["steps"].append({"step": step, "error": "no_fetch_tools_available"})
                    break
            elif not filtered_candidates:
                # Fallback: if filtering left nothing, log and stop (do NOT fall back to all candidates)
                _debug("step_candidates_empty", step=step, allowed_categories=allowed_categories)
                state["steps"].append({"step": step, "error": "no_valid_tools_for_category", "allowed": allowed_categories})
                break
        else:
            allowed_categories = sorted({schema_map[c].category for c in candidates})
            # Exclude unknown-category tools from first step unless they're the only option
            non_unknown = [c for c in candidates if schema_map[c].category != "unknown"]
            filtered_candidates = non_unknown if non_unknown else list(candidates)
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

        # ── DIRECT URL: force fetch on step 1 if URL was in query ──────────
        if step == 1 and direct_url and not state["steps"]:
            fetch_candidates_now = [k for k in filtered_candidates if schema_map[k].category == "fetch"]
            if not fetch_candidates_now:
                fetch_candidates_now = [k for k in schema_map if schema_map[k].category == "fetch"]
            if fetch_candidates_now:
                best_fetch_key = fetch_candidates_now[0]
                session.selected_url = direct_url
                action = PlannerAction(
                    tool=schema_map[best_fetch_key].name,
                    server=schema_map[best_fetch_key].server,
                    arguments={"url": direct_url},
                )
                _debug("direct_url_fetch", step=step, url=direct_url, action=action.key)

        # ── FORCE FETCH: bypass planner when session has a URL ready ────────
        # Also handles fetch-failed: advance selected_url to next result and retry
        elif last_successful_category == "search" and session.best_fetch_url() and filtered_candidates:
            best_fetch_key = filtered_candidates[0]
            action = PlannerAction(
                tool=schema_map[best_fetch_key].name,
                server=schema_map[best_fetch_key].server,
                arguments={"url": session.best_fetch_url()},
            )
            _debug("force_fetch_from_memory", step=step, url=session.best_fetch_url(), action=action.key)

        elif last_step_category == "fetch" and state["results"] and not execution_ok(state["results"][-1]) and session.last_search_results:
            # Last fetch failed — try next URL from search results
            used_urls = {s.get("arguments", {}).get("url", "") for s in state["steps"] if s.get("tool", {}).get("category") == "fetch"}
            next_result = next((r for r in session.last_search_results if r["url"] not in used_urls), None)
            if next_result and filtered_candidates:
                best_fetch_key = filtered_candidates[0]
                session.selected_url = next_result["url"]
                action = PlannerAction(
                    tool=schema_map[best_fetch_key].name,
                    server=schema_map[best_fetch_key].server,
                    arguments={"url": next_result["url"]},
                )
                _debug("fetch_retry_next_url", step=step, url=next_result["url"], action=action.key)
            else:
                _debug("fetch_all_urls_exhausted", step=step)

        if action is None:
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
                    session_context=session.to_context_block(),
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
                    } if suggested is not None else None,
                    final=final.response if final is not None else None,
                )

                if final is not None:
                    final_response = final.response
                    _debug("planner_final", step=step, attempt=attempt, response=final_response)
                    break

                if parse_error or suggested is None:
                    correction_error = parse_error or "planner output invalid"
                    if correction_error == state["last_error"]:
                        state["error_count"] += 1
                    else:
                        state["last_error"] = correction_error
                        state["error_count"] = 1
                    if attempt >= MAX_CORRECTION_RETRIES:
                        # CRITICAL FIX: Fail fast with clear message
                        if "no_tool_for_capability" in correction_error:
                            capability = correction_error.split(":")[-1] if ":" in correction_error else "unknown"
                            final_response = f"I don't have a tool available for '{capability}' capability. Please try a different request."
                            _debug("capability_not_available", step=step, capability=capability)
                            break
                        state["steps"].append({"step": step, "error": "planner_parse_error", "detail": correction_error})
                        _debug("planner_parse_failed", step=step, attempt=attempt, detail=correction_error)
                    else:
                        _debug("planner_retry", step=step, attempt=attempt, correction_error=correction_error)
                    continue

                action = PlannerAction(
                    tool=suggested.tool.strip().lower(),
                    server=suggested.server.strip().lower(),
                    arguments=dict(suggested.arguments or {}),
                )
                # ── TIME NORMALIZATION: map city names to IANA timezones ────────
                if action.category == "time" and "timezone" not in action.arguments:
                    tz = map_city_to_timezone(user_query)
                    action = PlannerAction(
                        tool=action.tool,
                        server=action.server,
                        arguments={**action.arguments, "timezone": tz},
                    )
                    _debug("time_tz_normalized", step=step, timezone=tz)
                if not action.server:
                    action = resolve_action_server(
                        action,
                        tool_index=tool_index,
                        catalog=catalog,
                        candidate_keys=filtered_candidates,
                        schema_map=schema_map,
                    )
                _debug(
                    "action_resolved",
                    step=step,
                    attempt=attempt,
                    resolved_action={"server": action.server, "tool": action.tool, "key": action.key, "category": action.category},
                )

                # ── CAPABILITY VALIDATION ──
                if action.key not in filtered_candidates:
                    if attempt >= MAX_CORRECTION_RETRIES:
                        # Auto-correct: search+url → fetch
                        if last_successful_category == "search" and "url" in action.arguments:
                            fetch_keys = [k for k in filtered_candidates if schema_map[k].category == "fetch"]
                            if fetch_keys:
                                action = PlannerAction(
                                    tool=schema_map[fetch_keys[0]].name,
                                    server=schema_map[fetch_keys[0]].server,
                                    arguments=action.arguments,
                                )
                                _debug("auto_correct_fetch", step=step, corrected_action=action.key)
                            else:
                                correction_error = f"capability_not_allowed:{action.category}"
                                continue
                        else:
                            correction_error = f"capability_not_allowed:{action.category}"
                            continue
                    else:
                        correction_error = f"capability_not_allowed:{action.category} (allowed:{allowed_categories})"
                        if correction_error == state["last_error"]:
                            state["error_count"] += 1
                        else:
                            state["last_error"] = correction_error
                            state["error_count"] = 1
                        continue

                # ── ARGUMENT VALIDATION ──
                if action.category == "search" and "url" in action.arguments:
                    fetch_keys = [k for k in filtered_candidates if schema_map[k].category == "fetch"]
                    if fetch_keys and attempt >= MAX_CORRECTION_RETRIES:
                        action = PlannerAction(
                            tool=schema_map[fetch_keys[0]].name,
                            server=schema_map[fetch_keys[0]].server,
                            arguments=action.arguments,
                        )
                        _debug("auto_correct_search_to_fetch", step=step, corrected_action=action.key)
                    else:
                        correction_error = "search_cannot_accept_url_argument (use fetch capability)"
                        if correction_error == state["last_error"]:
                            state["error_count"] += 1
                        else:
                            state["last_error"] = correction_error
                            state["error_count"] = 1
                        continue

                # ── SCHEMA VALIDATION: check required arguments ──────────────
                validation_result = validate_action(action, schema_map, set(filtered_candidates))
                if validation_result.status != VALIDATION_VALID:
                    if validation_result.status == VALIDATION_MISSING_ARGS and attempt >= MAX_CORRECTION_RETRIES:
                        # Ask user for missing arguments instead of failing silently
                        catalog_tool = next((t for t in catalog if t.key == action.key), None)
                        user_prompt = build_user_prompt_for_missing_args(
                            tool_name=action.tool,
                            category=action.category,
                            missing_args=validation_result.missing or [],
                            schema=schema_map[action.key],
                            catalog_tool=catalog_tool,
                        )
                        final_response = user_prompt
                        _debug("missing_args_ask_user", step=step, missing=validation_result.missing, prompt=user_prompt)
                        break
                    else:
                        correction_error = f"{validation_result.status}:{validation_result.message}"
                        if correction_error == state["last_error"]:
                            state["error_count"] += 1
                        else:
                            state["last_error"] = correction_error
                            state["error_count"] = 1
                        continue

        if final_response:
            break

        if action is None:
            _debug("step_no_action", step=step)
            continue

        # ── ARGUMENT VALIDATION: Fix empty required arguments ────────────────
        if action.tool == "obsidian_list_files_in_dir" and not action.arguments.get("dirpath"):
            # Empty dirpath → use vault version instead
            vault_tool = "obsidian_list_files_in_vault"
            if f"obsidian.{vault_tool}" in schema_map:
                action = PlannerAction(
                    tool=vault_tool,
                    server="obsidian",
                    arguments={},
                )
                _debug("auto_correct_empty_dirpath", step=step, corrected_tool=vault_tool)

        # ── ENHANCED ERROR CONTEXT: build detailed error message ────────────
        def _build_error_message(exec_result: Dict[str, Any]) -> str:
            """Build user-friendly error message with error type and details."""
            error_type = exec_result.get("error_type", "unknown")
            error_msg = exec_result.get("error", "Tool execution failed")
            status_code = exec_result.get("status_code", 500)
            
            lines = []
            
            # User-friendly message first
            if error_type == "timeout":
                lines.append("⚠️ The request took too long to complete.")
                lines.append("")
                lines.append("This usually means:")
                lines.append("• The service is temporarily unavailable")
                lines.append("• The network connection is slow")
                lines.append("• The request is too complex")
                lines.append("")
                lines.append("Please try again in a moment.")
            elif error_type == "connection":
                lines.append("⚠️ Unable to connect to the service.")
                lines.append("")
                lines.append("This usually means:")
                lines.append("• The service is not running")
                lines.append("• Network connectivity issues")
                lines.append("• Firewall blocking the connection")
                lines.append("")
                lines.append("Please check if the service is running and try again.")
            elif error_type == "auth":
                lines.append("⚠️ Authentication failed.")
                lines.append("")
                lines.append("This usually means:")
                lines.append("• Invalid API key or credentials")
                lines.append("• Expired authentication token")
                lines.append("• Insufficient permissions")
                lines.append("")
                lines.append("Please verify your credentials and try again.")
            elif error_type == "validation":
                lines.append("⚠️ Invalid request format.")
                lines.append("")
                lines.append(f"Error: {error_msg}")
                lines.append("")
                lines.append("Please check your input and try again.")
            elif error_type == "not_found":
                lines.append("⚠️ The requested resource was not found.")
                lines.append("")
                lines.append("Please check the resource name and try again.")
            else:
                lines.append(f"⚠️ An error occurred: {error_msg}")
                lines.append("")
                lines.append("Please try again or contact support if the issue persists.")
            
            # Technical details for debugging (will be removed in production)
            lines.append("")
            lines.append("---")
            lines.append("🔧 Debug Info (for development):")
            lines.append(f"Error Type: {error_type}")
            lines.append(f"HTTP Status: {status_code}")
            lines.append(f"Tool: {action.category}")
            lines.append(f"Details: {error_msg}")
            
            return "\n".join(lines)

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
        action_key = (action.server, action.tool, json.dumps(action.arguments, sort_keys=True, default=str))
        state["used_actions"].add(action_key)

        # ── Update session memory with this step's output ────────────────
        session.update_from_step(
            tool_category=action.category,
            tool_name=action.tool,
            arguments=action.arguments,
            result=exec_result,
        )

        # ── ERROR HANDLING: return user-friendly error message on failure ────
        if not exec_ok:
            # For critical errors (auth, timeout, connection), stop and explain
            error_type = exec_result.get("error_type", "unknown")
            if error_type in {"auth", "timeout", "connection"} and step >= 2:
                final_response = _build_error_message(exec_result)
                _debug("critical_error_stop", step=step, error_type=error_type)
                break
            # For validation errors, continue to next step (may auto-correct)
            _debug("execution_failed_continue", step=step, error_type=error_type)

        if should_stop(state):
            # ── Summarize fetch content instead of returning raw ──────────
            if action.category == "fetch":
                source_url = action.arguments.get("url", "")
                summary = summarize_fetch_result(exec_result, user_query, source_url, llm_call)
                final_response = summary or final_response_from_result(exec_result)
            elif action.tool == "obsidian_list_files_in_vault" or action.tool == "obsidian_list_files_in_dir":
                # Format Obsidian file list responses
                result_data = exec_result.get("result", [])
                
                # CRITICAL FIX: Don't mask connection errors as "no files"
                if not exec_ok:
                    final_response = _build_error_message(exec_result)
                elif isinstance(result_data, str) and ("error" in result_data.lower() or "exception" in result_data.lower()):
                    # Result contains error message - treat as error
                    final_response = _build_error_message({"error_type": "connection", "error": result_data, "status_code": 503})
                elif isinstance(result_data, list) and result_data:
                    lines = []
                    lines.append("📁 Files in your Obsidian vault:")
                    lines.append("")
                    for item in result_data[:50]:
                        lines.append(f"• {item}")
                    if len(result_data) > 50:
                        lines.append("")
                        lines.append(f"... and {len(result_data) - 50} more files")
                    lines.append("")
                    lines.append("Let me know if you want to open or edit any file.")
                    final_response = "\n".join(lines)
                else:
                    final_response = "📁 No files found in the vault."
            else:
                final_response = final_response_from_result(exec_result)
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

    # ── GROUNDING ENFORCEMENT: block final without fetch for search queries ──
    if not final_response:
        # Check if this was a search-based query that requires fetched content
        search_steps = [s for s in state["steps"] if s.get("tool", {}).get("category") == "search" and "error" not in s]
        if search_steps and not session.fetched_content:
            _debug("final_blocked_no_fetch", search_steps=len(search_steps), fetched_content=bool(session.fetched_content))
            final_response = "I was unable to retrieve content from the sources. Please try again or rephrase your query."
        elif state["results"]:
            # If last successful step was fetch, summarize its content directly
            fetch_steps = [s for s in state["steps"] if s.get("tool", {}).get("category") == "fetch" and "error" not in s]
            if fetch_steps:
                last_fetch = fetch_steps[-1]
                source_url = last_fetch.get("arguments", {}).get("url", "")
                fetch_result = last_fetch.get("result", {})
                summary = summarize_fetch_result(fetch_result, user_query, source_url, llm_call)
                if summary:
                    final_response = summary

            if not final_response:
                # Check if last step had an error - provide user-friendly message
                if state["steps"] and "error" in state["steps"][-1]:
                    last_step = state["steps"][-1]
                    last_result = state["results"][-1] if state["results"] else {}
                    if not execution_ok(last_result):
                        final_response = _build_error_message(last_result)
                    else:
                        # Generic synthesis for successful non-search queries
                        results_summary = json.dumps(state["results"], default=str)
                        synthesis_prompt = (
                            f"The user asked: {user_query}\n\n"
                            f"Tool execution results:\n{results_summary[:3000]}\n\n"
                            "Provide a clear, concise answer in a friendly conversational tone. "
                            "Format the response with proper structure (bullet points, emojis where appropriate). "
                            "Do not mention tools or technical details."
                        )
                        try:
                            final_response = llm_call(synthesis_prompt)
                        except Exception:  # pylint: disable=broad-except
                            final_response = final_response_from_result(state["results"][-1])
                else:
                    # Generic synthesis for successful non-search queries
                    results_summary = json.dumps(state["results"], default=str)
                    synthesis_prompt = (
                        f"The user asked: {user_query}\n\n"
                        f"Tool execution results:\n{results_summary[:3000]}\n\n"
                        "Provide a clear, concise answer in a friendly conversational tone. "
                        "Format the response with proper structure (bullet points, emojis where appropriate). "
                        "Do not mention tools or technical details."
                    )
                    try:
                        final_response = llm_call(synthesis_prompt)
                    except Exception:  # pylint: disable=broad-except
                        final_response = final_response_from_result(state["results"][-1])
        else:
            final_response = "I was unable to find an answer using the available tools."
        _debug("final_default", response=final_response, steps=len(state["steps"]))

    emit_event(events, step="final", tool="", status="ok", latency=0.0)
    result = {"response": final_response, "steps": state["steps"], "events": events, "unavailable": unavailable, "rejected": rejected}

    # ── Persist to long-term memory for future RAG retrieval ────────────
    if final_response:
        await asyncio.to_thread(save_agent_result, user_query, final_response)

    return {
        "success": True,
        "error": None,
        "result": result,
        "status": "ok",
        "response": final_response,
        "steps": state["steps"],
    }
