import { useCallback, useRef } from 'react';
import { generateStream, stopGeneration } from '../core/api/llm';
import { synthesizeAudio, stopTTS } from '../core/api/tts';
import { audioPlayer } from '../core/audio/player';
import { browserTTS } from '../core/audio/browserTTS';
import { useAgentStore } from '../core/state/agentStore';
import { useSettingsStore } from '../core/state/settingsStore';
import { cleanForSpeech } from '../core/utils/cleanForSpeech';

/**
 * LLM Streaming hook with:
 * - First-token latency tracking
 * - Sentence-boundary TTS chunking with debounce
 * - onStreamComplete callback for duplex auto-relisten
 * - AbortController for cancelling stale streams
 */
export const useLLMStream = () => {
  const { appendResponse, setState, addMessage, setFirstTokenLatency } = useAgentStore();
  const { provider, model } = useSettingsStore();
  const bufferRef = useRef('');
  const abortRef = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);
  const ttsQueueRef = useRef<Promise<void>>(Promise.resolve());
  const streamStartRef = useRef(0);
  const firstTokenRef = useRef(false);
  const onCompleteRef = useRef<(() => void) | null>(null);

  /** Set callback fired when stream + TTS are fully done. */
  const setOnStreamComplete = useCallback((cb: (() => void) | null) => {
    onCompleteRef.current = cb;
  }, []);

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
    firstTokenRef.current = false;
    streamStartRef.current = performance.now();

    // Cancel any previous in-flight stream
    controllerRef.current?.abort();
    controllerRef.current = new AbortController();
    
    // Clear the queue empty callback to prevent old sessions from firing prematurely mid-stream
    audioPlayer.onQueueEmpty = null;
    
    try {
      const response = await generateStream(query, provider, model);
      if (!response.ok) {
        console.error('LLM returned', response.status, await response.text());
        setState('error');
        setTimeout(() => setState('idle'), 2000);
        return;
      }
      if (!response.body) throw new Error('No readable stream');
      
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      
      setState('speaking');

      let fullResponse = '';
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
              // Track first-token latency
              if (!firstTokenRef.current) {
                firstTokenRef.current = true;
                const latency = performance.now() - streamStartRef.current;
                setFirstTokenLatency(Math.round(latency));
                console.log(`[LLM] ⚡ First token in ${latency.toFixed(0)}ms`);
              }

              appendResponse(token);
              fullResponse += token;
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
                // Chain TTS calls to maintain order but don't block stream
                ttsQueueRef.current = ttsQueueRef.current.then(() =>
                  speakInBrowser(sentenceToSpeak)
                );
              }
            }
          } catch {
            // Not JSON — might be raw text from non-NDJSON backend
            appendResponse(line);
            fullResponse += line;
            bufferRef.current += line;
          }
        }
      }

      // Flush remaining buffer
      if (bufferRef.current.trim().length > 0 && !abortRef.current) {
        const remaining = bufferRef.current;
        bufferRef.current = '';
        ttsQueueRef.current = ttsQueueRef.current.then(() =>
          speakInBrowser(remaining)
        );
      }

      // Add assistant message to chat history
      if (fullResponse.trim() && !abortRef.current) {
        addMessage({ role: 'assistant', content: fullResponse });
      }
      
      // We wait for the API calls to finish queueing.
      // The actual transition to 'idle'/relisten happens in audioPlayer.onQueueEmpty.
      ttsQueueRef.current = ttsQueueRef.current.then(() => {
        if (abortRef.current) return;
        
        // If there's nothing playing/queued, transition immediately
        if (audioPlayer.queueLength === 0) {
          setState('idle');
          onCompleteRef.current?.();
        } else {
          // Otherwise, overwrite onQueueEmpty to transition when playback fully concludes
          audioPlayer.onQueueEmpty = () => {
            if (!abortRef.current) {
              setState('idle');
              onCompleteRef.current?.();
            }
          };
        }
      });

    } catch (error: any) {
      if (error?.name === 'AbortError') {
        console.log('[LLM] Stream aborted (stale)');
      } else {
        console.error('Streaming error:', error);
        setState('error');
        setTimeout(() => setState('idle'), 2000);
      }
    }
  }, [provider, model, appendResponse, setState, speakInBrowser, addMessage, setFirstTokenLatency]);

  const interrupt = useCallback(async () => {
    abortRef.current = true;
    setState('interrupting');
    audioPlayer.stop();
    browserTTS.stop();
    controllerRef.current?.abort();
    await Promise.all([
      stopGeneration().catch(() => {}),
      stopTTS().catch(() => {}),
    ]);
    setState('idle');
  }, [setState]);

  return { handleStream, interrupt, setOnStreamComplete };
};
