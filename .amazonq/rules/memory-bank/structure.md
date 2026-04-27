# Voice2 - Project Structure

## Directory Layout

```
voice2/
├── core/                    # Shared config and device management
│   ├── config.py            # Pydantic Settings (env-driven, lru_cache singleton)
│   └── device_manager.py    # Hardware/device abstraction
│
├── services/                # Microservice entry points (each runs as FastAPI app)
│   ├── whisper_service.py   # ASR: faster-whisper transcription (port 8001)
│   ├── llm_service.py       # LLM gateway with provider routing (port 8002)
│   ├── tts_service.py       # TTS synthesis + ordered playback engine (port 8003)
│   ├── intent_service.py    # ONNX intent classifier (port 8004)
│   ├── audio_playback_engine.py  # Chunk-ordered PCM playback via sounddevice
│   ├── router.py            # Service-level HTTP routing helpers
│   ├── llm_service.py       # LLM provider gateway
│   ├── llm_models.py        # LLM request/response models
│   ├── llm_metrics.py       # Per-request LLM timing metrics
│   ├── mcp_tools.py         # MCP tool integration
│   ├── mcp_docker_bridge.py # Docker-based MCP bridge
│   ├── stream_manager.py    # Service-level stream lifecycle
│   ├── dev_manager.py       # Dev/debug utilities
│   ├── providers/           # LLM provider implementations
│   │   ├── ollama.py        # Ollama streaming client
│   │   ├── lmstudio.py      # LM Studio client
│   │   ├── openai.py        # OpenAI-compatible client
│   │   ├── custom.py        # Custom endpoint client
│   │   └── common.py        # Shared provider utilities
│   └── agent_control/       # Multi-step agent execution
│       ├── control_plane.py # Agent orchestration
│       ├── executor.py      # Tool execution
│       ├── intent_router.py # Agent intent routing
│       ├── planner.py       # Task planning
│       ├── catalog.py       # Tool catalog
│       ├── schema.py        # Agent schemas
│       ├── types.py         # Agent type definitions
│       ├── security.py      # Agent security checks
│       ├── validation.py    # Input validation
│       └── observability.py # Agent tracing/logging
│
├── orchestrator/            # Turn pipeline and context assembly
│   ├── pipeline.py          # run_pipeline / run_pipeline_streaming (core turn logic)
│   ├── context_engine.py    # build_prompt: assembles system+history+memory+emotion
│   ├── memory_buffer.py     # ConversationBuffer: rolling turn history
│   ├── gpu_lock.py          # asynccontextmanager GPU serialization lock
│   └── main.py              # Orchestrator FastAPI app entry point
│
├── streaming/               # Token and audio streaming
│   ├── llm_streamer.py      # stream_llm(): async generator over Ollama token stream
│   └── tts_streamer.py      # stream_tts_from_tokens(): adaptive chunking + TTS dispatch
│
├── duplex/                  # Real-time duplex audio management
│   ├── audio_listener.py    # Microphone input listener
│   ├── interrupt_controller.py  # Interrupt trigger + cancellation event
│   ├── speech_capture.py    # VAD-gated speech segment capture
│   ├── state_machine.py     # AssistantState enum + AssistantStateController
│   ├── stream_manager.py    # Duplex stream lifecycle
│   └── vad_engine.py        # Voice activity detection
│
├── humanization/            # Emotion and speech quality
│   ├── emotion_engine.py    # EmotionEngine: tracks emotional state across turns
│   ├── emotion_tagger.py    # parse/strip/format emotion tags; EmotionStreamBuffer
│   ├── prosody_engine.py    # apply_prosody(): rate/pitch adjustments
│   ├── speech_normalizer.py # markdown_to_speech(): strips markdown for TTS
│   └── voice_style.py       # Voice style profiles (INDIAN_NEUTRAL_FEMALE etc.)
│
├── memory/                  # Semantic memory
│   ├── memory_manager.py    # MemoryManager: add_interaction, retrieve, format_memories
│   ├── vector_store.py      # Qdrant client wrapper
│   └── embedding_model.py   # SentenceTransformer embedding wrapper
│
├── performance/             # Observability
│   ├── metrics_logger.py    # log_metrics(): structured JSON timing logs
│   └── profiler.py          # Optional profiling utilities
│
├── monitoring/
│   └── resource_monitor.py  # CPU/GPU/memory resource monitoring
│
├── frontend/                # React/TypeScript SPA
│   ├── src/
│   │   ├── App.tsx          # Root component
│   │   ├── core/            # API clients, state management
│   │   ├── hooks/           # React hooks
│   │   └── ui/              # UI components
│   ├── package.json         # Node dependencies (React, Vite, TypeScript)
│   └── vite.config.ts       # Vite build config
│
├── logs/
│   └── dev-manager/         # Per-service runtime logs (written by dev_manager.py)
│       ├── llm.log          # LLM service stdout/stderr (inspect for 502 tracebacks)
│       ├── tts.log
│       ├── whisper.log
│       └── intent.log
├── tests/                   # pytest test suite
├── models/                  # Local model files (faster-whisper-small)
├── qdrant_data/             # Qdrant vector DB persistence
├── prompts/
│   └── system_prompt.txt    # LLM system prompt
├── core/config.py           # Single Settings source of truth
├── requirements.txt         # Python dependencies
├── setup.ps1                # One-time environment setup
└── start_stack.ps1          # Launch all services
```

## Core Architectural Patterns

### Microservice Decomposition
Each capability (ASR, LLM, TTS, Intent) runs as an independent FastAPI service on a dedicated port. Services communicate via HTTP (httpx async client).

### Streaming Pipeline
```
Microphone → VAD → Whisper ASR → Intent → Memory Retrieval
→ Emotion Update → build_prompt → stream_llm (token generator)
→ EmotionStreamBuffer → adaptive chunking → /speak (TTS)
→ AudioPlaybackEngine (ordered PCM queue)
```

### Cancellation / Interruption Chain
`asyncio.Event` (cancellation_event) + `InterruptController.is_triggered()` + `is_generation_current_fn()` — checked at every async boundary in pipeline, llm_streamer, and tts_streamer.

### Configuration
Single `Settings` (pydantic-settings) loaded once via `get_settings()` (lru_cache). All services import from `core.config`. Runtime TTS overrides stored in `_runtime_settings` (module-level, protected by `threading.Lock`).

### Generation Tracking
Monotonically increasing `generation_id` passed through pipeline → llm_streamer → tts_streamer → TTS service `/speak`. Stale requests (generation_id < current) are dropped at the TTS service boundary.

### Dev-Manager Auto-Reload
Auto-reload is implemented via `dev_manager.py` restart logic (PID rotation), NOT `uvicorn --reload` on each service. Verified working: file change on `llm_service.py` produced PID rotation `before=9872 → after=30912` (`reload=ok`). Child service output goes to `logs/dev-manager/<service>.log` (not DEVNULL) for traceback inspection.

### Agent Loop Stability (verified fixes)
- `control_plane.py`: parse failures no longer dereference `suggested` before any step is recorded — eliminates `steps=[] 502` crash path
- `agent_control/__init__.py` + `llm_service.py`: broad-except paths now log full tracebacks (not just `"Agent loop failed"`)
- `dev_manager.py`: child stdout/stderr redirected from DEVNULL to `logs/dev-manager/<service>.log`

### Debugging 502 on /agent/loop
1. `.\ start_stack.ps1 -UseDevManager`
2. Trigger `/agent/loop`
3. `Get-Content logs\dev-manager\llm.log -Tail 50` for exact traceback
4. Or run `.\show_service_logs.ps1` to monitor all services in real-time

### Dynamic Tool-Aware Agent
The agent system dynamically loads all available tools from MCP registries at runtime:
- `catalog.py`: `load_catalog()` discovers tools from builtin, custom, and Docker MCP servers
- `intent_router.py`: Priority-based routing matches query keywords to tool categories:
  - Time queries → time tools (highest priority)
  - URL/webpage queries → fetch tools
  - File queries → file tools
  - Storage queries → save/write tools
  - General knowledge → search tools (lowest priority, fallback only)
- `planner.py`: `build_planner_prompt()` presents full tool details to LLM with:
  - Tool name, server, category, "Best used for" description
  - Required and optional arguments with types and descriptions
  - Tool selection strategy (prefer specialized over generic)
  - Decision process with examples (time→time tools, content→fetch, knowledge→search)
- LLM receives comprehensive tool information for intelligent capability-aware selection
- No hardcoded tool lists — all tools auto-discovered, routing via keyword matching

### Schema Architecture (Type Safety)
- `CatalogTool`: raw tool from MCP registry (has `.tool`, `.description`, `.input_schema`)
- `ToolSchema`: normalized internal contract (has `.name`, `.required_args`, `.optional_args`)
- `PlannerAction`: LLM output representation (has `.tool`, `.server`, `.arguments`)
- Flow: `CatalogTool` → `normalize_schemas()` → `ToolSchema` → planner → `PlannerAction`
- Planner receives both `schema_map` (normalized) and `catalog` (full details) for rich prompts
