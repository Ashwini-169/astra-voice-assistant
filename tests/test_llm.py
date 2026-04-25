import time

from fastapi import HTTPException
from fastapi.testclient import TestClient

from services import llm_service
from services.llm_models import MCPToolCallRequest


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload

    def iter_lines(self):
        line = b'{"response":"echo:stream","done":true}'
        yield line


def _fake_post(url, json, timeout, **kwargs):  # pylint: disable=redefined-outer-name
    return _DummyResponse({"response": f"echo:{json.get('prompt', '')}"})


def test_generate(monkeypatch):
    monkeypatch.setattr(llm_service._http_session, "post", _fake_post)

    with TestClient(llm_service.app) as client:
        start = time.perf_counter()
        response = client.post("/generate", json={"prompt": "hello"})
        latency = time.perf_counter() - start

    print(f"llm latency: {latency:.3f}s")
    assert response.status_code == 200
    data = response.json()
    assert data["model"] == llm_service.get_settings().llm_model
    assert data["provider"] in {"ollama", "lmstudio", "openai", "custom"}
    assert data["response"].startswith("echo:")


def test_generate_stream(monkeypatch):
    monkeypatch.setattr(llm_service._http_session, "post", _fake_post)

    with TestClient(llm_service.app) as client:
        response = client.post("/generate", json={"prompt": "hello", "stream": True})

    assert response.status_code == 200
    assert "echo:stream" in response.text


def test_settings_update_and_reset():
    with TestClient(llm_service.app) as client:
        updated = client.post("/settings", json={"provider": "custom", "custom_url": "http://localhost:9999"})
        assert updated.status_code == 200
        assert updated.json()["settings"]["provider"] == "custom"

        current = client.get("/settings")
        assert current.status_code == 200
        assert current.json()["provider"] == "custom"

        reset = client.post("/settings/reset")
        assert reset.status_code == 200
        assert reset.json()["settings"]["provider"] in {"ollama", "lmstudio", "openai", "custom"}


def test_mcp_file_search_endpoint():
    with TestClient(llm_service.app) as client:
        response = client.post("/mcp/files/search", json={"query": "LLM", "limit": 3, "path": "services"})
    assert response.status_code == 200
    body = response.json()
    assert "matches" in body


def test_metrics_endpoint(monkeypatch):
    monkeypatch.setattr(llm_service._http_session, "post", _fake_post)
    with TestClient(llm_service.app) as client:
        _ = client.post("/generate", json={"prompt": "metrics check"})
        response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert "latency_ms" in body
    assert "throughput" in body
    assert "errors" in body


def test_mcp_catalog_filters_unavailable_servers(monkeypatch):
    monkeypatch.setattr(
        llm_service,
        "list_servers",
        lambda: {
            "builtin": [{"name": "file-search", "tools": ["search_files"]}],
            "custom": [{"name": "private-docs", "enabled": False, "tools": ["write_doc"]}],
        },
    )
    monkeypatch.setattr(
        llm_service.mcp_bridge,
        "list_servers",
        lambda: [
            {"name": "duckduckgo", "status": "running"},
            {"name": "youtube_transcript", "status": "error"},
        ],
    )
    monkeypatch.setattr(
        llm_service.mcp_bridge,
        "list_all_tools",
        lambda: [{"server": "duckduckgo", "tool": "search", "description": "web search"}],
    )

    with TestClient(llm_service.app) as client:
        response = client.get("/mcp/catalog")

    assert response.status_code == 200
    body = response.json()
    assert any(t["server"] == "duckduckgo" and t["tool"] == "search" for t in body["tools"])
    assert any(s["server"] == "youtube_transcript" for s in body["unavailable_servers"])
    assert any(s["server"] == "private-docs" for s in body["unavailable_servers"])


def test_execute_tool_call_returns_typed_http_error(monkeypatch):
    def _boom(_request):
        raise HTTPException(
            status_code=404,
            detail={"error": "MCP server not found", "error_type": "not_found"},
        )

    monkeypatch.setattr(llm_service, "call_tool", _boom)
    monkeypatch.setattr(llm_service.mcp_bridge, "get_server", lambda _name: None)

    result = llm_service._execute_tool_call(  # pylint: disable=protected-access
        MCPToolCallRequest(server="missing", tool="search_files", arguments={})
    )
    assert result["ok"] is False
    assert result["status_code"] == 404
    assert result["error_type"] == "not_found"


def test_agent_loop_records_typed_tool_error(monkeypatch):
    outputs = iter(
        [
            '{"action":"tool","server":"file-search","tool":"search_files","arguments":{"query":"x","limit":1}}',
            '{"action":"final","response":"done"}',
        ]
    )
    monkeypatch.setattr(llm_service, "_llm_call_text", lambda _prompt, _settings: next(outputs))
    monkeypatch.setattr(
        llm_service,
        "list_servers",
        lambda: {"builtin": [{"name": "file-search", "tools": ["search_files"]}], "custom": []},
    )
    monkeypatch.setattr(llm_service.mcp_bridge, "list_servers", lambda: [])
    monkeypatch.setattr(llm_service.mcp_bridge, "list_all_tools", lambda: [])
    monkeypatch.setattr(llm_service.mcp_bridge, "get_server", lambda _name: None)

    def _fail_tool(_request):
        raise HTTPException(status_code=504, detail={"error": "Custom MCP timeout", "error_type": "timeout"})

    monkeypatch.setattr(llm_service, "call_tool", _fail_tool)

    with TestClient(llm_service.app) as client:
        response = client.post("/agent/loop", json={"prompt": "test mcp loop", "max_steps": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["response"] == "done"
    first_step = body["steps"][0]
    assert first_step["result"]["ok"] is False
    assert first_step["result"]["error_type"] == "timeout"
    assert first_step["result"]["status_code"] == 504
    assert isinstance(first_step["latency_ms"], int)


def test_normalize_action_hybrid_tool_object_falls_back_to_search():
    tool_specs = [{"server": "duckduckgo", "tool": "search"}, {"server": "browser-search", "tool": "read_page"}]
    action = {"tool": {"server": "duckduckgo", "name": "server:duckduckgo"}}

    normalized = llm_service._normalize_action(  # pylint: disable=protected-access
        action, tool_specs, user_query="latest ai voice assistant"
    )

    assert normalized["tool"] == "search"
    assert normalized["server"] == "duckduckgo"
    assert normalized["arguments"] == {"query": "latest ai voice assistant"}


def test_normalize_action_dot_notation():
    tool_specs = [{"server": "duckduckgo", "tool": "search"}]
    action = {"tool": "duckduckgo.search", "arguments": {"query": "weather"}}

    normalized = llm_service._normalize_action(action, tool_specs)  # pylint: disable=protected-access

    assert normalized["tool"] == "search"
    assert normalized["server"] == "duckduckgo"
    assert normalized["arguments"] == {"query": "weather"}


def test_disabled_builtin_tool_returns_error():
    with TestClient(llm_service.app) as client:
        toggle = client.patch("/mcp/servers/browser-search/enabled", json={"enabled": False})
        assert toggle.status_code == 200

        response = client.post(
            "/mcp/tools/call",
            json={"server": "browser-search", "tool": "search_web", "arguments": {"query": "x"}},
        )
        assert response.status_code == 400
        assert "disabled" in str(response.json().get("detail", "")).lower()

        reenable = client.patch("/mcp/servers/browser-search/enabled", json={"enabled": True})
        assert reenable.status_code == 200


def test_toggle_builtin_server_reflected_in_list():
    with TestClient(llm_service.app) as client:
        disable = client.patch("/mcp/servers/browser-search/enabled", json={"enabled": False})
        assert disable.status_code == 200

        listed = client.get("/mcp/servers")
        assert listed.status_code == 200
        browser = next(s for s in listed.json()["builtin"] if s["name"] == "browser-search")
        assert browser["enabled"] is False

        enable = client.patch("/mcp/servers/browser-search/enabled", json={"enabled": True})
        assert enable.status_code == 200
