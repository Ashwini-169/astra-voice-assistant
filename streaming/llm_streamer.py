"""Streaming LLM client against Ollama with GPU serialization.

Performance knobs (set via config / env vars)::

    AI_ASSISTANT_LLM_NUM_PREDICT=300   # max tokens per response
    AI_ASSISTANT_LLM_NUM_CTX=2048      # context window size

Cancellation: pass an ``asyncio.Event`` as *cancellation_event*.  When set
the generator stops yielding tokens and the HTTP stream is closed.
"""
import asyncio
import json
import logging
import time
from typing import AsyncIterator, Optional

import httpx

from core.config import get_settings
from orchestrator.gpu_lock import gpu_lock

logger = logging.getLogger(__name__)


async def stream_llm(
    prompt: str,
    *,
    cancellation_event: Optional[asyncio.Event] = None,
    generation_id: int = 0,
) -> AsyncIterator[str]:
    """Stream tokens from Ollama.

    Parameters
    ----------
    prompt : str
        Assembled prompt (system + history + user).
    cancellation_event : asyncio.Event, optional
        If provided and set, the stream stops early so the caller can
        begin a new turn.
    """
    settings = get_settings()
    url = f"{str(settings.ollama_api_url).rstrip('/')}/api/generate"
    first_token_latency_ms: Optional[float] = None
    token_count = 0
    start = time.perf_counter()

    # Build Ollama payload with performance options
    payload: dict = {
        "model": settings.llm_model,
        "prompt": prompt,
        "stream": True,
        "keep_alive": "24h",
    }
    if settings.llm_num_predict > 0:
        payload["options"] = {
            "num_predict": settings.llm_num_predict,
            "num_ctx": settings.llm_num_ctx,
        }

    async with gpu_lock():
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        # ── cancellation check (fast: ~0 cost) ─────
                        if cancellation_event and cancellation_event.is_set():
                            logger.info("[llm] gen=%d cancelled after %d tokens", generation_id, token_count)
                            return

                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        token = data.get("response", "")
                        if token:
                            if first_token_latency_ms is None:
                                first_token_latency_ms = (time.perf_counter() - start) * 1000
                            token_count += 1
                            yield token
                        if data.get("done", False):
                            break
            except GeneratorExit:
                logger.debug("LLM stream cancelled by caller")
                return
            except Exception as exc:
                logger.error("LLM stream error: %s", exc)
                raise

    elapsed = (time.perf_counter() - start) * 1000
    if first_token_latency_ms is not None:
        logger.info(
            json.dumps({
                "stage": "llm_stream",
                "generation_id": generation_id,
                "first_token_ms": round(first_token_latency_ms, 2),
                "tokens": token_count,
                "total_ms": round(elapsed, 1),
            })
        )
