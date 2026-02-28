import asyncio
import pytest
from duplex.interrupt_controller import InterruptController
from duplex.state_machine import AssistantState, AssistantStateController
from duplex.stream_manager import ResponseStreamManager, StreamState


def test_interrupt_controller_flag_cycle():
    controller = InterruptController()
    assert controller.is_triggered() is False
    controller.trigger()
    assert controller.is_triggered() is True
    controller.clear()
    assert controller.is_triggered() is False


def test_state_controller_transitions_and_visual_label():
    controller = AssistantStateController()
    assert controller.get_state() == AssistantState.IDLE

    controller.set_state(AssistantState.SPEAKING)
    assert controller.get_state() == AssistantState.SPEAKING
    assert "speaking" in controller.visual_label()


@pytest.mark.anyio
async def test_rsm_single_stream_guarantee():
    """RSM enforces ACTIVE_STREAM_COUNT <= 1."""
    ic = InterruptController()
    rsm = ResponseStreamManager(ic)
    assert rsm.state == StreamState.IDLE
    assert not rsm.has_active

    stream1 = await rsm.start_turn()
    assert stream1.id
    assert not stream1.is_cancelled
    stats = rsm.stats()
    assert stats["total_turns"] == 1

    # Starting a new turn cancels the previous
    stream2 = await rsm.start_turn()
    assert stream1.is_cancelled
    assert not stream2.is_cancelled
    assert rsm.active_stream is stream2
    stats = rsm.stats()
    assert stats["total_turns"] == 2

    rsm.complete_turn({"dummy": True})
    assert stream2.is_done


@pytest.mark.anyio
async def test_rsm_cancel_sets_event():
    """Cancelling a stream sets the cancel_event so LLM/TTS can check it."""
    ic = InterruptController()
    rsm = ResponseStreamManager(ic)
    stream = await rsm.start_turn()

    assert not stream.cancel_event.is_set()
    await rsm.cancel_active()
    assert stream.cancel_event.is_set()
    assert stream.state == StreamState.CANCELLED


@pytest.mark.anyio
async def test_rsm_generation_id_increments():
    """Each start_turn bumps the generation counter."""
    ic = InterruptController()
    rsm = ResponseStreamManager(ic)
    assert rsm.current_generation_id == 0

    s1 = await rsm.start_turn()
    assert s1.generation_id == 1
    assert rsm.current_generation_id == 1

    s2 = await rsm.start_turn()
    assert s2.generation_id == 2
    assert rsm.current_generation_id == 2
    # Previous stream should be cancelled
    assert s1.is_cancelled


# ── Speech normalizer tests ─────────────────────────────────────────────

from humanization.speech_normalizer import markdown_to_speech


def test_speech_normalizer_strips_bold_and_italic():
    assert markdown_to_speech("**hello** world") == "hello world"
    assert markdown_to_speech("*italic* text") == "italic text"
    assert markdown_to_speech("***both***") == "both"


def test_speech_normalizer_strips_code_blocks():
    text = "Before\n```python\nprint('hi')\n```\nAfter"
    result = markdown_to_speech(text)
    assert "```" not in result
    assert "print" not in result
    assert "Before" in result
    assert "After" in result


def test_speech_normalizer_strips_inline_code():
    assert markdown_to_speech("Use `pip install`") == "Use pip install"


def test_speech_normalizer_strips_headings():
    assert markdown_to_speech("## Heading\nBody") == "Heading\nBody"


def test_speech_normalizer_strips_bullets():
    text = "- item one\n- item two\n* item three"
    result = markdown_to_speech(text)
    assert "-" not in result
    assert "*" not in result
    assert "item one" in result


def test_speech_normalizer_strips_links():
    assert markdown_to_speech("[click here](http://example.com)") == "click here"


def test_speech_normalizer_handles_empty():
    assert markdown_to_speech("") == ""
    assert markdown_to_speech("   ") == ""


def test_speech_normalizer_stray_asterisks():
    """Lone asterisks that aren't part of markdown pairs get removed."""
    text = "* * * Some text * * *"
    result = markdown_to_speech(text)
    assert "*" not in result
    assert "Some text" in result


# ── Stream isolation tests (noisy-overlap fix) ──────────────────────────


@pytest.mark.anyio
async def test_rsm_is_generation_current():
    """is_generation_current() returns True only for the latest gen."""
    ic = InterruptController()
    rsm = ResponseStreamManager(ic)

    s1 = await rsm.start_turn()
    assert rsm.is_generation_current(s1.generation_id)
    assert not rsm.is_generation_current(0)

    s2 = await rsm.start_turn()
    assert rsm.is_generation_current(s2.generation_id)
    # s1's gen is now stale
    assert not rsm.is_generation_current(s1.generation_id)


@pytest.mark.anyio
async def test_rsm_active_stream_count_lifecycle():
    """active_stream_count tracks 0 → 1 (start) → 0 (complete)."""
    ic = InterruptController()
    rsm = ResponseStreamManager(ic)
    assert rsm.active_stream_count == 0

    await rsm.start_turn()
    assert rsm.active_stream_count == 1

    rsm.complete_turn({"ok": True})
    assert rsm.active_stream_count == 0


@pytest.mark.anyio
async def test_rsm_active_stream_count_never_exceeds_one():
    """Starting a new turn while one is active still keeps count <= 1."""
    ic = InterruptController()
    rsm = ResponseStreamManager(ic)

    await rsm.start_turn()
    assert rsm.active_stream_count == 1

    # start_turn cancels the old one, creates new → count stays 1
    await rsm.start_turn()
    assert rsm.active_stream_count == 1


@pytest.mark.anyio
async def test_rsm_stats_includes_stream_count():
    """stats() dict exposes active_stream_count."""
    ic = InterruptController()
    rsm = ResponseStreamManager(ic)
    await rsm.start_turn()
    s = rsm.stats()
    assert "active_stream_count" in s
    assert s["active_stream_count"] == 1


@pytest.mark.anyio
async def test_tts_send_segment_drops_stale_generation(monkeypatch):
    """_send_tts_segment silently drops segments when gen is stale."""
    from streaming.tts_streamer import _send_tts_segment
    from humanization.emotion_tagger import EmotionSegment

    sent_payloads: list = []

    async def _fake_post(self, url, **kwargs):
        sent_payloads.append(kwargs.get("json"))

        class FakeResp:
            status_code = 200
        return FakeResp()

    monkeypatch.setattr("httpx.AsyncClient.post", _fake_post)

    seg = EmotionSegment(text="Hello there friend!", emotion=None)
    async with __import__("httpx").AsyncClient() as client:
        # is_generation_current_fn returns False → segment should be dropped
        await _send_tts_segment(seg, client, generation_id=1,
                                is_generation_current_fn=lambda: False)
    assert len(sent_payloads) == 0, "Stale segment should have been dropped"


@pytest.mark.anyio
async def test_tts_send_segment_sends_when_current(monkeypatch):
    """_send_tts_segment sends the segment when generation is current."""
    from streaming.tts_streamer import _send_tts_segment
    from humanization.emotion_tagger import EmotionSegment

    sent_payloads: list = []

    async def _fake_post(self, url, **kwargs):
        sent_payloads.append(kwargs.get("json"))

        class FakeResp:
            status_code = 200
        return FakeResp()

    monkeypatch.setattr("httpx.AsyncClient.post", _fake_post)

    seg = EmotionSegment(text="Hello there friend!", emotion=None)
    async with __import__("httpx").AsyncClient() as client:
        await _send_tts_segment(seg, client, generation_id=1,
                                is_generation_current_fn=lambda: True)
    assert len(sent_payloads) == 1, "Current segment should have been sent"


# ── EmotionStreamBuffer sentence-boundary chunking tests ─────────────

from humanization.emotion_tagger import EmotionStreamBuffer


def test_stream_buffer_sentence_flush_without_emotion_tags():
    """Long untagged text flushes at sentence boundaries, not only at finish()."""
    buf = EmotionStreamBuffer()
    # Feed enough text to exceed the flush threshold (~80 chars)
    text = "Once upon a time there was a humble woodcutter named Raj who lived nearby. He walked to the pond every day."
    segments = []
    for ch in text:
        segments.extend(buf.feed(ch))
    # Should have flushed at sentence boundary before finish()
    assert len(segments) >= 1, (
        f"Expected sentence-boundary flush for {len(text)}-char untagged text, got {len(segments)} segments"
    )
    # finish() flushes the remainder
    segments.extend(buf.finish())
    # Reassemble — should equal original text (modulo whitespace)
    combined = " ".join(s.text for s in segments)
    assert "woodcutter" in combined
    assert "pond" in combined


def test_stream_buffer_no_premature_flush():
    """Short text (<80 chars) should NOT flush until finish()."""
    buf = EmotionStreamBuffer()
    text = "Hello world."
    segments = []
    for ch in text:
        segments.extend(buf.feed(ch))
    assert len(segments) == 0, "Short text should not flush during feed()"
    final = buf.finish()
    assert len(final) == 1
    assert "Hello world" in final[0].text


def test_stream_buffer_emotion_tags_still_work():
    """Emotion tags still produce segments (sentence flush doesn't interfere)."""
    buf = EmotionStreamBuffer()
    text = "(excited)Wow this is amazing! (sad)But then it ended."
    segments = []
    for ch in text:
        segments.extend(buf.feed(ch))
    segments.extend(buf.finish())
    # Should have at least 2 segments (one per emotion tag)
    assert len(segments) >= 2
    emotions = [s.emotion for s in segments if s.emotion]
    assert "excited" in emotions


def test_stream_buffer_multiple_sentence_flushes():
    """Very long untagged text produces multiple sentence-boundary flushes."""
    buf = EmotionStreamBuffer()
    # 3 sentences, each well over the threshold
    text = (
        "The quick brown fox jumped over the lazy dog near the river bank. "
        "Then the fox ran through the forest and found a hidden cave with treasure inside. "
        "Finally the fox returned home safely and lived happily ever after."
    )
    segments = []
    for ch in text:
        segments.extend(buf.feed(ch))
    segments.extend(buf.finish())
    # With 3 sentences totaling ~200 chars, should produce multiple segments
    assert len(segments) >= 2, f"Expected multiple flush segments, got {len(segments)}"
