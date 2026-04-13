# Project Completion Report

Date: 2026-04-13
Status: Shipping iteratively with production-focused improvements.

## YC-style snapshot
- Problem: local assistants are too slow/choppy and fail badly under interruption.
- Product: a local-first voice stack with fast first response and robust interruption.
- Wedge: superior perceived latency on commodity Windows hardware.
- Moat: tight orchestration + adaptive chunking + runtime tuning surface.

## Recent shipped improvements
- Decoupled LLM token intake from TTS HTTP send path.
- Adaptive TTS chunk strategy:
  - first chunk small (fast first speech)
  - later chunks larger (natural flow)
- Runtime TTS settings API:
  - `GET/POST /settings`
  - `POST /settings/reset`
  - `GET /streaming-config`
- Edge speech speed baseline raised slightly (`edge_base_rate_pct=8`).
- Stream buffer now flushes earlier on no-tag text.
- Better generation/staleness guards for interrupt safety.

## Quality status
- Syntax checks pass on updated streaming/TTS modules.
- Targeted streaming tests pass for:
  - first-audio before completion
  - early interrupt exit
  - chunk coalescing quality

## Remaining risks
- Windows microphone host/backend instability (`MME error 1`) can still occur depending on OS/device state.
- Test environments missing optional deps may fail broader suites.

## Next priorities
1. Harden audio input fallback and degraded mode for duplex start.
2. Add one-call tuning presets (`fast`, `balanced`, `natural`).
3. Add continuous benchmark scripts for first-audio and interruption latency.

## Dev Manager: startup fix (2026-04-13)

- **Root cause:** `dev-manager` FastAPI startup was blocked by `start_all()`, which waits for Whisper/LLM/TTS/Intent. Because startup blocked, the `/health` endpoint did not come up in time for `start_stack.ps1` to detect service readiness.
- **Fix applied:** `start_all(reason="startup")` now runs in a background bootstrap thread instead of blocking startup. Also replaced `uvicorn.run("services.dev_manager:app", ...)` with `uvicorn.run(app, ...)` to avoid self-import/module-path issues when launching as a script.
- **Validated:** Python syntax compile passes for `services/dev_manager.py`.
- **How to validate locally:**
  - Run: `.
    start_stack.ps1 -UseDevManager -ServicesOnly`
  - If it still fails, run once to capture the traceback:
    `venv\python.exe services\dev_manager.py`

This change should make `http://127.0.0.1:3900/health` respond quickly while services continue booting in the background.

## Frontend Integration: Full Control API Layer (2026-04-13)

### Service base URLs (default local ports)
- Whisper: `http://127.0.0.1:8001`
- LLM: `http://127.0.0.1:8002`
- TTS: `http://127.0.0.1:8003`
- Intent: `http://127.0.0.1:8004`
- Dev Manager: `http://127.0.0.1:3900`

### LLM service (`:8002`)

#### Core generation and control
- `POST /generate`
  - Request:
    - `prompt: string` (required)
    - `provider?: "ollama" | "lmstudio" | "openai" | "custom"`
    - `model?: string`
    - `stream?: boolean`
    - `temperature?: number`
    - `max_tokens?: number`
    - `top_p?: number`
    - `stop?: string[]`
    - `voice_mode?: boolean`
  - Response: `{ provider, model, response, request_id }`
  - Stream mode: NDJSON with `application/x-ndjson`.

- `GET /providers`
  - Response: active provider and configured provider list.

- `GET /models?provider=<name>`
  - Optional `provider` query: `ollama | lmstudio | openai | custom`
  - Response:
    - With provider: `{ provider, models: string[] }`
    - Without provider: `{ active_provider, models: { ollama, lmstudio, openai, custom } }`

- `GET /settings`
  - Response: effective runtime LLM settings.

- `POST /settings`
  - Partial update of runtime settings.
  - Request can include any subset of:
    - `provider, model, temperature, max_tokens, top_p, stop, stream, voice_mode`
    - `ollama_url, lmstudio_url, openai_url, openai_api_key`
    - `custom_url, custom_api_key, custom_mode`
  - Response: `{ status: "updated", settings: ... }`

- `POST /settings/reset`
  - Response: `{ status: "reset", settings: ... }`

- `POST /stop`
  - Cancels all active stream generations.
  - Response: `{ status: "stopped", cancelled_streams: number }`

#### Tooling and agent loop
- `GET /mcp/servers`
- `POST /mcp/servers`
  - Request: `{ name, base_url, description?, enabled?, tools?, auth_header? }`
- `DELETE /mcp/servers/{name}`
- `GET /mcp/tools?server=<server_name>`
- `POST /mcp/tools/call`
  - Request: `{ server, tool, arguments }`
- `POST /mcp/browser/search`
  - Request: `{ query, limit? }`
- `POST /mcp/files/search`
  - Request: `{ query, limit?, path? }`
- `POST /mcp/music/control`
  - Request: `{ action, value? }` where action in `play|pause|resume|stop|next|previous|set_volume`

- `POST /agent/loop`
  - Purpose: tool-aware loop (`LLM -> tool -> result -> LLM`).
  - Request:
    - `prompt: string`
    - `max_steps?: number` (bounded server-side)
    - `provider?, model?, temperature?`
  - Response: `{ status, steps: [...], response }`

#### Observability
- `GET /metrics`
  - Response includes:
    - `latency_ms` (`avg`, `p95`, `samples`)
    - `throughput` (`tokens_total_est`, `tokens_per_sec_est`)
    - `errors`, `error_rate`, `requests`

- `GET /health`
  - Response on success: `{ status: "ok", service: "llm", provider, model, backend_ready: true }`
  - Returns `503` if backend unavailable.

---

### Whisper service (`:8001`)

- `POST /transcribe` (multipart form-data)
  - Field: `audio_file` (audio file)
  - Response: `{ text, language, duration, segments }`

- `GET /health`
  - Response: `{ status: "ok", service: "whisper" }`

---

### TTS service (`:8003`)

- `POST /speak`
  - Request:
    - `text: string`
    - `emotion?: string`
    - `chunk_id?: number`
    - `generation_id?: number`
  - Response: `{ accepted, backend_status, backend }`

- `POST /stop`
  - Immediate playback stop + generation bump.
  - Response: `{ stopped, count, generation }`

- `GET /settings`
- `POST /settings`
  - Runtime tuning fields:
    - `backend` (`edge|piper|fish_speech`)
    - `piper_api_url`, `fish_speech_api_url`
    - `edge_default_voice`, `edge_base_rate_pct`
    - `chunk_initial_words`, `chunk_steady_words`, `chunk_max_chars`
- `POST /settings/reset`
- `GET /streaming-config`
  - Response: `chunk_initial_words`, `chunk_steady_words`, `chunk_max_chars`
- `GET /debug/playback`
- `GET /health`

---

### Intent service (`:8004`)

- `POST /classify`
  - Request: `{ text }`
  - Response: `{ label, scores, provider }`

- `GET /health`
  - Response: `{ status: "ok", service: "intent", fallback_mode }`

---

### Dev Manager service (`:3900`) for frontend ops panel

- `GET /health`
  - Response: `{ ok: true }`

- `GET /status`
  - Response: `{ services: { whisper|llm|tts|intent -> status object } }`

- `POST /reload/{service_name}`
  - `service_name`: `whisper | llm | tts | intent`

- `POST /reload-all`
  - Response: restart results for all services.

---

### Recommended frontend call flow
1. On app start:
   - check `/health` for all services
   - load `/providers`, `/models`, `/settings` (LLM) and `/settings` (TTS)
2. For voice turn:
   - Whisper `POST /transcribe`
   - LLM `POST /generate` (stream true for low latency UX)
   - TTS `POST /speak` per chunk (or from orchestrator)
3. On user interrupt:
   - LLM `POST /stop`
   - TTS `POST /stop`
4. For admin/debug panel:
   - LLM `GET /metrics`
   - TTS `GET /debug/playback`
   - Dev Manager `GET /status`, `POST /reload/{service_name}`
