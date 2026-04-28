"""Async orchestration pipeline for the assistant."""
import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, Optional, Tuple

import httpx
from pydantic import BaseModel

from core.config import get_settings
from duplex.audio_listener import AudioListener
from duplex.interrupt_controller import InterruptController
from duplex.state_machine import AssistantState, AssistantStateController
from humanization.emotion_engine import EmotionEngine
from humanization.prosody_engine import apply_prosody
from humanization.voice_style import INDIAN_NEUTRAL_FEMALE
from memory.memory_manager import MemoryManager
from orchestrator.context_engine import build_prompt
from orchestrator.memory_buffer import ConversationBuffer
from humanization.emotion_tagger import (
    EmotionStreamBuffer,  # noqa: F401  (imported for type completeness)
    format_emotion_display,
    parse_emotion_segments,
    strip_emotion_tags,
)
from performance.metrics_logger import log_metrics
from streaming.llm_streamer import stream_llm
from streaming.tts_streamer import stream_tts_from_tokens

logger = logging.getLogger(__name__)

# ── Tool-query keywords (drives Mode A vs Mode B routing) ────────────────────
_TOOL_KEYWORDS = frozenset({
    "time", "clock", "timezone", "zone", "date", "today", "now",
    "search", "find", "news", "latest", "headline", "tell me about",
    "what is", "who is", "weather",
    "read", "fetch", "open", "webpage", "website",
    "save", "note", "append", "store", "write",
    "file", "folder", "directory",
    "play", "pause", "stop", "volume", "music",
})


def _needs_tools(text: str) -> bool:
    """Return True when the query should route to the agent (Mode A)."""
    q = text.lower()
    return any(kw in q for kw in _TOOL_KEYWORDS)


async def _text_to_token_stream(text: str) -> AsyncIterator[str]:
    """Yield a synthesized text string as a token stream for TTS."""
    # Chunk at word boundaries so TTS adaptive chunker works naturally
    words = text.split()
    chunk: list[str] = []
    for word in words:
        chunk.append(word)
        if len(chunk) >= 6:
            yield " ".join(chunk) + " "
            chunk = []
            await asyncio.sleep(0)
    if chunk:
        yield " ".join(chunk)


def _set_state(state_controller: Optional[AssistantStateController], state: AssistantState, visual_feedback: bool) -> None:
    if state_controller is None:
        return
    state_controller.set_state(state)
    if visual_feedback:
        logger.info(state_controller.visual_label())


def _resolve_emotional_context(emotion_engine: EmotionEngine, state_obj: Any) -> str:
    if hasattr(emotion_engine, "emotional_context"):
        try:
            return str(emotion_engine.emotional_context())
        except Exception:  # pylint: disable=broad-except
            pass
    if hasattr(state_obj, "emotional_context"):
        return str(state_obj.emotional_context())
    return "User sentiment: neutral. Conversation depth: 0."


class PipelineRequest(BaseModel):
    text: str


class PipelineResult(BaseModel):
    intent: str
    assistant_text: str
    assistant_markdown: Optional[str] = None  # raw LLM output with markdown intact
    tts_status: Optional[int]
    timings_ms: Dict[str, float]
    emotional_context: Optional[str] = None
    memories_used: Optional[str] = None


async def _post_json(client: httpx.AsyncClient, url: str, payload: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
    response = await client.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


async def _call_intent(client: httpx.AsyncClient, text: str) -> Tuple[str, float]:
    start = time.perf_counter()
    settings = get_settings()
    host = settings.intent_host
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    url = f"{host}:{settings.intent_port}" if host.startswith("http") else f"http://{host}:{settings.intent_port}"
    try:
        data = await _post_json(client, f"{url}/classify", {"text": text})
        intent = data.get("label", "unknown")
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Intent service unavailable, defaulting to chat: %s", exc)
        intent = "chat"
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(json.dumps({"stage": "intent", "intent": intent, "intent_ms": round(elapsed_ms, 2)}))
    return intent, elapsed_ms


async def _call_agent(text: str) -> Tuple[str, float]:
    """Mode A: call agent loop (non-stream) and return synthesized text."""
    start = time.perf_counter()
    settings = get_settings()
    host = settings.llm_host
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    url = f"http://{host}:{settings.llm_port}"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            data = await _post_json(client, f"{url}/agent/loop", {"prompt": text, "max_steps": 4}, timeout=60.0)
        response_text = str(data.get("response", "")).strip()
        if not response_text:
            response_text = "I was unable to find an answer using the available tools."
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[pipeline] agent call failed, falling back to LLM stream: %s", exc)
        response_text = ""
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(json.dumps({"stage": "agent", "agent_ms": round(elapsed_ms, 2)}))
    return response_text, elapsed_ms


async def _call_llm(client: httpx.AsyncClient, prompt: str) -> Tuple[str, float]:
    start = time.perf_counter()
    settings = get_settings()
    host = settings.llm_host
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    url = f"{host}:{settings.llm_port}" if host.startswith("http") else f"http://{host}:{settings.llm_port}"
    data = await _post_json(client, f"{url}/generate", {"prompt": prompt})
    response_text = data.get("response", "")
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(json.dumps({"stage": "llm", "llm_ms": round(elapsed_ms, 2)}))
    return response_text, elapsed_ms


async def _collect_llm_stream(prompt: str) -> Tuple[str, float]:
    """Consume streamed LLM tokens and return full text + elapsed latency."""
    start = time.perf_counter()
    parts: list[str] = []
    async for token in stream_llm(prompt, generation_id=0):
        parts.append(token)
    text = "".join(parts)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(json.dumps({"stage": "llm_stream_collected", "llm_ms": round(elapsed_ms, 2)}))
    return text, elapsed_ms


async def _call_tts(client: httpx.AsyncClient, text: str) -> Tuple[Optional[int], float]:
    start = time.perf_counter()
    settings = get_settings()
    host = settings.tts_host
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    url = f"{host}:{settings.tts_port}" if host.startswith("http") else f"http://{host}:{settings.tts_port}"
    data = await _post_json(client, f"{url}/speak", {"text": text})
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(json.dumps({"stage": "tts", "tts_ms": round(elapsed_ms, 2)}))
    return data.get("backend_status"), elapsed_ms


async def run_pipeline(
    text: str,
    buffer: ConversationBuffer,
    memory_manager: Optional[MemoryManager] = None,
    emotion_engine: Optional[EmotionEngine] = None,
) -> PipelineResult:
    timings: Dict[str, float] = {"whisper_ms": 0.0, "intent_ms": 0.0, "llm_ms": 0.0, "tts_ms": 0.0, "embedding_ms": 0.0, "memory_ms": 0.0}
    intent = "unknown"
    assistant_text = ""
    tts_status: Optional[int] = None
    memories_used: Optional[str] = None
    emotional_context: Optional[str] = None

    memory_manager = memory_manager or MemoryManager()
    emotion_engine = emotion_engine or EmotionEngine()

    try:
        async with httpx.AsyncClient() as client:
            intent, intent_ms = await _call_intent(client, text)
            timings["intent_ms"] = intent_ms

            if intent != "chat":
                assistant_text = f"Intent '{intent}' not supported in this phase."
                buffer.add("user", text)
                buffer.add("assistant", assistant_text)
                return PipelineResult(intent=intent, assistant_text=assistant_text, tts_status=None, timings_ms=timings)

            # Memory retrieval
            mem_start = time.perf_counter()
            memories = memory_manager.retrieve(text)
            memories_used = memory_manager.format_memories(memories)
            timings["memory_ms"] = (time.perf_counter() - mem_start) * 1000

            # Emotion state update
            state_obj = emotion_engine.update(text)
            emotional_context = _resolve_emotional_context(emotion_engine, state_obj)

            prompt = build_prompt(buffer, text, emotional_state=emotional_context, retrieved_memories=memories_used)

            assistant_text, llm_ms = await _collect_llm_stream(prompt)
            timings["llm_ms"] = llm_ms

            # ── Emotion parsing ───────────────────────────────────────
            emotion_segs = parse_emotion_segments(assistant_text)
            logger.info("🎭 %s", format_emotion_display(emotion_segs))
            clean_text = strip_emotion_tags(assistant_text)

            prosody_text, _ = apply_prosody(clean_text)
            tts_status, tts_ms = await _call_tts(client, prosody_text)
            timings["tts_ms"] = tts_ms

            # Store memory after response (use clean text)
            embed_start = time.perf_counter()
            memory_manager.add_interaction(text, clean_text)
            timings["embedding_ms"] = (time.perf_counter() - embed_start) * 1000
            assistant_text = clean_text
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Pipeline failed: %s", exc)
        assistant_text = f"Service error: {exc}"
        return PipelineResult(intent=intent, assistant_text=assistant_text, tts_status=tts_status, timings_ms=timings, emotional_context=emotional_context, memories_used=memories_used)

    buffer.add("user", text)
    buffer.add("assistant", assistant_text)

    log_metrics({
        "whisper_ms": timings.get("whisper_ms"),
        "intent_ms": timings.get("intent_ms"),
        "llm_ms": timings.get("llm_ms"),
        "tts_ms": timings.get("tts_ms"),
        "memory_ms": timings.get("memory_ms"),
        "embedding_ms": timings.get("embedding_ms"),
    })

    return PipelineResult(
        intent=intent,
        assistant_text=assistant_text,
        assistant_markdown=assistant_text,
        tts_status=tts_status,
        timings_ms=timings,
        emotional_context=emotional_context,
        memories_used=memories_used,
    )


async def run_pipeline_streaming(
    text: str,
    buffer: "ConversationBuffer",
    interrupt_controller: Optional["InterruptController"] = None,
    state_controller: Optional["AssistantStateController"] = None,
    audio_listener: Optional["AudioListener"] = None,
    visual_feedback: bool = True,
    memory_manager: Optional[MemoryManager] = None,
    emotion_engine: Optional[EmotionEngine] = None,
    cancellation_event: Optional[asyncio.Event] = None,
    generation_id: int = 0,
    is_generation_current_fn=None,
) -> PipelineResult:
    """Run full pipeline with automatic Mode A / Mode B routing.

    Mode A (agent, non-stream): query contains tool keywords → agent loop
        → synthesized text → streamed to TTS via _text_to_token_stream
    Mode B (direct stream): conversational query → stream_llm → TTS stream

    Parameters
    ----------
    cancellation_event : asyncio.Event, optional
        When set, the pipeline aborts quickly.
    generation_id : int
        Monotonically increasing turn id from the RSM.
    is_generation_current_fn : callable, optional
        Returns True if this generation_id is still current.
    """
    timings: Dict[str, float] = {"whisper_ms": 0.0, "intent_ms": 0.0, "llm_ms": 0.0, "tts_ms": 0.0, "embedding_ms": 0.0, "memory_ms": 0.0}
    tts_status: Optional[int] = None
    memories_used: Optional[str] = None
    emotional_context: Optional[str] = None
    assistant_text = ""
    interrupted = False
    intent = "chat"

    memory_manager = memory_manager or MemoryManager()
    emotion_engine = emotion_engine or EmotionEngine()

    _set_state(state_controller, AssistantState.LISTENING, visual_feedback)

    def _is_cancelled() -> bool:
        if cancellation_event and cancellation_event.is_set():
            return True
        if interrupt_controller and interrupt_controller.is_triggered():
            return True
        if is_generation_current_fn and not is_generation_current_fn():
            return True
        return False

    # ── Intent ───────────────────────────────────────────────────────
    async with httpx.AsyncClient() as client:
        intent, intent_ms = await _call_intent(client, text)
        timings["intent_ms"] = intent_ms

    # ── Memory retrieval ─────────────────────────────────────────────
    mem_start = time.perf_counter()
    memories = memory_manager.retrieve(text)
    memories_used = memory_manager.format_memories(memories)
    timings["memory_ms"] = (time.perf_counter() - mem_start) * 1000

    # ── Emotion ──────────────────────────────────────────────────────
    state_obj = emotion_engine.update(text)
    emotional_context = _resolve_emotional_context(emotion_engine, state_obj)

    if _is_cancelled():
        logger.info("[pipeline] gen=%d cancelled before LLM", generation_id)
        _set_state(state_controller, AssistantState.IDLE, visual_feedback)
        return PipelineResult(intent=intent, assistant_text="", tts_status=None, timings_ms=timings)

    _set_state(state_controller, AssistantState.THINKING, visual_feedback)
    llm_start = time.perf_counter()

    # ── Route: Mode A (agent) vs Mode B (stream) ─────────────────────
    use_agent = _needs_tools(text)
    logger.info(json.dumps({"stage": "route", "generation_id": generation_id, "mode": "agent" if use_agent else "stream", "query": text[:80]}))

    if use_agent:
        # ── Mode A: agent loop (non-stream) → synthesized text → TTS ─
        agent_text, agent_ms = await _call_agent(text)
        timings["llm_ms"] = agent_ms

        if not agent_text:
            # Agent failed — fall through to Mode B with full prompt
            use_agent = False
            logger.info("[pipeline] gen=%d agent returned empty, falling back to stream", generation_id)

        if agent_text and not _is_cancelled():
            assistant_text = agent_text
            clean_text = strip_emotion_tags(assistant_text)

            _set_state(state_controller, AssistantState.SPEAKING, visual_feedback)
            tts_start = time.perf_counter()

            # Stream synthesized agent text to TTS (same path as Mode B)
            try:
                tts_result = await stream_tts_from_tokens(
                    _text_to_token_stream(clean_text),
                    interrupt_controller=interrupt_controller,
                    cancellation_event=cancellation_event,
                    generation_id=generation_id,
                    is_generation_current_fn=is_generation_current_fn,
                )
                tts_status = 200 if tts_result == "completed" else None
                if tts_result == "interrupted":
                    interrupted = True
                    _set_state(state_controller, AssistantState.INTERRUPTED, visual_feedback)
            except asyncio.CancelledError:
                interrupted = True
                _set_state(state_controller, AssistantState.INTERRUPTED, visual_feedback)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("[pipeline] agent TTS stream error: %s", exc)

            timings["tts_ms"] = (time.perf_counter() - tts_start) * 1000

    if not use_agent:
        # ── Mode B: direct LLM stream → TTS stream ───────────────────
        prompt = build_prompt(buffer, text, emotional_state=emotional_context, retrieved_memories=memories_used)
        llm_done_ms: Optional[float] = None

        async def token_iter():
            nonlocal assistant_text, llm_done_ms
            try:
                async for token in stream_llm(prompt, cancellation_event=cancellation_event, generation_id=generation_id):
                    if _is_cancelled():
                        logger.debug("[pipeline] gen=%d token_iter cancelled", generation_id)
                        break
                    assistant_text += token
                    yield token
            finally:
                if llm_done_ms is None:
                    llm_done_ms = (time.perf_counter() - llm_start) * 1000

        _set_state(state_controller, AssistantState.SPEAKING, visual_feedback)
        tts_start = time.perf_counter()

        try:
            tts_result = await stream_tts_from_tokens(
                token_iter(),
                interrupt_controller=interrupt_controller,
                cancellation_event=cancellation_event,
                generation_id=generation_id,
                is_generation_current_fn=is_generation_current_fn,
            )
            tts_status = 200 if tts_result == "completed" else None
            if tts_result == "interrupted":
                interrupted = True
                _set_state(state_controller, AssistantState.INTERRUPTED, visual_feedback)
        except asyncio.CancelledError:
            interrupted = True
            _set_state(state_controller, AssistantState.INTERRUPTED, visual_feedback)
        except Exception as exc:
            logger.error("Streaming pipeline error: %s", exc)

        if llm_done_ms is None:
            llm_done_ms = (time.perf_counter() - llm_start) * 1000
        timings["llm_ms"] = llm_done_ms
        timings["tts_ms"] = (time.perf_counter() - tts_start) * 1000

    if interrupted:
        _set_state(state_controller, AssistantState.INTERRUPTED, visual_feedback)

    # ── Emotion parsing + clean text ─────────────────────────────────
    raw_markdown = assistant_text
    if assistant_text.strip():
        emotion_segs = parse_emotion_segments(assistant_text)
        logger.info("🎭 %s", format_emotion_display(emotion_segs))
        clean_text = strip_emotion_tags(assistant_text)
    else:
        clean_text = assistant_text

    # ── Save to memory (non-fatal) ────────────────────────────────────
    if _is_cancelled():
        logger.info("[pipeline] gen=%d cancelled — skipping buffer/memory save", generation_id)
    else:
        if clean_text.strip():
            try:
                embed_start = time.perf_counter()
                memory_manager.add_interaction(text, clean_text)
                timings["embedding_ms"] = (time.perf_counter() - embed_start) * 1000
            except Exception as exc:
                logger.warning("Memory save failed (non-fatal): %s", exc)

        buffer.add("user", text)
        buffer.add("assistant", clean_text)
    assistant_text = clean_text

    _set_state(state_controller, AssistantState.IDLE, visual_feedback)

    return PipelineResult(
        intent=intent,
        assistant_text=assistant_text,
        assistant_markdown=raw_markdown,
        tts_status=tts_status,
        timings_ms=timings,
        emotional_context=emotional_context,
        memories_used=memories_used,
    )
