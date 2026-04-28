# Voice2 Phase 2 Audit Report (2026-04-28)

Graph-based code navigation unavailable - analysis performed via runtime tests + config inspection (endpoint outputs only). Repo file reads and deep code tracing were intentionally skipped.

## 1. System Overview

- Services responding on all health endpoints (Whisper, LLM, TTS, Intent).
- Intent service reports `fallback_mode: true`, indicating degraded intent pipeline or upstream dependency not engaged.
- LLM metrics show zero samples and zero latency, suggesting no active inference calls during the audit window.
- TTS runtime settings and streaming config are reachable and consistent with expected chunking values.

## 2. Test Execution Summary

- Curl health and settings checks: success (all four services OK).
- LLM direct test (POST /llm/generate): 404 Not Found (no response generated).
- Pytest (non-interactive): 93 passed, 1 deselected, 12 warnings.
- Interactive test excluded by design: [tests/test_speech_recognition_interactive.py](tests/test_speech_recognition_interactive.py) requires stdin, not automation-safe.
- Warnings: FastAPI `on_event` deprecation and Pydantic v2 config/json deprecations surfaced during tests.

## 3. MCP Evaluation

### Builtin MCP (direct calls)

- `browser-search.search_web`: returned results successfully.
- `file-search.search_files`: returned matches (including references to `mcp_config.json`).
- `music-control.set_volume`: returned updated volume state.

### Docker MCP (direct calls)

- Docker-based MCP servers report `status: error` with reason: docker server not running.
- Direct call to docker MCP `time` server timed out (expected because docker MCP servers are down).

### Agent-loop MCP usage

- Agent loop call with `max_steps=1` timed out (no response within 30s).
- This blocks any confirmation of tool selection/parameterization via agent loop.

## 4. Agent System Analysis

- Agent loop timeout prevents verification of tool orchestration from prompt -> tool -> response.
- LLM metrics at zero imply the agent loop is not invoking LLM inference or requests are failing before metrics are recorded.
- Bottleneck suspicion: agent loop endpoint is blocked or waiting on unavailable tool/runtime resources.

## 5. Voice Pipeline (VAD + TTS + Duplex)

- TTS service is responsive and streaming chunk configuration is reachable.
- No live VAD/duplex interaction was executed in this run; latency and interruption behavior not directly measured.
- Intent service fallback mode suggests partial pipeline degradation in conversational flow.

## 6. Frontend-Backend Integration Audit

- Full contract drift review is blocked without repo reads (graph tools unavailable).
- Runtime signals indicate possible integration drift:
  - LLM metrics show no activity despite services being up.
  - Agent loop timed out, suggesting frontend workflow may be hitting a blocked backend path.
- Recommended to cross-check API contracts and UI flows against backend endpoints once file access is unblocked.

## 7. Latency & Bottlenecks

- LLM latency metrics show limited samples; cannot quantify reliably; this itself is a major diagnostic signal.
- Agent loop timeouts suggest end-to-end latency exceeds 30s or the loop is deadlocked.
- Docker MCP dependencies are offline, blocking any tools that rely on docker MCP servers.
- Intent service fallback mode likely bypasses normal intent classification, increasing response latency or reducing capability.
- Direct LLM endpoint returns 404, indicating a wiring or routing mismatch for inference calls.

## 8. Root Cause Analysis (provisional)

- Docker MCP unavailable -> tool catalog reduced -> agent/tool flow is constrained.
- Agent loop endpoint unresponsive -> central execution path is blocked (likely in LLM/tool selection or tool invocation).
- Intent fallback mode -> core pipeline is operating in a degraded mode.
- LLM direct endpoint returns 404 -> inference path is not exposed at /llm/generate or not wired.

## 9. Recommendations (production-grade)

1. Restore docker MCP runtime or disable docker MCP entries if not required, to avoid tool graph dead-ends.
2. Instrument agent loop with explicit timeout/trace logging and expose latency metrics for each step.
3. Add non-interactive test markers to exclude stdin-dependent tests in CI by default.
4. Restore or expose the correct LLM inference endpoint and align frontend/agent callers to it.
5. Add synthetic load (single LLM request) during health checks to validate LLM metrics and ensure inference is wired.
6. Once graph tools are available, perform full contract drift audit: UI -> API -> orchestrator mapping, and verify multi-step execution alignment.

---

### Appendices (runtime outputs summarized)

- Health checks: all services OK; Intent `fallback_mode: true`.
- LLM direct test: POST `/llm/generate` returned 404 Not Found.
- LLM metrics: `samples: 2`, `avg: 342103.49`, `p95: 342103.49` (values did not align with a successful direct response).
- TTS settings: `edge` backend, chunking values present (`5/14/140`).
- MCP catalog: builtin tools available; docker MCP servers in error.
