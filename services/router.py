"""Provider routing and request-context translation."""
import json
import logging
from typing import Iterator, List

from fastapi import HTTPException

from services.llm_models import GenerateRequest, LLMRequest, RuntimeSettings
from services.providers import PROVIDER_MODULES

logger = logging.getLogger(__name__)


def build_request_context(request: GenerateRequest, settings_obj: RuntimeSettings) -> LLMRequest:
    return LLMRequest(
        provider=settings_obj.provider,
        model=settings_obj.model,
        prompt=request.prompt,
        temperature=settings_obj.temperature,
        max_tokens=settings_obj.max_tokens,
        top_p=settings_obj.top_p,
        stop=settings_obj.stop or [],
        stream=bool(request.stream if request.stream is not None else settings_obj.stream),
        voice_mode=settings_obj.voice_mode,
    )


def list_models(provider: str, settings_obj: RuntimeSettings) -> List[str]:
    module = PROVIDER_MODULES.get(provider)
    if not module:
        raise HTTPException(status_code=400, detail=f"Unsupported provider '{provider}'")
    try:
        return module.list_models(settings_obj)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Model listing failed for provider '%s': %s", provider, exc)
        return []


def health(settings_obj: RuntimeSettings) -> bool:
    module = PROVIDER_MODULES.get(settings_obj.provider)
    if not module:
        return False
    try:
        return bool(module.health(settings_obj))
    except Exception:  # pylint: disable=broad-except
        return False


def generate_non_stream(request_ctx: LLMRequest, settings_obj: RuntimeSettings) -> str:
    module = PROVIDER_MODULES.get(request_ctx.provider)
    if not module:
        raise HTTPException(status_code=400, detail=f"Unsupported provider '{request_ctx.provider}'")
    return module.generate(request_ctx, settings_obj)


def stream_error(detail: str) -> Iterator[bytes]:
    line = json.dumps({"error": detail, "done": True}) + "\n"
    yield line.encode("utf-8")


def generate_stream(request_ctx: LLMRequest, settings_obj: RuntimeSettings, request_id: str, cancellation_event) -> Iterator[bytes]:
    module = PROVIDER_MODULES.get(request_ctx.provider)
    if not module:
        raise HTTPException(status_code=400, detail=f"Unsupported provider '{request_ctx.provider}'")
    try:
        if request_ctx.provider in {"openai", "lmstudio", "custom"}:
            yield from module.stream_generate(request_ctx, settings_obj, request_id, cancellation_event)
        else:
            yield from module.stream_generate(request_ctx, settings_obj, cancellation_event)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("LLM stream failed: %s", exc)
        yield from stream_error("LLM backend unavailable")

