export class AudioPlayer {
  private audioContext: AudioContext | null = null;
  private queue: Blob[] = [];
  private isPlaying = false;
  private currentSource: AudioBufferSourceNode | null = null;

  constructor() {
    this.audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
  }

  async resume() {
    if (this.audioContext?.state === 'suspended') {
      await this.audioContext.resume();
    }
  }

  async play(blob: Blob) {
    await this.resume();
    this.queue.push(blob);
    this.processQueue();
  }

  private async processQueue() {
    if (this.isPlaying || this.queue.length === 0 || !this.audioContext) return;
    
    this.isPlaying = true;
    const blob = this.queue.shift()!;
    const arrayBuffer = await blob.arrayBuffer();
    const audioBuffer = await this.audioContext.decodeAudioData(arrayBuffer);
    
    this.currentSource = this.audioContext.createBufferSource();
    this.currentSource.buffer = audioBuffer;
    this.currentSource.connect(this.audioContext.destination);
    
    this.currentSource.onended = () => {
      this.isPlaying = false;
      this.currentSource = null;
      this.processQueue();
    };
    
    this.currentSource.start();
  }

  stop() {
    if (this.currentSource) {
      this.currentSource.stop();
      this.currentSource.disconnect();
      this.currentSource = null;
    }
    this.queue = [];
    this.isPlaying = false;
  }
}

export const audioPlayer = new AudioPlayer();
