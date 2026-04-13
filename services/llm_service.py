"""LLM API layer: endpoints + orchestration (routing/providers/streams live in dedicated modules)."""
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, Iterator, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

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
    list_servers,
    list_tools,
    tool_browser_search,
    tool_file_search,
    tool_music_control,
    upsert_server,
)
from services.providers.common import _http_session
from services.providers.ollama import KEEP_ALIVE
from services.router import build_request_context, generate_non_stream, generate_stream, health as provider_health, list_models as provider_models
from services.stream_manager import stream_manager

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="LLM Service", version="0.3.0")


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
    if candidate.startswith("```"):
        first = candidate.find("{")
        last = candidate.rfind("}")
        if first >= 0 and last > first:
            candidate = candidate[first : last + 1]
    return json.loads(candidate)


def _llm_call_text(prompt: str, settings_obj: RuntimeSettings) -> str:
    request = GenerateRequest(prompt=prompt, stream=False)
    request_ctx = build_request_context(request, settings_obj)
    return generate_non_stream(request_ctx, settings_obj)


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


@app.post("/agent/loop")
async def agent_loop(request: AgentLoopRequest):
    start = time.perf_counter()
    settings_obj = _load_effective_settings(request.model_dump(exclude_none=True))
    max_steps = min(max(request.max_steps, 1), 8)

    tool_specs = []
    servers = list_servers()
    for server in servers["builtin"]:
        for tool in server.get("tools", []):
            tool_specs.append({"server": server["name"], "tool": tool})
    for server in servers["custom"]:
        for tool in server.get("tools", []):
            tool_specs.append({"server": server["name"], "tool": tool})

    trace = []
    user_task = request.prompt
    final_response = ""

    try:
        for step in range(1, max_steps + 1):
            planner_prompt = (
                "You are a tool-aware assistant. Decide the next action.\\n"
                "Return JSON only, no markdown.\\n"
                "Schema: {\"action\":\"tool\",\"server\":\"...\",\"tool\":\"...\",\"arguments\":{}} "
                "or {\"action\":\"final\",\"response\":\"...\"}.\\n"
                f"Available tools: {json.dumps(tool_specs)}\\n"
                f"User task: {user_task}\\n"
                f"Trace so far: {json.dumps(trace)}"
            )
            raw_action = _llm_call_text(planner_prompt, settings_obj)
            action = _extract_json_object(raw_action)

            if action.get("action") == "final":
                final_response = str(action.get("response", "")).strip()
                break

            if action.get("action") != "tool":
                final_response = raw_action.strip()
                break

            call_request = MCPToolCallRequest(
                server=str(action.get("server", "")),
                tool=str(action.get("tool", "")),
                arguments=action.get("arguments", {}) or {},
            )
            tool_result = call_tool(call_request)
            trace.append(
                {
                    "step": step,
                    "tool": {"server": call_request.server, "name": call_request.tool},
                    "arguments": call_request.arguments,
                    "result": tool_result,
                }
            )

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
