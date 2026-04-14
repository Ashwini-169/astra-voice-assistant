import { useAgentStore } from '../state/agentStore';

export class AudioPlayer {
  private audioContext: AudioContext | null = null;
  private gainNode: GainNode | null = null;
  private queue: Blob[] = [];
  private isPlaying = false;
  private currentSource: AudioBufferSourceNode | null = null;
  private analyser: AnalyserNode | null = null;
  private animFrameId: number | null = null;
  private _onQueueEmpty: (() => void) | null = null;

  constructor() {
    const AudioCtx = (window.AudioContext || (window as any).webkitAudioContext);
    this.audioContext = new AudioCtx();
    this.gainNode = this.audioContext.createGain();
    this.analyser = this.audioContext.createAnalyser();
    this.analyser.fftSize = 256;
    
    // Graph: Source -> Analyser -> Gain -> Destination
    this.analyser.connect(this.gainNode);
    this.gainNode.connect(this.audioContext.destination);
    
    this.gainNode.gain.value = 1.0; // default 100%
  }

  async resume() {
    if (this.audioContext?.state === 'suspended') {
      await this.audioContext.resume();
    }
  }

  /** Register callback fired when the last queued audio finishes playing. */
  set onQueueEmpty(cb: (() => void) | null) {
    this._onQueueEmpty = cb;
  }

  async play(blob: Blob) {
    await this.resume();
    this.queue.push(blob);
    this.processQueue();
  }

  private loopAnalayser = () => {
    if (!this.analyser) return;

    const dataArray = new Uint8Array(this.analyser.frequencyBinCount);
    this.analyser.getByteTimeDomainData(dataArray);

    let sumSquares = 0;
    for (let i = 0; i < dataArray.length; i++) {
        const normalized = (dataArray[i] / 128.0) - 1.0; // -1 to 1
        sumSquares += normalized * normalized;
    }
    const rms = Math.sqrt(sumSquares / dataArray.length);

    // RMS is generally small (e.g. 0 to 0.4 max). Multiply to normalize 0–1 roughly.
    const normalizedRms = Math.min(1, rms * 5); 
    
    // Safety check ensuring we don't access if React isn't ready
    try {
        useAgentStore.getState().setPlaybackRms(normalizedRms);
    } catch(e) {}

    this.animFrameId = requestAnimationFrame(this.loopAnalayser);
  };


  private async processQueue() {
    if (this.isPlaying || this.queue.length === 0 || !this.audioContext) return;
    
    this.isPlaying = true;
    try { useAgentStore.getState().setIsAudioPlaying(true); } catch(e) {}
    const blob = this.queue.shift()!;

    try {
      const arrayBuffer = await blob.arrayBuffer();
      const audioBuffer = await this.audioContext.decodeAudioData(arrayBuffer);
      
      this.currentSource = this.audioContext.createBufferSource();
      this.currentSource.buffer = audioBuffer;
      this.currentSource.connect(this.analyser!); // Connect to analyser (which is connected to gain node)
      
      this.currentSource.onended = () => {
        this.isPlaying = false;
        this.currentSource = null;
        if (this.animFrameId !== null) {
            cancelAnimationFrame(this.animFrameId);
            this.animFrameId = null;
            try { useAgentStore.getState().setPlaybackRms(0); } catch(e) {}
        }
        if (this.queue.length > 0) {
          this.processQueue();
        } else {
          // Queue is now empty — all audio has finished
          try { useAgentStore.getState().setIsAudioPlaying(false); } catch(e) {}
          this._onQueueEmpty?.();
        }
      };
      
      this.currentSource.start();
      
      if (this.animFrameId === null) {
          this.loopAnalayser();
      }
    } catch (e) {
      console.error('[AudioPlayer] Decode/playback error:', e);
      this.isPlaying = false;
      this.currentSource = null;
      if (this.animFrameId !== null) {
          cancelAnimationFrame(this.animFrameId);
          this.animFrameId = null;
          try { useAgentStore.getState().setPlaybackRms(0); } catch(e) {}
      }
      // Try next in queue even on error
      if (this.queue.length > 0) {
        this.processQueue();
      } else {
        try { useAgentStore.getState().setIsAudioPlaying(false); } catch(e) {}
        this._onQueueEmpty?.();
      }
    }
  }

  stop() {
    if (this.currentSource) {
      try {
        this.currentSource.stop();
        this.currentSource.disconnect();
      } catch { /* already stopped */ }
      this.currentSource = null;
    }
    if (this.animFrameId !== null) {
        cancelAnimationFrame(this.animFrameId);
        this.animFrameId = null;
        try { useAgentStore.getState().setPlaybackRms(0); } catch(e) {}
    }
    this.queue = [];
    this.isPlaying = false;
    
    // Resume context if it was suspended for interrupt
    if (this.audioContext?.state === 'suspended') {
        this.audioContext.resume().catch(()=>{});
    }
    
    try { useAgentStore.getState().setIsAudioPlaying(false); } catch(e) {}
  }

  // NOTE: pauseForInterrupt/resumeAfterInterrupt removed.
  // FSM architecture uses hard stop() for all interrupts.

  setVolume(volume: number) {
    if (this.gainNode) {
      // Linear to power curve adjustment for better perceived volume control
      // Or just linear for simplicity as a start
      this.gainNode.gain.setTargetAtTime(volume / 100, this.audioContext!.currentTime, 0.05);
    }
  }

  get queueLength() {
    return this.queue.length + (this.isPlaying ? 1 : 0);
  }
}

export const audioPlayer = new AudioPlayer();
