"""LLM service that proxies requests to a local Ollama instance."""
import logging
from typing import Any, Dict, Iterator

import json
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.config import get_settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="LLM Service", version="0.1.0")

KEEP_ALIVE = "24h"

_http_session = requests.Session()


class GenerateRequest(BaseModel):
    prompt: str
    stream: bool = False


class GenerateResponse(BaseModel):
    model: str
    response: str


def _ollama_ready() -> bool:
    settings = get_settings()
    try:
        response = _http_session.get(f"{str(settings.ollama_api_url).rstrip('/')}/api/tags", timeout=5)
        return response.status_code == 200
    except Exception:  # pylint: disable=broad-except
        return False


def _post_generate(payload: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    try:
        response = _http_session.post(
            f"{str(settings.ollama_api_url).rstrip('/')}/api/generate",
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Ollama request failed: %s", exc)
        raise HTTPException(status_code=502, detail="LLM backend unavailable") from exc


def _stream_generate(payload: Dict[str, Any]) -> Iterator[bytes]:
    settings = get_settings()
    url = f"{str(settings.ollama_api_url).rstrip('/')}/api/generate"
    try:
        with _http_session.post(url, json=payload, stream=True, timeout=60) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    yield line + b"\n"
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Ollama stream failed: %s", exc)
        error_line = json.dumps({"error": "LLM backend unavailable", "done": True}) + "\n"
        yield error_line.encode("utf-8")


@app.on_event("startup")
async def warmup_model() -> None:
    settings = get_settings()
    payload = {
        "model": settings.llm_model,
        "prompt": "warmup",
        "keep_alive": KEEP_ALIVE,
        "stream": False,
    }
    try:
        _post_generate(payload)
        logger.info("Warmed up Ollama model '%s'", settings.llm_model)
    except HTTPException:
        logger.warning("Warmup failed for model '%s'", settings.llm_model, exc_info=True)


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    settings = get_settings()
    payload = {
        "model": settings.llm_model,
        "prompt": request.prompt,
        "keep_alive": KEEP_ALIVE,
        "stream": bool(request.stream),
    }
    if request.stream:
        return StreamingResponse(_stream_generate(payload), media_type="application/x-ndjson")

    data = _post_generate(payload)
    text = data.get("response", "")
    return GenerateResponse(model=settings.llm_model, response=text)


@app.get("/health")
async def health():
    settings = get_settings()
    backend_ready = _ollama_ready()
    if backend_ready:
        return {"status": "ok", "service": "llm", "model": settings.llm_model, "backend_ready": True}
    raise HTTPException(status_code=503, detail="LLM backend unavailable")


if __name__ == "__main__":
    from uvicorn import run

    settings = get_settings()
    run(app, host=settings.llm_host, port=settings.llm_port, log_level=settings.log_level.lower())
