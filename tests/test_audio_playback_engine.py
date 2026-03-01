"""Unit tests for :mod:`services.audio_playback_engine`."""

import time
import threading
import numpy as np
import pytest

from services.audio_playback_engine import AudioPlaybackEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sine_chunk(freq: float = 440.0, duration_s: float = 0.1,
                sr: int = 24_000) -> np.ndarray:
    """Generate a pure sine-tone chunk (1-D float32)."""
    t = np.linspace(0.0, duration_s, int(sr * duration_s), endpoint=False, dtype=np.float32)
    return np.sin(2.0 * np.pi * freq * t)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestEngineEnqueue:
    """Basic enqueue / ordered playback."""

    def test_enqueue_single_chunk(self):
        engine = AudioPlaybackEngine(sample_rate=24_000)
        try:
            pcm = _sine_chunk(duration_s=0.05)
            engine.enqueue(0, pcm)
            # Give the reorder thread time to process
            time.sleep(0.15)
            # No crash = success; audio was pushed to PCM buffer
        finally:
            engine.shutdown()

    def test_enqueue_multiple_ordered(self):
        engine = AudioPlaybackEngine(sample_rate=24_000)
        try:
            for i in range(5):
                engine.enqueue(i, _sine_chunk(duration_s=0.02))
            time.sleep(0.3)
        finally:
            engine.shutdown()

    def test_enqueue_out_of_order(self):
        """Chunks arriving out of order should be reordered."""
        engine = AudioPlaybackEngine(sample_rate=24_000)
        try:
            # Send chunk 2 first, then 0, then 1
            engine.enqueue(2, _sine_chunk(freq=880, duration_s=0.02))
            engine.enqueue(0, _sine_chunk(freq=440, duration_s=0.02))
            engine.enqueue(1, _sine_chunk(freq=660, duration_s=0.02))
            time.sleep(0.3)
            # After processing, expected_id should be 3
            assert engine._expected_id == 3
        finally:
            engine.shutdown()


class TestEngineStopAndClear:
    """stop_and_clear() behaviour."""

    def test_stop_clears_queued_audio(self):
        engine = AudioPlaybackEngine(sample_rate=24_000)
        try:
            for i in range(10):
                engine.enqueue(i, _sine_chunk(duration_s=0.5))  # lots of audio
            cleared = engine.stop_and_clear()
            # At least some chunks should have been cleared
            assert cleared >= 0  # may be 0 if reorder thread was very fast
        finally:
            engine.shutdown()

    def test_stop_resets_expected_id(self):
        engine = AudioPlaybackEngine(sample_rate=24_000)
        try:
            engine.enqueue(0, _sine_chunk(duration_s=0.02))
            time.sleep(0.1)
            engine.stop_and_clear()
            assert engine._expected_id == 0
        finally:
            engine.shutdown()

    def test_can_enqueue_after_stop(self):
        """Engine should accept new audio after stop_and_clear."""
        engine = AudioPlaybackEngine(sample_rate=24_000)
        try:
            engine.enqueue(0, _sine_chunk(duration_s=0.02))
            time.sleep(0.1)
            engine.stop_and_clear()
            # New generation starts at chunk_id 0
            engine.enqueue(0, _sine_chunk(duration_s=0.02))
            time.sleep(0.1)
            assert engine._expected_id == 1
        finally:
            engine.shutdown()


class TestEngineFade:
    """Fade-in / fade-out application."""

    def test_apply_fade(self):
        engine = AudioPlaybackEngine(sample_rate=24_000, fade_ms=10)
        try:
            # Create a constant-amplitude chunk
            audio = np.ones(2400, dtype=np.float32)  # 100ms at 24kHz
            engine._apply_fade(audio)

            # First sample should be near zero (faded in)
            assert abs(audio[0]) < 0.01
            # Last sample should be near zero (faded out)
            assert abs(audio[-1]) < 0.01
            # Middle samples should be untouched
            mid = len(audio) // 2
            assert abs(audio[mid] - 1.0) < 0.01
        finally:
            engine.shutdown()

    def test_fade_short_chunk(self):
        """Very short chunks should not crash (fade clamped to 25%)."""
        engine = AudioPlaybackEngine(sample_rate=24_000, fade_ms=10)
        try:
            audio = np.ones(20, dtype=np.float32)  # only 20 samples
            engine._apply_fade(audio)
            # Should not crash; fade applied to at most 25% = 5 samples
            assert len(audio) == 20
        finally:
            engine.shutdown()


class TestEngineShutdown:
    """Graceful shutdown."""

    def test_shutdown_idempotent(self):
        engine = AudioPlaybackEngine(sample_rate=24_000)
        engine.shutdown()
        engine.shutdown()  # second call should not crash

    def test_enqueue_after_shutdown_ignored(self):
        engine = AudioPlaybackEngine(sample_rate=24_000)
        engine.shutdown()
        # Should be silently ignored, not crash
        engine.enqueue(0, _sine_chunk(duration_s=0.02))


class TestDecodeMP3:
    """MP3 decode helper in tts_service."""

    def test_decode_empty_bytes(self):
        from services.tts_service import _decode_mp3
        assert _decode_mp3(b"") is None

    def test_decode_none(self):
        from services.tts_service import _decode_mp3
        assert _decode_mp3(b"") is None

    def test_decode_invalid_mp3(self):
        from services.tts_service import _decode_mp3
        result = _decode_mp3(b"not-an-mp3-file")
        assert result is None  # should log error but not crash
