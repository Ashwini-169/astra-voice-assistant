"""Chunked TTS streaming orchestrator with emotion-tag awareness."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Optional

import httpx

from core.config import get_settings
from duplex.interrupt_controller import InterruptController
from humanization.emotion_tagger import EmotionSegment, EmotionStreamBuffer
from humanization.speech_normalizer import markdown_to_speech

logger = logging.getLogger(__name__)

_tts_send_lock = asyncio.Lock()
_WORD_RE = re.compile(r"\\b[\\w']+\\b")
_SENTENCE_ENDERS = (".", "!", "?", ";", ":")

DEFAULT_CHUNK_INITIAL_WORDS = 5
DEFAULT_CHUNK_STEADY_WORDS = 14
DEFAULT_CHUNK_MAX_CHARS = 140


@dataclass
class ChunkProfile:
    initial_words: int = DEFAULT_CHUNK_INITIAL_WORDS
    steady_words: int = DEFAULT_CHUNK_STEADY_WORDS
    max_chars: int = DEFAULT_CHUNK_MAX_CHARS


def _tts_url() -> str:
    settings = get_settings()
    host = settings.tts_host
    port = settings.tts_port
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return host if host.startswith("http") else f"http://{host}:{port}"


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _looks_like_sentence_end(text: str) -> bool:
    stripped = text.rstrip()
    return bool(stripped) and stripped.endswith(_SENTENCE_ENDERS)


async def _fetch_chunk_profile(client: httpx.AsyncClient) -> ChunkProfile:
    url = _tts_url()
    try:
        resp = await client.get(f"{url}/streaming-config", timeout=2.0)
        resp.raise_for_status()
        data = resp.json()
        return ChunkProfile(
            initial_words=max(1, int(data.get("chunk_initial_words", DEFAULT_CHUNK_INITIAL_WORDS))),
            steady_words=max(1, int(data.get("chunk_steady_words", DEFAULT_CHUNK_STEADY_WORDS))),
            max_chars=max(40, int(data.get("chunk_max_chars", DEFAULT_CHUNK_MAX_CHARS))),
        )
    except Exception:  # pylint: disable=broad-except
        return ChunkProfile()


async def _send_tts_segment(
    seg: EmotionSegment,
    client: httpx.AsyncClient,
    generation_id: int = 0,
    is_generation_current_fn: Optional[Callable[[], bool]] = None,
    chunk_id: int = 0,
) -> None:
    if is_generation_current_fn and not is_generation_current_fn():
        logger.debug("[tts-stream] gen=%d stale - dropping segment (%d chars)", generation_id, len(seg.text))
        return

    url = _tts_url()
    speech_text = markdown_to_speech(seg.text)
    if not speech_text.strip():
        logger.debug("[tts-stream] gen=%d skipping empty segment after normalization", generation_id)
        return

    payload: dict = {
        "text": speech_text,
        "chunk_id": chunk_id,
        "generation_id": generation_id,
    }
    if seg.emotion:
        payload["emotion"] = seg.emotion

    async with _tts_send_lock:
        if is_generation_current_fn and not is_generation_current_fn():
            logger.debug("[tts-stream] gen=%d stale after lock - dropping segment", generation_id)
            return
        try:
            resp = await client.post(f"{url}/speak", json=payload, timeout=30.0)
            logger.info(
                "[tts-stream] gen=%d chunk=%d sent segment (%d chars, words=%d, emotion=%s) -> %s",
                generation_id,
                chunk_id,
                len(seg.text),
                _word_count(seg.text),
                seg.emotion,
                resp.status_code,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("[tts-stream] gen=%d TTS segment failed (non-fatal): %s", generation_id, exc)


def _is_interrupted(
    interrupt_flag: Optional[asyncio.Event],
    interrupt_controller: Optional[InterruptController],
    cancellation_event: Optional[asyncio.Event] = None,
) -> bool:
    if interrupt_flag and interrupt_flag.is_set():
        return True
    if interrupt_controller and interrupt_controller.is_triggered():
        return True
    if cancellation_event and cancellation_event.is_set():
        return True
    return False


async def stream_tts(
    text: str,
    interrupt_flag: Optional[asyncio.Event] = None,
    interrupt_controller: Optional[InterruptController] = None,
) -> str:
    """Send pre-formed text (possibly emotion-tagged) to TTS in chunks."""
    from humanization.emotion_tagger import parse_emotion_segments

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
    cancellation_event: Optional[asyncio.Event] = None,
    generation_id: int = 0,
    is_generation_current_fn: Optional[Callable[[], bool]] = None,
) -> str:
    """Stream LLM tokens through EmotionStreamBuffer and send adaptive chunks to TTS.

    Strategy:
    - first chunk small for fast first audio
    - subsequent chunks larger for smoother, less broken speech
    """
    buf = EmotionStreamBuffer()
    chunk_counter = 0
    emitted_chunks = 0
    segment_queue: asyncio.Queue[Optional[EmotionSegment]] = asyncio.Queue()
    interrupted = False

    pending_text = ""
    pending_emotion: Optional[str] = None

    def _is_stale() -> bool:
        return bool(is_generation_current_fn and not is_generation_current_fn())

    def _should_abort() -> bool:
        return _is_interrupted(interrupt_flag, interrupt_controller, cancellation_event) or _is_stale()

    async def _produce_segments() -> None:
        nonlocal interrupted
        try:
            async for token in token_iter:  # type: ignore[union-attr]
                if _should_abort():
                    interrupted = True
                    logger.debug("[tts-stream] gen=%d interrupted/stale during token intake", generation_id)
                    break

                for seg in buf.feed(token):
                    await segment_queue.put(seg)

            if not interrupted:
                for seg in buf.finish():
                    await segment_queue.put(seg)
        finally:
            await segment_queue.put(None)

    async with httpx.AsyncClient() as client:
        profile = await _fetch_chunk_profile(client)
        producer_task = asyncio.create_task(_produce_segments())

        async def _flush_pending() -> None:
            nonlocal pending_text, pending_emotion, chunk_counter, emitted_chunks
            if not pending_text.strip():
                pending_text = ""
                pending_emotion = None
                return
            await _send_tts_segment(
                EmotionSegment(emotion=pending_emotion, text=pending_text.strip()),
                client,
                generation_id=generation_id,
                is_generation_current_fn=is_generation_current_fn,
                chunk_id=chunk_counter,
            )
            chunk_counter += 1
            emitted_chunks += 1
            pending_text = ""
            pending_emotion = None
            await asyncio.sleep(0)

        try:
            while True:
                if _should_abort():
                    interrupted = True
                    logger.debug("[tts-stream] gen=%d interrupted/stale during TTS send loop", generation_id)
                    break

                seg = await segment_queue.get()
                if seg is None:
                    break
                if not seg.text.strip():
                    continue

                # Keep emotions coherent per chunk
                if pending_text and seg.emotion != pending_emotion:
                    await _flush_pending()

                if not pending_text:
                    pending_text = seg.text.strip()
                    pending_emotion = seg.emotion
                else:
                    pending_text = f"{pending_text} {seg.text.strip()}".strip()

                target_words = profile.initial_words if emitted_chunks == 0 else profile.steady_words
                words = _word_count(pending_text)
                should_flush = (
                    words >= target_words
                    or len(pending_text) >= profile.max_chars
                    or (_looks_like_sentence_end(pending_text) and words >= max(3, target_words - 2))
                )
                if should_flush:
                    await _flush_pending()

            await _flush_pending()
        finally:
            if interrupted and not producer_task.done():
                producer_task.cancel()
            try:
                await producer_task
            except asyncio.CancelledError:
                pass

    return "interrupted" if interrupted else "completed"
