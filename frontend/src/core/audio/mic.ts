/**
 * MicRecorder - captures microphone audio using MediaRecorder API.
 * Used as part of the Whisper fallback when Browser ASR is unavailable.
 */
export class MicRecorder {
  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private _isRecording = false;

  get isRecording() {
    return this._isRecording;
  }

  async start(): Promise<void> {
    if (this._isRecording) return;

    this.chunks = [];
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: 16000,
        echoCancellation: true,
        noiseSuppression: true,
      },
    });

    this.recorder = new MediaRecorder(this.stream, {
      mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm',
    });

    this.recorder.ondataavailable = (e) => {
      if (e.data.size > 0) this.chunks.push(e.data);
    };

    this.recorder.start(250); // collect data every 250ms
    this._isRecording = true;
    console.log('[MicRecorder] Recording started');
  }

  async stop(): Promise<Blob> {
    return new Promise((resolve) => {
      if (!this.recorder || this.recorder.state === 'inactive') {
        this._isRecording = false;
        resolve(new Blob(this.chunks, { type: 'audio/webm' }));
        return;
      }

      this.recorder.onstop = () => {
        const blob = new Blob(this.chunks, { type: 'audio/webm' });
        this.chunks = [];
        this._isRecording = false;

        // Release mic
        this.stream?.getTracks().forEach((t) => t.stop());
        this.stream = null;
        this.recorder = null;

        console.log('[MicRecorder] Recording stopped, blob size:', blob.size);
        resolve(blob);
      };

      this.recorder.stop();
    });
  }

  cancel(): void {
    if (this.recorder && this.recorder.state !== 'inactive') {
      this.recorder.stop();
    }
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.recorder = null;
    this.chunks = [];
    this._isRecording = false;
  }
}
