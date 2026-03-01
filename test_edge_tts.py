"""Test Edge TTS synthesis directly."""
import asyncio
import logging
import io

logging.basicConfig(level=logging.INFO)

async def test_edge_tts():
    import edge_tts
    
    text = "Hello, this is a test of Edge TTS synthesis."
    voice = "en-IN-NeerjaNeural"
    
    print(f"Synthesizing: {text}")
    print(f"Voice: {voice}")
    
    communicate = edge_tts.Communicate(text, voice=voice)
    audio_data = io.BytesIO()
    
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.write(chunk["data"])
    
    mp3_bytes = audio_data.getvalue()
    print(f"\n✅ Generated {len(mp3_bytes)} bytes of MP3 audio")
    print(f"   MP3 size: {len(mp3_bytes) / 1024:.2f} KB")
    
    if mp3_bytes:
        # Save to file for manual inspection
        output_file = "test_edge_output.mp3"
        with open(output_file, "wb") as f:
            f.write(mp3_bytes)
        print(f"   Saved to: {output_file}")
        print(f"   You can play this file manually to verify Edge TTS works")
        return True
    else:
        print("❌ No audio data generated!")
        return False

if __name__ == "__main__":
    success = asyncio.run(test_edge_tts())
    if success:
        print("\n✅ Edge TTS synthesis successful")
    else:
        print("\n❌ Edge TTS synthesis failed")
