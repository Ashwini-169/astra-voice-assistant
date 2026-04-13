import asyncio

import pytest

from duplex.interrupt_controller import InterruptController
from humanization.emotion_tagger import (
    EmotionStreamBuffer,
    EmotionSegment,
    parse_emotion_segments,
    strip_emotion_tags,
)
from streaming.tts_streamer import stream_tts, stream_tts_from_tokens


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


def test_emotion_stream_buffer_word_boundary_flush_without_punctuation():
    buf = EmotionStreamBuffer()
    out = []
    for ch in "this stream should flush early without punctuation markers":
        out.extend(buf.feed(ch))
    assert out, "Expected early flush on word boundary for low-latency streaming"


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


@pytest.mark.anyio
async def test_stream_tts_from_tokens_starts_before_llm_finishes(monkeypatch):
    sent: list[str] = []
    first_send = asyncio.Event()

    async def fake_send(seg, client, **kwargs):  # pylint: disable=unused-argument
        sent.append(seg.text)
        first_send.set()

    async def fake_profile(client):  # pylint: disable=unused-argument
        from streaming.tts_streamer import ChunkProfile
        return ChunkProfile(initial_words=5, steady_words=8, max_chars=160)

    monkeypatch.setattr("streaming.tts_streamer._send_tts_segment", fake_send)
    monkeypatch.setattr("streaming.tts_streamer._fetch_chunk_profile", fake_profile)

    async def token_iter():
        # Long enough (with spaces) to trigger early word-boundary flush
        first_part = "this is a low latency streaming"
        for ch in first_part:
            yield ch
            await asyncio.sleep(0)
        # Keep stream open; TTS should already have started by now
        await asyncio.sleep(0.05)
        for ch in " pipeline test":
            yield ch

    stream_task = asyncio.create_task(stream_tts_from_tokens(token_iter()))
    await asyncio.wait_for(first_send.wait(), timeout=0.2)
    status = await stream_task

    assert status == "completed"
    assert sent


@pytest.mark.anyio
async def test_stream_tts_from_tokens_interrupts_early(monkeypatch):
    interrupt_controller = InterruptController()
    sent_count = 0

    async def fake_send(seg, client, **kwargs):  # pylint: disable=unused-argument
        nonlocal sent_count
        sent_count += 1
        interrupt_controller.trigger()

    monkeypatch.setattr("streaming.tts_streamer._send_tts_segment", fake_send)

    async def token_iter():
        for _ in range(500):
            yield "a"
            await asyncio.sleep(0)

    status = await stream_tts_from_tokens(
        token_iter(),
        interrupt_controller=interrupt_controller,
    )

    assert status == "interrupted"
    assert sent_count >= 1


@pytest.mark.anyio
async def test_stream_tts_from_tokens_coalesces_micro_segments(monkeypatch):
    sent_segments: list[str] = []

    async def fake_profile(client):  # pylint: disable=unused-argument
        from streaming.tts_streamer import ChunkProfile
        return ChunkProfile(initial_words=5, steady_words=8, max_chars=160)

    async def fake_send(seg, client, **kwargs):  # pylint: disable=unused-argument
        sent_segments.append(seg.text)

    monkeypatch.setattr("streaming.tts_streamer._fetch_chunk_profile", fake_profile)
    monkeypatch.setattr("streaming.tts_streamer._send_tts_segment", fake_send)

    async def token_iter():
        for ch in "this should sound natural and not break after every two words in playback":
            yield ch
            await asyncio.sleep(0)

    status = await stream_tts_from_tokens(token_iter())
    assert status == "completed"
    assert sent_segments
    assert all(len(seg.split()) >= 5 for seg in sent_segments[:-1])
