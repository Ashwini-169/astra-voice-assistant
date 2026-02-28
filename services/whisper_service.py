"""Whisper transcription service running on GPU."""
import asyncio
import ctypes.util
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from core.config import get_settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Whisper Service", version="0.1.0")

WHISPER_MODEL_NAME = "small"       # 244M params — much better accuracy than 'base'
WHISPER_DEVICE = "cuda"
WHISPER_COMPUTE_TYPE = "int8_float16"
WHISPER_LANGUAGE = "en"             # Force English — prevents Urdu/Arabic hallucinations
WHISPER_INITIAL_PROMPT = "Hello, this is a conversation in English."  # Primes decoder

_whisper_model: Optional[Any] = None
_model_lock = asyncio.Lock()


def _select_whisper_runtime() -> tuple[str, str]:
    requested_device = WHISPER_DEVICE
    requested_compute = WHISPER_COMPUTE_TYPE

    if os.name == "nt" and requested_device == "cuda":
        cudnn_name = "cudnn_ops_infer64_8.dll"
        dll_path = ctypes.util.find_library("cudnn_ops_infer64_8")
        if not dll_path:
            logger.warning("%s not found; falling back Whisper to CPU mode", cudnn_name)
            return "cpu", "int8"

    return requested_device, requested_compute


def _log_gpu_memory() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            free_bytes, total_bytes = torch.cuda.mem_get_info()
            logger.info(
                "GPU memory - free: %s MB, total: %s MB",
                int(free_bytes / (1024 * 1024)),
                int(total_bytes / (1024 * 1024)),
            )
    except Exception as exc:  # pragma: no cover - best-effort metric
        logger.warning("Unable to query GPU memory: %s", exc)


def load_whisper_model():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model

    from faster_whisper import WhisperModel

    settings = get_settings()
    device, compute_type = _select_whisper_runtime()
    _whisper_model = WhisperModel(
        WHISPER_MODEL_NAME,
        device=device,
        compute_type=compute_type,
    )
    logger.info(
        "Loaded Whisper model '%s' on %s with compute_type=%s",
        WHISPER_MODEL_NAME,
        device,
        compute_type,
    )
    _log_gpu_memory()
    logger.info(
        "Whisper service ready on %s:%s",
        settings.whisper_host,
        settings.whisper_port,
    )
    return _whisper_model


class TranscriptionResponse(BaseModel):
    text: str
    language: Optional[str]
    duration: Optional[float]
    segments: List[Dict[str, float]]


@app.on_event("startup")
async def startup_event() -> None:
    async with _model_lock:
        load_whisper_model()


async def _transcribe_file(temp_path: str):
    model = _whisper_model
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    segments_result, info = await run_in_threadpool(
        model.transcribe,
        temp_path,
        language=WHISPER_LANGUAGE,
        initial_prompt=WHISPER_INITIAL_PROMPT,
        beam_size=5,
        vad_filter=True,           # Skip silence frames → faster + cleaner
        vad_parameters={"min_silence_duration_ms": 500},
    )
    segments = list(segments_result)
    assembled_text = " ".join(segment.text.strip() for segment in segments)
    segment_spans = [
        {
            "start": float(getattr(segment, "start", 0.0) or 0.0),
            "end": float(getattr(segment, "end", 0.0) or 0.0),
        }
        for segment in segments
    ]
    return {
        "text": assembled_text,
        "language": getattr(info, "language", None),
        "duration": getattr(info, "duration", None),
        "segments": segment_spans,
    }


@app.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(audio_file: UploadFile = File(...)) -> JSONResponse:
    if audio_file.content_type and not audio_file.content_type.startswith("audio"):
        raise HTTPException(status_code=400, detail="Invalid file type")

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(audio_file.filename or "audio")[1] or ".wav") as tmp:
            content = await audio_file.read()
            tmp.write(content)
            temp_path = tmp.name
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to store upload: {exc}") from exc

    try:
        result = await _transcribe_file(temp_path)
        return JSONResponse(content=result)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            logger.warning("Temporary audio file cleanup failed", exc_info=True)


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok", "service": "whisper"}


if __name__ == "__main__":
    from uvicorn import run

    settings = get_settings()
    run(app, host=settings.whisper_host, port=settings.whisper_port, log_level=settings.log_level.lower())
