"""Test audio playback directly."""
import requests
import time

# Test the TTS service /speak endpoint
response = requests.post(
    "http://127.0.0.1:8003/speak",
    json={"text": "Hello, this is a test.", "chunk_id": 0, "generation_id": 999},
    timeout=10
)

print(f"Status: {response.status_code}")
print(f"Response: {response.json()}")

# Give it time to play
print("\nWaiting 3 seconds for audio playback...")
time.sleep(3)
print("Done. Did you hear audio?")
