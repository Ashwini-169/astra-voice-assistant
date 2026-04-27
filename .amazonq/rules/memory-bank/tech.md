# Voice2 - Technology Stack

## Backend (Python)

### Runtime
- Python 3.11+ (venv at `./venv/python.exe`)
- All services launched via `uvicorn` ASGI server

### Core Frameworks
| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | 0.115.0 | HTTP microservice framework |
| uvicorn[standard] | 0.30.6 | ASGI server |
| pydantic | 2.8.2 | Data validation and models |
| pydantic-settings | 2.4.0 | Env-driven configuration |
| httpx | 0.27.0 | Async HTTP client (inter-service calls) |
| requests | 2.32.3 | Sync HTTP (Piper/Fish-Speech, monkeypatchable in tests) |

### AI / ML
| Package | Version | Purpose |
|---------|---------|---------|
| faster-whisper | 1.0.3 | CTranslate2-based Whisper ASR |
| ctranslate2 | 4.4.0 | Optimized inference engine |
| onnxruntime-directml | 1.18.0 | DirectML GPU acceleration (Windows) |
| torch | 2.4.1 | PyTorch (embeddings, VAD) |
| sentence-transformers | 3.0.1 | all-MiniLM-L6-v2 embeddings for memory |
| numpy | 1.26.4 | PCM audio array processing |

### Audio
| Package | Version | Purpose |
|---------|---------|---------|
| sounddevice | 0.4.7 | Microphone input + PCM playback |
| miniaudio | ≥1.60 | MP3 decode to PCM float32 |
| edge-tts | 7.2.8 | Microsoft Edge Neural TTS |

### Storage
| Package | Version | Purpose |
|---------|---------|---------|
| qdrant-client | 1.12.1 | Local vector store for semantic memory |

### Testing
| Package | Version | Purpose |
|---------|---------|---------|
| pytest | 8.3.2 | Test runner |

### System Utilities
| Package | Version | Purpose |
|---------|---------|---------|
| psutil | 5.9.8 | Resource monitoring |
| python-multipart | 0.0.9 | FastAPI file upload support |

## Frontend (TypeScript)

### Runtime
- Node.js + npm
- TypeScript ~6.0.2

### Frameworks & Libraries
| Package | Version | Purpose |
|---------|---------|---------|
| react | ^19.2.4 | UI framework |
| react-dom | ^19.2.4 | DOM rendering |
| zustand | ^5.0.12 | State management |
| three | ^0.183.2 | 3D graphics (visual feedback) |
| lucide-react | ^1.8.0 | Icon library |
| tailwindcss | ^4.2.2 | Utility CSS |
| vite | ^8.0.4 | Build tool + dev server |

## External Dependencies (must be installed separately)

| Tool | Purpose | Default URL |
|------|---------|-------------|
| Ollama | LLM inference server | http://127.0.0.1:11434 |
| Piper TTS server | Offline TTS fallback | http://127.0.0.1:59125 |
| Fish-Speech / OpenAudio S1 Mini | Expressive TTS | http://127.0.0.1:8080 |
| Qdrant | Vector DB (embedded via qdrant-client) | local file |

## Environment Variables (prefix: `AI_ASSISTANT_`)

```
AI_ASSISTANT_LLM_MODEL=qwen2.5:3b
AI_ASSISTANT_LLM_PROVIDER=ollama
AI_ASSISTANT_LLM_NUM_PREDICT=300
AI_ASSISTANT_LLM_NUM_CTX=2048
AI_ASSISTANT_LLM_TEMPERATURE=0.7
AI_ASSISTANT_TTS_BACKEND=edge
AI_ASSISTANT_PIPER_VOICE=en_US-lessac-medium
HF_HOME=.hf_cache
HUGGINGFACE_HUB_CACHE=.hf_cache/hub
```

## Development Commands

### Setup
```powershell
.\setup.ps1                          # Create venv, install requirements
```

### Start Services
```powershell
.\start_stack.ps1 -ServicesOnly      # Start all 4 services only
.\start_stack.ps1                    # Start services + duplex conversation mode
.\start_stack.ps1 -ShowTtsLogs       # Show TTS service window
.\start_stack.ps1 -UseDevManager     # Use dev manager (auto-reload + per-service log files)
```

### Dev-Manager Log Files
When using `-UseDevManager`, child service output goes to `logs/dev-manager/<service>.log`:
```powershell
Get-Content logs\dev-manager\llm.log -Tail 50      # Inspect LLM tracebacks
Get-Content logs\dev-manager\tts.log -Tail 50
Get-Content logs\dev-manager\whisper.log -Tail 50
Get-Content logs\dev-manager\intent.log -Tail 50
```

### Live Log Viewer (All Services)
Open all 4 service logs in separate windows with live tail:
```powershell
.\show_service_logs.ps1
```
Each window shows color-coded output (errors in red, warnings in yellow, info in service-specific colors).

### Health Checks
```powershell
curl http://127.0.0.1:8001/health    # Whisper
curl http://127.0.0.1:8002/health    # LLM
curl http://127.0.0.1:8003/health    # TTS
curl http://127.0.0.1:8004/health    # Intent
```

### Testing
```powershell
.\venv\python.exe -m pytest tests -q
.\venv\python.exe -m pytest -q tests/test_streaming.py
```

### Frontend
```powershell
cd frontend
npm install
npm run dev      # Dev server
npm run build    # Production build
npm run lint     # ESLint
```

### TTS Runtime API
```powershell
curl http://127.0.0.1:8003/settings                    # GET current settings
curl -X POST http://127.0.0.1:8003/settings -d '{...}' # PATCH settings
curl -X POST http://127.0.0.1:8003/settings/reset       # Reset to defaults
curl http://127.0.0.1:8003/streaming-config             # GET chunk profile
```

## Model Cache
- HuggingFace models cached in `.hf_cache/` (set by setup.ps1 and start_stack.ps1)
- Whisper model: `models/faster-whisper-small/` (local copy)
- Qdrant data: `qdrant_data/`
