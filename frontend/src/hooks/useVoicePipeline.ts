import { useEffect, useRef, useCallback } from 'react';
import { useAgentStore } from '../core/state/agentStore';
import { useLLMStream } from './useLLMStream';
import { BrowserASR } from '../core/asr/browserASR';
import { MicRecorder } from '../core/audio/mic';
import { BrowserVAD } from '../core/vad/browserVAD';
import { transcribeAudio } from '../core/api/whisper';

/**
 * Duplex Voice Pipeline with VAD
 * ===============================
 *
 * Architecture (mirrors backend duplex/):
 *
 *   Mic Input
 *      ↓
 *   BrowserVAD (speech detection)
 *      ↓
 *   Browser ASR (primary, instant)
 *      ↓ (fallback on error / unsupported)
 *   MicRecorder + Whisper API
 *      ↓
 *   LLM /generate (stream=true)
 *      ↓
 *   Token stream → sentence buffer → cleanForSpeech
 *      ↓
 *   TTS /synthesize → audio/mpeg
 *      ↓
 *   AudioPlayer (browser-side playback)
 *
 * VAD Controls:
 *   - Auto-stop: detects silence after speech → triggers transcription
 *   - Barge-in: detects speech during assistant playback → triggers interrupt
 *   - No manual stop needed in normal flow
 */
export const useVoicePipeline = () => {
  const { setTranscript, setState, reset } = useAgentStore();
  const { handleStream, interrupt } = useLLMStream();

  const asrRef = useRef<BrowserASR | null>(null);
  const micRef = useRef<MicRecorder | null>(null);
  const vadRef = useRef<BrowserVAD | null>(null);
  const usingWhisperRef = useRef(false);
  const activeRef = useRef(false);
  const processingRef = useRef(false); // prevent double-fire

  // ── Check Browser ASR support ──
  const isBrowserASRSupported = useCallback(() => {
    return !!(
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition
    );
  }, []);

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
        // Restart recording for next utterance
        if (activeRef.current) {
          await micRef.current.start();
        }
        return;
      }

      const text = await transcribeAudio(audioBlob);
      if (text.trim()) {
        console.log('[Pipeline] Whisper transcribed:', text);
        setTranscript(text);
        handleStream(text);
      } else {
        console.warn('[Pipeline] Whisper returned empty');
        setState('listening');
        // Restart recording
        if (activeRef.current) {
          await micRef.current.start();
        }
      }
    } catch (e) {
      console.error('[Pipeline] Whisper transcription failed:', e);
      setState('listening');
      if (activeRef.current) {
        await micRef.current?.start();
      }
    }
    processingRef.current = false;
  }, [setState, setTranscript, handleStream]);

  // ── Handle barge-in (VAD detected speech while Astra is speaking) ──
  const handleBargeIn = useCallback(async () => {
    const currentState = useAgentStore.getState().state;
    if (currentState !== 'speaking' && currentState !== 'thinking') return;

    console.log('[Pipeline] ⚡ VAD barge-in detected → interrupting');
    await interrupt();
    reset();
    setState('listening');

    // Restart ASR/recording for the new utterance
    if (activeRef.current) {
      if (isBrowserASRSupported() && asrRef.current) {
        asrRef.current.start();
      } else if (micRef.current) {
        try { await micRef.current.start(); } catch (e) { /* ignore */ }
      }
    }
  }, [interrupt, reset, setState, isBrowserASRSupported]);

  // ── Start Pipeline ──
  const startPipeline = useCallback(async () => {
    const currentState = useAgentStore.getState().state;
    if (currentState !== 'idle') return;

    reset();
    setState('listening');
    activeRef.current = true;
    processingRef.current = false;

    // Initialize VAD with backend-matched defaults
    // (energyThreshold=450, silenceMsToStop=900, maxDurationMs=12000, frameIntervalMs=30)
    if (!vadRef.current) {
      vadRef.current = new BrowserVAD();
    }

    // Setup VAD callbacks
    vadRef.current
      .on('speech-end', async () => {
        const currentState = useAgentStore.getState().state;
        if (currentState !== 'listening') return;

        if (usingWhisperRef.current) {
          // Whisper path: VAD detected end-of-speech → transcribe
          await handleWhisperTranscription();
        }
        // Browser ASR path: ASR handles its own finalization,
        // but VAD helps with barge-in detection
      })
      .on('speech-start', () => {
        const currentState = useAgentStore.getState().state;
        if (currentState === 'speaking' || currentState === 'thinking') {
          handleBargeIn();
        }
      });

    // Start VAD (always runs for barge-in detection)
    await vadRef.current.start();

    if (isBrowserASRSupported()) {
      console.log('[Pipeline] Using Browser ASR + VAD');
      usingWhisperRef.current = false;

      if (!asrRef.current) {
        asrRef.current = new BrowserASR(
          (text: string, isFinal: boolean) => {
            const currentState = useAgentStore.getState().state;
            setTranscript(text);

            if (currentState === 'speaking') {
              handleBargeIn();
              return;
            }

            if (isFinal && text.trim() && currentState === 'listening') {
              console.log('[Pipeline] Browser ASR final:', text);
              asrRef.current?.stop();
              handleStream(text);
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
        setState('idle');
      }
    }
  }, [reset, setState, isBrowserASRSupported, handleStream, setTranscript, handleWhisperTranscription, handleBargeIn]);

  // ── Stop Pipeline ──
  const stopPipeline = useCallback(async () => {
    activeRef.current = false;

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
      vadRef.current?.stop();
      asrRef.current?.stop();
      micRef.current?.cancel();
    };
  }, []);

  return { startPipeline, stopPipeline };
};
