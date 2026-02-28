import time

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
