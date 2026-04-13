const API_BASE = 'http://127.0.0.1:8002';

export const generateStream = async (query: string, provider: string, model: string): Promise<Response> => {
  const payload: any = { prompt: query || "", stream: true };
  if (provider && provider !== 'default') payload.provider = provider;
  if (model && model !== 'default') payload.model = model;

  console.log("Generating with payload:", payload);

  return await fetch(`${API_BASE}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
};

export const stopGeneration = async (): Promise<void> => {
  await fetch(`${API_BASE}/stop`, { method: 'POST' });
};
