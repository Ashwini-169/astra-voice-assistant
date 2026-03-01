import time
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from services import tts_service


def _fake_post(url, json, timeout):  # pylint: disable=redefined-outer-name
    class _DummyResponse:
        status_code = 200

        def raise_for_status(self):
            return None

    return _DummyResponse()


def test_speak_piper(monkeypatch):
    """Test /speak with piper backend (mocked HTTP)."""
    monkeypatch.setenv("AI_ASSISTANT_TTS_BACKEND", "piper")
    # Clear cached settings so env var takes effect
    from core.config import get_settings
    get_settings.cache_clear()

    monkeypatch.setattr(tts_service._http_session, "post", _fake_post)

    with TestClient(tts_service.app) as client:
        start = time.perf_counter()
        response = client.post("/speak", json={"text": "Hello world"})
        latency = time.perf_counter() - start

    print(f"tts piper latency: {latency:.3f}s")
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert data["backend_status"] == 200
    assert data["backend"] == "piper"

    get_settings.cache_clear()


def test_speak_edge(monkeypatch):
    """Test /speak with edge backend (mocked _speak_edge)."""
    monkeypatch.setenv("AI_ASSISTANT_TTS_BACKEND", "edge")
    from core.config import get_settings
    get_settings.cache_clear()

    async def fake_edge(payload):
        return {"status_code": 200, "audio_bytes": b"fake-audio"}

    monkeypatch.setattr(tts_service, "_speak_edge", fake_edge)

    with TestClient(tts_service.app) as client:
        response = client.post("/speak", json={"text": "(excited)Hello!", "emotion": "excited"})

    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert data["backend"] == "edge"

    get_settings.cache_clear()


def test_stop_clears_engine_and_increments_generation(monkeypatch):
    """Test /stop increments generation and clears the engine."""
    monkeypatch.setenv("AI_ASSISTANT_TTS_BACKEND", "edge")
    from core.config import get_settings
    get_settings.cache_clear()

    initial_gen = tts_service._current_generation

    with TestClient(tts_service.app) as client:
        response = client.post("/stop")

    assert response.status_code == 200
    data = response.json()
    assert data["stopped"] is True
    assert tts_service._current_generation == initial_gen + 1

    get_settings.cache_clear()


def test_speak_rejects_stale_generation(monkeypatch):
    """Test that /speak rejects requests with a stale generation_id."""
    monkeypatch.setenv("AI_ASSISTANT_TTS_BACKEND", "edge")
    from core.config import get_settings
    get_settings.cache_clear()

    async def fake_edge(payload):
        return {"status_code": 200, "audio_bytes": b"fake-audio"}

    monkeypatch.setattr(tts_service, "_speak_edge", fake_edge)

    with TestClient(tts_service.app) as client:
        # First bump the generation by calling /stop
        client.post("/stop")
        current_gen = tts_service._current_generation

        # Now send a /speak with an older generation_id
        response = client.post("/speak", json={
            "text": "stale message",
            "generation_id": current_gen - 1,
        })

    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is False
    assert data["backend"] == "stale"

    get_settings.cache_clear()


def test_speak_accepts_current_generation(monkeypatch):
    """Test that /speak accepts requests with current generation_id."""
    monkeypatch.setenv("AI_ASSISTANT_TTS_BACKEND", "edge")
    from core.config import get_settings
    get_settings.cache_clear()

    async def fake_edge(payload):
        return {"status_code": 200, "audio_bytes": b"fake-audio"}

    monkeypatch.setattr(tts_service, "_speak_edge", fake_edge)

    with TestClient(tts_service.app) as client:
        current_gen = tts_service._current_generation
        response = client.post("/speak", json={
            "text": "valid message",
            "generation_id": current_gen,
            "chunk_id": 0,
        })

    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert data["backend"] == "edge"

    get_settings.cache_clear()
