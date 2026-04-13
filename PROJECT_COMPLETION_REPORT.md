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
