"""Unified LLM gateway service with provider routing + MCP-style tool APIs."""
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional
from urllib.parse import quote_plus

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.config import get_settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="LLM Service", version="0.2.0")

KEEP_ALIVE = "24h"
_http_session = requests.Session()
_workspace_root = Path(__file__).resolve().parents[1]


class RuntimeSettings(BaseModel):
    provider: Literal["ollama", "lmstudio", "openai", "custom"] = "ollama"
    model: str
    temperature: float = 0.7
    max_tokens: int = 300
    top_p: float = 0.95
    stop: List[str] = Field(default_factory=list)
    stream: bool = False
    voice_mode: bool = False
    ollama_url: str
    lmstudio_url: str = "http://127.0.0.1:1234"
    openai_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    custom_url: str = ""
    custom_api_key: str = ""
    custom_mode: Literal["openai", "prompt"] = "openai"


class SettingsUpdate(BaseModel):
    provider: Optional[Literal["ollama", "lmstudio", "openai", "custom"]] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    stop: Optional[List[str]] = None
    stream: Optional[bool] = None
    voice_mode: Optional[bool] = None
    ollama_url: Optional[str] = None
    lmstudio_url: Optional[str] = None
    openai_url: Optional[str] = None
    openai_api_key: Optional[str] = None
    custom_url: Optional[str] = None
    custom_api_key: Optional[str] = None
    custom_mode: Optional[Literal["openai", "prompt"]] = None


class GenerateRequest(BaseModel):
    prompt: str
    provider: Optional[Literal["ollama", "lmstudio", "openai", "custom"]] = None
    model: Optional[str] = None
    stream: Optional[bool] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    stop: Optional[List[str]] = None
    voice_mode: Optional[bool] = None


class GenerateResponse(BaseModel):
    provider: str
    model: str
    response: str
    request_id: str


class MCPServerConfig(BaseModel):
    name: str
    base_url: str
    description: str = ""
    enabled: bool = True
    tools: List[str] = Field(default_factory=list)
    auth_header: Optional[str] = None


class MCPToolCallRequest(BaseModel):
    server: str
    tool: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class BrowserSearchRequest(BaseModel):
    query: str
    limit: int = 5


class FileSearchRequest(BaseModel):
    query: str
    limit: int = 20
    path: str = "."


class MusicControlRequest(BaseModel):
    action: Literal["play", "pause", "resume", "stop", "next", "previous", "set_volume"]
    value: Optional[int] = None


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

_active_stream_cancellations: Dict[str, threading.Event] = {}
_streams_lock = threading.Lock()

_custom_mcp_servers: Dict[str, MCPServerConfig] = {}
_music_state: Dict[str, Any] = {"status": "stopped", "volume": 50, "track": None}


def _load_effective_settings(request: Optional[GenerateRequest] = None) -> RuntimeSettings:
    with _settings_lock:
        active = _runtime_settings.model_copy(deep=True)
    if request is None:
        return active

    payload = request.model_dump(exclude_none=True)
    if "provider" in payload:
        active.provider = payload["provider"]
    if "model" in payload:
        active.model = payload["model"]
    if "stream" in payload:
        active.stream = payload["stream"]
    if "temperature" in payload:
        active.temperature = payload["temperature"]
    if "max_tokens" in payload:
        active.max_tokens = payload["max_tokens"]
    if "top_p" in payload:
        active.top_p = payload["top_p"]
    if "stop" in payload:
        active.stop = payload["stop"]
    if "voice_mode" in payload:
        active.voice_mode = payload["voice_mode"]
    return active


def _stream_error(detail: str) -> Iterator[bytes]:
    line = json.dumps({"error": detail, "done": True}) + "\n"
    yield line.encode("utf-8")


def _extract_openai_content(data: Dict[str, Any]) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""
    msg = choices[0].get("message", {})
    return msg.get("content", "") or ""


def _iter_openai_stream_lines(response: requests.Response, request_id: str) -> Iterator[bytes]:
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        if raw_line.startswith("data:"):
            raw_line = raw_line[5:].strip()
        if raw_line == "[DONE]":
            done_line = json.dumps({"done": True, "request_id": request_id}) + "\n"
            yield done_line.encode("utf-8")
            return
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        choices = payload.get("choices", [])
        delta = choices[0].get("delta", {}) if choices else {}
        token = delta.get("content", "")
        if token:
            out_line = json.dumps({"response": token, "done": False, "request_id": request_id}) + "\n"
            yield out_line.encode("utf-8")
    done_line = json.dumps({"done": True, "request_id": request_id}) + "\n"
    yield done_line.encode("utf-8")


def _ollama_models(base_url: str) -> List[str]:
    response = _http_session.get(f"{base_url.rstrip('/')}/api/tags", timeout=10)
    response.raise_for_status()
    data = response.json()
    models = []
    for item in data.get("models", []):
        name = item.get("name")
        if name:
            models.append(name)
    return models


def _openai_compatible_models(base_url: str, api_key: str = "") -> List[str]:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    response = _http_session.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=15)
    response.raise_for_status()
    data = response.json()
    models = []
    for item in data.get("data", []):
        model_id = item.get("id")
        if model_id:
            models.append(model_id)
    return models


def _provider_generate_non_stream(provider: str, settings_obj: RuntimeSettings, request: GenerateRequest) -> str:
    prompt = request.prompt
    if provider == "ollama":
        payload: Dict[str, Any] = {
            "model": settings_obj.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": KEEP_ALIVE,
        }
        if settings_obj.max_tokens > 0:
            payload["options"] = {
                "temperature": settings_obj.temperature,
                "num_predict": settings_obj.max_tokens,
                "num_ctx": get_settings().llm_num_ctx,
                "top_p": settings_obj.top_p,
                "stop": settings_obj.stop,
            }
        response = _http_session.post(f"{settings_obj.ollama_url}/api/generate", json=payload, timeout=60)
        response.raise_for_status()
        return response.json().get("response", "")

    if provider in ("lmstudio", "openai", "custom"):
        if provider == "lmstudio":
            base_url = settings_obj.lmstudio_url
            api_key = ""
        elif provider == "openai":
            base_url = settings_obj.openai_url
            api_key = settings_obj.openai_api_key
        else:
            base_url = settings_obj.custom_url
            api_key = settings_obj.custom_api_key

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        if provider == "custom" and settings_obj.custom_mode == "prompt":
            payload = {"prompt": prompt, "stream": False}
            response = _http_session.post(base_url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()
            data = response.json()
            return str(data.get("response", data.get("text", "")))

        payload = {
            "model": settings_obj.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": settings_obj.temperature,
            "max_tokens": settings_obj.max_tokens,
            "top_p": settings_obj.top_p,
            "stop": settings_obj.stop or None,
        }
        response = _http_session.post(f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        return _extract_openai_content(response.json())

    raise HTTPException(status_code=400, detail=f"Unsupported provider '{provider}'")


def _provider_generate_stream(
    provider: str,
    settings_obj: RuntimeSettings,
    request: GenerateRequest,
    request_id: str,
    cancellation_event: threading.Event,
) -> Iterator[bytes]:
    prompt = request.prompt
    try:
        if provider == "ollama":
            payload: Dict[str, Any] = {
                "model": settings_obj.model,
                "prompt": prompt,
                "stream": True,
                "keep_alive": KEEP_ALIVE,
            }
            if settings_obj.max_tokens > 0:
                payload["options"] = {
                    "temperature": settings_obj.temperature,
                    "num_predict": settings_obj.max_tokens,
                    "num_ctx": get_settings().llm_num_ctx,
                    "top_p": settings_obj.top_p,
                    "stop": settings_obj.stop,
                }
            with _http_session.post(f"{settings_obj.ollama_url}/api/generate", json=payload, stream=True, timeout=90) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if cancellation_event.is_set():
                        break
                    if line:
                        yield line + b"\n"
                return

        if provider in ("lmstudio", "openai", "custom"):
            if provider == "lmstudio":
                base_url = settings_obj.lmstudio_url
                api_key = ""
            elif provider == "openai":
                base_url = settings_obj.openai_url
                api_key = settings_obj.openai_api_key
            else:
                base_url = settings_obj.custom_url
                api_key = settings_obj.custom_api_key

            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            if provider == "custom" and settings_obj.custom_mode == "prompt":
                payload = {"prompt": prompt, "stream": True}
                with _http_session.post(base_url, json=payload, headers=headers, stream=True, timeout=90) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if cancellation_event.is_set():
                            break
                        if line:
                            yield line + b"\n"
                return

            payload = {
                "model": settings_obj.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "temperature": settings_obj.temperature,
                "max_tokens": settings_obj.max_tokens,
                "top_p": settings_obj.top_p,
                "stop": settings_obj.stop or None,
            }
            with _http_session.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
                stream=True,
                timeout=90,
            ) as response:
                response.raise_for_status()
                for chunk in _iter_openai_stream_lines(response, request_id=request_id):
                    if cancellation_event.is_set():
                        break
                    yield chunk
            return
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("LLM stream failed: %s", exc)
        yield from _stream_error("LLM backend unavailable")


def _provider_health(settings_obj: RuntimeSettings) -> bool:
    try:
        if settings_obj.provider == "ollama":
            _ollama_models(settings_obj.ollama_url)
            return True
        if settings_obj.provider == "lmstudio":
            _openai_compatible_models(settings_obj.lmstudio_url)
            return True
        if settings_obj.provider == "openai":
            _openai_compatible_models(settings_obj.openai_url, settings_obj.openai_api_key)
            return True
        if settings_obj.provider == "custom":
            if settings_obj.custom_mode == "openai":
                _openai_compatible_models(settings_obj.custom_url, settings_obj.custom_api_key)
            else:
                resp = _http_session.get(settings_obj.custom_url, timeout=10)
                resp.raise_for_status()
            return True
    except Exception:  # pylint: disable=broad-except
        return False
    return False


def _provider_models(provider: str, settings_obj: RuntimeSettings) -> List[str]:
    try:
        if provider == "ollama":
            return _ollama_models(settings_obj.ollama_url)
        if provider == "lmstudio":
            return _openai_compatible_models(settings_obj.lmstudio_url)
        if provider == "openai":
            return _openai_compatible_models(settings_obj.openai_url, settings_obj.openai_api_key)
        if provider == "custom":
            if settings_obj.custom_mode == "openai":
                return _openai_compatible_models(settings_obj.custom_url, settings_obj.custom_api_key)
            return [settings_obj.model]
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Model listing failed for provider '%s': %s", provider, exc)
    return []


def _register_stream(request_id: str) -> threading.Event:
    event = threading.Event()
    with _streams_lock:
        _active_stream_cancellations[request_id] = event
    return event


def _finish_stream(request_id: str) -> None:
    with _streams_lock:
        _active_stream_cancellations.pop(request_id, None)


def _builtin_mcp_servers() -> List[Dict[str, Any]]:
    return [
        {
            "name": "browser-search",
            "kind": "builtin",
            "description": "Simple web search over DuckDuckGo HTML endpoint.",
            "tools": ["search_web"],
        },
        {
            "name": "file-search",
            "kind": "builtin",
            "description": "Search files in workspace for matching text.",
            "tools": ["search_files"],
        },
        {
            "name": "music-control",
            "kind": "builtin",
            "description": "Basic play/pause/stop/volume control state endpoint.",
            "tools": ["play", "pause", "resume", "stop", "next", "previous", "set_volume"],
        },
    ]


def _tool_browser_search(query: str, limit: int = 5) -> Dict[str, Any]:
    safe_query = quote_plus(query)
    url = f"https://duckduckgo.com/html/?q={safe_query}"
    response = _http_session.get(url, timeout=15)
    response.raise_for_status()
    html = response.text

    results: List[Dict[str, str]] = []
    marker = 'class="result__a"'
    parts = html.split(marker)
    for part in parts[1 : limit + 1]:
        href_idx = part.find("href=")
        if href_idx < 0:
            continue
        start = part.find('"', href_idx) + 1
        end = part.find('"', start)
        link = part[start:end]
        text_start = part.find(">", end) + 1
        text_end = part.find("</a>", text_start)
        title = part[text_start:text_end].strip()
        if title:
            results.append({"title": title, "url": link})
    return {"query": query, "results": results}


def _tool_file_search(query: str, limit: int = 20, base_path: str = ".") -> Dict[str, Any]:
    target_root = (_workspace_root / base_path).resolve()
    if _workspace_root not in [target_root, *target_root.parents]:
        raise HTTPException(status_code=400, detail="path must be within workspace")

    matches: List[Dict[str, Any]] = []
    for file_path in target_root.rglob("*"):
        if len(matches) >= limit:
            break
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".mp3", ".wav", ".onnx", ".pt"}:
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # pylint: disable=broad-except
            continue
        idx = content.lower().find(query.lower())
        if idx >= 0:
            line_no = content[:idx].count("\n") + 1
            preview = content[max(0, idx - 40) : idx + min(120, len(query) + 80)].replace("\n", " ")
            matches.append(
                {
                    "file": str(file_path.relative_to(_workspace_root)).replace("\\", "/"),
                    "line": line_no,
                    "preview": preview.strip(),
                }
            )
    return {"query": query, "matches": matches}


def _tool_music_control(action: str, value: Optional[int] = None) -> Dict[str, Any]:
    if action == "set_volume":
        if value is None:
            raise HTTPException(status_code=400, detail="set_volume requires 'value'")
        _music_state["volume"] = max(0, min(100, int(value)))
    elif action in {"play", "resume"}:
        _music_state["status"] = "playing"
    elif action == "pause":
        _music_state["status"] = "paused"
    elif action == "stop":
        _music_state["status"] = "stopped"
    elif action in {"next", "previous"}:
        _music_state["status"] = "playing"
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported music action '{action}'")
    return dict(_music_state)


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
    settings_obj = _load_effective_settings(request)
    provider = settings_obj.provider
    request_id = str(uuid.uuid4())
    do_stream = request.stream if request.stream is not None else settings_obj.stream

    if do_stream:
        cancellation_event = _register_stream(request_id)

        def _wrapped_stream() -> Iterator[bytes]:
            try:
                yield from _provider_generate_stream(provider, settings_obj, request, request_id, cancellation_event)
            finally:
                _finish_stream(request_id)

        return StreamingResponse(_wrapped_stream(), media_type="application/x-ndjson")

    try:
        text = _provider_generate_non_stream(provider, settings_obj, request)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Generation failed: %s", exc)
        raise HTTPException(status_code=502, detail="LLM backend unavailable") from exc
    return GenerateResponse(provider=provider, model=settings_obj.model, response=text, request_id=request_id)


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
        return {"provider": p, "models": _provider_models(p, settings_obj)}

    all_models = {}
    for p in ["ollama", "lmstudio", "openai", "custom"]:
        all_models[p] = _provider_models(p, settings_obj)
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
    cancelled = 0
    with _streams_lock:
        for event in _active_stream_cancellations.values():
            event.set()
            cancelled += 1
    return {"status": "stopped", "cancelled_streams": cancelled}


@app.get("/mcp/servers")
async def list_mcp_servers():
    custom = [cfg.model_dump() for cfg in _custom_mcp_servers.values()]
    return {"builtin": _builtin_mcp_servers(), "custom": custom}


@app.post("/mcp/servers")
async def upsert_mcp_server(config: MCPServerConfig):
    _custom_mcp_servers[config.name] = config
    return {"status": "registered", "server": config.model_dump()}


@app.delete("/mcp/servers/{name}")
async def delete_mcp_server(name: str):
    removed = _custom_mcp_servers.pop(name, None)
    if removed is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return {"status": "deleted", "name": name}


@app.get("/mcp/tools")
async def list_mcp_tools(server: str):
    if server == "browser-search":
        return {"server": server, "tools": ["search_web"]}
    if server == "file-search":
        return {"server": server, "tools": ["search_files"]}
    if server == "music-control":
        return {"server": server, "tools": ["play", "pause", "resume", "stop", "next", "previous", "set_volume"]}
    config = _custom_mcp_servers.get(server)
    if config is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return {"server": server, "tools": config.tools}


@app.post("/mcp/tools/call")
async def call_mcp_tool(request: MCPToolCallRequest):
    if request.server == "browser-search" and request.tool == "search_web":
        return _tool_browser_search(
            query=str(request.arguments.get("query", "")),
            limit=int(request.arguments.get("limit", 5)),
        )
    if request.server == "file-search" and request.tool == "search_files":
        return _tool_file_search(
            query=str(request.arguments.get("query", "")),
            limit=int(request.arguments.get("limit", 20)),
            base_path=str(request.arguments.get("path", ".")),
        )
    if request.server == "music-control":
        value = request.arguments.get("value")
        value_int = int(value) if value is not None else None
        return _tool_music_control(action=request.tool, value=value_int)

    config = _custom_mcp_servers.get(request.server)
    if config is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    if not config.enabled:
        raise HTTPException(status_code=400, detail="MCP server is disabled")

    headers = {"Content-Type": "application/json"}
    if config.auth_header:
        headers["Authorization"] = config.auth_header
    payload = {"tool": request.tool, "arguments": request.arguments}
    try:
        response = _http_session.post(config.base_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return {"server": request.server, "tool": request.tool, "result": response.json()}
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Custom MCP tool call failed: %s", exc)
        raise HTTPException(status_code=502, detail="Custom MCP tool call failed") from exc


@app.post("/mcp/browser/search")
async def browser_search(request: BrowserSearchRequest):
    return _tool_browser_search(query=request.query, limit=request.limit)


@app.post("/mcp/files/search")
async def file_search(request: FileSearchRequest):
    return _tool_file_search(query=request.query, limit=request.limit, base_path=request.path)


@app.post("/mcp/music/control")
async def music_control(request: MusicControlRequest):
    return _tool_music_control(request.action, request.value)


@app.get("/health")
async def health():
    settings_obj = _load_effective_settings()
    backend_ready = _provider_health(settings_obj)
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
