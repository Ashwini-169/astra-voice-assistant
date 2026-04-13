"""Custom provider adapter supporting prompt and OpenAI-compatible modes."""
from typing import Dict, Iterator, List

from services.llm_models import LLMRequest, RuntimeSettings
from services.providers.common import _http_session, extract_openai_content, iter_openai_stream_lines, openai_compatible_models


def _headers(settings_obj: RuntimeSettings) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings_obj.custom_api_key:
        headers["Authorization"] = f"Bearer {settings_obj.custom_api_key}"
    return headers


def generate(request: LLMRequest, settings_obj: RuntimeSettings) -> str:
    headers = _headers(settings_obj)
    if settings_obj.custom_mode == "prompt":
        payload = {"prompt": request.prompt, "stream": False}
        response = _http_session.post(settings_obj.custom_url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        return str(data.get("response", data.get("text", "")))

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
        f"{settings_obj.custom_url.rstrip('/')}/chat/completions",
        json=payload,
        headers=headers,
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
    headers = _headers(settings_obj)
    if settings_obj.custom_mode == "prompt":
        payload = {"prompt": request.prompt, "stream": True}
        with _http_session.post(settings_obj.custom_url, json=payload, headers=headers, stream=True, timeout=90) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if cancellation_event.is_set():
                    break
                if line:
                    yield line + b"\n"
        return

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
        f"{settings_obj.custom_url.rstrip('/')}/chat/completions",
        json=payload,
        headers=headers,
        stream=True,
        timeout=90,
    ) as response:
        response.raise_for_status()
        for chunk in iter_openai_stream_lines(response, request_id=request_id):
            if cancellation_event.is_set():
                break
            yield chunk


def list_models(settings_obj: RuntimeSettings) -> List[str]:
    if settings_obj.custom_mode == "openai":
        return openai_compatible_models(settings_obj.custom_url, settings_obj.custom_api_key)
    return [settings_obj.model]


def health(settings_obj: RuntimeSettings) -> bool:
    if settings_obj.custom_mode == "openai":
        list_models(settings_obj)
        return True
    resp = _http_session.get(settings_obj.custom_url, timeout=10)
    resp.raise_for_status()
    return True

