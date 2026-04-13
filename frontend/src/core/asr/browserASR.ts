export class BrowserASR {
  private recognition: any;
  private isListening = false;
  private onResult: (text: string, isFinal: boolean) => void;
  private onError: (error: any) => void;

  constructor(onResult: (text: string, isFinal: boolean) => void, onError: (error: any) => void) {
    this.onResult = onResult;
    this.onError = onError;
    
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (SpeechRecognition) {
      this.recognition = new SpeechRecognition();
      this.recognition.continuous = true;
      this.recognition.interimResults = true;
      this.recognition.lang = 'en-IN'; // Fallback to parameterizing this later
      
      this.recognition.onresult = (event: any) => {
        let interimTranscript = '';
        let finalTranscript = '';
        
        for (let i = event.resultIndex; i < event.results.length; ++i) {
          if (event.results[i].isFinal) {
            finalTranscript += event.results[i][0].transcript;
          } else {
            interimTranscript += event.results[i][0].transcript;
          }
        }
        
        if (finalTranscript) {
          this.onResult(finalTranscript, true);
        } else if (interimTranscript) {
          this.onResult(interimTranscript, false);
        }
      };

      this.recognition.onerror = (event: any) => {
        this.onError(event.error);
        this.stop();
      };
      
      this.recognition.onend = () => {
        if (this.isListening) {
          // Restart if it stopped unexpectedly while we should be listening
          try {
             this.recognition.start();
          } catch(e) {}
        }
      }
    } else {
      console.warn("Browser Speech Recognition not supported.");
    }
  }

  start() {
    if (!this.recognition || this.isListening) return;
    this.isListening = true;
    try {
      this.recognition.start();
    } catch(e) {}
  }

  stop() {
    this.isListening = false;
    if (this.recognition) {
        try {
            this.recognition.stop();
        } catch(e) {}
    }
  }
}
