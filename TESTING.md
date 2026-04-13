# Testing Guide

## Goal
Validate low-latency streaming quality, interruption behavior, and duplex stability.

## 1) Unit tests
```powershell
.\venv\python.exe -m pytest tests -q
```

## 2) Streaming-specific tests
```powershell
.\venv\python.exe -m pytest -q tests/test_streaming.py
```

Focus areas:
- early first-audio start
- interrupt short-circuit
- chunk coalescing (avoid two-word fragmentation)

## 3) TTS service tests
```powershell
.\venv\python.exe -m pytest -q tests/test_tts.py
```

If missing dependencies in test env, install required package set in `requirements.txt`.

## 4) Manual duplex test
1. Start stack: `./start_stack.ps1`
2. Speak naturally; interrupt during playback
3. Confirm previous turn stops and new turn starts quickly

## 5) Runtime tuning validation
Read settings:
```powershell
curl http://127.0.0.1:8003/settings
```

Set test profile:
```powershell
curl -X POST http://127.0.0.1:8003/settings -H "Content-Type: application/json" -d '{"edge_base_rate_pct":8,"chunk_initial_words":5,"chunk_steady_words":14,"chunk_max_chars":140}'
```

Check streamer view:
```powershell
curl http://127.0.0.1:8003/streaming-config
```

## 6) Common failures
### `PortAudioError ... MME error 1`
- microphone permission denied
- microphone in use by another app
- invalid/unstable default input device

### Choppy speech
- increase `chunk_steady_words` (e.g. 14 -> 18)
- increase `chunk_max_chars` (e.g. 140 -> 180)
- reduce `edge_base_rate_pct` if speech sounds too rushed

### Slow first speech
- reduce `chunk_initial_words` (e.g. 5 -> 4)

## 7) Performance target (balanced profile)
- first speech chunk: fast (small initial chunk)
- subsequent chunks: smooth, phrase-level output
- interruption: immediate stop + next turn takeover
