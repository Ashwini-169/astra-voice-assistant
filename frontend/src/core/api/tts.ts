const API_BASE = 'http://127.0.0.1:8003';

/**
 * Synthesize speech and return audio blob for browser playback.
 * Calls /synthesize which returns raw audio/mpeg bytes.
 */
export const synthesizeAudio = async (text: string, emotion?: string): Promise<Blob> => {
  const response = await fetch(`${API_BASE}/synthesize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, emotion }),
  });
  if (!response.ok) throw new Error(`TTS synthesize failed: ${response.status}`);
  return await response.blob();
};

/**
 * Stop server-side TTS playback (used by duplex mode).
 */
export const stopTTS = async (): Promise<void> => {
  await fetch(`${API_BASE}/stop`, { method: 'POST' }).catch(() => {});
};
