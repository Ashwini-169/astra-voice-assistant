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
