/**
 * BrowserTTS - uses the native Web Speech API (speechSynthesis) as a
 * fallback when the backend TTS /synthesize endpoint is unavailable.
 */
export class BrowserTTS {
  private synth: SpeechSynthesis;
  private currentUtterance: SpeechSynthesisUtterance | null = null;

  constructor() {
    this.synth = window.speechSynthesis;
  }

  static isSupported(): boolean {
    return 'speechSynthesis' in window;
  }

  speak(text: string): Promise<void> {
    return new Promise((resolve, reject) => {
      if (!BrowserTTS.isSupported()) {
        reject(new Error('Browser SpeechSynthesis not supported'));
        return;
      }

      this.stop(); // cancel anything currently speaking

      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = 'en-IN';
      utterance.rate = 1.05;
      utterance.pitch = 1.0;

      // Try to use a good English voice
      const voices = this.synth.getVoices();
      const preferred = voices.find(
        (v) =>
          v.lang.startsWith('en') &&
          (v.name.includes('Google') || v.name.includes('Microsoft') || v.name.includes('Natural'))
      );
      if (preferred) utterance.voice = preferred;

      utterance.onend = () => {
        this.currentUtterance = null;
        resolve();
      };
      utterance.onerror = (e) => {
        this.currentUtterance = null;
        reject(e);
      };

      this.currentUtterance = utterance;
      this.synth.speak(utterance);
    });
  }

  stop(): void {
    this.synth.cancel();
    this.currentUtterance = null;
  }
}

export const browserTTS = new BrowserTTS();
