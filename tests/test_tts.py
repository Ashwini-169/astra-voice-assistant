import time
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from services import tts_service


def _fake_post(url, json, timeout):  # pylint: disable=redefined-outer-name
    class _DummyResponse:
        status_code = 200

        def raise_for_status(self):
            return None

    return _DummyResponse()


def _set_runtime_defaults(client: TestClient, backend: str = "edge") -> None:
    response = client.post(
        "/settings",
        json={
            "backend": backend,
            "edge_base_rate_pct": 8,
            "chunk_initial_words": 5,
            "chunk_steady_words": 14,
            "chunk_max_chars": 140,
        },
    )
    assert response.status_code == 200


def test_speak_piper(monkeypatch):
    """Test /speak with piper backend (mocked HTTP)."""
    monkeypatch.setattr(tts_service._http_session, "post", _fake_post)

    with TestClient(tts_service.app) as client:
        _set_runtime_defaults(client, backend="piper")
        start = time.perf_counter()
        response = client.post("/speak", json={"text": "Hello world"})
        latency = time.perf_counter() - start

    print(f"tts piper latency: {latency:.3f}s")
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert data["backend_status"] == 200
    assert data["backend"] == "piper"


def test_speak_edge(monkeypatch):
    """Test /speak with edge backend (mocked _speak_edge)."""

    async def fake_edge(payload):
        return {"status_code": 200, "audio_bytes": b"fake-audio"}

    monkeypatch.setattr(tts_service, "_speak_edge", fake_edge)

    with TestClient(tts_service.app) as client:
        _set_runtime_defaults(client, backend="edge")
        response = client.post("/speak", json={"text": "(excited)Hello!", "emotion": "excited"})

    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert data["backend"] == "edge"


def test_stop_clears_engine_and_increments_generation(monkeypatch):
    """Test /stop increments generation when playback was stopped."""
    monkeypatch.setattr(tts_service._engine, "stop_and_clear", lambda: 1)

    initial_gen = tts_service._current_generation

    with TestClient(tts_service.app) as client:
        response = client.post("/stop")

    assert response.status_code == 200
    data = response.json()
    assert data["stopped"] is True
    assert tts_service._current_generation == initial_gen + 1


def test_speak_rejects_stale_generation(monkeypatch):
    """Test that /speak rejects requests with a stale generation_id."""

    async def fake_edge(payload):
        return {"status_code": 200, "audio_bytes": b"fake-audio"}

    monkeypatch.setattr(tts_service, "_speak_edge", fake_edge)
    monkeypatch.setattr(tts_service._engine, "stop_and_clear", lambda: 1)

    with TestClient(tts_service.app) as client:
        _set_runtime_defaults(client, backend="edge")

        client.post("/stop")
        current_gen = tts_service._current_generation

        response = client.post(
            "/speak",
            json={
                "text": "stale message",
                "generation_id": current_gen - 1,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is False
    assert data["backend"] == "stale"


def test_speak_accepts_current_generation(monkeypatch):
    """Test that /speak accepts requests with current generation_id."""

    async def fake_edge(payload):
        return {"status_code": 200, "audio_bytes": b"fake-audio"}

    monkeypatch.setattr(tts_service, "_speak_edge", fake_edge)

    with TestClient(tts_service.app) as client:
        _set_runtime_defaults(client, backend="edge")
        current_gen = tts_service._current_generation
        response = client.post(
            "/speak",
            json={
                "text": "valid message",
                "generation_id": current_gen,
                "chunk_id": 0,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] is True
    assert data["backend"] == "edge"


def test_speak_resets_sequence_on_generation_change(monkeypatch):
    """Chunk ids restart at 0 each turn; ensure service resets ordering per generation."""

    async def fake_edge(payload):
        return {"status_code": 200, "audio_bytes": b"fake-audio"}

    monkeypatch.setattr(tts_service, "_speak_edge", fake_edge)

    with tts_service._last_seen_generation_lock:
        tts_service._last_seen_generation_id = None

    reset_spy = MagicMock(return_value=0)
    monkeypatch.setattr(tts_service._engine, "reset_sequence", reset_spy)

    with TestClient(tts_service.app) as client:
        _set_runtime_defaults(client, backend="edge")

        with tts_service._generation_lock:
            base_gen = tts_service._current_generation

        client.post("/speak", json={"text": "turn1", "generation_id": base_gen, "chunk_id": 0})
        client.post("/speak", json={"text": "turn1b", "generation_id": base_gen, "chunk_id": 1})
        client.post("/speak", json={"text": "turn2", "generation_id": base_gen + 1, "chunk_id": 0})

    assert reset_spy.call_count == 2


def test_runtime_settings_and_streaming_config_endpoints():
    with TestClient(tts_service.app) as client:
        response = client.post(
            "/settings",
            json={
                "backend": "piper",
                "piper_api_url": "http://127.0.0.1:60000",
                "fish_speech_api_url": "http://127.0.0.1:9000",
                "edge_base_rate_pct": 10,
                "chunk_initial_words": 6,
                "chunk_steady_words": 16,
                "chunk_max_chars": 180,
            },
        )
        assert response.status_code == 200

        settings_data = client.get("/settings")
        assert settings_data.status_code == 200
        body = settings_data.json()
        assert body["backend"] == "piper"
        assert body["piper_api_url"] == "http://127.0.0.1:60000"
        assert body["chunk_initial_words"] == 6
        assert body["chunk_steady_words"] == 16

        stream_cfg = client.get("/streaming-config")
        assert stream_cfg.status_code == 200
        assert stream_cfg.json()["chunk_initial_words"] == 6
        assert stream_cfg.json()["chunk_steady_words"] == 16

        reset = client.post("/settings/reset")
        assert reset.status_code == 200
