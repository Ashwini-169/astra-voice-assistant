import { useEffect, useRef } from 'react';
import { fetchMetrics } from '../core/api/metrics';
import { useAgentStore } from '../core/state/agentStore';

/**
 * Polls GET /metrics every 3s and writes to agentStore.
 */
export const useMetrics = () => {
  const setMetrics = useAgentStore((s) => s.setMetrics);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const poll = async () => {
      try {
        const raw = await fetchMetrics();
        setMetrics({
          latencyMs: raw.latency_ms,
          tokensPerSec: raw.throughput.tokens_per_sec_est,
          requests: raw.requests,
          errors: raw.errors,
          errorRate: raw.error_rate,
        });
      } catch {
        // Backend unreachable — ignore silently
      }
    };

    // Initial fetch
    poll();
    intervalRef.current = setInterval(poll, 3000);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [setMetrics]);
};
