const API_BASE = 'http://127.0.0.1:8002';

export const generateStream = async (
  query: string,
  provider: string,
  model: string,
  signal?: AbortSignal,
): Promise<Response> => {
  const payload: any = { prompt: query || "", stream: true };
  if (provider && provider !== 'default') payload.provider = provider;
  if (model && model !== 'default') payload.model = model;

  console.log("Generating with payload:", payload);

  return await fetch(`${API_BASE}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });
};

export const stopGeneration = async (): Promise<void> => {
  await fetch(`${API_BASE}/stop`, { method: 'POST' });
};

export interface AgentLoopResult {
  status: string;
  steps: any[];
  response: string;
}

export const callAgentLoop = async (
  query: string,
  provider: string,
  model: string,
  signal?: AbortSignal,
): Promise<AgentLoopResult> => {
  const payload: any = { prompt: query || "", max_steps: 5 };
  if (provider && provider !== 'default') payload.provider = provider;
  if (model && model !== 'default') payload.model = model;

  const res = await fetch(`${API_BASE}/agent/loop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });
  
  if (!res.ok) throw new Error(`Agent loop failed: ${res.status}`);
  const data = await res.json();
  return data;
};
