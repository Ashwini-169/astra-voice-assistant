import time

from fastapi.testclient import TestClient

from services import llm_service


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
