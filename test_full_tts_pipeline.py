"""Test the complete TTS pipeline: Edge TTS → MP3 decode → AudioPlaybackEngine."""
import asyncio
import io
import logging
import time
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

from services.audio_playback_engine import AudioPlaybackEngine

try:
    import miniaudio
except ImportError:
    print("❌ miniaudio not installed! Install it with: pip install miniaudio")
    exit(1)

async def test_full_pipeline():
    import edge_tts
    
    # 1. Generate audio with Edge TTS
    print("1️⃣ Generating speech with Edge TTS...")
    text = "Hello! This is a complete test of the text to speech pipeline."
    communicate = edge_tts.Communicate(text, voice="en-IN-NeerjaNeural")
    audio_data = io.BytesIO()
    
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.write(chunk["data"])
    
    mp3_bytes = audio_data.getvalue()
    print(f"   ✅ Generated {len(mp3_bytes)} bytes of MP3")
    
    # 2. Decode MP3 to PCM
    print("\n2️⃣ Decoding MP3 to PCM...")
    decoded = miniaudio.decode(
        mp3_bytes,
        output_format=miniaudio.SampleFormat.FLOAT32,
        nchannels=1,
        sample_rate=24000,
    )
    pcm = np.frombuffer(decoded.samples, dtype=np.float32).copy()
    print(f"   ✅ Decoded to {len(pcm)} PCM samples ({len(pcm)/24000:.2f} seconds)")
    
    # 3. Play through AudioPlaybackEngine
    print("\n3️⃣ Playing through AudioPlaybackEngine...")
    engine = AudioPlaybackEngine(sample_rate=24000, channels=1)
    engine.enqueue(chunk_id=0, pcm_float32=pcm)
    
    # Wait for playback
    duration = len(pcm) / 24000
    wait_time = duration + 0.5  # add buffer
    print(f"\n🔊 PLAYING NOW... (duration: {duration:.1f}s)")
    print("   Listen carefully for the voice!")
    time.sleep(wait_time)
    
    print("\n✅ Pipeline test complete!")
    print("   Did you hear the voice?")
    
    return True

if __name__ == "__main__":
    asyncio.run(test_full_pipeline())
