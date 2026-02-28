import io
import time
import wave

from fastapi.testclient import TestClient

from services import whisper_service


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _FakeInfo:
    def __init__(self, language: str = "en", duration: float = 0.1) -> None:
        self.language = language
        self.duration = duration


class _FakeModel:
    def transcribe(self, _: str, **kwargs):
        return [_FakeSegment(0.0, 0.1, "hello")], _FakeInfo()


def _silent_wav_bytes(duration_seconds: float = 0.1, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        samples = [0] * int(duration_seconds * rate)
        wav_file.writeframes(b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in samples))
    return buffer.getvalue()


def test_transcribe(monkeypatch):
    monkeypatch.setattr(whisper_service, "_whisper_model", _FakeModel())
    monkeypatch.setattr(whisper_service, "load_whisper_model", lambda: whisper_service._whisper_model)

    with TestClient(whisper_service.app) as client:
        start = time.perf_counter()
        response = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", _silent_wav_bytes(), "audio/wav")},
        )
        latency = time.perf_counter() - start

    print(f"whisper latency: {latency:.3f}s")
    assert response.status_code == 200
    data = response.json()
    assert data["text"]
    assert "segments" in data
    assert isinstance(data["segments"], list)
