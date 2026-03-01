"""Text-to-speech proxy service.

Supports three backends controlled by ``AI_ASSISTANT_TTS_BACKEND``:

* ``edge``        — Microsoft Edge TTS (default, no server needed, internet required)
* ``piper``       — strips emotion tags, forwards plain text to Piper
* ``fish_speech`` — reconstructs ``(emotion)text`` and sends to OpenAudio S1 Mini

Audio playback
~~~~~~~~~~~~~~
Uses :class:`AudioPlaybackEngine` — a single continuous ``sounddevice``
output stream with ordered chunk playback and fade smoothing.  Replaces
the legacy per-chunk MCI (``winmm``) daemon-thread approach which could
overlap, click, and play out of order.
"""
import asyncio
import io
import logging
import threading
from typing import Any, Dict, Optional

import numpy as np
import requests
from fastapi import FastAPI, HTTPException
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

app = FastAPI(title="TTS Service", version="0.5.0")

_http_session = requests.Session()

# ── Playback engine (replaces MCI) ──────────────────────────────────────────
_engine = AudioPlaybackEngine(sample_rate=24_000, channels=1)

# ── Generation tracking — stale /speak requests are rejected ────────────────
_current_generation: int = 0
_generation_lock = threading.Lock()

# ── Chunk counter — auto-assigned when client doesn't send chunk_id ─────────
_chunk_counter: int = 0
_chunk_lock = threading.Lock()

# ── Edge TTS voice mapping by emotion ────────────────────────────────────────
# Indian English voices with style adjustments
_EDGE_DEFAULT_VOICE = "en-IN-NeerjaNeural"  # Female Indian English
_EDGE_EMOTION_VOICES: Dict[str, str] = {
    # Most Edge voices don't support style changes, but we can pick voices
    # that naturally suit certain tones
    "excited":    "en-IN-NeerjaNeural",
    "joyful":     "en-IN-NeerjaNeural",
    "delighted":  "en-IN-NeerjaNeural",
    "sad":        "en-IN-NeerjaNeural",
    "empathetic": "en-IN-NeerjaNeural",
    "angry":      "en-IN-PrabhatNeural",  # Male voice for contrast
    "confident":  "en-IN-PrabhatNeural",
    "whispering": "en-IN-NeerjaNeural",
}


class SpeakRequest(BaseModel):
    text: str
    emotion: Optional[str] = None
    chunk_id: Optional[int] = None        # set by tts_streamer
    generation_id: Optional[int] = None   # set by tts_streamer


class SpeakResponse(BaseModel):
    accepted: bool
    backend_status: int
    backend: str


# ── Backend implementations ───────────────────────────────────────────────────


def _decode_mp3(mp3_bytes: bytes) -> Optional[np.ndarray]:
    """Decode MP3 bytes → 1-D float32 PCM numpy array at engine sample rate.

    Uses ``miniaudio`` (built-in dr_mp3 decoder) with automatic
    resampling and channel conversion so the output always matches the
    :class:`AudioPlaybackEngine` format (24 kHz mono float32).

    Returns ``None`` if the audio is empty or miniaudio is unavailable.
    """
    if not mp3_bytes:
        return None
    if miniaudio is None:
        logger.error("[tts] miniaudio not installed — cannot decode MP3")
        return None
    try:
        # Ask miniaudio to convert to engine's target format in one step:
        #   - nchannels=1  → stereo → mono mix-down
        #   - sample_rate  → resample to engine rate (e.g. 44100 → 24000)
        target_rate = _engine._sr
        decoded = miniaudio.decode(
            mp3_bytes,
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=1,
            sample_rate=target_rate,
        )
        pcm = np.frombuffer(decoded.samples, dtype=np.float32).copy()
        logger.info("[tts] decoded %d MP3 bytes → %d PCM samples @ %d Hz (%.2fs)",
                    len(mp3_bytes), len(pcm), target_rate,
                    len(pcm) / target_rate)
        return pcm
    except Exception as exc:
        logger.error("[tts] MP3 decode failed: %s", exc)
        return None


async def _speak_edge(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Synthesize speech using Microsoft Edge TTS (no server needed).

    Returns a dict with ``status_code`` and ``audio_bytes`` keys.
    Audio is decoded to PCM and enqueued for ordered playback via the
    :class:`AudioPlaybackEngine`.
    """
    import edge_tts  # lazy import — only loaded when backend=edge

    clean_text = strip_emotion_tags(payload["text"])
    if not clean_text.strip():
        return {"status_code": 200, "audio_bytes": b""}

    emotion = payload.get("emotion")
    voice = _EDGE_EMOTION_VOICES.get(emotion, _EDGE_DEFAULT_VOICE) if emotion else _EDGE_DEFAULT_VOICE

    # Adjust rate/pitch based on emotion
    rate = "+0%"
    pitch = "+0Hz"
    if emotion in ("excited", "joyful", "delighted", "surprised"):
        rate = "+15%"
        pitch = "+3Hz"
    elif emotion in ("sad", "depressed", "sympathetic"):
        rate = "-10%"
        pitch = "-2Hz"
    elif emotion in ("whispering", "scared", "nervous"):
        rate = "-15%"
        pitch = "-3Hz"
    elif emotion in ("angry", "frustrated", "upset"):
        rate = "+10%"
        pitch = "+2Hz"

    chunk_id = payload.get("chunk_id", 0)

    try:
        communicate = edge_tts.Communicate(clean_text, voice=voice, rate=rate, pitch=pitch)
        audio_data = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.write(chunk["data"])

        mp3_bytes = audio_data.getvalue()

        # Decode MP3 → PCM float32 and enqueue for ordered playback
        if mp3_bytes:
            pcm = _decode_mp3(mp3_bytes)
            if pcm is not None and len(pcm) > 0:
                _engine.enqueue(chunk_id, pcm)

        logger.info("[tts] edge synthesized %d bytes → PCM | voice=%s | emotion=%s | chunk=%d",
                     len(mp3_bytes), voice, emotion, chunk_id)
        return {"status_code": 200, "audio_bytes": mp3_bytes}
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Edge TTS failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Edge TTS failed: {exc}") from exc

def _speak_piper(payload: Dict[str, Any]) -> requests.Response:
    """Send plain stripped text to Piper TTS server."""
    settings = get_settings()
    clean_text = strip_emotion_tags(payload["text"])
    try:
        response = _http_session.post(
            f"{str(settings.piper_api_url).rstrip('/')}/synthesize",
            json={"text": clean_text},
            timeout=15,
        )
        response.raise_for_status()
        return response
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Piper request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Piper TTS backend unavailable") from exc


def _speak_fish_speech(payload: Dict[str, Any]) -> requests.Response:
    """Send emotion-tagged text to OpenAudio S1 Mini / Fish-Speech server.

    Fish-Speech natively understands ``(emotion)text`` format, so we
    reconstruct the tag if an emotion was parsed from the LLM output.
    """
    settings = get_settings()
    emotion = payload.get("emotion")
    raw_text = payload["text"]
    text_for_model = f"({emotion}){raw_text}" if emotion else raw_text
    try:
        response = _http_session.post(
            f"{str(settings.fish_speech_api_url).rstrip('/')}/v1/tts",
            json={"text": text_for_model, "streaming": False},
            timeout=60,  # S1 Mini on CPU is slow — allow up to 60s
        )
        response.raise_for_status()
        return response
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Fish-Speech request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Fish-Speech TTS backend unavailable") from exc


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/speak", response_model=SpeakResponse)
async def speak(request: SpeakRequest) -> SpeakResponse:
    """Synthesise and enqueue one TTS segment for ordered playback.

    If the request carries a ``generation_id`` that is older than the
    current generation (bumped on ``/stop``), the request is rejected as
    stale — this prevents audio from cancelled turns leaking through the
    pipeline.
    """
    global _chunk_counter

    # ── Reject stale generations ─────────────────────────────────────
    if request.generation_id is not None:
        with _generation_lock:
            if request.generation_id < _current_generation:
                logger.info(
                    "[tts] dropping stale /speak  gen=%d < current=%d",
                    request.generation_id, _current_generation,
                )
                return SpeakResponse(accepted=False, backend_status=200, backend="stale")

    # ── Resolve chunk_id — prefer client-supplied, else auto-assign ──
    if request.chunk_id is not None:
        chunk_id = request.chunk_id
    else:
        with _chunk_lock:
            chunk_id = _chunk_counter
            _chunk_counter += 1

    settings = get_settings()
    backend = settings.tts_backend.lower()

    payload: Dict[str, Any] = {
        "text": request.text,
        "emotion": request.emotion,
        "chunk_id": chunk_id,
    }

    if backend == "edge":
        logger.info("[tts] edge backend | emotion=%s | chunk=%d | %.60s",
                    request.emotion, chunk_id, request.text)
        result = await _speak_edge(payload)
        return SpeakResponse(accepted=True, backend_status=result["status_code"], backend=backend)
    elif backend == "fish_speech":
        logger.info("[tts] fish_speech backend | emotion=%s | %.60s", request.emotion, request.text)
        response = _speak_fish_speech(payload)
    else:
        logger.info("[tts] piper backend | emotion=%s | %.60s", request.emotion, request.text)
        response = _speak_piper(payload)

    return SpeakResponse(accepted=True, backend_status=response.status_code, backend=backend)


@app.post("/stop")
async def stop_playback():
    """Immediately stop all active TTS audio playback.

    Called by the Response Stream Manager when the user interrupts.
    Increments the generation counter ONLY if audio was actually stopped,
    preventing spurious rejections of valid /speak requests.
    """
    global _current_generation, _chunk_counter

    stopped = _engine.stop_and_clear()
    
    # Only increment generation if we actually stopped audio
    if stopped > 0:
        with _generation_lock:
            _current_generation += 1
            gen = _current_generation
        with _chunk_lock:
            _chunk_counter = 0
        logger.info("[tts] /stop → cleared %d chunk(s), generation now %d", stopped, gen)
    else:
        with _generation_lock:
            gen = _current_generation
        logger.debug("[tts] /stop → nothing playing, generation unchanged (%d)", gen)
    
    return {"stopped": True, "count": stopped, "generation": gen}


@app.get("/health")
async def health():
    settings = get_settings()
    return {"status": "ok", "service": "tts", "backend": settings.tts_backend}


@app.on_event("shutdown")
async def _on_shutdown():
    """Release the audio device when the service stops."""
    _engine.shutdown()


if __name__ == "__main__":
    from uvicorn import run

    settings = get_settings()
    run(app, host=settings.tts_host, port=settings.tts_port, log_level=settings.log_level.lower())
