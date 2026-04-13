# Quick Start

## 1) Setup
```powershell
.\setup.ps1
```

## 2) Start services
```powershell
.\start_stack.ps1 -ServicesOnly
```

## 3) Health checks
```powershell
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8002/health
curl http://127.0.0.1:8003/health
curl http://127.0.0.1:8004/health
```

## 4) Run duplex
```powershell
.\start_stack.ps1
```

## Runtime TTS tuning (no restart required)
Read current settings:
```powershell
curl http://127.0.0.1:8003/settings
```

Apply balanced low-latency preset:
```powershell
curl -X POST http://127.0.0.1:8003/settings -H "Content-Type: application/json" -d '{"backend":"edge","edge_base_rate_pct":8,"chunk_initial_words":5,"chunk_steady_words":14,"chunk_max_chars":140}'
```

Faster but slightly less natural:
```powershell
curl -X POST http://127.0.0.1:8003/settings -H "Content-Type: application/json" -d '{"chunk_initial_words":4,"chunk_steady_words":10,"edge_base_rate_pct":12}'
```

More natural but slower first speech:
```powershell
curl -X POST http://127.0.0.1:8003/settings -H "Content-Type: application/json" -d '{"chunk_initial_words":7,"chunk_steady_words":18,"edge_base_rate_pct":6}'
```

Reset:
```powershell
curl -X POST http://127.0.0.1:8003/settings/reset
```

## Useful commands
```powershell
.\venv\python.exe -m pytest tests -q
.\venv\python.exe -m orchestrator.main --text "hello"
```

## Mic failure (`MME error 1`) quick fix
- Enable microphone access in Windows privacy settings.
- Close Zoom/Teams/OBS/browser tabs that may hold exclusive mic access.
- Re-select your preferred default input device in Windows sound settings.
