"""MCP-like tool registry and builtin tool implementations."""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from fastapi import HTTPException

from services.llm_models import MCPServerConfig, MCPToolCallRequest
from services.providers.common import _http_session

logger = logging.getLogger(__name__)
_workspace_root = Path(__file__).resolve().parents[1]
_custom_mcp_servers: Dict[str, MCPServerConfig] = {}
_music_state: Dict[str, Any] = {"status": "stopped", "volume": 50, "track": None}

_PERSIST_PATH = _workspace_root / "mcp_servers.json"


def _save_custom_servers() -> None:
    """Persist custom MCP servers to mcp_servers.json."""
    try:
        data = {name: cfg.model_dump() for name, cfg in _custom_mcp_servers.items()}
        _PERSIST_PATH.write_text(
            __import__("json").dumps(data, indent=2), encoding="utf-8"
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[mcp] failed to persist custom servers: %s", exc)


def load_persisted_servers() -> None:
    """Load custom MCP servers from mcp_servers.json on startup."""
    if not _PERSIST_PATH.exists():
        return
    try:
        raw = __import__("json").loads(_PERSIST_PATH.read_text(encoding="utf-8"))
        for name, cfg_dict in raw.items():
            _custom_mcp_servers[name] = MCPServerConfig(**cfg_dict)
        logger.info("[mcp] loaded %d persisted custom servers", len(raw))
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[mcp] failed to load persisted servers: %s", exc)
_builtin_registry: Dict[str, Dict[str, Any]] = {
    "browser-search": {
        "enabled": True,
        "tools": ["search_web", "read_page"],
        "label": "browser-search",
        "type": "builtin",
        "description": "Simple web search over DuckDuckGo HTML endpoint and page reading.",
    },
    "file-search": {
        "enabled": True,
        "tools": ["search_files"],
        "label": "file-search",
        "type": "builtin",
        "description": "Search files in workspace for matching text.",
    },
    "music-control": {
        "enabled": True,
        "tools": ["play", "pause", "resume", "stop", "next", "previous", "set_volume"],
        "label": "music-control",
        "type": "builtin",
        "description": "Basic play/pause/stop/volume control state endpoint.",
    },
}


def builtin_servers() -> List[Dict[str, Any]]:
    return [
        {
            "name": name,
            "kind": "builtin",
            "type": cfg["type"],
            "label": cfg["label"],
            "enabled": bool(cfg["enabled"]),
            "description": cfg["description"],
            "tools": list(cfg["tools"]),
        }
        for name, cfg in _builtin_registry.items()
    ]


def list_servers() -> dict:
    custom = [cfg.model_dump() for cfg in _custom_mcp_servers.values()]
    return {"builtin": builtin_servers(), "custom": custom}


def upsert_server(config: MCPServerConfig) -> dict:
    _custom_mcp_servers[config.name] = config
    _save_custom_servers()
    return {"status": "registered", "server": config.model_dump()}


def delete_server(name: str) -> dict:
    removed = _custom_mcp_servers.pop(name, None)
    if removed is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    _save_custom_servers()
    return {"status": "deleted", "name": name}


def set_server_enabled(name: str, enabled: bool) -> dict:
    if name in _builtin_registry:
        _builtin_registry[name]["enabled"] = bool(enabled)
        return {"status": "updated", "server": name, "enabled": bool(enabled), "type": "builtin"}

    config = _custom_mcp_servers.get(name)
    if config is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    config.enabled = bool(enabled)
    return {"status": "updated", "server": name, "enabled": bool(enabled), "type": "custom"}


def is_tool_allowed(server: str, tool: str) -> bool:
    if server in _builtin_registry:
        cfg = _builtin_registry[server]
        return bool(cfg["enabled"]) and tool in cfg["tools"]

    config = _custom_mcp_servers.get(server)
    if config is not None:
        return bool(config.enabled) and tool in config.tools

    # Unknown here may still be docker-managed and validated by the docker bridge.
    return True


def list_tools(server: str) -> dict:
    if server in _builtin_registry:
        cfg = _builtin_registry[server]
        return {"server": server, "enabled": bool(cfg["enabled"]), "tools": list(cfg["tools"])}
    config = _custom_mcp_servers.get(server)
    if config is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return {"server": server, "enabled": bool(config.enabled), "tools": config.tools}


def tool_browser_search(query: str, limit: int = 5) -> Dict[str, Any]:
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


def tool_read_page(url: str) -> Dict[str, Any]:
    try:
        response = _http_session.get(url, timeout=15)
        response.raise_for_status()
        html = response.text

        # Basic text extraction (strip script/style tags)
        import re

        # Remove scripts and styles
        clean_html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
        # Remove all other tags
        text = re.sub(r"<[^>]+>", " ", clean_html)
        # Normalize whitespace
        text = re.sub(r"\s+", " ", text).strip()

        # Limit to first 10k chars for the LLM
        return {"url": url, "content": text[:10000]}
    except Exception as exc:
        logger.error("Failed to read page %s: %s", url, exc)
        return {"url": url, "error": str(exc)}


def tool_file_search(query: str, limit: int = 20, base_path: str = ".") -> Dict[str, Any]:
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


def tool_music_control(action: str, value: Optional[int] = None) -> Dict[str, Any]:
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


def call_tool(request: MCPToolCallRequest) -> dict:
    if not is_tool_allowed(request.server, request.tool):
        raise HTTPException(status_code=400, detail=f"{request.server}.{request.tool} disabled")

    if request.server == "browser-search" and request.tool == "search_web":
        return tool_browser_search(
            query=str(request.arguments.get("query", "")),
            limit=int(request.arguments.get("limit", 5)),
        )
    if request.server == "browser-search" and request.tool == "read_page":
        return tool_read_page(url=str(request.arguments.get("url", "")))
    if request.server == "file-search" and request.tool == "search_files":
        return tool_file_search(
            query=str(request.arguments.get("query", "")),
            limit=int(request.arguments.get("limit", 20)),
            base_path=str(request.arguments.get("path", ".")),
        )
    if request.server == "music-control":
        value = request.arguments.get("value")
        value_int = int(value) if value is not None else None
        return tool_music_control(action=request.tool, value=value_int)

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
        message = str(exc)
        lower_msg = message.lower()
        error_type = "server"
        status_code = 502
        if "timed out" in lower_msg or "timeout" in lower_msg:
            error_type = "timeout"
            status_code = 504
        elif "refused" in lower_msg or "connection" in lower_msg or "unreachable" in lower_msg:
            error_type = "connection"
            status_code = 502
        raise HTTPException(
            status_code=status_code,
            detail={"error": "Custom MCP tool call failed", "error_type": error_type, "message": message},
        ) from exc
