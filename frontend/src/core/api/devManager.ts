const API_BASE = 'http://127.0.0.1:3900';

export const checkHealth = async () => {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) throw new Error('Health check failed');
  return await response.json();
};

export const getStatus = async () => {
  const response = await fetch(`${API_BASE}/status`);
  if (!response.ok) throw new Error('Get status failed');
  return await response.json();
};

export const reloadService = async (serviceName: string) => {
  const response = await fetch(`${API_BASE}/reload/${serviceName}`, { method: 'POST' });
  if (!response.ok) throw new Error(`Reload ${serviceName} failed`);
  return await response.json();
};
