const API_BASE = 'http://127.0.0.1:8001';

/**
 * Transcribe audio via Whisper backend.
 * The backend expects multipart form-data with field name "audio_file".
 */
export const transcribeAudio = async (audioBlob: Blob): Promise<string> => {
  const formData = new FormData();
  formData.append('audio_file', audioBlob, 'audio.webm');
  
  const response = await fetch(`${API_BASE}/transcribe`, {
    method: 'POST',
    body: formData,
  });
  
  if (!response.ok) throw new Error('Transcription failed');
  const data = await response.json();
  return data.text || '';
};
