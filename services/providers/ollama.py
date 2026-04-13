"""Ollama provider adapter."""
from typing import Any, Dict, Iterator, List

from core.config import get_settings
from services.llm_models import LLMRequest, RuntimeSettings
from services.providers.common import _http_session

KEEP_ALIVE = "24h"


def generate(request: LLMRequest, settings_obj: RuntimeSettings) -> str:
    payload: Dict[str, Any] = {
        "model": request.model,
        "prompt": request.prompt,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
    }
    if request.max_tokens > 0:
        payload["options"] = {
            "temperature": request.temperature,
            "num_predict": request.max_tokens,
            "num_ctx": get_settings().llm_num_ctx,
            "top_p": request.top_p,
            "stop": request.stop,
        }
    response = _http_session.post(f"{settings_obj.ollama_url}/api/generate", json=payload, timeout=60)
    response.raise_for_status()
    return response.json().get("response", "")


def stream_generate(request: LLMRequest, settings_obj: RuntimeSettings, cancellation_event) -> Iterator[bytes]:
    payload: Dict[str, Any] = {
        "model": request.model,
        "prompt": request.prompt,
        "stream": True,
        "keep_alive": KEEP_ALIVE,
    }
    if request.max_tokens > 0:
        payload["options"] = {
            "temperature": request.temperature,
            "num_predict": request.max_tokens,
            "num_ctx": get_settings().llm_num_ctx,
            "top_p": request.top_p,
            "stop": request.stop,
        }
    with _http_session.post(f"{settings_obj.ollama_url}/api/generate", json=payload, stream=True, timeout=90) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if cancellation_event.is_set():
                break
            if line:
                yield line + b"\n"


def list_models(settings_obj: RuntimeSettings) -> List[str]:
    response = _http_session.get(f"{settings_obj.ollama_url.rstrip('/')}/api/tags", timeout=10)
    response.raise_for_status()
    data = response.json()
    models = []
    for item in data.get("models", []):
        name = item.get("name")
        if name:
            models.append(name)
    return models


def health(settings_obj: RuntimeSettings) -> bool:
    list_models(settings_obj)
    return True

