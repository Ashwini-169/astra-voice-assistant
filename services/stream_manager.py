"""Thread-safe stream cancellation registry."""
import threading
from typing import Dict


class StreamManager:
    def __init__(self) -> None:
        self._active_stream_cancellations: Dict[str, threading.Event] = {}
        self._streams_lock = threading.Lock()

    def register(self, request_id: str) -> threading.Event:
        event = threading.Event()
        with self._streams_lock:
            self._active_stream_cancellations[request_id] = event
        return event

    def finish(self, request_id: str) -> None:
        with self._streams_lock:
            self._active_stream_cancellations.pop(request_id, None)

    def stop_all(self) -> int:
        cancelled = 0
        with self._streams_lock:
            for event in self._active_stream_cancellations.values():
                event.set()
                cancelled += 1
        return cancelled


stream_manager = StreamManager()

