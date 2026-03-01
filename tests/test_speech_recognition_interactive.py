"""Interactive speech recognition test.

Displays text for you to speak, records your voice, and transcribes it
to verify the speech recognition is working.
"""

import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from duplex.vad_engine import VADEngine
from duplex.speech_capture import SpeechCapture


# Test sentences - feel free to pick any one and speak it clearly
TEST_SENTENCES = [
    "Hello, how are you today?",
    "The quick brown fox jumps over the lazy dog.",
    "What is the weather like outside?",
    "I love listening to music and reading books.",
    "Can you help me with this task?",
    "The sun is shining brightly in the sky.",
    "Do you speak English fluently?",
    "I would like a cup of coffee please.",
    "Technology is changing the world rapidly.",
    "What time is it right now?",
]


def test_speech_recognition():
    """Interactive speech recognition test."""
    print("\n" + "="*70)
    print("🎤 SPEECH RECOGNITION TEST")
    print("="*70)
    
    # Initialize VAD and speech capture
    print("\n⏳ Initializing speech capture...")
    try:
        # Use less aggressive VAD (aggressiveness=1 instead of 2) for better speech detection
        # Lower energy threshold to catch quieter speech (300 instead of 450)
        vad_engine = VADEngine(aggressiveness=1, energy_threshold=300.0)
        capture = SpeechCapture(
            vad_engine=vad_engine,
            sample_rate=16000,
            channels=1,
            frame_ms=30,
            silence_ms_to_stop=900,
            max_record_seconds=12.0,
        )
        
        if not capture.available:
            print("❌ ERROR: sounddevice not available")
            return
        
        print("✅ Speech capture ready\n")
    except Exception as e:
        print(f"❌ Initialization failed: {e}")
        return
    
    # Display test sentences
    print("📝 AVAILABLE TEST SENTENCES:")
    print("-" * 70)
    for i, sentence in enumerate(TEST_SENTENCES, 1):
        print(f"  {i:2d}. {sentence}")
    print("-" * 70)
    
    # Ask user to choose
    while True:
        try:
            choice = input("\nPick a sentence number (1-10) to speak, or 'q' to quit: ").strip()
            if choice.lower() == 'q':
                print("Exiting...")
                return
            
            idx = int(choice) - 1
            if 0 <= idx < len(TEST_SENTENCES):
                selected_sentence = TEST_SENTENCES[idx]
                break
            else:
                print("❌ Invalid choice. Please enter a number between 1 and 10.")
        except ValueError:
            print("❌ Invalid input. Please enter a number.")
    
    print("\n" + "="*70)
    print(f"📣 SPEAK THIS TEXT:\n")
    print(f"   ➜  {selected_sentence}")
    print("\n" + "="*70)
    
    # Wait 2 seconds before recording
    print("\n⏳ Recording will start in 2 seconds...")
    print("   (Speak clearly and naturally)\n")
    time.sleep(2)
    
    # Record
    print("🔴 RECORDING... (waiting for speech)")
    start = time.perf_counter()
    wav_bytes, diagnostics = capture.capture_utterance_wav_with_diagnostics(wait_seconds=15.0)
    elapsed = time.perf_counter() - start
    
    if wav_bytes is None:
        print(f"❌ No speech detected after {elapsed:.1f}s")
        print(f"   Diagnostics: {diagnostics}")
        return
    
    print(f"✅ Recording complete ({elapsed:.1f}s)")
    print(f"\n📊 CAPTURE DIAGNOSTICS:")
    print(f"   ├─ Total frames:    {diagnostics.total_frames}")
    print(f"   ├─ Speech frames:   {diagnostics.speech_frames}")
    print(f"   ├─ Duration:        {diagnostics.duration_sec:.2f}s")
    print(f"   ├─ Avg RMS:         {diagnostics.avg_rms:.1f}")
    print(f"   └─ Max RMS:         {diagnostics.max_rms:.1f}\n")
    
    # Validate that we captured enough speech
    speech_ratio = diagnostics.speech_frames / max(1, diagnostics.total_frames)
    if speech_ratio < 0.1:  # Less than 10% speech frames
        print(f"⚠️  WARNING: Only {speech_ratio*100:.1f}% of recording marked as speech")
        print(f"   This is too low for reliable transcription. Try speaking louder or more clearly.\n")
    
    # Send to Whisper for transcription
    print("⏳ Transcribing with Whisper...")
    try:
        response = requests.post(
            "http://127.0.0.1:8001/transcribe",
            files={"audio_file": ("speech.wav", wav_bytes, "audio/wav")},
            timeout=30,
        )
        
        if response.status_code != 200:
            print(f"❌ Whisper service error: {response.status_code}")
            print(f"   Response: {response.text}")
            return
        
        result = response.json()
        transcribed_text = result.get("text", "").strip()
        confidence = result.get("language", "unknown")
        
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to Whisper service on port 8001")
        print("   Make sure the service is running: python -m services.whisper_service")
        return
    except Exception as e:
        print(f"❌ Transcription failed: {e}")
        return
    
    # Display results
    print("\n" + "="*70)
    print("📝 RESULTS")
    print("="*70)
    print(f"\n📌 Expected (what you spoke):")
    print(f"   ➜  {selected_sentence}")
    print(f"\n🎙️  Transcribed (what Whisper heard):")
    print(f"   ➜  {transcribed_text}")
    print(f"\n🌍 Language detected: {confidence}")
    
    # Simple matching
    expected_lower = selected_sentence.lower()
    transcribed_lower = transcribed_text.lower()
    
    if transcribed_lower == expected_lower:
        print("\n✅ PERFECT MATCH! Speech recognition working perfectly!")
    elif transcribed_lower in expected_lower or expected_lower in transcribed_lower:
        print("\n✅ PARTIAL MATCH! Close enough - speech recognition is working well.")
    else:
        # Calculate word overlap
        expected_words = set(expected_lower.split())
        transcribed_words = set(transcribed_lower.split())
        overlap = expected_words & transcribed_words
        overlap_percent = (len(overlap) / len(expected_words) * 100) if expected_words else 0
        
        if overlap_percent >= 70:
            print(f"\n🟡 MOSTLY CORRECT ({overlap_percent:.0f}% word match)")
        elif overlap_percent >= 40:
            print(f"\n🟠 PARTIALLY CORRECT ({overlap_percent:.0f}% word match)")
        else:
            print(f"\n❌ LOW MATCH ({overlap_percent:.0f}% word match)")
            print("   Try speaking more clearly or adjusting microphone position.")
    
    print("\n" + "="*70 + "\n")
    
    # Option to test again
    again = input("Test again? (y/n): ").strip().lower()
    if again == 'y':
        test_speech_recognition()


if __name__ == "__main__":
    test_speech_recognition()
