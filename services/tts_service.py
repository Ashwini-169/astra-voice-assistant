"""Text-to-speech proxy service.

Supports three backends controlled by runtime settings:

* edge        - Microsoft Edge TTS (default)
* piper       - strips emotion tags, forwards plain text to Piper
* fish_speech - reconstructs (emotion)text and sends to OpenAudio S1 Mini
"""

import asyncio
import io
import logging
import threading
import time
from typing import Any, Dict, Optional

import numpy as np
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    import miniaudio
except ImportError:
    miniaudio = None  # type: ignore[assignment]

from core.config import get_settings
from humanization.emotion_tagger import strip_emotion_tags
from services.audio_playback_engine import AudioPlaybackEngine

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="TTS Service", version="0.6.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_http_session = requests.Session()

# Playback engine
_engine = AudioPlaybackEngine(sample_rate=24_000, channels=1)

# Generation tracking - stale /speak requests are rejected
_current_generation: int = 0
_generation_lock = threading.Lock()

# Chunk counter - auto-assigned when client doesn't send chunk_id
_chunk_counter: int = 0
_chunk_lock = threading.Lock()

_last_seen_generation_id: Optional[int] = None
_last_seen_generation_lock = threading.Lock()

# Edge voice mapping by emotion
_EDGE_DEFAULT_VOICE = "en-IN-NeerjaNeural"
_EDGE_EMOTION_VOICES: Dict[str, str] = {
    "excited": "en-IN-NeerjaNeural",
    "joyful": "en-IN-NeerjaNeural",
    "delighted": "en-IN-NeerjaNeural",
    "sad": "en-IN-NeerjaNeural",
    "empathetic": "en-IN-NeerjaNeural",
    "angry": "en-IN-PrabhatNeural",
    "confident": "en-IN-PrabhatNeural",
    "whispering": "en-IN-NeerjaNeural",
}


class TTSRuntimeSettings(BaseModel):
    backend: str = "edge"
    piper_api_url: str = "http://127.0.0.1:59125"
    piper_voice: str = "en_US-lessac-medium"
    piper_speaker_id: Optional[int] = None
    fish_speech_api_url: str = "http://127.0.0.1:8080"
    edge_offline_fallback_enabled: bool = True
    edge_offline_check_url: str = "https://www.microsoft.com"
    edge_offline_check_timeout_sec: float = 0.5
    edge_offline_state_ttl_sec: float = 3.0
    edge_timeout_sec: float = 1.5
    edge_default_voice: str = _EDGE_DEFAULT_VOICE
    edge_base_rate_pct: int = 8
    chunk_initial_words: int = 5
    chunk_steady_words: int = 14
    chunk_max_chars: int = 140


class TTSSettingsUpdate(BaseModel):
    backend: Optional[str] = None
    piper_api_url: Optional[str] = None
    piper_voice: Optional[str] = None
    piper_speaker_id: Optional[int] = None
    fish_speech_api_url: Optional[str] = None
    edge_offline_fallback_enabled: Optional[bool] = None
    edge_offline_check_url: Optional[str] = None
    edge_offline_check_timeout_sec: Optional[float] = None
    edge_offline_state_ttl_sec: Optional[float] = None
    edge_timeout_sec: Optional[float] = None
    edge_default_voice: Optional[str] = None
    edge_base_rate_pct: Optional[int] = None
    chunk_initial_words: Optional[int] = None
    chunk_steady_words: Optional[int] = None
    chunk_max_chars: Optional[int] = None


class SpeakRequest(BaseModel):
    text: str
    emotion: Optional[str] = None
    chunk_id: Optional[int] = None
    generation_id: Optional[int] = None


class SpeakResponse(BaseModel):
    accepted: bool
    backend_status: int
    backend: str


_runtime_lock = threading.Lock()


def _default_runtime_settings() -> TTSRuntimeSettings:
    settings = get_settings()
    return TTSRuntimeSettings(
        backend=settings.tts_backend.lower(),
        piper_api_url=str(settings.piper_api_url).rstrip("/"),
        piper_voice=settings.piper_voice,
        piper_speaker_id=settings.piper_speaker_id,
        fish_speech_api_url=str(settings.fish_speech_api_url).rstrip("/"),
        edge_offline_fallback_enabled=settings.tts_edge_offline_fallback_enabled,
        edge_offline_check_url=settings.tts_edge_offline_check_url,
        edge_offline_check_timeout_sec=settings.tts_edge_offline_check_timeout_sec,
        edge_offline_state_ttl_sec=settings.tts_edge_offline_state_ttl_sec,
        edge_timeout_sec=settings.tts_edge_timeout_sec,
        edge_default_voice=_EDGE_DEFAULT_VOICE,
        edge_base_rate_pct=8,
        chunk_initial_words=5,
        chunk_steady_words=14,
        chunk_max_chars=140,
    )


_runtime_settings = _default_runtime_settings()


def _load_runtime_settings() -> TTSRuntimeSettings:
    with _runtime_lock:
        return _runtime_settings.model_copy(deep=True)


class _NetworkState:
    def __init__(self):
        self._online = True
        self._last_check = 0.0
        self._lock = asyncio.Lock()

    async def get_status(self, runtime: TTSRuntimeSettings) -> bool:
        ttl = max(0.1, float(runtime.edge_offline_state_ttl_sec))
        now = time.monotonic()
        if now - self._last_check <= ttl:
            return self._online

        async with self._lock:
            now = time.monotonic()
            if now - self._last_check <= ttl:
                return self._online
            self._online = await self._probe(runtime)
            self._last_check = now
            return self._online

    async def _probe(self, runtime: TTSRuntimeSettings) -> bool:
        def _head() -> bool:
            try:
                response = _http_session.head(
                    runtime.edge_offline_check_url,
                    timeout=max(0.1, float(runtime.edge_offline_check_timeout_sec)),
                    allow_redirects=True,
                )
                return response.status_code < 500
            except Exception:
                return False

        return await asyncio.to_thread(_head)


_network_state = _NetworkState()


def _decode_mp3(mp3_bytes: bytes) -> Optional[np.ndarray]:
    """Decode MP3 bytes to mono float32 PCM at engine sample rate."""
    if not mp3_bytes:
        return None
    if miniaudio is None:
        logger.error("[tts] miniaudio not installed - cannot decode MP3")
        return None
    try:
        target_rate = _engine._sr
        decoded = miniaudio.decode(
            mp3_bytes,
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=1,
            sample_rate=target_rate,
        )
        pcm = np.frombuffer(decoded.samples, dtype=np.float32).copy()
        logger.info(
            "[tts] decoded %d MP3 bytes -> %d PCM samples @ %d Hz (%.2fs)",
            len(mp3_bytes),
            len(pcm),
            target_rate,
            len(pcm) / target_rate,
        )
        return pcm
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("[tts] MP3 decode failed: %s", exc)
        return None


async def _speak_edge(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Synthesize speech using Microsoft Edge TTS."""
    import edge_tts  # lazy import

    clean_text = strip_emotion_tags(payload["text"])
    if not clean_text.strip():
        return {"status_code": 200, "audio_bytes": b""}

    runtime = _load_runtime_settings()
    emotion = payload.get("emotion")
    default_voice = runtime.edge_default_voice or _EDGE_DEFAULT_VOICE
    voice = _EDGE_EMOTION_VOICES.get(emotion, default_voice) if emotion else default_voice

    rate_pct = runtime.edge_base_rate_pct
    pitch = "+0Hz"
    if emotion in ("excited", "joyful", "delighted", "surprised"):
        rate_pct += 10
        pitch = "+3Hz"
    elif emotion in ("sad", "depressed", "sympathetic"):
        rate_pct -= 10
        pitch = "-2Hz"
    elif emotion in ("whispering", "scared", "nervous"):
        rate_pct -= 15
        pitch = "-3Hz"
    elif emotion in ("angry", "frustrated", "upset"):
        rate_pct += 8
        pitch = "+2Hz"

    rate_pct = max(-40, min(60, rate_pct))
    rate = f"{rate_pct:+d}%"
    chunk_id = payload.get("chunk_id", 0)

    try:
        communicate = edge_tts.Communicate(clean_text, voice=voice, rate=rate, pitch=pitch)
        audio_data = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.write(chunk["data"])

        mp3_bytes = audio_data.getvalue()
        if mp3_bytes:
            pcm = _decode_mp3(mp3_bytes)
            if pcm is not None and len(pcm) > 0:
                _engine.enqueue(chunk_id, pcm)

        logger.info(
            "[tts] edge synthesized %d bytes -> PCM | voice=%s | emotion=%s | rate=%s | chunk=%d",
            len(mp3_bytes),
            voice,
            emotion,
            rate,
            chunk_id,
        )
        return {"status_code": 200, "audio_bytes": mp3_bytes}
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Edge TTS failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Edge TTS failed: {exc}") from exc


async def _speak_piper(payload: Dict[str, Any]) -> requests.Response:
    """Send plain stripped text to Piper TTS server in a background thread.

    Keeps using the synchronous `requests` session (so tests can monkeypatch
    `tts_service._http_session.post`) but offloads the network call to a
    thread to avoid blocking the asyncio event loop.
    """
    runtime = _load_runtime_settings()
    clean_text = strip_emotion_tags(payload["text"])
    piper_payload: Dict[str, Any] = {"text": clean_text, "voice": runtime.piper_voice}
    if runtime.piper_speaker_id is not None:
        piper_payload["speaker_id"] = runtime.piper_speaker_id

    def _do_post() -> requests.Response:
        resp = _http_session.post(
            f"{runtime.piper_api_url}/synthesize",
            json=piper_payload,
            timeout=15,
        )
        if resp.status_code in {400, 404, 422}:
            logger.info("[tts] Piper rejected voice payload; retrying legacy text-only request")
            resp = _http_session.post(
                f"{runtime.piper_api_url}/synthesize",
                json={"text": clean_text},
                timeout=15,
            )
        resp.raise_for_status()
        return resp

    try:
        response = await asyncio.to_thread(_do_post)
        return response
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Piper request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Piper TTS backend unavailable") from exc


async def _route_edge_with_fallback(payload: Dict[str, Any]) -> Dict[str, Any]:
    runtime = _load_runtime_settings()
    if not runtime.edge_offline_fallback_enabled:
        result = await _speak_edge(payload)
        return {"result": result, "backend": "edge"}

    is_online = await _network_state.get_status(runtime)
    if not is_online:
        logger.info("[tts] offline detected -> piper fallback")
        piper_response = await _speak_piper(payload)
        return {"result": {"status_code": piper_response.status_code}, "backend": "piper"}

    try:
        result = await asyncio.wait_for(_speak_edge(payload), timeout=max(0.2, float(runtime.edge_timeout_sec)))
        return {"result": result, "backend": "edge"}
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[tts] edge failed (%s) -> piper fallback", exc)
        piper_response = await _speak_piper(payload)
        return {"result": {"status_code": piper_response.status_code}, "backend": "piper"}


async def _speak_fish_speech(payload: Dict[str, Any]) -> requests.Response:
    """Send emotion-tagged text to OpenAudio S1 Mini / Fish-Speech server in a background thread."""
    runtime = _load_runtime_settings()
    emotion = payload.get("emotion")
    raw_text = payload["text"]
    text_for_model = f"({emotion}){raw_text}" if emotion else raw_text

    def _do_post() -> requests.Response:
        resp = _http_session.post(
            f"{runtime.fish_speech_api_url}/v1/tts",
            json={"text": text_for_model, "streaming": False},
            timeout=60,
        )
        resp.raise_for_status()
        return resp

    try:
        response = await asyncio.to_thread(_do_post)
        return response
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Fish-Speech request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Fish-Speech TTS backend unavailable") from exc


class SynthesizeRequest(BaseModel):
    text: str
    emotion: Optional[str] = None


@app.post("/synthesize")
async def synthesize(request: SynthesizeRequest):
    """Return raw MP3 audio bytes for browser-side playback."""
    import edge_tts  # lazy import
    from fastapi.responses import Response as FastAPIResponse

    clean_text = strip_emotion_tags(request.text)
    if not clean_text.strip():
        return FastAPIResponse(content=b"", media_type="audio/mpeg")

    runtime = _load_runtime_settings()
    payload = {"text": request.text, "emotion": request.emotion}
    if runtime.backend.lower() == "piper":
        response = await _speak_piper(payload)
        media_type = response.headers.get("content-type", "audio/wav")
        return FastAPIResponse(content=response.content, media_type=media_type)
    if runtime.backend.lower() == "edge" and runtime.edge_offline_fallback_enabled:
        is_online = await _network_state.get_status(runtime)
        if not is_online:
            logger.info("[tts] /synthesize offline detected -> piper fallback")
            response = await _speak_piper(payload)
            media_type = response.headers.get("content-type", "audio/wav")
            return FastAPIResponse(content=response.content, media_type=media_type)

    emotion = request.emotion
    default_voice = runtime.edge_default_voice or _EDGE_DEFAULT_VOICE
    voice = _EDGE_EMOTION_VOICES.get(emotion, default_voice) if emotion else default_voice

    rate_pct = runtime.edge_base_rate_pct
    pitch = "+0Hz"
    if emotion in ("excited", "joyful", "delighted", "surprised"):
        rate_pct += 10
        pitch = "+3Hz"
    elif emotion in ("sad", "depressed", "sympathetic"):
        rate_pct -= 10
        pitch = "-2Hz"
    elif emotion in ("whispering", "scared", "nervous"):
        rate_pct -= 15
        pitch = "-3Hz"
    elif emotion in ("angry", "frustrated", "upset"):
        rate_pct += 8
        pitch = "+2Hz"

    rate_pct = max(-40, min(60, rate_pct))
    rate = f"{rate_pct:+d}%"

    try:
        communicate = edge_tts.Communicate(clean_text, voice=voice, rate=rate, pitch=pitch)
        audio_data = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.write(chunk["data"])

        mp3_bytes = audio_data.getvalue()
        logger.info(
            "[tts] /synthesize %d bytes | voice=%s | emotion=%s | rate=%s",
            len(mp3_bytes), voice, emotion, rate,
        )
        return FastAPIResponse(content=mp3_bytes, media_type="audio/mpeg")
    except Exception as exc:  # pylint: disable=broad-except
        if runtime.edge_offline_fallback_enabled:
            logger.warning("[tts] /synthesize edge failed (%s) -> piper fallback", exc)
            response = await _speak_piper(payload)
            media_type = response.headers.get("content-type", "audio/wav")
            return FastAPIResponse(content=response.content, media_type=media_type)
        logger.error("Edge TTS synthesize failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Edge TTS failed: {exc}") from exc


@app.post("/speak", response_model=SpeakResponse)
async def speak(request: SpeakRequest) -> SpeakResponse:
    """Synthesise and enqueue one TTS segment for ordered playback."""
    global _chunk_counter

    if request.generation_id is not None:
        with _generation_lock:
            if request.generation_id < _current_generation:
                logger.info(
                    "[tts] dropping stale /speak gen=%d < current=%d",
                    request.generation_id,
                    _current_generation,
                )
                return SpeakResponse(accepted=False, backend_status=200, backend="stale")

    if request.generation_id is not None:
        with _last_seen_generation_lock:
            global _last_seen_generation_id  # pylint: disable=global-statement
            if _last_seen_generation_id is None or request.generation_id != _last_seen_generation_id:
                _engine.reset_sequence()
                with _chunk_lock:
                    _chunk_counter = 0
                _last_seen_generation_id = request.generation_id

    if request.chunk_id is not None:
        chunk_id = request.chunk_id
    else:
        with _chunk_lock:
            chunk_id = _chunk_counter
            _chunk_counter += 1

    runtime = _load_runtime_settings()
    backend = runtime.backend.lower()

    payload: Dict[str, Any] = {
        "text": request.text,
        "emotion": request.emotion,
        "chunk_id": chunk_id,
    }

    if backend == "edge":
        logger.info("[tts] edge backend | emotion=%s | chunk=%d | %.60s", request.emotion, chunk_id, request.text)
        route = await _route_edge_with_fallback(payload)
        return SpeakResponse(accepted=True, backend_status=route["result"]["status_code"], backend=route["backend"])

    if backend == "fish_speech":
        logger.info("[tts] fish_speech backend | emotion=%s | %.60s", request.emotion, request.text)
        response = await _speak_fish_speech(payload)
    else:
        logger.info("[tts] piper backend | emotion=%s | %.60s", request.emotion, request.text)
        response = await _speak_piper(payload)

    return SpeakResponse(accepted=True, backend_status=response.status_code, backend=backend)


@app.post("/stop")
async def stop_playback():
    """Immediately stop all active TTS audio playback."""
    global _current_generation, _chunk_counter

    stopped = _engine.stop_and_clear()

    if stopped > 0:
        with _generation_lock:
            _current_generation += 1
            gen = _current_generation
        with _chunk_lock:
            _chunk_counter = 0
        with _last_seen_generation_lock:
            global _last_seen_generation_id  # pylint: disable=global-statement
            _last_seen_generation_id = None
        logger.info("[tts] /stop -> cleared %d chunk(s), generation now %d", stopped, gen)
    else:
        with _generation_lock:
            gen = _current_generation
        logger.debug("[tts] /stop -> nothing playing, generation unchanged (%d)", gen)

    return {"stopped": True, "count": stopped, "generation": gen}


@app.get("/health")
async def health():
    runtime = _load_runtime_settings()
    return {"status": "ok", "service": "tts", "backend": runtime.backend}


@app.get("/settings")
async def get_runtime_settings():
    return _load_runtime_settings().model_dump()


@app.post("/settings")
async def update_runtime_settings(update: TTSSettingsUpdate):
    with _runtime_lock:
        current = _runtime_settings.model_dump()
        for key, value in update.model_dump(exclude_none=True).items():
            current[key] = value

        current["backend"] = str(current["backend"]).lower()
        if current["backend"] not in {"edge", "piper", "fish_speech"}:
            raise HTTPException(status_code=400, detail="backend must be one of: edge, piper, fish_speech")

        if int(current["chunk_initial_words"]) < 1:
            raise HTTPException(status_code=400, detail="chunk_initial_words must be >= 1")
        if int(current["chunk_steady_words"]) < int(current["chunk_initial_words"]):
            raise HTTPException(status_code=400, detail="chunk_steady_words must be >= chunk_initial_words")
        if int(current["chunk_max_chars"]) < 40:
            raise HTTPException(status_code=400, detail="chunk_max_chars must be >= 40")
        current["piper_voice"] = str(current["piper_voice"]).strip()
        if not current["piper_voice"]:
            raise HTTPException(status_code=400, detail="piper_voice must not be empty")
        if current["piper_speaker_id"] is not None and int(current["piper_speaker_id"]) < 0:
            raise HTTPException(status_code=400, detail="piper_speaker_id must be >= 0")
        if float(current["edge_offline_check_timeout_sec"]) <= 0:
            raise HTTPException(status_code=400, detail="edge_offline_check_timeout_sec must be > 0")
        if float(current["edge_offline_state_ttl_sec"]) <= 0:
            raise HTTPException(status_code=400, detail="edge_offline_state_ttl_sec must be > 0")
        if float(current["edge_timeout_sec"]) <= 0:
            raise HTTPException(status_code=400, detail="edge_timeout_sec must be > 0")

        globals()["_runtime_settings"] = TTSRuntimeSettings(**current)

    return {"status": "updated", "settings": _load_runtime_settings().model_dump()}


@app.post("/settings/reset")
async def reset_runtime_settings():
    with _runtime_lock:
        globals()["_runtime_settings"] = _default_runtime_settings()
    return {"status": "reset", "settings": _load_runtime_settings().model_dump()}


@app.get("/streaming-config")
async def get_streaming_config():
    runtime = _load_runtime_settings()
    return {
        "chunk_initial_words": runtime.chunk_initial_words,
        "chunk_steady_words": runtime.chunk_steady_words,
        "chunk_max_chars": runtime.chunk_max_chars,
    }


@app.get("/debug/playback")
async def debug_playback():
    """Expose playback-engine diagnostics for audio troubleshooting."""
    return {
        "generation": _current_generation,
        "chunk_counter": _chunk_counter,
        "last_seen_generation_id": _last_seen_generation_id,
        "engine": _engine.debug_state(),
    }


@app.on_event("shutdown")
async def _on_shutdown():
    """Release the audio device when the service stops."""
    _engine.shutdown()


if __name__ == "__main__":
    from uvicorn import run

    settings = get_settings()
    run(app, host=settings.tts_host, port=settings.tts_port, log_level=settings.log_level.lower())
