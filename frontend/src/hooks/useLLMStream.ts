import { useCallback, useRef } from 'react';
import { generateStream, stopGeneration } from '../core/api/llm';
import { synthesizeAudio, stopTTS } from '../core/api/tts';
import { audioPlayer } from '../core/audio/player';
import { browserTTS } from '../core/audio/browserTTS';
import { useAgentStore } from '../core/state/agentStore';
import { useSettingsStore } from '../core/state/settingsStore';
import { cleanForSpeech } from '../core/utils/cleanForSpeech';

export const useLLMStream = () => {
  const { appendResponse, setState } = useAgentStore();
  const { provider, model } = useSettingsStore();
  const bufferRef = useRef('');
  const abortRef = useRef(false);

  /**
   * Send buffered text to TTS and play audio in the browser.
   * Primary: backend /synthesize → MP3 → AudioPlayer
   * Fallback: browser speechSynthesis
   */
  const speakInBrowser = useCallback(async (rawText: string) => {
    if (!rawText.trim() || abortRef.current) return;
    // Strip markdown/symbols for speech, but UI keeps the original
    const speechText = cleanForSpeech(rawText);
    if (!speechText.trim()) return;
    try {
      const audioBlob = await synthesizeAudio(speechText);
      if (audioBlob.size > 0 && !abortRef.current) {
        await audioPlayer.play(audioBlob);
      }
    } catch (e) {
      console.warn('[TTS] Backend synthesize failed, falling back to browser TTS:', e);
      try {
        if (!abortRef.current) await browserTTS.speak(speechText);
      } catch (e2) {
        console.error('[TTS] Browser TTS also failed:', e2);
      }
    }
  }, []);

  const handleStream = useCallback(async (query: string) => {
    setState('thinking');
    bufferRef.current = '';
    abortRef.current = false;
    
    try {
      const response = await generateStream(query, provider, model);
      if (!response.ok) {
        console.error('LLM returned', response.status, await response.text());
        setState('idle');
        return;
      }
      if (!response.body) throw new Error('No readable stream');
      
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      
      setState('speaking');

      let leftover = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (abortRef.current) break;
        
        const chunk = decoder.decode(value, { stream: true });
        const lines = (leftover + chunk).split('\n');
        leftover = lines.pop() || '';

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const data = JSON.parse(line);
            const token = data.response || '';
            if (token) {
              appendResponse(token);
              bufferRef.current += token;
              
              // TTS sentence-boundary sync: flush on sentence-ending punctuation or buffer length
              const trimmed = bufferRef.current.trimEnd();
              if (
                trimmed.endsWith('.') ||
                trimmed.endsWith('?') ||
                trimmed.endsWith('!') ||
                trimmed.endsWith(':') ||
                bufferRef.current.length > 100
              ) {
                const sentenceToSpeak = bufferRef.current;
                bufferRef.current = '';
                // Fire and forget — don't await so LLM stream continues
                speakInBrowser(sentenceToSpeak);
              }
            }
          } catch (e) {
            // Not JSON — might be raw text from non-NDJSON backend
            appendResponse(line);
            bufferRef.current += line;
          }
        }
      }

      // Flush remaining buffer
      if (bufferRef.current.trim().length > 0 && !abortRef.current) {
        await speakInBrowser(bufferRef.current);
        bufferRef.current = '';
      }
      
      if (!abortRef.current) {
        setState('idle');
      }
    } catch (error) {
      console.error('Streaming error:', error);
      setState('idle');
    }
  }, [provider, model, appendResponse, setState, speakInBrowser]);

  const interrupt = useCallback(async () => {
    abortRef.current = true;
    setState('interrupting');
    audioPlayer.stop();
    browserTTS.stop();
    await Promise.all([
      stopGeneration().catch(() => {}),
      stopTTS().catch(() => {}),
    ]);
    setState('idle');
  }, [setState]);

  return { handleStream, interrupt };
};
