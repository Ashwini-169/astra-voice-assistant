"""Text-to-speech proxy service.

Supports three backends controlled by ``AI_ASSISTANT_TTS_BACKEND``:

* ``edge``        — Microsoft Edge TTS (default, no server needed, internet required)
* ``piper``       — strips emotion tags, forwards plain text to Piper
* ``fish_speech`` — reconstructs ``(emotion)text`` and sends to OpenAudio S1 Mini
"""
import asyncio
import ctypes
import io
import logging
import os
import tempfile
import threading
from typing import Any, Dict, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.config import get_settings
from humanization.emotion_tagger import strip_emotion_tags

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="TTS Service", version="0.3.0")

_http_session = requests.Session()

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


class SpeakResponse(BaseModel):
    accepted: bool
    backend_status: int
    backend: str


# ── Backend implementations ───────────────────────────────────────────────────


def _play_mp3_mci(filepath: str) -> None:
    """Play an .mp3 file on Windows using MCI (winmm.dll).

    Runs in a daemon thread.  Blocks until playback finishes, then
    deletes the temp file.  No extra packages required.
    """
    alias = f"tts_{threading.get_ident()}"
    try:
        winmm = ctypes.windll.winmm  # type: ignore[attr-defined]
        winmm.mciSendStringW(f'open "{filepath}" type mpegvideo alias {alias}', None, 0, 0)
        winmm.mciSendStringW(f"play {alias} wait", None, 0, 0)
        winmm.mciSendStringW(f"close {alias}", None, 0, 0)
    except Exception:
        logger.exception("MCI playback failed for %s", filepath)
    finally:
        try:
            os.unlink(filepath)
        except OSError:
            pass


async def _speak_edge(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Synthesize speech using Microsoft Edge TTS (no server needed).

    Returns a dict with ``status_code`` and ``audio_bytes`` keys.
    Audio is saved to a temp file and played via the system default player.
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

    try:
        communicate = edge_tts.Communicate(clean_text, voice=voice, rate=rate, pitch=pitch)
        audio_data = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.write(chunk["data"])
        audio_data.seek(0)

        # Play audio in background (non-blocking)
        if audio_data.getbuffer().nbytes > 0:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.write(audio_data.read())
            tmp.close()
            # Windows: play .mp3 via MCI (winmm) — no extra packages needed
            threading.Thread(
                target=_play_mp3_mci, args=(tmp.name,), daemon=True
            ).start()
        logger.info("[tts] edge synthesized %d bytes | voice=%s | emotion=%s",
                     audio_data.getbuffer().nbytes, voice, emotion)
        return {"status_code": 200, "audio_bytes": audio_data.getvalue()}
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
    settings = get_settings()
    backend = settings.tts_backend.lower()
    payload = {"text": request.text, "emotion": request.emotion}

    if backend == "edge":
        logger.info("[tts] edge backend | emotion=%s | %.60s", request.emotion, request.text)
        result = await _speak_edge(payload)
        return SpeakResponse(accepted=True, backend_status=result["status_code"], backend=backend)
    elif backend == "fish_speech":
        logger.info("[tts] fish_speech backend | emotion=%s | %.60s", request.emotion, request.text)
        response = _speak_fish_speech(payload)
    else:
        logger.info("[tts] piper backend | emotion=%s | %.60s", request.emotion, request.text)
        response = _speak_piper(payload)

    return SpeakResponse(accepted=True, backend_status=response.status_code, backend=backend)


@app.get("/health")
async def health():
    settings = get_settings()
    return {"status": "ok", "service": "tts", "backend": settings.tts_backend}


if __name__ == "__main__":
    from uvicorn import run

    settings = get_settings()
    run(app, host=settings.tts_host, port=settings.tts_port, log_level=settings.log_level.lower())
