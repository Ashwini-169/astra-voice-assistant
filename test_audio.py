"""Manual audio playback smoke test.

This script is intentionally executable as a standalone tool, not a pytest test.
"""

import time

import requests


def main() -> None:
    response = requests.post(
        "http://127.0.0.1:8003/speak",
        json={"text": "Hello, this is a test.", "chunk_id": 0, "generation_id": 999},
        timeout=10,
    )

    print(f"Status: {response.status_code}")
    try:
        print(f"Response: {response.json()}")
    except Exception:  # pylint: disable=broad-except
        print(f"Response (non-JSON): {response.text[:200]}")

    print("\nWaiting 3 seconds for audio playback...")
    time.sleep(3)
    print("Done. Did you hear audio?")


if __name__ == "__main__":
    main()
