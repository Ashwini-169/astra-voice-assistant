from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
import requests
from fastapi import FastAPI, HTTPException

ROOT_DIR = Path(__file__).resolve().parents[1]
LOCAL_PYTHON = ROOT_DIR / "venv" / "python.exe"
DEFAULT_PYTHON = LOCAL_PYTHON if LOCAL_PYTHON.exists() else Path(sys.executable)


@dataclass(frozen=True)
class ServiceConfig:
    name: str
    module: str
    port: int
    timeout_seconds: int

    @property
    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/health"

    @property
    def command(self) -> list[str]:
        return [
            str(DEFAULT_PYTHON),
            "-m",
            "uvicorn",
            f"{self.module}:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        ]


class ServiceManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._watcher_thread: threading.Thread | None = None
        self._bootstrap_thread: threading.Thread | None = None

        self._configs: dict[str, ServiceConfig] = {
            "whisper": ServiceConfig("whisper", "services.whisper_service", 8001, 180),
            "llm": ServiceConfig("llm", "services.llm_service", 8002, 180),
            "tts": ServiceConfig("tts", "services.tts_service", 8003, 120),
            "intent": ServiceConfig("intent", "services.intent_service", 8004, 120),
        }
        self._order = ["whisper", "llm", "tts", "intent"]
        self._processes: dict[str, subprocess.Popen[Any] | None] = {name: None for name in self._configs}
        self._restart_count: dict[str, int] = {name: 0 for name in self._configs}
        self._last_reason: dict[str, str] = {name: "never" for name in self._configs}
        self._last_error: dict[str, str | None] = {name: None for name in self._configs}
        self._last_start_at: dict[str, float | None] = {name: None for name in self._configs}

        self._file_map = {
            "services/tts_service.py": "tts",
            "services/llm_service.py": "llm",
            "services/whisper_service.py": "whisper",
            "services/intent_service.py": "intent",
        }
        self._global_roots = {
            "core",
            "duplex",
            "humanization",
            "memory",
            "orchestrator",
            "performance",
            "prompts",
            "streaming",
        }
        self._watch_roots = [
            ROOT_DIR / "services",
            ROOT_DIR / "core",
            ROOT_DIR / "duplex",
            ROOT_DIR / "humanization",
            ROOT_DIR / "memory",
            ROOT_DIR / "orchestrator",
            ROOT_DIR / "performance",
            ROOT_DIR / "prompts",
            ROOT_DIR / "streaming",
        ]

    def start(self) -> None:
        self._bootstrap_thread = threading.Thread(
            target=self.start_all,
            kwargs={"reason": "startup"},
            name="dev-manager-bootstrap",
            daemon=True,
        )
        self._bootstrap_thread.start()
        self._watcher_thread = threading.Thread(target=self._watch_loop, name="dev-manager-watcher", daemon=True)
        self._watcher_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        for service_name in self._order:
            self._stop_service(service_name)

    def list_status(self) -> dict[str, Any]:
        now = time.time()
        payload: dict[str, Any] = {}
        with self._lock:
            for service_name in self._order:
                cfg = self._configs[service_name]
                proc = self._processes[service_name]
                pid = proc.pid if proc and proc.poll() is None else None
                started_at = self._last_start_at[service_name]
                payload[service_name] = {
                    "port": cfg.port,
                    "pid": pid,
                    "running": pid is not None,
                    "healthy": self._is_healthy(cfg),
                    "restarts": self._restart_count[service_name],
                    "last_reason": self._last_reason[service_name],
                    "last_error": self._last_error[service_name],
                    "uptime_seconds": int(now - started_at) if started_at else None,
                }
        return payload

    def start_all(self, reason: str) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for service_name in self._order:
            results[service_name] = self.restart(service_name, reason=reason)
        return results

    def restart(self, service_name: str, reason: str) -> dict[str, Any]:
        if service_name not in self._configs:
            raise KeyError(service_name)

        cfg = self._configs[service_name]
        self._stop_service(service_name)

        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("AI_ASSISTANT_TTS_BACKEND", "edge")

        process = subprocess.Popen(
            cfg.command,
            cwd=str(ROOT_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )

        with self._lock:
            self._processes[service_name] = process
            self._restart_count[service_name] += 1
            self._last_reason[service_name] = reason
            self._last_error[service_name] = None
            self._last_start_at[service_name] = time.time()

        healthy = self._wait_healthy(cfg, timeout_seconds=cfg.timeout_seconds)
        if not healthy:
            message = f"{service_name} did not become healthy within {cfg.timeout_seconds}s"
            with self._lock:
                self._last_error[service_name] = message
            return {"service": service_name, "healthy": False, "message": message}

        return {
            "service": service_name,
            "healthy": True,
            "pid": process.pid,
            "message": f"{service_name} restarted",
        }

    def _stop_service(self, service_name: str) -> None:
        cfg = self._configs[service_name]
        proc: subprocess.Popen[Any] | None
        with self._lock:
            proc = self._processes[service_name]

        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

        self._kill_port_listeners(cfg.port, exclude_pid=os.getpid())

        with self._lock:
            self._processes[service_name] = None

    def _kill_port_listeners(self, port: int, exclude_pid: int | None = None) -> None:
        for conn in psutil.net_connections(kind="inet"):
            if not conn.laddr or conn.laddr.port != port or not conn.pid:
                continue
            if exclude_pid and conn.pid == exclude_pid:
                continue
            try:
                proc = psutil.Process(conn.pid)
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except psutil.TimeoutExpired:
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _wait_healthy(self, cfg: ServiceConfig, timeout_seconds: int) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._is_healthy(cfg):
                return True
            time.sleep(1.0)
        return False

    @staticmethod
    def _is_healthy(cfg: ServiceConfig) -> bool:
        try:
            response = requests.get(cfg.health_url, timeout=3)
            return response.status_code == 200
        except requests.RequestException:
            return False

    def _snapshot_files(self) -> dict[str, float]:
        snapshot: dict[str, float] = {}
        for root in self._watch_roots:
            if not root.exists():
                continue
            for path in root.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                try:
                    rel = path.relative_to(ROOT_DIR).as_posix()
                    snapshot[rel] = path.stat().st_mtime
                except (OSError, ValueError):
                    continue
        return snapshot

    def _map_changed_path_to_services(self, rel_path: str) -> set[str]:
        if rel_path == "services/dev_manager.py":
            return set()

        if rel_path in self._file_map:
            return {self._file_map[rel_path]}

        if rel_path.startswith("services/"):
            name = Path(rel_path).name
            if name.endswith("_service.py"):
                service_name = name.replace("_service.py", "")
                if service_name in self._configs:
                    return {service_name}
            return set(self._order)

        first = rel_path.split("/", 1)[0]
        if first in self._global_roots:
            return set(self._order)

        return set()

    def _watch_loop(self) -> None:
        last_snapshot = self._snapshot_files()
        pending: dict[str, float] = {}
        debounce_seconds = 0.8

        while not self._stop_event.is_set():
            time.sleep(1.0)
            current = self._snapshot_files()

            changed_paths: set[str] = set()
            for rel_path, mtime in current.items():
                if last_snapshot.get(rel_path) != mtime:
                    changed_paths.add(rel_path)
            for rel_path in last_snapshot:
                if rel_path not in current:
                    changed_paths.add(rel_path)

            if changed_paths:
                now = time.time()
                for rel_path in changed_paths:
                    for service_name in self._map_changed_path_to_services(rel_path):
                        pending[service_name] = now

            if pending:
                now = time.time()
                ready = [name for name, ts in pending.items() if (now - ts) >= debounce_seconds]
                for service_name in ready:
                    self.restart(service_name, reason="file-change")
                    pending.pop(service_name, None)

            last_snapshot = current


manager = ServiceManager()
app = FastAPI(title="Voice2 Dev Manager", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    manager.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    manager.stop()


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/status")
def status() -> dict[str, Any]:
    return {"services": manager.list_status()}


@app.post("/reload/{service_name}")
def reload_service(service_name: str) -> dict[str, Any]:
    if service_name not in manager._configs:
        raise HTTPException(status_code=404, detail=f"Unknown service: {service_name}")
    return manager.restart(service_name, reason="api")


@app.post("/reload-all")
def reload_all() -> dict[str, Any]:
    return {"results": manager.start_all(reason="api")}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=3900,
        reload=False,
        log_level="info",
    )
