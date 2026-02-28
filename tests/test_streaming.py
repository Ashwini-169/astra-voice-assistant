import asyncio

import pytest

from duplex.interrupt_controller import InterruptController
from humanization.emotion_tagger import (
    EmotionStreamBuffer,
    EmotionSegment,
    parse_emotion_segments,
    strip_emotion_tags,
)
from streaming.tts_streamer import stream_tts


# ── EmotionStreamBuffer unit tests ───────────────────────────────────────────

def test_emotion_segment_parsing():
    segs = parse_emotion_segments("(laughing)Hahaha! (shocked)Wait really?")
    assert len(segs) == 2
    assert segs[0].emotion == "laughing"
    assert segs[0].text == "Hahaha!"
    assert segs[1].emotion == "shocked"
    assert segs[1].text == "Wait really?"


def test_strip_emotion_tags():
    assert strip_emotion_tags("(laughing)Hahaha! (shocked)What?!") == "Hahaha! What?!"
    assert strip_emotion_tags("<excited>Wow!") == "Wow!"


def test_emotion_stream_buffer_flushes_on_tag():
    buf = EmotionStreamBuffer()
    out = buf.feed("(excited)Wow yaar! ")
    # Pre-tag text (None emotion) -> nothing before tag, so nothing flushed yet
    out += buf.feed("(sad)Lekin...")
    # "Wow yaar!" with emotion=excited should have been flushed when we hit (sad)
    assert any(s.emotion == "excited" and "Wow" in s.text for s in out)


def test_emotion_stream_buffer_finish():
    buf = EmotionStreamBuffer()
    buf.feed("(confident)Jo bhi ho,")
    buf.feed(" dekh lenge.")
    remaining = buf.finish()
    assert remaining
    assert remaining[0].emotion == "confident"
    assert "dekh lenge" in remaining[0].text


def test_neutral_text_no_tags():
    segs = parse_emotion_segments("Just a normal sentence.")
    assert len(segs) == 1
    assert segs[0].emotion is None


# ── stream_tts integration test (mocked) ─────────────────────────────────────

@pytest.mark.anyio
async def test_stream_tts_calls_backend(monkeypatch):
    calls = []

    async def fake_send(seg: EmotionSegment, client) -> None:  # pylint: disable=unused-argument
        calls.append((seg.emotion, seg.text))

    monkeypatch.setattr("streaming.tts_streamer._send_tts_segment", fake_send)

    await stream_tts("(excited)Hello world! (relaxed)This is a test.", interrupt_flag=None)
    assert calls
    emotions = [c[0] for c in calls]
    assert "excited" in emotions


@pytest.mark.anyio
async def test_stream_interrupt_controller(monkeypatch):
    calls = []
    interrupt_controller = InterruptController()

    async def fake_send(seg, client):  # pylint: disable=unused-argument
        calls.append(seg)

    monkeypatch.setattr("streaming.tts_streamer._send_tts_segment", fake_send)

    interrupt_controller.trigger()
    status = await stream_tts("Hello interruption", interrupt_controller=interrupt_controller)

    assert status == "interrupted"
    assert calls == []
