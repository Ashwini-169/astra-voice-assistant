import json
import time
from typing import Any, Dict

import pytest

from orchestrator.memory_buffer import ConversationBuffer
from orchestrator import pipeline


class _FakeMemory:
    def retrieve(self, query: str):  # pylint: disable=unused-argument
        return ["Memory 1"]

    def format_memories(self, memories):
        return " | ".join(memories)

    def add_interaction(self, user_text: str, assistant_text: str):  # pylint: disable=unused-argument
        return None


class _FakeEmotion:
    def update(self, text):  # pylint: disable=unused-argument
        class _State:
            def emotional_context(self):
                return "neutral"

        return _State()


@pytest.mark.anyio
async def test_pipeline_flow(monkeypatch):
    buffer = ConversationBuffer(max_turns=3)

    async def fake_post_json(_client, url: str, payload: Dict[str, Any], timeout: float = 15.0):  # pylint: disable=unused-argument
        if url.endswith("/classify"):
            return {"label": "chat"}
        if url.endswith("/speak"):
            return {"accepted": True, "backend_status": 200}
        raise RuntimeError("Unexpected URL")

    async def fake_stream_llm(prompt: str, **_kwargs):  # pylint: disable=unused-argument
        text = f"Echo: {prompt[:20]}"
        for tok in text.split(" "):
            yield tok + " "

    monkeypatch.setattr(pipeline, "_post_json", fake_post_json)
    monkeypatch.setattr(pipeline, "stream_llm", fake_stream_llm)

    start = time.perf_counter()
    result = await pipeline.run_pipeline("Hello there", buffer, memory_manager=_FakeMemory(), emotion_engine=_FakeEmotion())
    latency = time.perf_counter() - start

    print(f"pipeline latency: {latency:.3f}s")

    assert result.assistant_text
    assert buffer.get_history(), "memory buffer should be updated"
    assert json.loads(result.json())["timings_ms"]
