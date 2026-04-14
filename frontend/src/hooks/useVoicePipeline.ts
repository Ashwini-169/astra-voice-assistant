import { useEffect, useRef, useCallback } from 'react';
import { useAgentStore } from '../core/state/agentStore';
import { useLLMStream } from './useLLMStream';
import { BrowserASR } from '../core/asr/browserASR';
import { MicRecorder } from '../core/audio/mic';
import { BrowserVAD } from '../core/vad/browserVAD';
import { transcribeAudio } from '../core/api/whisper';

/**
 * Full Duplex Voice Pipeline
 * ==========================
 *
 * Zero Human Intervention Architecture:
 *
 *   [Page Load / Duplex Enable]
 *      ↓
 *   Start Mic + VAD + ASR
 *      ↓
 *   ASR partial transcript (3+ words, 600ms debounce)
 *      ↓  (early start — don't wait for isFinal)
 *   LLM /generate (stream=true)
 *      ↓
 *   Token stream → sentence buffer → TTS /synthesize
 *      ↓
 *   AudioPlayer playback → onQueueEmpty
 *      ↓
 *   Auto-restart listening → (loop forever)
 *
 * Barge-in: VAD speech-start during speaking → interrupt → relisten
 */

const PARTIAL_WORD_THRESHOLD = 3;
const PARTIAL_DEBOUNCE_MS = 600;

export const useVoicePipeline = () => {
  const { setTranscript, setPartialTranscript, setState, softReset, addMessage, setMicRms, duplexEnabled } = useAgentStore();
  const { handleStream, interrupt, setOnStreamComplete } = useLLMStream();

  const asrRef = useRef<BrowserASR | null>(null);
  const micRef = useRef<MicRecorder | null>(null);
  const vadRef = useRef<BrowserVAD | null>(null);
  const usingWhisperRef = useRef(false);
  const activeRef = useRef(false);
  const processingRef = useRef(false);
  const partialTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastPartialRef = useRef('');

  // ── Check Browser ASR support ──
  const isBrowserASRSupported = useCallback(() => {
    return !!(
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition
    );
  }, []);

  // ── Re-enter listening state (for duplex auto-loop) ──
  const reenterListening = useCallback(async () => {
    if (!activeRef.current || !duplexEnabled) return;

    console.log('[Pipeline] 🔄 Duplex auto-relisten');
    softReset();
    setState('listening');
    lastPartialRef.current = '';

    // Restart ASR
    if (isBrowserASRSupported() && asrRef.current) {
      asrRef.current.start();
    } else if (micRef.current) {
      try { await micRef.current.start(); } catch { /* ignore */ }
    }
  }, [duplexEnabled, softReset, setState, isBrowserASRSupported]);

  // ── Handle Whisper transcription (called by VAD speech-end) ──
  const handleWhisperTranscription = useCallback(async () => {
    if (!micRef.current?.isRecording || processingRef.current) return;
    processingRef.current = true;

    console.log('[Pipeline] VAD speech-end → stopping mic, transcribing via Whisper');
    setState('thinking');

    try {
      const audioBlob = await micRef.current.stop();
      if (audioBlob.size < 1000) {
        console.warn('[Pipeline] Audio too short (<1KB), ignoring');
        setState('listening');
        processingRef.current = false;
        if (activeRef.current) {
          await micRef.current.start();
        }
        return;
      }

      const text = await transcribeAudio(audioBlob);
      if (text.trim()) {
        console.log('[Pipeline] Whisper transcribed:', text);
        setTranscript(text);
        addMessage({ role: 'user', content: text });
        handleStream(text);
      } else {
        console.warn('[Pipeline] Whisper returned empty');
        setState('listening');
        if (activeRef.current) {
          await micRef.current.start();
        }
      }
    } catch (e) {
      console.error('[Pipeline] Whisper transcription failed:', e);
      setState('error');
      setTimeout(() => {
        setState('listening');
        if (activeRef.current) micRef.current?.start().catch(() => {});
      }, 1500);
    }
    processingRef.current = false;
  }, [setState, setTranscript, handleStream, addMessage]);

  // ── Handle barge-in (VAD detected speech while Astra is speaking) ──
  const handleBargeIn = useCallback(async () => {
    const currentState = useAgentStore.getState().state;
    if (currentState !== 'speaking' && currentState !== 'thinking') return;

    console.log('[Pipeline] ⚡ VAD barge-in detected → interrupting');
    await interrupt();
    softReset();
    setState('listening');
    lastPartialRef.current = '';

    // Restart ASR/recording for the new utterance
    if (activeRef.current) {
      if (isBrowserASRSupported() && asrRef.current) {
        asrRef.current.start();
      } else if (micRef.current) {
        try { await micRef.current.start(); } catch { /* ignore */ }
      }
    }
  }, [interrupt, softReset, setState, isBrowserASRSupported]);

  // ── Start Pipeline ──
  const startPipeline = useCallback(async () => {
    const currentState = useAgentStore.getState().state;
    if (currentState !== 'idle') return;

    softReset();
    setState('listening');
    activeRef.current = true;
    processingRef.current = false;
    lastPartialRef.current = '';

    // Register the auto-relisten callback on the LLM stream hook
    setOnStreamComplete(reenterListening);

    // Initialize VAD with backend-matched defaults
    if (!vadRef.current) {
      vadRef.current = new BrowserVAD();
    }

    // Setup VAD callbacks
    vadRef.current
      .on('speech-end', async () => {
        const currentState = useAgentStore.getState().state;
        if (currentState !== 'listening') return;

        if (usingWhisperRef.current) {
          await handleWhisperTranscription();
        }
      })
      .on('speech-start', () => {
        const currentState = useAgentStore.getState().state;
        if (currentState === 'speaking' || currentState === 'thinking') {
          handleBargeIn();
        }
      })
      .on('vad', (isSpeech: boolean) => {
        // Feed normalized RMS to the store for orb animation
        if (vadRef.current) {
          const diag = vadRef.current.getDiagnostics();
          // Normalize maxRms from 16-bit scale (0-32767) to 0-1
          const normalized = Math.min(1, diag.maxRms / 3000);
          setMicRms(isSpeech ? normalized : 0);
        }
      });

    // Start VAD (always runs for barge-in detection)
    await vadRef.current.start();

    if (isBrowserASRSupported()) {
      console.log('[Pipeline] Using Browser ASR + VAD (duplex)');
      usingWhisperRef.current = false;

      if (!asrRef.current) {
        asrRef.current = new BrowserASR(
          (text: string, isFinal: boolean) => {
            const currentState = useAgentStore.getState().state;

            if (currentState === 'speaking') {
              handleBargeIn();
              return;
            }

            if (currentState !== 'listening') return;

            if (isFinal && text.trim()) {
              // Final transcript — cancel any pending partial timer
              if (partialTimerRef.current) {
                clearTimeout(partialTimerRef.current);
                partialTimerRef.current = null;
              }
              console.log('[Pipeline] Browser ASR final:', text);
              setTranscript(text);
              setPartialTranscript('');
              asrRef.current?.stop();
              addMessage({ role: 'user', content: text });
              handleStream(text);
            } else if (!isFinal && text.trim()) {
              // Interim/partial transcript
              setPartialTranscript(text);
              lastPartialRef.current = text;

              // Early LLM start: if 3+ words and debounce passes
              const wordCount = text.trim().split(/\s+/).length;
              if (wordCount >= PARTIAL_WORD_THRESHOLD) {
                if (partialTimerRef.current) {
                  clearTimeout(partialTimerRef.current);
                }
                partialTimerRef.current = setTimeout(() => {
                  const state = useAgentStore.getState().state;
                  if (state === 'listening' && lastPartialRef.current.trim()) {
                    console.log(`[Pipeline] ⚡ Early LLM start (${wordCount} words, ${PARTIAL_DEBOUNCE_MS}ms debounce)`);
                    const partialText = lastPartialRef.current;
                    setTranscript(partialText);
                    setPartialTranscript('');
                    asrRef.current?.stop();
                    addMessage({ role: 'user', content: partialText });
                    handleStream(partialText);
                  }
                  partialTimerRef.current = null;
                }, PARTIAL_DEBOUNCE_MS);
              }
            }
          },
          async (error: any) => {
            console.warn('[Pipeline] Browser ASR error:', error, '→ switching to Whisper');
            usingWhisperRef.current = true;
            if (activeRef.current) {
              if (!micRef.current) micRef.current = new MicRecorder();
              try { await micRef.current.start(); } catch (e) {
                console.error('[Pipeline] Mic access failed:', e);
              }
            }
          }
        );
      }

      asrRef.current.start();
    } else {
      console.log('[Pipeline] No Browser ASR → Whisper + VAD (auto-stop)');
      usingWhisperRef.current = true;
      if (!micRef.current) micRef.current = new MicRecorder();
      try {
        await micRef.current.start();
      } catch (e) {
        console.error('[Pipeline] Mic access denied:', e);
        setState('error');
        setTimeout(() => setState('idle'), 2000);
      }
    }
  }, [softReset, setState, isBrowserASRSupported, handleStream, setTranscript, setPartialTranscript, handleWhisperTranscription, handleBargeIn, setOnStreamComplete, reenterListening, addMessage, setMicRms]);

  // ── Stop Pipeline ──
  const stopPipeline = useCallback(async () => {
    activeRef.current = false;

    // Clear partial debounce timer
    if (partialTimerRef.current) {
      clearTimeout(partialTimerRef.current);
      partialTimerRef.current = null;
    }

    // Stop VAD
    vadRef.current?.stop();

    // Stop ASR
    asrRef.current?.stop();

    // Stop mic recording
    micRef.current?.cancel();

    const currentState = useAgentStore.getState().state;
    if (currentState === 'speaking' || currentState === 'thinking') {
      await interrupt();
    } else {
      setState('idle');
    }
  }, [setState, interrupt]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (partialTimerRef.current) clearTimeout(partialTimerRef.current);
      vadRef.current?.stop();
      asrRef.current?.stop();
      micRef.current?.cancel();
    };
  }, []);

  return { startPipeline, stopPipeline };
};
