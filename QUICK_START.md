# Quick Start (5 Minutes)

## 1) Clone and open
```powershell
git clone <your-repo-url>
cd voice2
```

## 2) One-time setup
```powershell
.\setup.ps1
```

Optional (downloads model in advance):
```powershell
.\setup.ps1 -PullModel
```

## 3) Start services only
```powershell
.\start_stack.ps1 -ServicesOnly
```

## 4) Verify APIs
```powershell
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8002/health
curl http://127.0.0.1:8003/health
curl http://127.0.0.1:8004/health
```

## 5) Run duplex mode
```powershell
.\start_stack.ps1
```

## Useful commands
Run tests:
```powershell
.\venv\python.exe -m pytest tests -q
```

Run text mode:
```powershell
.\venv\python.exe -m orchestrator.main --text "hello"
```
