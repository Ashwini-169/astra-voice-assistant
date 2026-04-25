import { useEffect, useRef, useCallback } from 'react';
import { useAgentStore } from '../core/state/agentStore';
import type { AgentState } from '../core/state/agentStore';
import { useSettingsStore } from '../core/state/settingsStore';
import { useLLMStream } from './useLLMStream';
import { BrowserASR } from '../core/asr/browserASR';
import { MicRecorder } from '../core/audio/mic';
import { BrowserVAD } from '../core/vad/browserVAD';
import { transcribeAudio } from '../core/api/whisper';

/**
 * FSM Voice Pipeline
 * ==================
 *
 * Finite State Machine architecture for full-duplex voice.
 * Every event handler checks FSM state FIRST — no exceptions.
 *
 * States & Transitions:
 *   IDLE ──────→ LISTENING ──────→ THINKING ──────→ SPEAKING ──────→ IDLE
 *     ↑              │                                  │              │
 *     │              └── (empty ASR) ──→ IDLE           │              │
 *     │                                                 │              │
 *     │              LISTENING ←── INTERRUPTING ←───────┘              │
 *     │                                                                │
 *     └────────── (auto-relisten after cooldown) ──────────────────────┘
 *
 * Hard Rules:
 *   1. Every callback guards on FSM state FIRST
 *   2. No parallel LLM streams (stream versioning in useLLMStream)
 *   3. VAD runs continuously but speech-end only fires in LISTENING
 *   4. During SPEAKING, only strong RMS (≥900) can trigger barge-in
 *   5. Auto-relisten fires ONCE from IDLE, after POST_TTS_COOLDOWN_MS
 *   6. Empty ASR → IDLE dead-end (no mic restart loop)
 *   7. ASR callbacks are IGNORED during SPEAKING/THINKING/INTERRUPTING
 */

// ── Tuning Constants ──
const PARTIAL_WORD_THRESHOLD = 3;
const PARTIAL_DEBOUNCE_MS = 600;
const POST_TTS_COOLDOWN_MS = 500;     // silence after TTS before relisten
const INTERRUPT_LOCK_MS = 1500;       // prevent rapid re-interrupts

export const useVoicePipeline = () => {
  const {
    setTranscript, setPartialTranscript, setState,
    softReset, addMessage, setMicRms, duplexEnabled,
  } = useAgentStore();
  const { handleStream, interrupt, setOnStreamComplete } = useLLMStream();

  // ── Hardware Refs ──
  const asrRef = useRef<BrowserASR | null>(null);
  const micRef = useRef<MicRecorder | null>(null);
  const vadRef = useRef<BrowserVAD | null>(null);

  // ── Pipeline Control ──
  const activeRef = useRef(false);
  const usingWhisperRef = useRef(false);
  const partialTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastPartialRef = useRef('');
  const lastTTSEndRef = useRef<number>(0);
  const interruptLockRef = useRef(false);
  const relistenTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ═══════════════════════════════════════════════════════
  // FSM CORE
  // ═══════════════════════════════════════════════════════

  /** Read current FSM state (snapshot, not reactive). */
  const getState = useCallback((): AgentState => {
    return useAgentStore.getState().state;
  }, []);

  /** Transition with logging. The ONLY way state should change. */
  const transition = useCallback((to: AgentState, reason: string = '') => {
    const from = useAgentStore.getState().state;
    if (from === to) return; // no-op
    console.log(`[FSM] ${from} → ${to}${reason ? ` (${reason})` : ''}`);
    setState(to);
  }, [setState]);

  // ═══════════════════════════════════════════════════════
  // UTILITIES
  // ═══════════════════════════════════════════════════════

  const isBrowserASRSupported = useCallback(() => {
    return !!(
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition
    );
  }, []);

  /** Clear all pending timers. */
  const clearTimers = useCallback(() => {
    if (partialTimerRef.current) {
      clearTimeout(partialTimerRef.current);
      partialTimerRef.current = null;
    }
    if (relistenTimerRef.current) {
      clearTimeout(relistenTimerRef.current);
      relistenTimerRef.current = null;
    }
  }, []);

  /** Start ASR (Browser or Whisper mic). */
  const startASR = useCallback(async () => {
    if (isBrowserASRSupported() && asrRef.current) {
      try { asrRef.current.start(); } catch { /* already running */ }
    } else if (micRef.current) {
      try { await micRef.current.start(); } catch { /* ignore */ }
    }
  }, [isBrowserASRSupported]);

  const dispatchTranscript = useCallback((text: string, source: string, isFinal: boolean) => {
    const trimmedText = text.trim();
    if (trimmedText.length < 3) {
      console.log('[Voice Dispatch] ignored short text', { source, isFinal, text: trimmedText });
      return;
    }

    const modeSnapshot = useAgentStore.getState().mode;
    console.log('[Voice Dispatch]', { mode: modeSnapshot, source, isFinal, text: trimmedText });
    addMessage({ role: 'user', content: trimmedText });
    handleStream(trimmedText, modeSnapshot, source, isFinal);
  }, [addMessage, handleStream]);

  // ═══════════════════════════════════════════════════════
  // AUTO-RELISTEN (duplex loop)
  // ═══════════════════════════════════════════════════════

  const reenterListening = useCallback(async () => {
    if (!activeRef.current || !duplexEnabled) return;

    // HARD RULE: Only relisten from IDLE
    const state = getState();
    if (state !== 'idle') {
      console.log(`[FSM] 🚫 Relisten blocked (state=${state})`);
      return;
    }

    // Cooldown guard
    const elapsed = Date.now() - lastTTSEndRef.current;
    if (elapsed < POST_TTS_COOLDOWN_MS) {
      console.log(`[FSM] 🚫 Relisten blocked (cooldown ${elapsed}ms/${POST_TTS_COOLDOWN_MS}ms)`);
      // Schedule single retry after remaining cooldown
      if (!relistenTimerRef.current) {
        relistenTimerRef.current = setTimeout(() => {
          relistenTimerRef.current = null;
          reenterListening();
        }, POST_TTS_COOLDOWN_MS - elapsed);
      }
      return;
    }

    console.log('[FSM] 🔄 Auto-relisten');
    softReset();
    transition('listening', 'auto-relisten');
    lastPartialRef.current = '';
    await startASR();
  }, [duplexEnabled, softReset, transition, getState, startASR]);

  // ═══════════════════════════════════════════════════════
  // WHISPER TRANSCRIPTION (VAD speech-end path)
  // ═══════════════════════════════════════════════════════

  const handleWhisperTranscription = useCallback(async () => {
    if (!micRef.current?.isRecording) return;

    // GUARD: only from LISTENING
    if (getState() !== 'listening') return;

    console.log('[FSM] VAD speech-end → Whisper transcribe');
    transition('thinking', 'whisper');

    try {
      const audioBlob = await micRef.current.stop();

      if (audioBlob.size < 1000) {
        console.warn('[FSM] Audio too short (<1KB)');
        transition('listening', 'audio-too-short');
        if (activeRef.current) await micRef.current.start().catch(() => {});
        return;
      }

      const text = await transcribeAudio(audioBlob);

      if (text.trim().length >= 3) {
        console.log('[FSM] Whisper:', text);
        setTranscript(text);
        dispatchTranscript(text, 'whisper', true);
      } else {
        // DEAD END: no valid speech → idle, let auto-relisten handle re-entry
        console.warn(`[FSM] Whisper empty ("${text}") → dead end`);
        transition('idle', 'empty-transcript');
      }
    } catch (e) {
      console.error('[FSM] Whisper failed:', e);
      transition('error', 'whisper-error');
      setTimeout(() => transition('idle', 'error-recovery'), 1500);
    }
  }, [getState, transition, setTranscript, dispatchTranscript]);

  // ═══════════════════════════════════════════════════════
  // BARGE-IN (interrupt during SPEAKING)
  // ═══════════════════════════════════════════════════════

  const handleBargeIn = useCallback(async () => {
    // GUARD: interrupt lock (prevent rapid re-fire)
    if (interruptLockRef.current) return;

    // GUARD: only from SPEAKING or THINKING
    const state = getState();
    if (state !== 'speaking' && state !== 'thinking') return;

    console.log('[FSM] ⚡ Barge-in → INTERRUPTING');
    interruptLockRef.current = true;
    setTimeout(() => { interruptLockRef.current = false; }, INTERRUPT_LOCK_MS);

    // 1. Transition to interrupting
    transition('interrupting', 'barge-in');

    // 2. Clear all timers
    clearTimers();

    // 3. HARD KILL: LLM stream + TTS + audio queue
    await interrupt();

    // 4. Clean slate → listening for the real utterance
    softReset();
    transition('listening', 'post-interrupt');
    lastPartialRef.current = '';

    // 5. Start ASR to capture the user's actual speech
    if (activeRef.current) {
      await startASR();
    }
  }, [getState, transition, clearTimers, interrupt, softReset, startASR]);

  // ═══════════════════════════════════════════════════════
  // START PIPELINE
  // ═══════════════════════════════════════════════════════

  const startPipeline = useCallback(async () => {
    // GUARD: only from IDLE
    if (getState() !== 'idle') return;

    softReset();
    transition('listening', 'pipeline-start');
    activeRef.current = true;
    lastPartialRef.current = '';

    // ── Register stream-complete callback ──
    // This fires when LLM stream + TTS queue are fully drained
    setOnStreamComplete(() => {
      lastTTSEndRef.current = Date.now();
      const state = getState();
      if (state === 'speaking' || state === 'thinking') {
        transition('idle', 'stream-complete');
      }
      // Schedule auto-relisten with cooldown
      if (duplexEnabled && activeRef.current) {
        if (relistenTimerRef.current) clearTimeout(relistenTimerRef.current);
        relistenTimerRef.current = setTimeout(() => {
          relistenTimerRef.current = null;
          reenterListening();
        }, POST_TTS_COOLDOWN_MS);
      }
    });

    // ── Initialize VAD ──
    if (!vadRef.current) {
      vadRef.current = new BrowserVAD();
    }

    vadRef.current
      // ── VAD: speech-end ──
      .on('speech-end', async () => {
        // GUARD: only in LISTENING
        if (getState() !== 'listening') return;
        if (usingWhisperRef.current) {
          await handleWhisperTranscription();
        }
      })

      // ── VAD: speech-start (barge-in gate) ──
      .on('speech-start', (rawRms: number) => {
        const state = getState();

        if (state === 'speaking' || state === 'thinking') {
          // PRE-VALIDATION: Echo-aware Strong Speech Gate
          const { isAudioPlaying } = useAgentStore.getState();
          const { bargeInRmsNormal, bargeInRmsEcho } = useSettingsStore.getState();
          
          const threshold = isAudioPlaying ? bargeInRmsEcho : bargeInRmsNormal;
          if (rawRms < threshold) {
            console.log(`[FSM] ❌ Echo rejected (RMS ${rawRms.toFixed(0)} < ${threshold}${isAudioPlaying ? ' [TTS active]' : ''})`);
            return;
          }
          console.log(`[FSM] ✅ Strong interrupt (RMS ${rawRms.toFixed(0)}, threshold ${threshold})`);
          handleBargeIn();
        }
        // All other states: VAD speech-start is informational only
      })

      // ── VAD: continuous RMS feed for orb animation ──
      .on('vad', (isSpeech: boolean) => {
        if (vadRef.current) {
          const diag = vadRef.current.getDiagnostics();
          const normalized = Math.min(1, diag.maxRms / 3000);
          setMicRms(isSpeech ? normalized : 0);
        }
      });

    // Start VAD
    await vadRef.current.start();

    // ── Initialize ASR ──
    if (isBrowserASRSupported()) {
      console.log('[FSM] Mode: Browser ASR + VAD (duplex)');
      usingWhisperRef.current = false;

      if (!asrRef.current) {
        asrRef.current = new BrowserASR(
          // ── ASR Result Callback ──
          (text: string, isFinal: boolean) => {
            const state = getState();

            // HARD RULE: ASR is SILENT during non-listening states.
            // Barge-in is handled exclusively by VAD speech-start.
            if (state !== 'listening') return;

            if (isFinal && text.trim()) {
              // Validate minimum length
              if (text.trim().length < 3) {
                console.log(`[FSM] ASR final too short ("${text}")`);
                return;
              }

              // Cancel partial timer
              if (partialTimerRef.current) {
                clearTimeout(partialTimerRef.current);
                partialTimerRef.current = null;
              }

              console.log('[FSM] ASR final:', text);
              setTranscript(text);
              setPartialTranscript('');
              asrRef.current?.stop();
              dispatchTranscript(text, 'browser-asr-final', true);

            } else if (!isFinal && text.trim()) {
              // Interim transcript — feed UI + early LLM debounce
              setPartialTranscript(text);
              lastPartialRef.current = text;

              if (useAgentStore.getState().mode === 'agent') {
                console.log('[FSM] Agent mode: waiting for final ASR transcript');
                return;
              }

              const wordCount = text.trim().split(/\s+/).length;
              if (wordCount >= PARTIAL_WORD_THRESHOLD) {
                if (partialTimerRef.current) clearTimeout(partialTimerRef.current);
                partialTimerRef.current = setTimeout(() => {
                  partialTimerRef.current = null;
                  // Re-check state at debounce fire time
                  if (getState() !== 'listening' || !lastPartialRef.current.trim()) return;
                  if (useAgentStore.getState().mode === 'agent') return;

                  console.log(`[FSM] ⚡ Early LLM (${wordCount} words, ${PARTIAL_DEBOUNCE_MS}ms)`);
                  const partialText = lastPartialRef.current;
                  setTranscript(partialText);
                  setPartialTranscript('');
                  asrRef.current?.stop();
                  dispatchTranscript(partialText, 'browser-asr-partial', false);
                }, PARTIAL_DEBOUNCE_MS);
              }
            }
          },

          // ── ASR Error Callback ──
          async (error: any) => {
            console.warn('[FSM] Browser ASR error:', error, '→ Whisper fallback');
            usingWhisperRef.current = true;
            if (activeRef.current) {
              if (!micRef.current) micRef.current = new MicRecorder();
              try { await micRef.current.start(); } catch (e) {
                console.error('[FSM] Mic access failed:', e);
              }
            }
          }
        );
      }

      asrRef.current.start();
    } else {
      console.log('[FSM] Mode: Whisper + VAD (no Browser ASR)');
      usingWhisperRef.current = true;
      if (!micRef.current) micRef.current = new MicRecorder();
      try {
        await micRef.current.start();
      } catch (e) {
        console.error('[FSM] Mic access denied:', e);
        transition('error', 'mic-denied');
        setTimeout(() => transition('idle', 'error-recovery'), 2000);
      }
    }
  }, [
    getState, transition, softReset, isBrowserASRSupported,
    setTranscript, setPartialTranscript,
    handleWhisperTranscription, handleBargeIn,
    setOnStreamComplete, reenterListening,
    dispatchTranscript, setMicRms, duplexEnabled, startASR,
  ]);

  // ═══════════════════════════════════════════════════════
  // STOP PIPELINE
  // ═══════════════════════════════════════════════════════

  const stopPipeline = useCallback(async () => {
    activeRef.current = false;
    clearTimers();

    // Stop hardware
    vadRef.current?.stop();
    asrRef.current?.stop();
    micRef.current?.cancel();

    // Kill active generation if running
    const state = getState();
    if (state === 'speaking' || state === 'thinking') {
      await interrupt();
    }
    setState('idle');
  }, [getState, setState, interrupt, clearTimers]);

  // ── Cleanup on unmount ──
  useEffect(() => {
    return () => {
      if (partialTimerRef.current) clearTimeout(partialTimerRef.current);
      if (relistenTimerRef.current) clearTimeout(relistenTimerRef.current);
      vadRef.current?.stop();
      asrRef.current?.stop();
      micRef.current?.cancel();
    };
  }, []);

  return { startPipeline, stopPipeline, handleStream };
};
