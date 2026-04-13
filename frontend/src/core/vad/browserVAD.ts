/**
 * Browser-side Voice Activity Detection (VAD) engine.
 *
 * Mirrors the backend duplex/vad_engine.py + speech_capture.py settings:
 *
 * Backend reference values:
 *   VADEngine(aggressiveness=2, energy_threshold=450.0)  — speech_capture
 *   VADEngine(aggressiveness=3)                          — audio_listener (barge-in)
 *   SpeechCapture(frame_ms=30, silence_ms_to_stop=900, max_record_seconds=12.0)
 *
 * The backend energy_threshold=450 is on 16-bit PCM (range 0–32767).
 * Browser AnalyserNode uses getFloatTimeDomainData (range -1..+1) for
 * precision, then we scale to 16-bit equivalent RMS for identical threshold.
 *
 * Architecture:
 *   getUserMedia → AnalyserNode → periodic RMS check → speech/silence events
 */

export type VADEvent = 'speech-start' | 'speech-end' | 'speech' | 'silence';

export interface VADConfig {
  /**
   * RMS energy threshold in 16-bit PCM scale (0–32767).
   * Matches backend VADEngine.energy_threshold exactly.
   * Default: 450.0 (backend sweet spot)
   */
  energyThreshold: number;

  /**
   * Consecutive speech frames required before firing speech-start.
   * Prevents single-frame noise spikes from triggering false positives.
   * Mirrors webrtcvad aggressiveness=2 hangover behavior.
   * Default: 3
   */
  speechFramesToStart: number;

  /**
   * Silence duration in ms before firing speech-end.
   * Matches backend SpeechCapture.silence_ms_to_stop = 900
   */
  silenceMsToStop: number;

  /**
   * Max recording duration in ms before auto-stop.
   * Matches backend SpeechCapture.max_record_seconds = 12.0
   */
  maxDurationMs: number;

  /**
   * Analysis interval in ms.
   * Matches backend SpeechCapture.frame_ms = 30
   */
  frameIntervalMs: number;
}

/**
 * Exact backend sweet-spot values.
 * DO NOT change without also updating duplex/vad_engine.py +
 * duplex/speech_capture.py.
 */
const DEFAULT_CONFIG: VADConfig = {
  energyThreshold: 450.0,    // backend: VADEngine(energy_threshold=450.0)
  speechFramesToStart: 3,    // debounce: ~90ms of consecutive speech
  silenceMsToStop: 900,      // backend: SpeechCapture(silence_ms_to_stop=900)
  maxDurationMs: 12000,      // backend: SpeechCapture(max_record_seconds=12.0)
  frameIntervalMs: 30,       // backend: SpeechCapture(frame_ms=30)
};

export class BrowserVAD {
  private config: VADConfig;
  private stream: MediaStream | null = null;
  private audioContext: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private intervalId: ReturnType<typeof setInterval> | null = null;

  private _isRunning = false;
  private _isSpeaking = false;
  private silenceRunCount = 0;
  private speechRunCount = 0;       // consecutive speech frames for debounce
  private speechStartTime = 0;
  private totalFrames = 0;
  private speechFrames = 0;
  private maxRms = 0;
  private avgRms = 0;

  // Callbacks
  private onSpeechStart: (() => void) | null = null;
  private onSpeechEnd: ((durationMs: number) => void) | null = null;
  private onVADChange: ((isSpeech: boolean) => void) | null = null;

  constructor(config: Partial<VADConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  get isRunning() { return this._isRunning; }
  get isSpeaking() { return this._isSpeaking; }

  /**
   * Set event callbacks.
   */
  on(event: 'speech-start', cb: () => void): this;
  on(event: 'speech-end', cb: (durationMs: number) => void): this;
  on(event: 'vad', cb: (isSpeech: boolean) => void): this;
  on(event: string, cb: any): this {
    if (event === 'speech-start') this.onSpeechStart = cb;
    else if (event === 'speech-end') this.onSpeechEnd = cb;
    else if (event === 'vad') this.onVADChange = cb;
    return this;
  }

  /**
   * Start VAD analysis on the microphone.
   */
  async start(): Promise<void> {
    if (this._isRunning) return;

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: 16000,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    this.audioContext = new AudioContext({ sampleRate: 16000 });
    this.analyser = this.audioContext.createAnalyser();
    this.analyser.fftSize = 2048;               // match backend blocksize
    this.analyser.smoothingTimeConstant = 0.0;   // no smoothing — raw frames like backend

    this.source = this.audioContext.createMediaStreamSource(this.stream);
    this.source.connect(this.analyser);

    // Reset state
    this._isRunning = true;
    this._isSpeaking = false;
    this.silenceRunCount = 0;
    this.speechRunCount = 0;
    this.speechStartTime = 0;
    this.totalFrames = 0;
    this.speechFrames = 0;
    this.maxRms = 0;
    this.avgRms = 0;

    // Use Float32 for precise RMS matching the backend's int16 scaling
    const dataArray = new Float32Array(this.analyser.fftSize);

    this.intervalId = setInterval(() => {
      if (!this.analyser || !this._isRunning) return;

      this.analyser.getFloatTimeDomainData(dataArray);
      const rms = this.computeRMS16(dataArray);

      this.totalFrames++;
      this.maxRms = Math.max(this.maxRms, rms);
      this.avgRms = ((this.avgRms * (this.totalFrames - 1)) + rms) / this.totalFrames;

      const isSpeech = rms >= this.config.energyThreshold;
      if (isSpeech) this.speechFrames++;

      // Fire VAD change callback
      this.onVADChange?.(isSpeech);

      // ── State transitions ──
      // Mirrors backend: vad_engine.py + speech_capture.py + audio_listener.py
      if (!this._isSpeaking) {
        if (isSpeech) {
          this.speechRunCount++;
          // Debounce: require N consecutive speech frames (replaces webrtcvad hangover)
          if (this.speechRunCount >= this.config.speechFramesToStart) {
            this._isSpeaking = true;
            this.silenceRunCount = 0;
            this.speechStartTime = performance.now();
            console.log(`[VAD] 🎙️ Speech started (RMS: ${rms.toFixed(0)}, threshold: ${this.config.energyThreshold})`);
            this.onSpeechStart?.();
          }
        } else {
          this.speechRunCount = 0; // reset debounce counter
        }
      } else {
        // Currently in speech
        if (isSpeech) {
          this.silenceRunCount = 0;
        } else {
          this.silenceRunCount++;
        }

        // Check auto-stop conditions (matches backend speech_capture.py)
        const silenceFramesToStop = Math.max(1, Math.floor(this.config.silenceMsToStop / this.config.frameIntervalMs));
        const elapsed = performance.now() - this.speechStartTime;

        if (
          this.silenceRunCount >= silenceFramesToStop ||
          elapsed >= this.config.maxDurationMs
        ) {
          const reason = this.silenceRunCount >= silenceFramesToStop
            ? 'silence' : 'max-duration';
          console.log(
            `[VAD] 🔇 Speech ended (${reason}, duration: ${elapsed.toFixed(0)}ms, ` +
            `speech-frames: ${this.speechFrames}/${this.totalFrames}, ` +
            `avg_rms: ${this.avgRms.toFixed(0)}, max_rms: ${this.maxRms.toFixed(0)})`
          );
          this._isSpeaking = false;
          this.speechRunCount = 0;
          this.onSpeechEnd?.(elapsed);
        }
      }
    }, this.config.frameIntervalMs);

    console.log('[VAD] Started with backend-matched config:', {
      energyThreshold: this.config.energyThreshold,
      speechFramesToStart: this.config.speechFramesToStart,
      silenceMsToStop: this.config.silenceMsToStop,
      maxDurationMs: this.config.maxDurationMs,
      frameIntervalMs: this.config.frameIntervalMs,
    });
  }

  /**
   * Stop VAD analysis and release microphone.
   */
  stop(): void {
    this._isRunning = false;
    this._isSpeaking = false;

    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
    }

    this.source?.disconnect();
    this.source = null;
    this.analyser = null;

    if (this.audioContext && this.audioContext.state !== 'closed') {
      this.audioContext.close().catch(() => {});
    }
    this.audioContext = null;

    this.stream?.getTracks().forEach(t => t.stop());
    this.stream = null;

    console.log('[VAD] Stopped');
  }

  /**
   * Get diagnostics (mirrors backend CaptureDiagnostics).
   */
  getDiagnostics() {
    return {
      totalFrames: this.totalFrames,
      speechFrames: this.speechFrames,
      isSpeaking: this._isSpeaking,
      isRunning: this._isRunning,
      avgRms: Math.round(this.avgRms),
      maxRms: Math.round(this.maxRms),
      speechRatio: this.totalFrames > 0
        ? (this.speechFrames / this.totalFrames * 100).toFixed(1) + '%'
        : '0%',
    };
  }

  /**
   * Compute RMS energy scaled to 16-bit PCM range.
   *
   * Backend equivalent (vad_engine.py _energy_is_speech):
   *   samples = array('h')  # int16
   *   squares = sum(s*s for s in samples)
   *   rms = sqrt(squares / len(samples))
   *   return rms >= 450.0
   *
   * Browser getFloatTimeDomainData returns float32 in range [-1, +1].
   * We multiply by 32767 to match the int16 scale, then compute RMS.
   */
  private computeRMS16(dataArray: Float32Array): number {
    let sum = 0;
    for (let i = 0; i < dataArray.length; i++) {
      const sample16 = dataArray[i] * 32767;
      sum += sample16 * sample16;
    }
    return Math.sqrt(sum / dataArray.length);
  }
}
