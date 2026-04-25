# Voice2

YC-style summary: we are building the fastest local voice assistant for Windows laptops.

## Problem
Current local assistants feel slow, robotic, and fragile under interruption.

## Solution
Voice2 is a local-first, low-latency voice stack with:
- Whisper ASR
- Ollama LLM streaming
- Edge/Piper/Fish-Speech TTS
- Intent routing + memory
- interruption-safe duplex orchestration

## Why now
Consumer laptops now have enough GPU/CPU throughput for practical on-device assistants without cloud dependency.

## What improved recently
- True decoupled LLM->TTS streaming (producer/consumer)
- Adaptive chunking: fast first speech, smoother follow-up speech
- Runtime TTS settings API (`/settings`, `/streaming-config`)
- Slightly faster Edge default speech (`edge_base_rate_pct=8`)
- Earlier no-tag flushes for lower first-audio latency
- Better interruption handling and stale-generation guards

## Core architecture
- `services/whisper_service.py` - ASR
- `services/llm_service.py` - provider gateway
- `services/tts_service.py` - synthesis + playback control
- `orchestrator/pipeline.py` - turn pipeline + timing
- `streaming/llm_streamer.py` - token stream
- `streaming/tts_streamer.py` - adaptive speech chunk stream
- `duplex/` - interrupt and stream management

## TTS runtime control API
- `GET /settings`
- `POST /settings`
- `POST /settings/reset`
- `GET /streaming-config`

Important tunables:
- `edge_base_rate_pct`
- `edge_offline_fallback_enabled`
- `edge_offline_check_url`
- `edge_offline_check_timeout_sec`
- `edge_offline_state_ttl_sec`
- `edge_timeout_sec`
- `chunk_initial_words`
- `chunk_steady_words`
- `chunk_max_chars`
- `piper_api_url`
- `piper_voice` (defaults to `en_US-lessac-medium`, a female Piper voice)
- `piper_speaker_id`
- `fish_speech_api_url`

Offline routing behavior:
- If backend is `edge` and network probe says offline, TTS routes directly to Piper.
- If backend is `edge` but Edge synthesis errors/times out, TTS falls back to Piper.

Model cache behavior:
- `setup.ps1` and `start_stack.ps1` export `HF_HOME=.hf_cache` and `HUGGINGFACE_HUB_CACHE=.hf_cache/hub`.
- This keeps downloaded CPU models cached locally so they are reused instead of downloaded repeatedly.

## Quick start
```powershell
.\setup.ps1
.\start_stack.ps1 -ServicesOnly
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8002/health
curl http://127.0.0.1:8003/health
curl http://127.0.0.1:8004/health
.\start_stack.ps1
```

## Duplex troubleshooting (Windows mic)
If duplex fails with `PortAudioError ... MME error 1`:
1. Verify Windows microphone privacy permission.
2. Close apps that lock microphone input.
3. Re-select default input device in Windows sound settings.
4. Re-run `start_stack.ps1`.

## Validation
Run tests:
```powershell
.\venv\python.exe -m pytest tests -q
```

For focused streaming validation:
```powershell
.\venv\python.exe -m pytest -q tests/test_streaming.py
```
