const API_BASE = 'http://127.0.0.1:8004';

export const classifyIntent = async (text: string) => {
  const response = await fetch(`${API_BASE}/classify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) throw new Error('Intent classification failed');
  return await response.json();
};
