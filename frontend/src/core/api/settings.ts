const API_BASE = 'http://127.0.0.1:8002';

export const fetchProviders = async () => {
  const response = await fetch(`${API_BASE}/providers`);
  if (!response.ok) return [];
  return await response.json();
};

export const fetchModels = async (provider: string) => {
  const response = await fetch(`${API_BASE}/models?provider=${provider}`);
  if (!response.ok) return [];
  return await response.json();
};
