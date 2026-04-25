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

# Keep agent iterations short to avoid tool-call loops.
AGENT_MAX_STEPS = 2

# Server-level toggles used by agent/tool routing.
TOOLS: Dict[str, Dict[str, Any]] = {
    "duckduckgo": {"enabled": True, "tools": ["search", "fetch_content"]},
}


def _is_server_enabled(server: str) -> bool:
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
    }
    if tool_name == "duckduckgo":
        tool_name = "search"
    elif tool_name in aliases:
        tool_name = aliases[tool_name]

    # If tool missing, prefer a search-like default from available specs.
    if not tool_name:
        preferred = next(
            (
                spec
                for spec in tool_specs
                if spec["server"].lower() in {"duckduckgo", "browser-search"}
                and spec["tool"].lower() in {"search", "search_web"}
            ),
            None,
        )
        if preferred is None and tool_specs:
            preferred = tool_specs[0]
        if preferred:
            server_name = preferred["server"].lower()
            tool_name = preferred["tool"].lower()

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

    if not server_name and tool_specs:
        server_name = tool_specs[0]["server"].lower()

    if not arguments and tool_name in {"search", "search_web"} and user_query.strip():
        arguments = {"query": user_query}

    if (not tool_name or not server_name) and tool_specs:
        preferred = next(
            (
                spec
                for spec in tool_specs
                if spec["server"].lower() == "duckduckgo" and spec["tool"].lower() in {"search", "fetch_content"}
            ),
            None,
        )
        if preferred is None:
            preferred = tool_specs[0]
        server_name = server_name or preferred["server"].lower()
        tool_name = tool_name or preferred["tool"].lower()

    return {
        "action": "tool",
        "tool": tool_name or "search",
        "server": server_name or "duckduckgo",
        "arguments": arguments,
    }


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
    current = TOOLS.setdefault(server, {"enabled": True, "tools": []})
    current["enabled"] = not bool(current.get("enabled", True))
    return {"status": "ok", "server": server, "enabled": bool(current["enabled"])}


@app.post("/agent/loop")
async def agent_loop(request: AgentLoopRequest):
    start = time.perf_counter()
    settings_obj = _load_effective_settings(request.model_dump(exclude_none=True))
    max_steps = min(max(request.max_steps, 1), AGENT_MAX_STEPS)

    tool_specs, unavailable_servers = _build_tool_specs()

    trace = []
    user_task = request.prompt
    final_response = ""

    try:
        if not tool_specs:
            fallback = (
                "No MCP tools are currently available. "
                "Check MCP server health and retry after at least one server is running."
            )
            llm_metrics.record_success(time.perf_counter() - start, fallback)
            return {"status": "ok", "steps": trace, "response": fallback}

        for step in range(1, max_steps + 1):
            planner_prompt = (
                "You are a STRICT tool selector for an AI agent.\n\n"
                "Your job is to return EXACTLY ONE JSON object.\n\n"
                "DO NOT explain anything.\n"
                "DO NOT add text.\n"
                "DO NOT change schema.\n\n"
                "Return ONLY:\n\n"
                "{\n"
                '  "tool": "<tool_name>",\n'
                '  "server": "<server_name>",\n'
                '  "arguments": { ... }\n'
                "}\n\n"
                "RULES:\n\n"
                "1. tool MUST be a STRING\n"
                '   - ✅ "search"\n'
                '   - ❌ { "name": "search" }\n\n'
                "2. server MUST be a STRING\n"
                '   - ✅ "duckduckgo"\n'
                '   - ❌ "server:duckduckgo"\n\n'
                "3. NEVER use dot notation\n"
                '   - ❌ "duckduckgo.search"\n\n'
                "4. NEVER wrap tool inside object\n"
                '   - ❌ "tool": { "name": "search" }\n\n'
                "5. arguments MUST be an object\n\n"
                "6. If unsure, choose a search-capable tool from the available list and pass the user query in arguments.\n\n"
                "AVAILABLE TOOLS:\n"
                + "\n".join([f"- {t['server']}: {t['tool']}" for t in tool_specs]) + "\n\n"
                + (
                    "UNAVAILABLE SERVERS (DO NOT USE):\n"
                    + "\n".join([f"- {s['server']} (status: {s['status']})" for s in unavailable_servers])
                    + "\n\n"
                    if unavailable_servers
                    else ""
                )
                + "USER QUERY:\n"
                f"{user_task}\n\n"
                f"TRACE (History): {json.dumps(trace)}\n"
            )
            
            logger.info("========================================")
            logger.info("ðŸ§  STEP %d: Planning...", step)
            step_start_time = time.perf_counter()
            raw_action = _llm_call_text(planner_prompt, settings_obj)
            logger.info("â±ï¸ LLM Planning took %.3fs", time.perf_counter() - step_start_time)
            
            logger.info("RAW LLM OUTPUT: %s", raw_action)
            
            # Auto-Retry for LLM format failure
            if "{" not in raw_action or "}" not in raw_action:
                logger.warning("LLM failed to output JSON, injecting strict constraint and retrying...")
                retry_prompt = planner_prompt + "\n\nERROR: You did not output JSON. You MUST output ONLY a JSON object."
                raw_action = _llm_call_text(retry_prompt, settings_obj)
                logger.info("RETRY RAW LLM OUTPUT: %s", raw_action)
            
            try:
                action = _extract_json_object(raw_action)
            except Exception as e:
                logger.warning("Failed to parse LLM response as JSON: %s", raw_action)
                trace.append({"step": step, "error": f"Invalid JSON response: {str(e)}", "raw": raw_action})
                continue

            # Normalize to strict tool/server/arguments contract.
            action = _normalize_action(action, tool_specs, user_query=user_task)
            action_type = action.get("action", "tool")

            if action_type == "final":
                final_response = str(action.get("response", ""))
                if not final_response:
                    final_response = raw_action.strip()
                break

            call_request = MCPToolCallRequest(
                server=str(action.get("server", "")),
                tool=str(action.get("tool", "")),
                arguments=action.get("arguments", {}) or {},
            )
            
            logger.info("ðŸ› ï¸ EXEC: %s:%s %s", call_request.server, call_request.tool, call_request.arguments)
            # Execution with typed error capture
            tool_start = time.perf_counter()
            tool_result = _execute_tool_call(call_request)
            if not tool_result.get("ok", False):
                logger.warning(
                    "TOOL ERROR (%s): %s",
                    tool_result.get("error_type", "unknown"),
                    tool_result.get("error", ""),
                )
            else:
                logger.info("TOOL SUCCESS")

            tool_latency = time.perf_counter() - tool_start
            trace.append(
                {
                    "step": step,
                    "tool": {"server": call_request.server, "name": call_request.tool},
                    "arguments": call_request.arguments,
                    "result": tool_result,
                    "latency_ms": int(tool_latency * 1000),
                }
            )
            if tool_result.get("ok") and tool_result.get("result") is not None:
                break
            logger.info("========================================")

        if not final_response:
            summary_prompt = (
                "Write a concise final answer for the user based on this trace.\\n"
                f"User task: {user_task}\\n"
                f"Trace: {json.dumps(trace)}"
            )
            final_response = _llm_call_text(summary_prompt, settings_obj)

        llm_metrics.record_success(time.perf_counter() - start, final_response)
        return {"status": "ok", "steps": trace, "response": final_response}

    except Exception as exc:  # pylint: disable=broad-except
        llm_metrics.record_error(time.perf_counter() - start)
        logger.error("Agent loop failed: %s", exc)
        raise HTTPException(status_code=502, detail="Agent loop failed") from exc


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


