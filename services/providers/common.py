"""Shared HTTP/session helpers for provider adapters."""
import json
from typing import Any, Dict, Iterator, List

import requests

_http_session = requests.Session()
_http_session.headers.update({
    "User-Agent": "AstraAssistant/1.0 (https://github.com/Ashwini-169/astra-voice-assistant; support@example.com)"
})


def extract_openai_content(data: Dict[str, Any]) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""
    msg = choices[0].get("message", {})
    return msg.get("content", "") or ""


def iter_openai_stream_lines(response: requests.Response, request_id: str) -> Iterator[bytes]:
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        if raw_line.startswith("data:"):
            raw_line = raw_line[5:].strip()
        if raw_line == "[DONE]":
            done_line = json.dumps({"done": True, "request_id": request_id}) + "\n"
            yield done_line.encode("utf-8")
            return
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        choices = payload.get("choices", [])
        delta = choices[0].get("delta", {}) if choices else {}
        token = delta.get("content", "")
        if token:
            out_line = json.dumps({"response": token, "done": False, "request_id": request_id}) + "\n"
            yield out_line.encode("utf-8")
    done_line = json.dumps({"done": True, "request_id": request_id}) + "\n"
    yield done_line.encode("utf-8")


def openai_compatible_models(base_url: str, api_key: str = "") -> List[str]:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    response = _http_session.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=15)
    response.raise_for_status()
    data = response.json()
    models = []
    for item in data.get("data", []):
        model_id = item.get("id")
        if model_id:
            models.append(model_id)
    return models

