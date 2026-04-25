"""Docker MCP STDIO Bridge.

Manages Docker-based MCP servers via JSON-RPC 2.0 over STDIO transport.
Supports lifecycle management:  start → discover tools → call tools → stop.
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
    """A single running Docker MCP server process."""

    def __init__(self, name: str, command: str, args: List[str], env: Optional[Dict[str, str]] = None):
        self.name = name
        self.command = command
        self.args = args
        self.env = env or {}
        self.process: Optional[subprocess.Popen] = None
        self.tools: List[Dict[str, Any]] = []
        self.status: str = "stopped"  # stopped | starting | running | error
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._initialized = False

    def start(self) -> bool:
        """Start the Docker container and initialize the MCP session."""
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

            # MCP protocol: initialize handshake
            init_result = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "astra-voice", "version": "1.0.0"}
            })

            if init_result is None:
                self.status = "error"
                return False

            # Send initialized notification
            self._send_notification("notifications/initialized", {})
            self._initialized = True

            # Discover tools
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
        """Stop the Docker container."""
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
        """Call a tool via JSON-RPC over STDIO."""
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

        # MCP tool results have a "content" array
        if "content" in result:
            texts = []
            for item in result["content"]:
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
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
                        auto_start: bool = True) -> Dict[str, Any]:
        """Register and optionally start a Docker MCP server."""
        with self._lock:
            if name in self._servers:
                self._servers[name].stop()

            server = MCPDockerServer(name, command, args, env)
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
