const API_BASE = 'http://127.0.0.1:8002';

export interface RawMetrics {
  requests: number;
  errors: number;
  error_rate: number;
  latency_ms: { avg: number; p95: number; samples: number };
  throughput: { tokens_total_est: number; tokens_per_sec_est: number };
}

export const fetchMetrics = async (): Promise<RawMetrics> => {
  const response = await fetch(`${API_BASE}/metrics`, { signal: AbortSignal.timeout(3000) });
  if (!response.ok) throw new Error(`Metrics fetch failed: ${response.status}`);
  return await response.json();
};
