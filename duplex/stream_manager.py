"""Response Stream Manager (RSM) for controlled duplex conversation.

Core guarantee
--------------
    ACTIVE_STREAM_COUNT <= 1

At any moment exactly zero or one response pipeline is running.  Before a
new turn starts the RSM cancels the previous stream, stops TTS audio, and
ensures the GPU lock is free.

Architecture
~~~~~~~~~~~~
::

    AudioListener (VAD) ──► InterruptController ──► RSM.cancel_active()
                                                          │
                                                          ▼
                                   stream.cancel_event.set()
                                          │          │
                                   LLM stops     TTS /stop

"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import httpx

from core.config import get_settings
from duplex.interrupt_controller import InterruptController

logger = logging.getLogger(__name__)


# ── Stream states ────────────────────────────────────────────────────────────

class StreamState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


_STATE_ICON = {
    StreamState.IDLE: "⚪",
    StreamState.LISTENING: "🔵",
    StreamState.THINKING: "🟡",
    StreamState.SPEAKING: "🟢",
    StreamState.INTERRUPTED: "🔴",
    StreamState.CANCELLED: "🟤",
}


# ── ResponseStream ──────────────────────────────────────────────────────────

@dataclass
class ResponseStream:
    """One turn of assistant response with cancellation support."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    generation_id: int = 0
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    state: StreamState = StreamState.IDLE
    created_at: float = field(default_factory=time.perf_counter)
    completed_at: float = 0.0
    result: Any = None

    # ── helpers ──────────────────────────────────────────────────────

    @property
    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    @property
    def is_done(self) -> bool:
        return self.state in (StreamState.IDLE, StreamState.CANCELLED)

    @property
    def elapsed_ms(self) -> float:
        end = self.completed_at or time.perf_counter()
        return (end - self.created_at) * 1000

    def cancel(self) -> None:
        """Signal all pipeline stages to abort."""
        self.cancel_event.set()
        self.state = StreamState.CANCELLED

    def complete(self, result: Any = None) -> None:
        self.result = result
        self.completed_at = time.perf_counter()
        self.state = StreamState.IDLE

    def visual(self) -> str:
        icon = _STATE_ICON.get(self.state, "?")
        return f"{icon} stream={self.id} gen={self.generation_id} state={self.state.value}"


# ── ResponseStreamManager ────────────────────────────────────────────────────

class ResponseStreamManager:
    """Ensures exactly one active response stream exists at a time.

    Usage in the duplex loop::

        rsm = ResponseStreamManager(interrupt_controller)

        # -- new utterance arrives --
        stream = await rsm.start_turn()        # creates & activates stream
        result  = await run_pipeline_streaming(
            ..., cancellation_event=stream.cancel_event, ...
        )
        rsm.complete_turn(result)

    If AudioListener triggers an interrupt while the pipeline is running,
    the duplex loop should call ``await rsm.cancel_active()`` which will:

    1. Set the cancel event on the active stream
    2. Trigger the InterruptController (so pipeline stops)
    3. POST /stop to the TTS service (stops MCI playback)
    """

    def __init__(self, interrupt_controller: InterruptController) -> None:
        self._interrupt = interrupt_controller
        self._active: Optional[ResponseStream] = None
        self._lock = asyncio.Lock()

        # generation counter — monotonically increasing
        self._generation_counter: int = 0

        # active-stream count — must never exceed 1
        self._active_stream_count: int = 0

        # stats
        self._total_turns = 0
        self._total_interrupts = 0

    @property
    def current_generation_id(self) -> int:
        """The generation id of the most recent turn."""
        return self._generation_counter

    def is_generation_current(self, gen_id: int) -> bool:
        """Return True only if *gen_id* matches the latest generation.

        Safe to call from any coroutine / callback.  Used by the TTS
        streamer to gate every ``/speak`` POST.
        """
        return gen_id == self._generation_counter

    @property
    def active_stream_count(self) -> int:
        """Number of streams currently in a non-terminal state.

        Invariant: must always be 0 or 1.
        """
        return self._active_stream_count

    # ── public queries ───────────────────────────────────────────────

    @property
    def state(self) -> StreamState:
        if self._active is None:
            return StreamState.IDLE
        return self._active.state

    @property
    def has_active(self) -> bool:
        return (
            self._active is not None
            and not self._active.is_cancelled
            and self._active.state not in (StreamState.IDLE, StreamState.CANCELLED)
        )

    @property
    def active_stream(self) -> Optional[ResponseStream]:
        return self._active

    # ── lifecycle ────────────────────────────────────────────────────

    async def cancel_active(self) -> bool:
        """Cancel the currently active stream.  Returns True if cancelled."""
        async with self._lock:
            if self._active is None or self._active.is_done:
                return False

            stream = self._active
            logger.info(
                "[RSM] ⚡ Cancelling stream %s gen=%d (state=%s, elapsed=%.0f ms)",
                stream.id,
                stream.generation_id,
                stream.state.value,
                stream.elapsed_ms,
            )
            stream.cancel()
            self._active_stream_count = max(0, self._active_stream_count - 1)
            self._total_interrupts += 1

        # Stop TTS playback (outside lock to avoid deadlock)
        await self._stop_tts_playback()
        return True

    async def start_turn(self) -> ResponseStream:
        """Cancel any prior stream and create a fresh one.

        Always stops TTS playback (even if previous stream completed
        normally) to prevent MCI audio overlap between turns.
        """
        await self.cancel_active()
        await self._stop_tts_playback()  # always stop — MCI may outlive stream
        self._interrupt.clear()

        async with self._lock:
            self._generation_counter += 1
            stream = ResponseStream(generation_id=self._generation_counter)
            stream.state = StreamState.THINKING  # active, not idle
            self._active = stream
            self._active_stream_count = 1  # exactly one active stream
            self._total_turns += 1

            if self._active_stream_count > 1:
                logger.error(
                    "[RSM] ☢ INVARIANT VIOLATION: active_stream_count=%d > 1!",
                    self._active_stream_count,
                )

            logger.info(
                "[RSM] ▶ Stream %s started (gen=%d, turn=%d, active=%d)",
                stream.id,
                stream.generation_id,
                self._total_turns,
                self._active_stream_count,
            )
            return stream

    def complete_turn(self, result: Any = None) -> None:
        """Mark current stream as done."""
        if self._active:
            self._active.complete(result)
            self._active_stream_count = max(0, self._active_stream_count - 1)
            logger.info(
                "[RSM] ✓ Stream %s gen=%d completed (%.0f ms, active=%d)",
                self._active.id,
                self._active.generation_id,
                self._active.elapsed_ms,
                self._active_stream_count,
            )

    # ── TTS stop ─────────────────────────────────────────────────────

    async def _stop_tts_playback(self) -> None:
        """POST /stop to the TTS service to kill MCI playback instantly."""
        settings = get_settings()
        host = settings.tts_host
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        url = (
            f"{host}:{settings.tts_port}"
            if host.startswith("http")
            else f"http://{host}:{settings.tts_port}"
        )
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(f"{url}/stop")
            logger.debug("[RSM] TTS stop sent")
        except Exception:  # pylint: disable=broad-except
            pass  # best-effort; TTS may not be running

    # ── stats ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "total_turns": self._total_turns,
            "total_interrupts": self._total_interrupts,
            "current_generation_id": self._generation_counter,
            "active_stream_count": self._active_stream_count,
            "active_stream": self._active.id if self._active else None,
            "state": self.state.value,
        }
