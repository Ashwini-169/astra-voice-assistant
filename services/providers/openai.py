"""OpenAI-compatible provider adapter."""
from typing import Dict, Iterator, List

from services.llm_models import LLMRequest, RuntimeSettings
from services.providers.common import _http_session, extract_openai_content, iter_openai_stream_lines, openai_compatible_models


def _headers(settings_obj: RuntimeSettings) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings_obj.openai_api_key:
        headers["Authorization"] = f"Bearer {settings_obj.openai_api_key}"
    return headers


def generate(request: LLMRequest, settings_obj: RuntimeSettings) -> str:
    payload = {
        "model": request.model,
        "messages": [{"role": "user", "content": request.prompt}],
        "stream": False,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "top_p": request.top_p,
        "stop": request.stop or None,
    }
    response = _http_session.post(
        f"{settings_obj.openai_url.rstrip('/')}/chat/completions",
        json=payload,
        headers=_headers(settings_obj),
        timeout=60,
    )
    response.raise_for_status()
    return extract_openai_content(response.json())


def stream_generate(
    request: LLMRequest,
    settings_obj: RuntimeSettings,
    request_id: str,
    cancellation_event,
) -> Iterator[bytes]:
    payload = {
        "model": request.model,
        "messages": [{"role": "user", "content": request.prompt}],
        "stream": True,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "top_p": request.top_p,
        "stop": request.stop or None,
    }
    with _http_session.post(
        f"{settings_obj.openai_url.rstrip('/')}/chat/completions",
        json=payload,
        headers=_headers(settings_obj),
        stream=True,
        timeout=90,
    ) as response:
        response.raise_for_status()
        for chunk in iter_openai_stream_lines(response, request_id=request_id):
            if cancellation_event.is_set():
                break
            yield chunk


def list_models(settings_obj: RuntimeSettings) -> List[str]:
    return openai_compatible_models(settings_obj.openai_url, settings_obj.openai_api_key)


def health(settings_obj: RuntimeSettings) -> bool:
    list_models(settings_obj)
    return True

