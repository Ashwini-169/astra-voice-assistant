"""Basic in-process metrics for LLM latency, throughput, and errors."""
import statistics
import threading
from collections import deque
from dataclasses import dataclass


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 4))


@dataclass
class _Totals:
    requests: int = 0
    errors: int = 0
    total_tokens: int = 0
    total_generation_seconds: float = 0.0


class LLMMetrics:
    def __init__(self, max_latency_samples: int = 512) -> None:
        self._lock = threading.Lock()
        self._totals = _Totals()
        self._latency_samples = deque(maxlen=max_latency_samples)

    def record_success(self, latency_seconds: float, response_text: str) -> None:
        tokens = _estimate_tokens(response_text)
        with self._lock:
            self._totals.requests += 1
            self._totals.total_tokens += tokens
            self._totals.total_generation_seconds += max(latency_seconds, 0.0)
            self._latency_samples.append(max(latency_seconds, 0.0))

    def record_error(self, latency_seconds: float = 0.0) -> None:
        with self._lock:
            self._totals.requests += 1
            self._totals.errors += 1
            self._latency_samples.append(max(latency_seconds, 0.0))

    def snapshot(self) -> dict:
        with self._lock:
            latencies = list(self._latency_samples)
            requests = self._totals.requests
            errors = self._totals.errors
            total_tokens = self._totals.total_tokens
            total_seconds = self._totals.total_generation_seconds

        avg_ms = statistics.mean(latencies) * 1000 if latencies else 0.0
        p95_ms = statistics.quantiles(latencies, n=20)[-1] * 1000 if len(latencies) >= 20 else avg_ms
        tokens_per_sec = (total_tokens / total_seconds) if total_seconds > 0 else 0.0

        return {
            "requests": requests,
            "errors": errors,
            "error_rate": (errors / requests) if requests else 0.0,
            "latency_ms": {
                "avg": round(avg_ms, 2),
                "p95": round(p95_ms, 2),
                "samples": len(latencies),
            },
            "throughput": {
                "tokens_total_est": total_tokens,
                "tokens_per_sec_est": round(tokens_per_sec, 2),
            },
        }


llm_metrics = LLMMetrics()

