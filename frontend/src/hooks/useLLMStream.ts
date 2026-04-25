import { useCallback, useRef } from 'react';
import { generateStream, stopGeneration, callAgentLoop } from '../core/api/llm';
import { synthesizeAudio, stopTTS } from '../core/api/tts';
import { audioPlayer } from '../core/audio/player';
import { browserTTS } from '../core/audio/browserTTS';
import { useAgentStore } from '../core/state/agentStore';
import type { AppMode } from '../core/state/agentStore';
import { useSettingsStore } from '../core/state/settingsStore';
import { cleanForSpeech } from '../core/utils/cleanForSpeech';

/**
 * LLM Streaming hook — stream-versioned, preemptive.
 *
 * Architecture:
 *   - Every handleStream() call gets a unique streamId
 *   - Stale streams are silently discarded at every async boundary
 *   - TTS chunks carry their streamId and are rejected if stale
 *   - interrupt() is a mechanical kill switch — no state management
 *     (state transitions are owned by the FSM in useVoicePipeline)
 *   - onStreamComplete callback fires ONCE when TTS queue drains
 */
export const useLLMStream = () => {
  const { appendResponse, setState, setResponse, addMessage, setFirstTokenLatency } = useAgentStore();
  const { provider, model } = useSettingsStore();

  const currentStreamIdRef = useRef(0);
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
   * Synthesize and play a text chunk.
   * Silently aborts if streamId is stale (a newer request has started).
   */
  const speakInBrowser = useCallback(async (rawText: string, streamId: number) => {
    // Pre-check: bail if stale
    if (!rawText.trim() || abortRef.current || streamId !== currentStreamIdRef.current) return;

    const speechText = cleanForSpeech(rawText);
    if (!speechText.trim()) return;

    try {
      const audioBlob = await synthesizeAudio(speechText);

      // Post-check: bail if stale (synthesis took time, new stream may have started)
      if (streamId !== currentStreamIdRef.current || abortRef.current) return;

      if (audioBlob.size > 0) {
        await audioPlayer.play(audioBlob);
      }
    } catch (e) {
      console.warn('[TTS] Backend synthesize failed, falling back to browser TTS:', e);
      try {
        if (streamId === currentStreamIdRef.current && !abortRef.current) {
          await browserTTS.speak(speechText);
        }
      } catch (e2) {
        console.error('[TTS] Browser TTS also failed:', e2);
      }
    }
  }, []);

  const handleStream = useCallback(async (
    query: string,
    modeSnapshot?: AppMode,
    source: string = 'unknown',
    isFinal?: boolean,
  ) => {
    const trimmedQuery = query.trim();
    if (trimmedQuery.length < 3) {
      console.log('[Dispatch] ignored short text', { source, isFinal, text: trimmedQuery });
      return;
    }

    // ═══ PREEMPT: Kill any active stream + audio ═══
    currentStreamIdRef.current += 1;
    const streamId = currentStreamIdRef.current;
    const mode = modeSnapshot ?? useAgentStore.getState().mode;
    console.log('[Dispatch]', { mode, source, isFinal, text: trimmedQuery });

    abortRef.current = true;       // signal old speakInBrowser calls to bail
    audioPlayer.stop();            // destroy audio queue
    browserTTS.stop();             // kill browser speech
    controllerRef.current?.abort(); // abort old fetch

    // New stream state
    setState('thinking');
    setResponse('');
    bufferRef.current = '';
    abortRef.current = false;       // re-enable for new stream
    firstTokenRef.current = false;
    streamStartRef.current = performance.now();

    const controller = new AbortController();
    controllerRef.current = controller;
    audioPlayer.onQueueEmpty = null; // clear stale callback

    try {
      if (mode === 'agent') {
        console.log(`%c[LLM Agent] 🧠 Chat sent to LLM: "${query}"`, 'color: #0ea5e9; font-weight: bold;');
        const result = await callAgentLoop(trimmedQuery, provider, model, controller.signal);
        const finalResponse = result.response || "No response";
        
        // Output trace to Browser Console!
        if (result.steps && result.steps.length > 0) {
            result.steps.forEach((step: any) => {
                console.log(`%c[LLM Tool Execution] 🛠️ ${step.tool.server}/${step.tool.name}`, 'color: #f59e0b; font-weight: bold;');
                console.log('Arguments:', step.arguments);
                console.log('Result:', typeof step.result === 'object' && step.result.result ? step.result.result : step.result);
            });
        }
        
        // Stale check
        if (streamId !== currentStreamIdRef.current) return;
        setState('speaking');
        
        if (!firstTokenRef.current) {
          firstTokenRef.current = true;
          const latency = performance.now() - streamStartRef.current;
          setFirstTokenLatency(Math.round(latency));
          console.log(`%c[LLM Agent] ⚡ Total Request Latency: ${latency.toFixed(0)}ms`, 'color: #10b981; font-weight: bold;');
        }

        appendResponse(finalResponse);
        if (finalResponse.trim() && !abortRef.current && streamId === currentStreamIdRef.current) {
          addMessage({ role: 'assistant', content: finalResponse });
        }

        ttsQueueRef.current = ttsQueueRef.current.then(() =>
          speakInBrowser(finalResponse, streamId)
        );

        ttsQueueRef.current = ttsQueueRef.current.then(() => {
          if (abortRef.current || streamId !== currentStreamIdRef.current) return;
          if (audioPlayer.queueLength === 0) onCompleteRef.current?.();
          else audioPlayer.onQueueEmpty = () => { if (!abortRef.current && streamId === currentStreamIdRef.current) onCompleteRef.current?.(); };
        });
        return;
      }

      const response = await generateStream(trimmedQuery, provider, model, controller.signal);

      // Stale check: another handleStream may have fired while we awaited
      if (streamId !== currentStreamIdRef.current) return;

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

        // ═══ STALE GUARD: bail if a newer stream has started ═══
        if (streamId !== currentStreamIdRef.current) break;

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

              // Flush on sentence boundary or buffer length
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
                ttsQueueRef.current = ttsQueueRef.current.then(() =>
                  speakInBrowser(sentenceToSpeak, streamId)
                );
              }
            }
          } catch {
            // Non-JSON fallback
            appendResponse(line);
            fullResponse += line;
            bufferRef.current += line;
          }
        }
      }

      // Flush remaining buffer
      if (bufferRef.current.trim().length > 0 && !abortRef.current && streamId === currentStreamIdRef.current) {
        const remaining = bufferRef.current;
        bufferRef.current = '';
        ttsQueueRef.current = ttsQueueRef.current.then(() =>
          speakInBrowser(remaining, streamId)
        );
      }

      // Add assistant message to chat history
      if (fullResponse.trim() && !abortRef.current && streamId === currentStreamIdRef.current) {
        addMessage({ role: 'assistant', content: fullResponse });
      }

      // ═══ END-OF-STREAM: Wait for TTS queue to drain, then signal complete ═══
      ttsQueueRef.current = ttsQueueRef.current.then(() => {
        // Final stale check
        if (abortRef.current || streamId !== currentStreamIdRef.current) return;

        if (audioPlayer.queueLength === 0) {
          // Nothing queued → fire immediately
          onCompleteRef.current?.();
        } else {
          // Wait for last audio chunk to finish
          audioPlayer.onQueueEmpty = () => {
            if (!abortRef.current && streamId === currentStreamIdRef.current) {
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
  }, [provider, model, appendResponse, setState, setResponse, speakInBrowser, addMessage, setFirstTokenLatency]);

  /**
   * Mechanical kill switch.
   * Stops all audio, aborts LLM fetch, cancels backend generation.
   * Does NOT manage FSM state — that's the pipeline's job.
   */
  const interrupt = useCallback(async () => {
    abortRef.current = true;
    currentStreamIdRef.current += 1;
    audioPlayer.stop();
    browserTTS.stop();
    controllerRef.current?.abort();
    await Promise.all([
      stopGeneration().catch(() => {}),
      stopTTS().catch(() => {}),
    ]);
    // NOTE: No setState here. The FSM in useVoicePipeline owns all transitions.
  }, []);

  return { handleStream, interrupt, setOnStreamComplete };
};
