"""Test AudioPlaybackEngine directly with a beep sound."""
import logging
import numpy as np
import time
from services.audio_playback_engine import AudioPlaybackEngine

# Enable logging to see what's happening
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s:%(name)s:%(message)s')

print("Creating AudioPlaybackEngine...")
engine = AudioPlaybackEngine(sample_rate=24000, channels=1)

# Generate a simple 440 Hz sine wave (A note) for 1 second
print("Generating test tone (440 Hz beep)...")
duration = 1.0  # seconds
sample_rate = 24000
t = np.linspace(0, duration, int(sample_rate * duration), dtype=np.float32)
frequency = 440.0  # Hz (A note)
audio = (np.sin(2 * np.pi * frequency * t) * 0.3).astype(np.float32)

print(f"Enqueuing {len(audio)} samples...")
engine.enqueue(chunk_id=0, pcm_float32=audio)

print("\n🔊 Playing beep for 2 seconds...")
print("   If you hear a beep, audio playback is working!")
time.sleep(2)

print("\n✅ Test complete.")
print("   Did you hear a 440 Hz beep?")
