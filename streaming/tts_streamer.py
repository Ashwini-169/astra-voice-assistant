"""Chunked TTS streaming orchestrator with emotion-tag awareness.

Uses ``EmotionStreamBuffer`` to detect emotion tag boundaries in the LLM
token stream and flush each segment — with its emotion label — to the
TTS service as soon as it is complete.
"""
import asyncio
import logging
from typing import AsyncIterator, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)

from core.config import get_settings
from duplex.interrupt_controller import InterruptController
from humanization.emotion_tagger import EmotionStreamBuffer, EmotionSegment

# Minimum characters to accumulate before sending a segment to TTS
# (avoids tiny HTTP calls for single-word segments between tags)
MIN_SEGMENT_CHARS = 30
CHUNK_SIZE = 120  # fallback for plain (no-tag) text chunking


def _tts_url() -> str:
    settings = get_settings()
    host = settings.tts_host
    port = settings.tts_port
    # 0.0.0.0 is a listen address, not a valid client target on Windows
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return host if host.startswith("http") else f"http://{host}:{port}"


async def _send_tts_segment(seg: EmotionSegment, client: httpx.AsyncClient) -> None:
    """POST a single emotion segment to the TTS service."""
    url = _tts_url()
    payload: dict = {"text": seg.text}
    if seg.emotion:
        payload["emotion"] = seg.emotion
    try:
        resp = await client.post(f"{url}/speak", json=payload, timeout=30.0)
        logger.info("[tts-stream] sent segment (%d chars, emotion=%s) → %s",
                    len(seg.text), seg.emotion, resp.status_code)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[tts-stream] TTS segment failed (non-fatal): %s", exc)


def _is_interrupted(
    interrupt_flag: Optional[asyncio.Event],
    interrupt_controller: Optional[InterruptController],
) -> bool:
    if interrupt_flag and interrupt_flag.is_set():
        return True
    if interrupt_controller and interrupt_controller.is_triggered():
        return True
    return False


async def stream_tts(
    text: str,
    interrupt_flag: Optional[asyncio.Event] = None,
    interrupt_controller: Optional[InterruptController] = None,
) -> str:
    """Send pre-formed text (possibly emotion-tagged) to TTS in chunks."""
    from humanization.emotion_tagger import parse_emotion_segments  # lazy import
    segments = parse_emotion_segments(text)
    async with httpx.AsyncClient() as client:
        for seg in segments:
            if _is_interrupted(interrupt_flag, interrupt_controller):
                return "interrupted"
            await _send_tts_segment(seg, client)
            await asyncio.sleep(0)
    return "completed"


async def stream_tts_from_tokens(
    token_iter: AsyncIterator[str],
    interrupt_flag: Optional[asyncio.Event] = None,
    interrupt_controller: Optional[InterruptController] = None,
) -> str:
    """Stream LLM tokens through EmotionStreamBuffer → TTS.

    As tokens arrive, completed emotion segments are sent to the TTS
    service immediately, so speech begins at each tag boundary rather
    than waiting for the full LLM response.
    """
    buf = EmotionStreamBuffer()
    pending_segs: List[EmotionSegment] = []

    async with httpx.AsyncClient() as client:
        async for token in token_iter:  # type: ignore[union-attr]
            if _is_interrupted(interrupt_flag, interrupt_controller):
                return "interrupted"

            completed = buf.feed(token)
            pending_segs.extend(completed)

            # Send any completed segments that meet minimum length
            remaining: List[EmotionSegment] = []
            for seg in pending_segs:
                if len(seg.text) >= MIN_SEGMENT_CHARS:
                    await _send_tts_segment(seg, client)
                    await asyncio.sleep(0)
                else:
                    remaining.append(seg)
            pending_segs = remaining

        # Flush whatever is left in the stream buffer
        for seg in buf.finish():
            pending_segs.append(seg)

        # Send all remaining segments
        for seg in pending_segs:
            if _is_interrupted(interrupt_flag, interrupt_controller):
                return "interrupted"
            if seg.text.strip():
                await _send_tts_segment(seg, client)
                await asyncio.sleep(0)

    return "completed"
