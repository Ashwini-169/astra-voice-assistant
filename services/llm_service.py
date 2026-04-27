"""LLM API layer: endpoints + orchestration (routing/providers/streams live in dedicated modules)."""
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel as PydanticBaseModel

from core.config import get_settings
from services.llm_metrics import llm_metrics
from services.llm_models import (
    AgentLoopRequest,
    BrowserSearchRequest,
    FileSearchRequest,
    GenerateRequest,
    GenerateResponse,
    MCPServerConfig,
    MCPToolCallRequest,
    MusicControlRequest,
    RuntimeSettings,
    SettingsUpdate,
)
from services.agent_control import run_phase2_agent_loop
from services.mcp_tools import (
    builtin_servers,
    call_tool,
    delete_server,
    is_tool_allowed,
    list_servers,
    list_tools,
    set_server_enabled,
    tool_browser_search,
    tool_file_search,
    tool_music_control,
    upsert_server,
)
from services.mcp_docker_bridge import mcp_bridge
from services.providers.common import _http_session
from services.providers.ollama import KEEP_ALIVE
from services.router import build_request_context, generate_non_stream, generate_stream, health as provider_health, list_models as provider_models
from services.stream_manager import stream_manager

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="LLM Service", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _default_runtime_settings() -> RuntimeSettings:
    settings = get_settings()
    return RuntimeSettings(
        provider=settings.llm_provider if settings.llm_provider in {"ollama", "lmstudio", "openai", "custom"} else "ollama",
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_num_predict,
        top_p=settings.llm_top_p,
        stream=False,
        ollama_url=str(settings.ollama_api_url).rstrip("/"),
        lmstudio_url=str(settings.lmstudio_api_url).rstrip("/"),
        openai_url=str(settings.openai_api_url).rstrip("/"),
        openai_api_key=settings.openai_api_key or os.getenv("OPENAI_API_KEY", ""),
        custom_url=(settings.custom_llm_api_url or "").rstrip("/"),
        custom_api_key=settings.custom_llm_api_key or os.getenv("CUSTOM_LLM_API_KEY", ""),
        custom_mode="prompt" if settings.custom_llm_mode == "prompt" else "openai",
    )


_runtime_settings = _default_runtime_settings()
_settings_lock = threading.Lock()
_tools_lock = threading.Lock()

# Keep agent iterations short to avoid tool-call loops.
AGENT_MAX_STEPS = 4

# Server-level toggles used by agent/tool routing.
TOOLS: Dict[str, Dict[str, Any]] = {
    "duckduckgo": {"enabled": True, "tools": ["search", "fetch_content"]},
}


def _is_server_enabled(server: str) -> bool:
    with _tools_lock:
        cfg = TOOLS.get(server)
    if cfg is None:
        return True
    return bool(cfg.get("enabled", True))


def _classify_error(status_code: int, message: str) -> str:
    msg = (message or "").lower()
    if status_code == 404:
        return "not_found"
    if status_code == 401:
        return "auth"
    if status_code in {408, 504}:
        return "timeout"
    if status_code in {400, 422}:
        return "validation"
    if status_code in {502, 503}:
        if any(token in msg for token in ("connect", "connection", "refused", "unreachable")):
            return "connection"
        return "server"
    if status_code >= 500:
        return "server"
    return "unknown"


def _build_tool_specs() -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    tool_specs: List[Dict[str, Any]] = []
    unavailable_servers: List[Dict[str, Any]] = []

    servers = list_servers()
    for server in servers["builtin"]:
        if not server.get("enabled", True):
            unavailable_servers.append(
                {
                    "server": server.get("name"),
                    "type": "builtin",
                    "status": "disabled",
                    "reason": "server disabled",
                }
            )
            continue
        for tool in server.get("tools", []):
            tool_specs.append({"server": server["name"], "tool": tool, "type": "builtin"})

    for server in servers["custom"]:
        if not server.get("enabled", True):
            unavailable_servers.append(
                {
                    "server": server.get("name"),
                    "type": "custom",
                    "status": "disabled",
                    "reason": "server disabled",
                }
            )
            continue
        for tool in server.get("tools", []):
            tool_specs.append({"server": server["name"], "tool": tool, "type": "custom"})

    docker_servers = mcp_bridge.list_servers()
    for ds in docker_servers:
        if not _is_server_enabled(ds.get("name", "")):
            unavailable_servers.append(
                {
                    "server": ds.get("name"),
                    "type": "docker",
                    "status": "disabled",
                    "reason": "server disabled",
                }
            )
            continue
        if ds.get("status") != "running":
            unavailable_servers.append(
                {
                    "server": ds.get("name"),
                    "type": "docker",
                    "status": ds.get("status"),
                    "reason": "docker server not running",
                }
            )

    for dt in mcp_bridge.list_all_tools():
        if not _is_server_enabled(dt.get("server", "")):
            continue
        tool_specs.append(
            {
                "server": dt["server"],
                "tool": dt["tool"],
                "type": "docker",
                "description": dt.get("description", ""),
            }
        )

    return tool_specs, unavailable_servers


def _execute_tool_call(call_request: MCPToolCallRequest) -> Dict[str, Any]:
    try:
        if not _is_server_enabled(call_request.server):
            return {
                "ok": False,
                "status_code": 400,
                "error_type": "disabled",
                "error": f"Tool disabled for server '{call_request.server}'",
                "result": None,
            }

        with _tools_lock:
            known_tools = TOOLS.get(call_request.server, {}).get("tools")
        if isinstance(known_tools, list) and known_tools and call_request.tool not in known_tools:
            return {
                "ok": False,
                "status_code": 400,
                "error_type": "validation",
                "error": f"Invalid tool {call_request.server}:{call_request.tool}",
                "result": None,
            }

        docker_server = mcp_bridge.get_server(call_request.server)
        if docker_server:
            if docker_server.status != "running":
                message = f"Docker MCP server '{call_request.server}' is '{docker_server.status}'"
                return {
                    "ok": False,
                    "status_code": 503,
                    "error_type": "unavailable",
                    "error": message,
                }
            raw_result = mcp_bridge.call_tool(call_request.server, call_request.tool, call_request.arguments)
        else:
            if not is_tool_allowed(call_request.server, call_request.tool):
                return {
                    "ok": False,
                    "status_code": 400,
                    "error_type": "disabled",
                    "error": f"{call_request.server}.{call_request.tool} disabled",
                    "result": None,
                }
            raw_result = call_tool(call_request)

        if isinstance(raw_result, dict) and raw_result.get("ok") is False:
            status_code = int(raw_result.get("status_code", 502))
            message = str(raw_result.get("error", "Tool call failed"))
            return {
                "ok": False,
                "status_code": status_code,
                "error_type": str(raw_result.get("error_type", _classify_error(status_code, message))),
                "error": message,
                "result": raw_result.get("result"),
            }

        if isinstance(raw_result, dict) and "error" in raw_result:
            message = str(raw_result.get("error", "Tool call failed"))
            status_code = int(raw_result.get("status_code", 502))
            return {
                "ok": False,
                "status_code": status_code,
                "error_type": _classify_error(status_code, message),
                "error": message,
                "result": raw_result.get("result"),
            }

        result_payload = raw_result.get("result") if isinstance(raw_result, dict) and "result" in raw_result else raw_result
        return {"ok": True, "status_code": 200, "error_type": None, "error": None, "result": result_payload}
    except HTTPException as exc:
        status_code = int(exc.status_code)
        detail = exc.detail
        if isinstance(detail, dict):
            message = str(detail.get("error", detail.get("detail", "Tool call failed")))
            error_type = str(detail.get("error_type", _classify_error(status_code, message)))
        else:
            message = str(detail)
            error_type = _classify_error(status_code, message)
        return {
            "ok": False,
            "status_code": status_code,
            "error_type": error_type,
            "error": message,
            "result": None,
        }
    except Exception as exc:  # pylint: disable=broad-except
        message = str(exc)
        return {
            "ok": False,
            "status_code": 500,
            "error_type": _classify_error(500, message),
            "error": message,
            "result": None,
        }


def _load_effective_settings(overrides: Optional[Dict[str, Any]] = None) -> RuntimeSettings:
    with _settings_lock:
        active = _runtime_settings.model_copy(deep=True)

    payload = overrides or {}
    for key, value in payload.items():
        if hasattr(active, key):
            setattr(active, key, value)
    return active


def _extract_stream_metrics(chunk: bytes) -> tuple[str, bool]:
    text = ""
    is_error = False
    try:
        payload = json.loads(chunk.decode("utf-8").strip())
        if isinstance(payload, dict):
            text = str(payload.get("response", ""))
            is_error = "error" in payload
    except Exception:  # pylint: disable=broad-except
        text = ""
    return text, is_error


def _extract_json_object(raw: str) -> Dict[str, Any]:
    candidate = raw.strip()
    # Remove markdown code blocks if present
    if candidate.startswith("```"):
        # Find first { and last }
        first = candidate.find("{")
        last = candidate.rfind("}")
        if first >= 0 and last > first:
            candidate = candidate[first : last + 1]
    return json.loads(candidate)


_TOOL_ARG_REQUIREMENTS: Dict[str, List[str]] = {
    "search": ["query"],
    "search_web": ["query"],
    "fetch_content": ["url"],
    "read_page": ["url"],
    "obsidian_append_content": ["filepath", "content"],
}


def _pick_obsidian_server(tool_specs: List[Dict[str, Any]]) -> Optional[str]:
    for spec in tool_specs:
        server = spec["server"].lower()
        if "obsidian" in server:
            return server
    return None


def _find_tool_match(
    tool_specs: List[Dict[str, Any]],
    tool_name: str,
    preferred_servers: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    matches = [spec for spec in tool_specs if spec["tool"].lower() == tool_name.lower()]
    if not matches:
        return None
    if preferred_servers:
        lowered = {s.lower() for s in preferred_servers}
        preferred = next((spec for spec in matches if spec["server"].lower() in lowered), None)
        if preferred:
            return preferred
    return matches[0]


def _route_intent_action(user_query: str, tool_specs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    query = (user_query or "").strip()
    lowered = query.lower()
    if not lowered:
        return None

    if any(token in lowered for token in ("note", "obsidian", "save", "append")):
        obsidian_tool = _find_tool_match(tool_specs, "obsidian_append_content")
        obsidian_server = _pick_obsidian_server(tool_specs)
        if obsidian_tool and obsidian_server:
            return {
                "action": "tool",
                "tool": "obsidian_append_content",
                "server": obsidian_server,
                "arguments": {"filepath": "Agent Inbox.md", "content": query},
            }

    if lowered.startswith("http://") or lowered.startswith("https://"):
        for tool_name in ("fetch_content", "read_page"):
            match = _find_tool_match(tool_specs, tool_name)
            if match:
                return {
                    "action": "tool",
                    "tool": match["tool"].lower(),
                    "server": match["server"].lower(),
                    "arguments": {"url": query},
                }

    if any(token in lowered for token in ("news", "latest", "headline", "search", "find", "who", "what", "when")):
        for tool_name in ("search", "search_web"):
            match = _find_tool_match(tool_specs, tool_name, preferred_servers=["duckduckgo", "browser-search"])
            if match:
                return {
                    "action": "tool",
                    "tool": match["tool"].lower(),
                    "server": match["server"].lower(),
                    "arguments": {"query": query},
                }
    return None


def _validate_and_repair_action(
    server_name: Optional[str],
    tool_name: Optional[str],
    arguments: Dict[str, Any],
    tool_specs: List[Dict[str, Any]],
    user_query: str,
) -> Dict[str, Any]:
    repaired_args = dict(arguments or {})
    repaired_tool = (tool_name or "").strip().lower() or None
    repaired_server = (server_name or "").strip().lower() or None

    # Tool and argument auto-corrections for known bad planner output.
    if repaired_tool == "append_content":
        repaired_tool = "obsidian_append_content"
    if repaired_tool and repaired_tool.startswith("obsidian") and "path" in repaired_args and "filepath" not in repaired_args:
        repaired_args["filepath"] = repaired_args.pop("path")
    if repaired_tool and repaired_tool.startswith("obsidian") and not repaired_server:
        repaired_server = _pick_obsidian_server(tool_specs)

    if repaired_tool and repaired_server:
        exact_match = next(
            (
                spec
                for spec in tool_specs
                if spec["tool"].lower() == repaired_tool and spec["server"].lower() == repaired_server
            ),
            None,
        )
        if exact_match is None:
            repaired_server = None

    if repaired_tool and not repaired_server:
        any_match = _find_tool_match(tool_specs, repaired_tool)
        if any_match:
            repaired_server = any_match["server"].lower()

    route_fallback = _route_intent_action(user_query, tool_specs)
    if (not repaired_tool or not repaired_server) and route_fallback:
        repaired_tool = route_fallback["tool"]
        repaired_server = route_fallback["server"]
        repaired_args = route_fallback["arguments"]

    if not repaired_tool or not repaired_server:
        search_default = (
            _find_tool_match(tool_specs, "search", preferred_servers=["duckduckgo", "browser-search"])
            or _find_tool_match(tool_specs, "search_web", preferred_servers=["duckduckgo", "browser-search"])
            or (tool_specs[0] if tool_specs else None)
        )
        if search_default:
            repaired_tool = search_default["tool"].lower()
            repaired_server = search_default["server"].lower()
            repaired_args = {"query": user_query} if user_query.strip() else {}

    required_args = _TOOL_ARG_REQUIREMENTS.get(repaired_tool or "", [])
    if "query" in required_args and "query" not in repaired_args and user_query.strip():
        repaired_args["query"] = user_query.strip()
    if "url" in required_args and "url" not in repaired_args:
        query = user_query.strip()
        if query.startswith("http://") or query.startswith("https://"):
            repaired_args["url"] = query
    if "filepath" in required_args and "filepath" not in repaired_args and "path" in repaired_args:
        repaired_args["filepath"] = repaired_args.pop("path")

    missing_required = [arg for arg in required_args if arg not in repaired_args]
    if missing_required:
        fallback = _route_intent_action(user_query, tool_specs)
        if fallback:
            repaired_tool = fallback["tool"]
            repaired_server = fallback["server"]
            repaired_args = fallback["arguments"]

    return {
        "action": "tool",
        "tool": repaired_tool or "search",
        "server": repaired_server or "duckduckgo",
        "arguments": repaired_args,
    }


def _normalize_action(action: Dict[str, Any], tool_specs: List[Dict[str, Any]], user_query: str = "") -> Dict[str, Any]:
    """Normalize planner output to a strict tool/server/arguments contract."""
    normalized = dict(action or {})
    arguments = normalized.get("arguments")
    if not isinstance(arguments, dict):
        arguments = normalized.get("args") if isinstance(normalized.get("args"), dict) else {}
    normalized["arguments"] = arguments

    # Preserve final answer when explicitly requested.
    action_type = str(normalized.get("action", "")).strip().lower()
    if action_type == "final" and normalized.get("response"):
        return {"action": "final", "response": str(normalized.get("response", ""))}

    tool_val: Any = normalized.get("tool")
    server_val: Any = normalized.get("server")
    tool_name: Optional[str]
    server_name: Optional[str]

    # Case 1: {"tool": {"server": "...", "name": "..."}} (hybrid schema)
    if isinstance(tool_val, dict):
        server_val = tool_val.get("server") or server_val
        tool_val = tool_val.get("name") or tool_val.get("tool")

    tool_name = tool_val.strip().lower() if isinstance(tool_val, str) else None
    server_name = server_val.strip().lower() if isinstance(server_val, str) else None

    # Case 2: "server:duckduckgo"
    if tool_name and tool_name.startswith("server:"):
        inferred_server = tool_name.split(":", 1)[1].strip().lower()
        if inferred_server:
            server_name = inferred_server
        tool_name = None

    if server_name and server_name.startswith("server:"):
        server_name = server_name.split(":", 1)[1].strip().lower()

    # Case 3: dot notation "duckduckgo.search"
    if tool_name and "." in tool_name:
        left, right = tool_name.split(".", 1)
        if left and right:
            server_name = left
            tool_name = right

    # Canonical aliases.
    aliases = {
        "web-search": "search_web",
        "web_search": "search_web",
        "read": "read_page",
        "fetch": "read_page",
        "append_content": "obsidian_append_content",
    }
    if tool_name == "duckduckgo":
        tool_name = "search"
    elif tool_name in aliases:
        tool_name = aliases[tool_name]

    intent_action = _route_intent_action(user_query, tool_specs)
    if not tool_name and intent_action:
        tool_name = intent_action["tool"]
        server_name = intent_action["server"]
        if not arguments:
            arguments = intent_action["arguments"]

    # If server missing/wrong, align to the discovered server for this tool.
    if tool_name:
        matches = [spec for spec in tool_specs if spec["tool"].lower() == tool_name]
        # If planner picked generic "search", support browser-search fallback.
        if not matches and tool_name == "search":
            matches = [spec for spec in tool_specs if spec["tool"].lower() == "search_web"]
            if matches:
                tool_name = "search_web"
        if matches:
            if not server_name or all(spec["server"].lower() != server_name for spec in matches):
                server_name = matches[0]["server"].lower()
        elif server_name:
            # tool may actually be server name; pick first tool in that server.
            server_matches = [spec for spec in tool_specs if spec["server"].lower() == tool_name]
            if server_matches:
                server_name = server_matches[0]["server"].lower()
                tool_name = server_matches[0]["tool"].lower()

    repaired = _validate_and_repair_action(server_name, tool_name, arguments, tool_specs, user_query)
    return repaired


def _tool_requirements_from_specs(tool_specs: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Build strict required-argument map for the current tool catalog."""
    requirements: Dict[str, List[str]] = {}
    for spec in tool_specs:
        server = str(spec.get("server", "")).strip().lower()
        tool = str(spec.get("tool", "")).strip().lower()
        if not server or not tool:
            continue
        key = f"{server}.{tool}"
        req = _TOOL_ARG_REQUIREMENTS.get(tool, [])
        requirements[key] = list(req)
    return requirements


def _validate_action_contract(
    action: Dict[str, Any],
    tool_specs: List[Dict[str, Any]],
    tool_requirements: Dict[str, List[str]],
) -> tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Validate normalized planner action against discovered tools and arg schema."""
    action_type = str(action.get("action", "tool")).strip().lower()
    if action_type == "final":
        response = str(action.get("response", "")).strip()
        if not response:
            return False, "final action missing response", None
        return True, None, {"type": "final"}

    server = str(action.get("server", "")).strip().lower()
    tool = str(action.get("tool", "")).strip().lower()
    args = action.get("arguments")
    if not isinstance(args, dict):
        return False, "arguments must be an object", None
    if not server or not tool:
        return False, "missing server/tool", None

    match = next(
        (spec for spec in tool_specs if str(spec.get("server", "")).lower() == server and str(spec.get("tool", "")).lower() == tool),
        None,
    )
    if match is None:
        return False, f"tool not available: {server}.{tool}", None

    key = f"{server}.{tool}"
    missing = [name for name in tool_requirements.get(key, []) if name not in args]
    if missing:
        return False, f"missing required arguments: {', '.join(missing)}", {"missing": missing}

    return True, None, {"type": "tool", "server": server, "tool": tool}


def _agent_ok(response_text: str, trace: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Uniform agent response contract with backward-compatible fields."""
    result = {"response": response_text, "steps": trace}
    return {
        "success": True,
        "error": None,
        "result": result,
        "status": "ok",
        "response": response_text,
        "steps": trace,
    }


def _agent_error(message: str, trace: Optional[List[Dict[str, Any]]] = None, status_code: int = 502) -> HTTPException:
    payload = {
        "success": False,
        "error": message,
        "result": None,
        "status": "error",
        "response": "",
        "steps": trace or [],
    }
    return HTTPException(status_code=status_code, detail=payload)


def _llm_call_text(prompt: str, settings_obj: RuntimeSettings) -> str:
    request = GenerateRequest(prompt=prompt, stream=False)
    request_ctx = build_request_context(request, settings_obj)
    return generate_non_stream(request_ctx, settings_obj)


@app.on_event("startup")
async def load_mcp_config() -> None:
    """Load Docker MCP servers from mcp_config.json if it exists."""
    import pathlib
    config_path = pathlib.Path(__file__).resolve().parents[1] / "mcp_config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            mcp_bridge.load_config(config)
            logger.info("Loaded MCP config from %s (%d servers)", config_path, len(config.get("mcpServers", {})))
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to load MCP config: %s", exc)


@app.on_event("startup")
async def warmup_model() -> None:
    settings_obj = _load_effective_settings()
    if settings_obj.provider != "ollama":
        return
    payload = {
        "model": settings_obj.model,
        "prompt": "warmup",
        "keep_alive": KEEP_ALIVE,
        "stream": False,
    }
    try:
        _http_session.post(f"{settings_obj.ollama_url}/api/generate", json=payload, timeout=20).raise_for_status()
        logger.info("Warmed up Ollama model '%s'", settings_obj.model)
    except Exception:  # pylint: disable=broad-except
        logger.warning("Warmup failed for model '%s'", settings_obj.model, exc_info=True)


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    start = time.perf_counter()
    settings_obj = _load_effective_settings(request.model_dump(exclude_none=True))
    request_ctx = build_request_context(request, settings_obj)
    request_id = str(uuid.uuid4())

    if request_ctx.stream:
        cancellation_event = stream_manager.register(request_id)

        def _wrapped_stream() -> Iterator[bytes]:
            buffer = []
            stream_error = False
            try:
                for chunk in generate_stream(request_ctx, settings_obj, request_id, cancellation_event):
                    token_text, is_error = _extract_stream_metrics(chunk)
                    if token_text:
                        buffer.append(token_text)
                    if is_error:
                        stream_error = True
                    yield chunk
            finally:
                stream_manager.finish(request_id)
                latency = time.perf_counter() - start
                if stream_error:
                    llm_metrics.record_error(latency)
                else:
                    llm_metrics.record_success(latency, "".join(buffer))

        return StreamingResponse(_wrapped_stream(), media_type="application/x-ndjson")

    try:
        text = generate_non_stream(request_ctx, settings_obj)
    except Exception as exc:  # pylint: disable=broad-except
        latency = time.perf_counter() - start
        llm_metrics.record_error(latency)
        logger.error("Generation failed: %s", exc)
        raise HTTPException(status_code=502, detail="LLM backend unavailable") from exc

    llm_metrics.record_success(time.perf_counter() - start, text)
    return GenerateResponse(provider=request_ctx.provider, model=request_ctx.model, response=text, request_id=request_id)


@app.get("/providers")
async def providers():
    settings_obj = _load_effective_settings()
    return {
        "active_provider": settings_obj.provider,
        "providers": [
            {"name": "ollama", "configured": bool(settings_obj.ollama_url)},
            {"name": "lmstudio", "configured": bool(settings_obj.lmstudio_url)},
            {"name": "openai", "configured": bool(settings_obj.openai_api_key)},
            {"name": "custom", "configured": bool(settings_obj.custom_url)},
        ],
    }


@app.get("/models")
async def models(provider: Optional[str] = Query(default=None)):
    settings_obj = _load_effective_settings()
    if provider:
        p = provider.lower()
        if p not in {"ollama", "lmstudio", "openai", "custom"}:
            raise HTTPException(status_code=400, detail="Unsupported provider")
        return {"provider": p, "models": provider_models(p, settings_obj)}

    all_models = {}
    for p in ["ollama", "lmstudio", "openai", "custom"]:
        all_models[p] = provider_models(p, settings_obj)
    return {"active_provider": settings_obj.provider, "models": all_models}


@app.get("/settings")
async def get_runtime_settings():
    return _load_effective_settings().model_dump()


@app.post("/settings")
async def update_runtime_settings(update: SettingsUpdate):
    with _settings_lock:
        current = _runtime_settings.model_dump()
        for key, value in update.model_dump(exclude_none=True).items():
            current[key] = value
        updated = RuntimeSettings(**current)
        globals()["_runtime_settings"] = updated
    return {"status": "updated", "settings": updated.model_dump()}


@app.post("/settings/reset")
async def reset_runtime_settings():
    with _settings_lock:
        globals()["_runtime_settings"] = _default_runtime_settings()
    return {"status": "reset", "settings": _load_effective_settings().model_dump()}


@app.post("/stop")
async def stop_all_streams():
    cancelled = stream_manager.stop_all()
    return {"status": "stopped", "cancelled_streams": cancelled}


@app.get("/mcp/servers")
async def list_mcp_servers():
    return list_servers()


@app.post("/mcp/servers")
async def register_mcp_server(config: MCPServerConfig):
    return upsert_server(config)


@app.delete("/mcp/servers/{name}")
async def remove_mcp_server(name: str):
    return delete_server(name)


class MCPServerToggleRequest(PydanticBaseModel):
    enabled: bool


class ToolToggleRequest(PydanticBaseModel):
    server: str


@app.patch("/mcp/servers/{name}/enabled")
async def update_mcp_server_enabled(name: str, request: MCPServerToggleRequest):
    return set_server_enabled(name, request.enabled)


@app.get("/mcp/tools")
async def list_mcp_tools(server: str):
    return list_tools(server)


@app.post("/mcp/tools/call")
async def call_mcp_tool(request: MCPToolCallRequest):
    return call_tool(request)


@app.post("/mcp/browser/search")
async def browser_search(request: BrowserSearchRequest):
    return tool_browser_search(query=request.query, limit=request.limit)


@app.post("/mcp/files/search")
async def file_search(request: FileSearchRequest):
    return tool_file_search(query=request.query, limit=request.limit, base_path=request.path)


@app.post("/mcp/music/control")
async def music_control(request: MusicControlRequest):
    return tool_music_control(request.action, request.value)


# â”€â”€ Docker MCP Server Management â”€â”€

class DockerServerRegisterRequest(PydanticBaseModel):
    name: str
    command: str = "docker"
    args: list = []
    env: dict = {}
    auto_start: bool = True


@app.get("/mcp/docker/servers")
async def list_docker_servers():
    servers = []
    for server in mcp_bridge.list_servers():
        row = dict(server)
        row["enabled"] = _is_server_enabled(str(server.get("name", "")))
        servers.append(row)
    return {"servers": servers}


@app.post("/mcp/docker/servers")
async def register_docker_server(req: DockerServerRegisterRequest):
    result = mcp_bridge.register_server(
        name=req.name, command=req.command, args=req.args,
        env=req.env, auto_start=req.auto_start,
    )
    return result


@app.delete("/mcp/docker/servers/{name}")
async def remove_docker_server(name: str):
    ok = mcp_bridge.remove_server(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Docker MCP server '{name}' not found")
    return {"status": "deleted", "name": name}


@app.post("/mcp/docker/servers/{name}/restart")
async def restart_docker_server(name: str):
    return mcp_bridge.restart_server(name)


@app.get("/mcp/docker/tools")
async def list_docker_tools():
    return {"tools": mcp_bridge.list_all_tools()}


@app.get("/mcp/catalog")
async def mcp_catalog():
    tools, unavailable = _build_tool_specs()
    return {
        "tools": tools,
        "unavailable_servers": unavailable,
        "counts": {
            "tools_total": len(tools),
            "unavailable_servers": len(unavailable),
        },
    }


@app.post("/mcp/docker/tools/call")
async def call_docker_tool(request: MCPToolCallRequest):
    return mcp_bridge.call_tool(request.server, request.tool, request.arguments)


@app.post("/mcp/docker/call")
async def call_docker_tool_alias(request: MCPToolCallRequest):
    return mcp_bridge.call_tool(request.server, request.tool, request.arguments)


@app.post("/tools/toggle")
async def toggle_tool(request: ToolToggleRequest):
    server = request.server
    with _tools_lock:
        current = TOOLS.setdefault(server, {"enabled": True, "tools": []})
        current["enabled"] = not bool(current.get("enabled", True))
        enabled = bool(current["enabled"])
    return {"status": "ok", "server": server, "enabled": enabled}


@app.post("/agent/loop")
async def agent_loop(request: AgentLoopRequest):
    start = time.perf_counter()
    settings_obj = _load_effective_settings(request.model_dump(exclude_none=True))
    max_steps = min(max(request.max_steps, 1), AGENT_MAX_STEPS)

    def _phase2_llm_call(prompt: str) -> str:
        return _llm_call_text(prompt, settings_obj)

    try:
        async def _phase2_execute(action: Any) -> Dict[str, Any]:
            req = MCPToolCallRequest(
                server=str(getattr(action, "server", "")),
                tool=str(getattr(action, "tool", "")),
                arguments=dict(getattr(action, "arguments", {}) or {}),
            )
            return _execute_tool_call(req)

        payload = await run_phase2_agent_loop(
            user_query=request.prompt,
            max_steps=max_steps,
            llm_call=_phase2_llm_call,
            execute_fn=_phase2_execute,
        )
        llm_metrics.record_success(time.perf_counter() - start, str(payload.get("response", "")))
        return payload
    except Exception as exc:  # pylint: disable=broad-except
        llm_metrics.record_error(time.perf_counter() - start)
        logger.exception("Agent loop failed: %s", exc)
        raise _agent_error("Agent loop failed", trace=[], status_code=502) from exc


@app.get("/metrics")
async def metrics():
    return llm_metrics.snapshot()


@app.get("/health")
async def health():
    settings_obj = _load_effective_settings()
    backend_ready = provider_health(settings_obj)
    if backend_ready:
        return {
            "status": "ok",
            "service": "llm",
            "provider": settings_obj.provider,
            "model": settings_obj.model,
            "backend_ready": True,
        }
    raise HTTPException(status_code=503, detail="LLM backend unavailable")


if __name__ == "__main__":
    from uvicorn import run

    settings = get_settings()
    run(app, host=settings.llm_host, port=settings.llm_port, log_level=settings.log_level.lower())
