"""Capture a single speech utterance from microphone using VAD."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import queue
import time
from io import BytesIO
from typing import Optional
import wave

import numpy as np

from duplex.vad_engine import VADEngine

try:
    import sounddevice as sd  # type: ignore
except Exception:  # pylint: disable=broad-except
    sd = None

logger = logging.getLogger(__name__)


@dataclass
class CaptureDiagnostics:
    total_frames: int = 0
    speech_frames: int = 0
    started: bool = False
    avg_rms: float = 0.0
    max_rms: float = 0.0
    duration_sec: float = 0.0
    error: str = ""


class SpeechCapture:
    def __init__(
        self,
        vad_engine: VADEngine,
        sample_rate: int = 16000,
        channels: int = 1,
        frame_ms: int = 30,
        silence_ms_to_stop: int = 900,
        max_record_seconds: float = 12.0,
    ) -> None:
        self._vad = vad_engine
        self._sample_rate = sample_rate
        self._channels = channels
        self._frame_ms = frame_ms
        self._silence_frames_to_stop = max(1, silence_ms_to_stop // frame_ms)
        self._max_record_seconds = max_record_seconds

    @property
    def available(self) -> bool:
        return sd is not None

    def capture_utterance_wav(self, wait_seconds: float = 10.0) -> Optional[bytes]:
        try:
            wav_bytes, _ = self.capture_utterance_wav_with_diagnostics(wait_seconds=wait_seconds)
            return wav_bytes
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Speech capture failed: %s", exc)
            return None

    def capture_utterance_wav_with_diagnostics(self, wait_seconds: float = 10.0) -> tuple[Optional[bytes], CaptureDiagnostics]:
        diagnostics = CaptureDiagnostics()
        if sd is None:
            return None, diagnostics

        blocksize = int(self._sample_rate * self._frame_ms / 1000)
        frames_queue: queue.Queue[bytes] = queue.Queue()
        started = False
        silence_run = 0
        collected: list[bytes] = []
        start_time = time.perf_counter()

        def callback(indata, _frames, _time_info, _status):
            pcm = self._to_pcm16_bytes(indata)
            frames_queue.put(pcm)

        try:
            with sd.InputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                blocksize=blocksize,
                callback=callback,
            ):
                while True:
                    try:
                        frame = frames_queue.get(timeout=0.2)
                    except queue.Empty:
                        if not started and (time.perf_counter() - start_time) > wait_seconds:
                            diagnostics.duration_sec = time.perf_counter() - start_time
                            diagnostics.started = started
                            return None, diagnostics
                        continue

                    diagnostics.total_frames += 1
                    rms = self._frame_rms(frame)
                    diagnostics.max_rms = max(diagnostics.max_rms, rms)
                    diagnostics.avg_rms = ((diagnostics.avg_rms * (diagnostics.total_frames - 1)) + rms) / diagnostics.total_frames
                    is_speech = self._vad.is_speech(frame, sample_rate=self._sample_rate)
                    if not started:
                        if is_speech:
                            started = True
                            diagnostics.speech_frames += 1
                            collected.append(frame)
                        continue

                    collected.append(frame)

                    if is_speech:
                        diagnostics.speech_frames += 1
                        silence_run = 0
                    else:
                        silence_run += 1

                    elapsed = time.perf_counter() - start_time
                    if silence_run >= self._silence_frames_to_stop or elapsed >= self._max_record_seconds:
                        break
        except Exception as exc:  # pylint: disable=broad-except
            diagnostics.duration_sec = time.perf_counter() - start_time
            diagnostics.started = started
            diagnostics.error = str(exc)
            logger.warning("Unable to open/read microphone input stream: %s", exc)
            return None, diagnostics

        diagnostics.duration_sec = time.perf_counter() - start_time
        diagnostics.started = started
        if not collected:
            return None, diagnostics

        return self._to_wav_bytes(b"".join(collected)), diagnostics

    @staticmethod
    def _to_pcm16_bytes(indata: np.ndarray) -> bytes:
        if indata.dtype == np.int16:
            return indata.tobytes()
        if np.issubdtype(indata.dtype, np.floating):
            clipped = np.clip(indata, -1.0, 1.0)
            return (clipped * 32767).astype(np.int16).tobytes()
        return indata.astype(np.int16).tobytes()

    def _to_wav_bytes(self, pcm_bytes: bytes) -> bytes:
        stream = BytesIO()
        with wave.open(stream, "wb") as wav_file:
            wav_file.setnchannels(self._channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(pcm_bytes)
        return stream.getvalue()

    @staticmethod
    def _frame_rms(frame: bytes) -> float:
        if not frame:
            return 0.0
        samples = np.frombuffer(frame, dtype=np.int16)
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
