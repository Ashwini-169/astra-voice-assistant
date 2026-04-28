"""Docker MCP STDIO Bridge.

Two execution modes per server:

- persistent (default): long-lived process, MCP handshake once, reuse for all calls.
  Works for: mcp/time, mcp/fetch, mcp/duckduckgo, mcp/youtube_transcript

- oneshot: fresh container per tool call, full MCP handshake inline, container exits.
  Works for: mcp/obsidian (and any image that exits after one request)
  Detected automatically when persistent start fails, or forced via
  register_server(oneshot=True).
"""
import json
#  
import logging
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# JSON-RPC 2.0 message IDs
_msg_counter = 0
_msg_lock = threading.Lock()


def _next_id() -> int:
    global _msg_counter
    with _msg_lock:
        _msg_counter += 1
        return _msg_counter


class MCPDockerServer:
    """A single Docker MCP server — persistent or one-shot.

    Persistent mode: process stays alive between calls (mcp/time, mcp/fetch).
    One-shot mode:   fresh container per call, full handshake inline (mcp/obsidian).
    """

    # Known one-shot images (substring match on any arg)
    _ONESHOT_IMAGES = {"obsidian"}

    def __init__(self, name: str, command: str, args: List[str],
                 env: Optional[Dict[str, str]] = None,
                 oneshot: Optional[bool] = None):
        self.name = name
        self.command = command
        self.args = args
        self.env = env or {}
        self.process: Optional[subprocess.Popen] = None
        self.tools: List[Dict[str, Any]] = []
        self.status: str = "stopped"
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._initialized = False
        # Auto-detect one-shot from image name if not forced
        if oneshot is None:
            joined = " ".join(args).lower()
            oneshot = any(img in joined for img in self._ONESHOT_IMAGES)
        self.oneshot: bool = oneshot

    # ── One-shot execution ────────────────────────────────────────────

    def _run_oneshot(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Spawn a fresh container, run full MCP handshake, call tool, return result."""
        import os
        run_env = os.environ.copy()
        run_env.update(self.env)

        try:
            proc = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=run_env,
                bufsize=0,
            )
        except Exception as exc:
            return {"ok": False, "error_type": "spawn", "status_code": 503,
                    "error": f"Failed to start container: {exc}"}

        def _write(obj: Dict[str, Any]) -> None:
            proc.stdin.write((json.dumps(obj) + "\n").encode())
            proc.stdin.flush()

        def _read(timeout: float = 15.0) -> Optional[Dict[str, Any]]:
            deadline = time.time() + timeout
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                try:
                    return json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue
            return None

        try:
            # 1. initialize
            _write({"jsonrpc": "2.0", "id": _next_id(), "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "astra-voice", "version": "1.0.0"}}})
            init_resp = _read(10.0)
            if not init_resp:
                proc.kill()
                return {"ok": False, "error_type": "timeout", "status_code": 504,
                        "error": "No initialize response from container"}

            # 2. initialized notification
            _write({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

            # 3. call tool
            call_id = _next_id()
            _write({"jsonrpc": "2.0", "id": call_id, "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments}})

            # Read until we get our call response
            deadline = time.time() + 20.0
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                try:
                    resp = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue
                if resp.get("id") == call_id:
                    if "error" in resp:
                        logger.error("[MCP:%s] oneshot RPC error: %s", self.name, resp["error"])
                        return {"ok": False, "error_type": "rpc", "status_code": 502,
                                "error": str(resp["error"])}
                    result = resp.get("result", {})
                    texts = [item.get("text", "") for item in result.get("content", [])
                             if item.get("type") == "text"]
                    return {"ok": True, "status_code": 200,
                            "result": "\n".join(texts) if texts else result}

            return {"ok": False, "error_type": "timeout", "status_code": 504,
                    "error": f"No tool response from '{self.name}'"}

        except Exception as exc:
            logger.error("[MCP:%s] oneshot failed: %s", self.name, exc)
            return {"ok": False, "error_type": "error", "status_code": 500, "error": str(exc)}
        finally:
            try:
                proc.stdin.close()
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    # ── Persistent mode ───────────────────────────────────────────────

    def _discover_tools_oneshot(self) -> List[Dict[str, Any]]:
        """Discover tools from a one-shot container (tools/list then exit)."""
        import os
        run_env = os.environ.copy()
        run_env.update(self.env)
        try:
            proc = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=run_env, bufsize=0,
            )
            init_id = _next_id()
            proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": init_id,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "astra-voice", "version": "1.0.0"}}}) + "\n").encode())
            proc.stdin.flush()
            # wait for init response
            deadline = time.time() + 8.0
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                try:
                    r = json.loads(line.decode().strip())
                    if r.get("id") == init_id:
                        break
                except json.JSONDecodeError:
                    continue
            proc.stdin.write((json.dumps({"jsonrpc": "2.0",
                "method": "notifications/initialized", "params": {}}) + "\n").encode())
            proc.stdin.flush()
            list_id = _next_id()
            proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": list_id,
                "method": "tools/list", "params": {}}) + "\n").encode())
            proc.stdin.flush()
            deadline = time.time() + 8.0
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                try:
                    r = json.loads(line.decode().strip())
                    if r.get("id") == list_id:
                        return r.get("result", {}).get("tools", [])
                except json.JSONDecodeError:
                    continue
        except Exception as exc:
            logger.warning("[MCP:%s] oneshot tool discovery failed: %s", self.name, exc)
        finally:
            try:
                proc.stdin.close()
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        return []

    def start(self) -> bool:
        """Start the server. One-shot servers discover tools then mark running."""
        if self.oneshot:
            # Discover tools via a throwaway container
            self.status = "starting"
            self.tools = self._discover_tools_oneshot()
            self.status = "running"
            logger.info("[MCP:%s] oneshot ready, %d tools: %s",
                        self.name, len(self.tools), [t.get("name") for t in self.tools])
            return True

        if self.process and self.process.poll() is None:
            logger.warning("[MCP:%s] Already running", self.name)
            return True

        self.status = "starting"
        try:
            import os
            run_env = os.environ.copy()
            run_env.update(self.env)

            self.process = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=run_env,
                bufsize=0,
            )
            logger.info("[MCP:%s] Process started (pid=%d)", self.name, self.process.pid)

            init_result = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "astra-voice", "version": "1.0.0"}
            })

            if init_result is None:
                # Persistent start failed — fall back to one-shot mode
                logger.warning("[MCP:%s] Persistent init failed, switching to oneshot mode", self.name)
                self.stop()
                self.oneshot = True
                return self.start()

            self._send_notification("notifications/initialized", {})
            self._initialized = True

            tools_result = self._send_request("tools/list", {})
            if tools_result and "tools" in tools_result:
                self.tools = tools_result["tools"]
                logger.info("[MCP:%s] Discovered %d tools: %s",
                            self.name, len(self.tools),
                            [t.get("name") for t in self.tools])
            else:
                self.tools = []
                logger.warning("[MCP:%s] No tools discovered", self.name)

            self.status = "running"
            return True

        except Exception as exc:
            logger.error("[MCP:%s] Failed to start: %s", self.name, exc)
            self.status = "error"
            self.stop()
            return False

    def stop(self):
        """Stop the persistent process (no-op for one-shot)."""
        if self.oneshot:
            self.status = "stopped"
            self.tools = []
            return
        if self.process:
            try:
                self.process.stdin.close()
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
        self.status = "stopped"
        self._initialized = False
        self.tools = []
        logger.info("[MCP:%s] Stopped", self.name)

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a tool — routes to oneshot or persistent path."""
        if self.oneshot:
            return self._run_oneshot(tool_name, arguments)

        if self.status != "running":
            return {
                "ok": False,
                "error_type": "unavailable",
                "status_code": 503,
                "error": f"Server '{self.name}' is not running (status: {self.status})",
            }

        result = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })

        if result is None:
            return {
                "ok": False,
                "error_type": "timeout",
                "status_code": 504,
                "error": f"No response from MCP server '{self.name}'",
            }

        if "content" in result:
            texts = [item.get("text", "") for item in result["content"]
                     if item.get("type") == "text"]
            return {
                "ok": True,
                "status_code": 200,
                "result": "\n".join(texts) if texts else str(result["content"]),
            }

        return {"ok": True, "status_code": 200, "result": result}

    def _send_request(self, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send a JSON-RPC request and wait for the response."""
        if not self.process or self.process.poll() is not None:
            logger.error("[MCP:%s] Process not running", self.name)
            return None

        msg_id = _next_id()
        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params,
        }

        try:
            with self._write_lock:
                payload = json.dumps(request) + "\n"
                self.process.stdin.write(payload.encode())
                self.process.stdin.flush()

            # Read response (with timeout)
            with self._read_lock:
                # Read lines until we get a response with our ID
                deadline = time.time() + 15  # 15s timeout
                while time.time() < deadline:
                    line = self.process.stdout.readline()
                    if not line:
                        break
                    line = line.decode().strip()
                    if not line:
                        continue
                    try:
                        response = json.loads(line)
                        if response.get("id") == msg_id:
                            if "error" in response:
                                logger.error("[MCP:%s] RPC error: %s", self.name, response["error"])
                                return None
                            return response.get("result", {})
                        # If it's a notification or different ID, skip
                    except json.JSONDecodeError:
                        continue

        except Exception as exc:
            logger.error("[MCP:%s] STDIO communication failed: %s", self.name, exc)
            return None

        logger.warning("[MCP:%s] Timeout waiting for response to %s", self.name, method)
        return None

    def _send_notification(self, method: str, params: Dict[str, Any]):
        """Send a JSON-RPC notification (no response expected)."""
        if not self.process or self.process.poll() is not None:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        try:
            with self._write_lock:
                payload = json.dumps(notification) + "\n"
                self.process.stdin.write(payload.encode())
                self.process.stdin.flush()
        except Exception as exc:
            logger.warning("[MCP:%s] Failed to send notification: %s", self.name, exc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": "docker",
            "mode": "oneshot" if self.oneshot else "persistent",
            "status": self.status,
            "tools": [t.get("name", "unknown") for t in self.tools],
            "tool_schemas": self.tools,
            "command": self.command,
            "args": self.args,
        }


class MCPDockerBridge:
    """Manages multiple Docker MCP server processes."""

    def __init__(self):
        self._servers: Dict[str, MCPDockerServer] = {}
        self._lock = threading.Lock()

    def _save_config(self):
        import pathlib
        config_path = pathlib.Path(__file__).resolve().parents[1] / "mcp_config.json"
        try:
            config = {}
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
            
            if "mcpServers" not in config:
                config["mcpServers"] = {}
            
            config["mcpServers"] = {
                name: {
                    "command": s.command,
                    "args": s.args,
                    "env": s.env,
                    "autoStart": True
                }
                for name, s in self._servers.items()
            }
            
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            logger.info("Saved mcp_config.json")
        except Exception as exc:
            logger.error("Failed to save mcp_config.json: %s", exc)

    def load_config(self, config: Dict[str, Any]):
        """Load MCP server configs from a dict (mcp_config.json format)."""
        servers_config = config.get("mcpServers", {})
        for name, cfg in servers_config.items():
            if cfg.get("type") == "docker" or cfg.get("command") == "docker":
                self.register_server(
                    name=name,
                    command=cfg.get("command", "docker"),
                    args=cfg.get("args", []),
                    env=cfg.get("env", {}),
                    auto_start=cfg.get("autoStart", True),
                )

    def register_server(self, name: str, command: str, args: List[str],
                        env: Optional[Dict[str, str]] = None,
                        auto_start: bool = True,
                        oneshot: Optional[bool] = None) -> Dict[str, Any]:
        """Register and optionally start a Docker MCP server."""
        with self._lock:
            if name in self._servers:
                self._servers[name].stop()

            server = MCPDockerServer(name, command, args, env, oneshot=oneshot)
            self._servers[name] = server

        if auto_start:
            server.start()

        self._save_config()

        return server.to_dict()

    def remove_server(self, name: str) -> bool:
        with self._lock:
            server = self._servers.pop(name, None)
        if server:
            server.stop()
            self._save_config()
            return True
        return False

    def get_server(self, name: str) -> Optional[MCPDockerServer]:
        return self._servers.get(name)

    def list_servers(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._servers.values()]

    def list_all_tools(self) -> List[Dict[str, Any]]:
        """Return all tools from all running Docker MCP servers."""
        tools = []
        for server in self._servers.values():
            if server.status == "running":
                for tool in server.tools:
                    tools.append({
                        "server": server.name,
                        "tool": tool.get("name", "unknown"),
                        "description": tool.get("description", ""),
                        "schema": tool.get("inputSchema", {}),
                    })
        return tools

    def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        server = self._servers.get(server_name)
        if not server:
            return {
                "ok": False,
                "error_type": "not_found",
                "status_code": 404,
                "error": f"Docker MCP server '{server_name}' not found",
            }
        return server.call_tool(tool_name, arguments)

    def restart_server(self, name: str) -> Dict[str, Any]:
        server = self._servers.get(name)
        if not server:
            return {"error": f"Server '{name}' not found"}
        server.stop()
        server.start()
        return server.to_dict()

    def stop_all(self):
        for server in self._servers.values():
            server.stop()

    def start_all(self):
        for server in self._servers.values():
            if server.status == "stopped":
                server.start()


# ── Singleton ──
mcp_bridge = MCPDockerBridge()
