"""Test sounddevice and audio hardware."""
import sys

try:
    import sounddevice as sd
    print("✅ sounddevice is installed")
    print(f"Version: {sd.__version__}")
    
    # List audio devices
    print("\n🔊 Audio Devices:")
    print(sd.query_devices())
    
    # Check default output device
    print("\n🎵 Default Output Device:")
    default_device = sd.query_devices(kind='output')
    print(default_device)
    
    # Try to create a test stream
    print("\n🧪 Testing audio stream creation...")
    stream = sd.OutputStream(
        samplerate=24000,
        channels=1,
        dtype='float32',
        blocksize=1024,
    )
    stream.start()
    print("✅ Audio stream created successfully!")
    stream.close()
    
except ImportError as e:
    print(f"❌ sounddevice NOT installed: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error testing audio: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n✅ All audio checks passed!")
