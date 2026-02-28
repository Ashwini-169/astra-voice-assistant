"""Optional background microphone listener for VAD-based interruptions."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import numpy as np

from duplex.interrupt_controller import InterruptController
from duplex.vad_engine import VADEngine

try:
    import sounddevice as sd  # type: ignore
except Exception:  # pylint: disable=broad-except
    sd = None

logger = logging.getLogger(__name__)


class AudioListener:
    def __init__(
        self,
        vad_engine: VADEngine,
        interrupt_controller: InterruptController,
        on_vad: Optional[Callable[[bool], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> None:
        self._vad = vad_engine
        self._interrupt_controller = interrupt_controller
        self._on_vad = on_vad
        self._on_barge_in = on_barge_in
        self._sample_rate = sample_rate
        self._channels = channels
        self._stream = None
        self._enabled = threading.Event()
        self._started = False
        self._prev_speech = False  # tracks silence→speech edge

    @property
    def available(self) -> bool:
        return sd is not None

    def set_interrupt_window(self, enabled: bool) -> None:
        if enabled:
            self._enabled.set()
        else:
            self._enabled.clear()

    @property
    def on_barge_in(self) -> Optional[Callable[[], None]]:
        return self._on_barge_in

    @on_barge_in.setter
    def on_barge_in(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_barge_in = cb

    def _callback(self, indata, frames, time_info, status):  # pylint: disable=unused-argument
        if status:
            logger.debug("AudioListener status: %s", status)

        if indata is None or indata.size == 0:
            return

        pcm = self._to_pcm16_bytes(indata)
        is_speech = self._vad.is_speech(pcm, sample_rate=self._sample_rate)
        if self._on_vad is not None:
            self._on_vad(is_speech)

        # ── Early barge-in: silence→speech edge fires TTS stop ─────────
        if is_speech and not self._prev_speech:
            if self._on_barge_in is not None:
                try:
                    self._on_barge_in()
                except Exception:  # pylint: disable=broad-except
                    pass  # never crash the audio thread
        self._prev_speech = is_speech

        if self._enabled.is_set() and is_speech:
            self._interrupt_controller.trigger()

    @staticmethod
    def _to_pcm16_bytes(indata: np.ndarray) -> bytes:
        if indata.dtype == np.int16:
            return indata.tobytes()
        if np.issubdtype(indata.dtype, np.floating):
            clipped = np.clip(indata, -1.0, 1.0)
            return (clipped * 32767).astype(np.int16).tobytes()
        return indata.astype(np.int16).tobytes()

    def start(self) -> bool:
        if sd is None:
            logger.warning("sounddevice not installed; AudioListener disabled")
            return False
        if self._started:
            return True
        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            callback=self._callback,
            dtype="int16",
        )
        self._stream.start()
        self._started = True
        return True

    def stop(self) -> None:
        if not self._started:
            return
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        finally:
            self._stream = None
            self._started = False
