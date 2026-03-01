"""Continuous ordered audio playback engine with fade smoothing.

Replaces per-chunk MCI playback with a single persistent ``sounddevice``
output stream.  Designed for real-time voice AI pipelines where multiple
short TTS segments must play back-to-back without overlap, clicks, or
ordering artefacts.

4-Layer Architecture
~~~~~~~~~~~~~~~~~~~~
::

    /speak → edge-tts synthesis → MP3 decode → enqueue(chunk_id, PCM)
                                                        ↓
                                              ┌─ Reorder Thread ─┐
                                              │  (fade smoothing) │
                                              └────────┬──────────┘
                                                       ↓
                                                 [PCM ring buffer]
                                                       ↓
                                              ┌─ Audio Callback ─┐
                                              │  (sounddevice)    │
                                              └────────┬──────────┘
                                                       ↓
                                                    Speaker

Guarantees
~~~~~~~~~~
* Chunks play in strict ``chunk_id`` order (reorder buffer).
* No audio overlap — single output stream, sequential ordering.
* No clicks/pops — 10 ms fade-in / fade-out on every chunk.
* Continuous audio device — opened once, **never** closed per chunk.
* Thread-safe ``stop_and_clear()`` silences output within one callback
  period (~42 ms).
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
except ImportError:                       # graceful degradation
    sd = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ── Sentinel for queue shutdown ───────────────────────────────────────────────
_SHUTDOWN = object()


class AudioPlaybackEngine:
    """Thread-safe ordered audio playback with fade smoothing.

    Parameters
    ----------
    sample_rate : int
        Expected PCM sample rate (must match decoded audio).
    channels : int
        Number of output channels (1 = mono).
    fade_ms : int
        Linear fade-in / fade-out duration per chunk (milliseconds).
    block_size : int
        ``sounddevice`` callback block size in frames.
    """

    # ── Tunables ──────────────────────────────────────────────────────────────
    DEFAULT_SAMPLE_RATE = 24_000
    DEFAULT_CHANNELS    = 1
    DEFAULT_FADE_MS     = 10
    DEFAULT_BLOCK_SIZE  = 1024       # ~42 ms at 24 kHz
    DTYPE               = "float32"

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        fade_ms: int = DEFAULT_FADE_MS,
        block_size: int = DEFAULT_BLOCK_SIZE,
    ) -> None:
        self._sr        = sample_rate
        self._channels  = channels
        self._block     = block_size
        self._fade_n    = max(1, int(fade_ms * sample_rate / 1000))

        # ── Input queue: (chunk_id, pcm_float32) pairs from /speak ────────
        self._in_q: queue.Queue = queue.Queue()

        # ── Reorder state (protected by _reorder_lock) ───────────────────
        self._expected_id: int                    = 0
        self._reorder: dict[int, np.ndarray]      = {}
        self._reorder_lock                        = threading.Lock()

        # ── PCM ring buffer consumed by the audio callback ───────────────
        # Each element is a 1-D float32 numpy array (one "slice").
        self._pcm_buf: collections.deque[np.ndarray] = collections.deque()
        self._buf_lock                                = threading.Lock()

        # ── Control ──────────────────────────────────────────────────────
        self._stop_flag  = threading.Event()
        self._active     = False          # True after first enqueue
        self._shutdown   = False
        self._stream: Optional[sd.OutputStream] = None

        # ── Reorder thread ───────────────────────────────────────────────
        self._thread = threading.Thread(
            target=self._reorder_loop, daemon=True, name="audio-reorder",
        )
        self._thread.start()
        logger.info(
            "[playback-engine] init  sr=%d  ch=%d  fade=%d samples  block=%d",
            sample_rate, channels, self._fade_n, block_size,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def enqueue(self, chunk_id: int, pcm_float32: np.ndarray) -> None:
        """Add a decoded audio chunk for ordered playback.

        *chunk_id* must start at **0** for each new TTS generation and
        increment by 1 for every successive segment.  Out-of-order
        arrivals are buffered and replayed in the correct sequence.

        *pcm_float32* is a **1-D** ``float32`` numpy array of mono
        samples at the engine's sample rate.
        """
        if self._shutdown:
            return
        if not self._active:
            self._open_stream()
            self._active = True
        self._in_q.put((chunk_id, pcm_float32))

    def stop_and_clear(self) -> int:
        """Stop playback instantly. Clear all queued audio.

        Returns the number of chunks that were discarded.  The engine
        remains open and can accept new audio via ``enqueue()`` immediately.
        """
        self._stop_flag.set()

        # 1. Drain the input queue
        cleared = 0
        while True:
            try:
                self._in_q.get_nowait()
                cleared += 1
            except queue.Empty:
                break

        # 2. Clear reorder buffer
        with self._reorder_lock:
            cleared += len(self._reorder)
            self._reorder.clear()
            self._expected_id = 0

        # 3. Clear PCM ring buffer (silences output on next callback)
        with self._buf_lock:
            self._pcm_buf.clear()

        self._stop_flag.clear()
        logger.info("[playback-engine] stop_and_clear → discarded %d chunk(s)", cleared)
        return cleared

    def shutdown(self) -> None:
        """Release the audio device.  Call once on process exit."""
        self._shutdown = True
        self._stop_flag.set()
        self._in_q.put(_SHUTDOWN)

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        logger.info("[playback-engine] shutdown complete")

    # ── Audio callback (real-time thread) ─────────────────────────────────────

    def _audio_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info,           # noqa: ANN001 – CData from sounddevice
        status,              # noqa: ANN001
    ) -> None:
        """Fill the output buffer from the PCM ring buffer.

        When the buffer is empty the callback writes silence — the audio
        device stays open, producing no click artefacts.
        """
        if status:
            logger.debug("[playback-engine] stream status: %s", status)

        flat = outdata.reshape(-1)          # shape → (frames * channels,)
        filled = 0

        # NOTE: we acquire the lock here; it is only contended when
        # stop_and_clear() runs (very infrequent).  The critical section
        # is kept tiny (just deque ops) to avoid blocking real-time audio.
        with self._buf_lock:
            while filled < frames and self._pcm_buf:
                chunk = self._pcm_buf[0]
                take = min(len(chunk), frames - filled)
                flat[filled: filled + take] = chunk[:take]
                if take < len(chunk):
                    # Put the remainder back in-place
                    self._pcm_buf[0] = chunk[take:]
                else:
                    self._pcm_buf.popleft()
                filled += take

        # Pad with silence if the buffer couldn't fill the frame
        if filled < frames:
            flat[filled:] = 0.0

    # ── Stream management ─────────────────────────────────────────────────────

    def _open_stream(self) -> None:
        """Lazily open the output stream on first ``enqueue()``."""
        if self._stream is not None:
            return
        if sd is None:
            logger.error(
                "[playback-engine] sounddevice not installed — audio output disabled"
            )
            return
        try:
            self._stream = sd.OutputStream(
                samplerate=self._sr,
                channels=self._channels,
                dtype=self.DTYPE,
                callback=self._audio_callback,
                blocksize=self._block,
            )
            self._stream.start()
            logger.info(
                "[playback-engine] output stream opened  sr=%d  block=%d",
                self._sr, self._block,
            )
        except Exception as exc:
            logger.error("[playback-engine] failed to open output stream: %s", exc)
            self._stream = None

    # ── Reorder loop (background thread) ──────────────────────────────────────

    def _reorder_loop(self) -> None:
        """Pull chunks from the input queue, reorder, fade, push to PCM buffer."""
        while True:
            try:
                item = self._in_q.get(timeout=0.1)
            except queue.Empty:
                continue

            if item is _SHUTDOWN:
                return                     # thread exit

            if self._stop_flag.is_set():
                continue                   # discard during stop

            chunk_id, pcm = item
            with self._reorder_lock:
                self._reorder[chunk_id] = pcm
                self._flush_ready()

    def _flush_ready(self) -> None:
        """Emit ready chunks in order.  Called with ``_reorder_lock`` held."""
        while self._expected_id in self._reorder:
            if self._stop_flag.is_set():
                return

            audio = self._reorder.pop(self._expected_id).astype(
                np.float32, copy=True,
            )
            cid = self._expected_id
            self._expected_id += 1

            # ── Apply fade-in / fade-out (click elimination) ─────────
            self._apply_fade(audio)

            # ── Push to PCM ring buffer in ~100 ms slices ────────────
            slice_len = max(1, self._sr // 10)
            with self._buf_lock:
                for i in range(0, len(audio), slice_len):
                    self._pcm_buf.append(audio[i: i + slice_len])

            logger.debug(
                "[playback-engine] chunk %d → %d samples (%.1f ms)",
                cid, len(audio), len(audio) / self._sr * 1000,
            )

    # ── Fade utility ──────────────────────────────────────────────────────────

    def _apply_fade(self, audio: np.ndarray) -> None:
        """In-place linear fade-in / fade-out.

        ``fade_n`` is clamped to at most 25 % of the chunk length so
        very short chunks (< 40 ms) still play correctly.
        """
        n = min(self._fade_n, len(audio) // 4)
        if n > 1:
            audio[:n]  *= np.linspace(0.0, 1.0, n, dtype=np.float32)
            audio[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
